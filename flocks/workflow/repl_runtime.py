"""Runtime abstraction for executing workflow nodes."""

from __future__ import annotations

import contextlib
import io
import json
import os
import shlex
import subprocess
import threading
import traceback
import uuid
from concurrent.futures import TimeoutError as _FuturesTimeoutError
from dataclasses import dataclass, field
from typing import Any, Dict, Optional, TextIO, Tuple

from .errors import NodeExecutionError
from .llm import get_lazy_llm
from .tools import ToolFacade, get_tool_registry


class Runtime:
    def execute(self, code: str, inputs: Dict[str, Any]) -> Tuple[Dict[str, Any], str]:
        raise NotImplementedError

    def reset(self) -> None:
        raise NotImplementedError


_RPC_MAX_BYTES = 4 * 1024 * 1024
_WORKFLOW_SITE_PACKAGES = "/workspace/.flocks/workflow/site-packages"


def _drain_text_stream(stream: TextIO, chunks: list[str]) -> None:
    try:
        while True:
            line = stream.readline()
            if line == "":
                break
            chunks.append(line)
    except Exception:
        return


@dataclass
class PythonExecRuntime(Runtime):
    globals: Dict[str, Any] = field(default_factory=dict)
    tool_registry: Optional[Any] = None  # FlocksToolAdapter or compatible

    def execute(self, code: str, inputs: Dict[str, Any]) -> Tuple[Dict[str, Any], str]:
        if not isinstance(code, str):
            raise NodeExecutionError(node_id="<runtime>", message=f"Code must be a string, got {type(code).__name__}")
        if not code.strip():
            raise NodeExecutionError(node_id="<runtime>", message="Code cannot be empty or whitespace-only")
        if not isinstance(inputs, dict):
            raise NodeExecutionError(node_id="<runtime>", message=f"Inputs must be a dict, got {type(inputs).__name__}")

        g = self.globals
        g["inputs"] = inputs
        g["outputs"] = {}
        g.setdefault("llm", get_lazy_llm())
        reg = self.tool_registry or get_tool_registry()
        g.setdefault("tool", ToolFacade(reg) if not isinstance(reg, ToolFacade) else reg)

        def get_path(path: str, data: Any = None) -> Any:
            if data is None:
                data = g.get("inputs", {})
            if path is None:
                return None
            p = str(path).strip()
            if not p:
                return None
            if p == "$":
                return data
            if p.startswith("$."):
                p = p[2:]
            cur: Any = data
            for part in p.split("."):
                if isinstance(cur, dict):
                    if part in cur:
                        cur = cur[part]
                    else:
                        return None
                elif isinstance(cur, list):
                    try:
                        idx = int(part)
                    except Exception:
                        return None
                    if 0 <= idx < len(cur):
                        cur = cur[idx]
                    else:
                        return None
                else:
                    return None
            return cur

        g.setdefault("get_path", get_path)

        buf = io.StringIO()
        try:
            with contextlib.redirect_stdout(buf):
                exec(code, g, g)
        except SystemExit:
            # Node code called exit() / sys.exit() — treat as early return with
            # whatever has been written to outputs so far.  Do NOT propagate
            # SystemExit; that would kill the asyncio event loop.
            pass
        except _FuturesTimeoutError:
            raise
        except SyntaxError as e:
            tb_str = "".join(traceback.format_exception(type(e), e, e.__traceback__))
            raise NodeExecutionError(
                node_id="<runtime>",
                message=f"Syntax error in code at line {e.lineno}: {e.msg}",
                stdout=buf.getvalue(),
                traceback=tb_str
            ) from e
        except AttributeError as e:
            tb_str = "".join(traceback.format_exception(type(e), e, e.__traceback__))
            error_msg = f"AttributeError: {e}"
            if "'NoneType' object has no attribute" in str(e):
                attr_name = str(e).split("'")[-2] if "'" in str(e) else "unknown"
                error_msg += f"\n提示: 对象为 None，无法访问属性 '{attr_name}'。"
            raise NodeExecutionError(node_id="<runtime>", message=error_msg, stdout=buf.getvalue(), traceback=tb_str) from e
        except KeyError as e:
            tb_str = "".join(traceback.format_exception(type(e), e, e.__traceback__))
            raise NodeExecutionError(
                node_id="<runtime>",
                message=f"Missing required input key: {e}",
                stdout=buf.getvalue(),
                traceback=tb_str
            ) from e
        except NameError as e:
            tb_str = "".join(traceback.format_exception(type(e), e, e.__traceback__))
            raise NodeExecutionError(
                node_id="<runtime>",
                message=f"Undefined variable or function: {e}",
                stdout=buf.getvalue(),
                traceback=tb_str
            ) from e
        except TypeError as e:
            tb_str = "".join(traceback.format_exception(type(e), e, e.__traceback__))
            error_msg = f"Type error during execution: {e}"
            raise NodeExecutionError(node_id="<runtime>", message=error_msg, stdout=buf.getvalue(), traceback=tb_str) from e
        except Exception as e:
            tb_str = "".join(traceback.format_exception(type(e), e, e.__traceback__))
            error_msg = f"Runtime error ({type(e).__name__}): {e}"
            raise NodeExecutionError(node_id="<runtime>", message=error_msg, stdout=buf.getvalue(), traceback=tb_str) from e

        out_obj = g.get("outputs", {})
        if out_obj is None:
            out_obj = {}
        if not isinstance(out_obj, dict):
            raise NodeExecutionError(node_id="<runtime>", message="`outputs` must be a dict")
        return out_obj, buf.getvalue()

    def reset(self) -> None:
        self.globals.clear()


@dataclass
class SandboxPythonExecRuntime(Runtime):
    """Execute workflow python code in sandbox via stdio RPC bridge."""

    sandbox: Dict[str, Any]
    tool_registry: Optional[Any] = None

    def execute(self, code: str, inputs: Dict[str, Any]) -> Tuple[Dict[str, Any], str]:
        if not isinstance(code, str):
            raise NodeExecutionError(
                node_id="<runtime>",
                message=f"Code must be a string, got {type(code).__name__}",
            )
        if not code.strip():
            raise NodeExecutionError(
                node_id="<runtime>",
                message="Code cannot be empty or whitespace-only",
            )
        if not isinstance(inputs, dict):
            raise NodeExecutionError(
                node_id="<runtime>",
                message=f"Inputs must be a dict, got {type(inputs).__name__}",
            )

        container_name = str(self.sandbox.get("container_name") or "").strip()
        if not container_name:
            raise NodeExecutionError(
                node_id="<runtime>",
                message="Sandbox runtime requires sandbox.container_name",
            )
        container_workdir = str(
            self.sandbox.get("container_workdir") or self.sandbox.get("workspace_dir") or "/workspace"
        ).strip()
        env = self.sandbox.get("env")
        if not isinstance(env, dict):
            env = {}
        token = uuid.uuid4().hex
        python_cmd = self._build_python_cmd(code=code, bridge_token=token)
        cmd = [
            "docker",
            "exec",
            "-i",
            "-w",
            container_workdir,
        ]
        for k, v in env.items():
            cmd.extend(["-e", f"{k}={v}"])
        cmd.extend([container_name, "sh", "-lc", python_cmd])
        try:
            proc = subprocess.Popen(
                cmd,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                bufsize=1,
                env={**os.environ, "LC_ALL": "C.UTF-8", "LANG": "C.UTF-8"},
            )
        except Exception as e:
            raise NodeExecutionError(
                node_id="<runtime>",
                message=f"Sandbox execution failed to start: {e}",
            ) from e

        if proc.stdin is None or proc.stdout is None or proc.stderr is None:
            raise NodeExecutionError(node_id="<runtime>", message="Sandbox process stdio is unavailable")

        stderr_chunks: list[str] = []
        stderr_thread = threading.Thread(
            target=_drain_text_stream,
            args=(proc.stderr, stderr_chunks),
            name="wf-sandbox-stderr",
            daemon=True,
        )
        stderr_thread.start()

        final_payload: Optional[Dict[str, Any]] = None
        try:
            self._write_json_line(
                proc.stdin,
                {"type": "init", "token": token, "inputs": inputs},
            )

            while True:
                line = proc.stdout.readline()
                if line == "":
                    break
                if len(line) > _RPC_MAX_BYTES:
                    raise NodeExecutionError(node_id="<runtime>", message="Sandbox RPC message too large")
                msg = self._parse_json_line(line)
                if msg is None:
                    continue
                msg_type = str(msg.get("type") or "").strip().lower()
                if msg_type == "rpc":
                    resp = self._handle_rpc_request(msg=msg, token=token)
                    self._write_json_line(proc.stdin, resp)
                elif msg_type == "final" and msg.get("token") == token:
                    payload = msg.get("payload")
                    final_payload = payload if isinstance(payload, dict) else {}
        finally:
            try:
                proc.stdin.close()
            except Exception:
                pass
            try:
                proc.stdout.close()
            except Exception:
                pass
            exit_code = proc.wait()
            stderr_thread.join(timeout=1.0)

        stderr_text = "".join(stderr_chunks)
        if exit_code != 0:
            msg = stderr_text.strip()
            if not msg:
                msg = f"Sandbox command exited with code {exit_code}"
            raise NodeExecutionError(
                node_id="<runtime>",
                message=f"Sandbox execution failed: {msg}",
                stdout="",
                traceback=stderr_text,
            )

        if final_payload is None:
            raise NodeExecutionError(
                node_id="<runtime>",
                message="Sandbox execution did not produce final payload",
                stdout="",
                traceback=stderr_text,
            )

        stdout = str(final_payload.get("stdout") or "")
        err = final_payload.get("error")
        if err:
            raise NodeExecutionError(
                node_id="<runtime>",
                message=f"Runtime error ({err.get('type', 'Exception')}): {err.get('message', '')}",
                stdout=stdout,
                traceback=str(err.get("traceback") or stderr_text or ""),
            )
        out_obj = final_payload.get("outputs", {})
        if out_obj is None:
            out_obj = {}
        if not isinstance(out_obj, dict):
            raise NodeExecutionError(node_id="<runtime>", message="`outputs` must be a dict")
        return out_obj, stdout

    def reset(self) -> None:
        # Stateless runtime; each execute call runs isolated command.
        return

    def _build_python_cmd(
        self,
        *,
        code: str,
        bridge_token: str,
    ) -> str:
        wrapped = f"""
import contextlib
import io
import json
import os
import sys
import traceback

outputs = {{}}
_MAX = {_RPC_MAX_BYTES}
_TOKEN = {json.dumps(bridge_token, ensure_ascii=False)}

def _read_json_line():
    line = sys.stdin.readline()
    if not line:
        raise RuntimeError("Bridge channel closed")
    if len(line) > _MAX:
        raise RuntimeError("Bridge payload too large")
    obj = json.loads(line)
    if not isinstance(obj, dict):
        raise RuntimeError("Bridge payload must be an object")
    return obj

def _rpc_call(payload):
    global _rpc_seq
    _rpc_seq += 1
    req = {{
        "type": "rpc",
        "token": _TOKEN,
        "id": str(_rpc_seq),
        "rpc": dict(payload),
    }}
    # Always use original stdout for RPC control channel.
    # User code stdout may be redirected for capture.
    _out = sys.__stdout__ if getattr(sys, "__stdout__", None) is not None else sys.stdout
    _out.write(json.dumps(req, ensure_ascii=False, default=str) + "\\n")
    _out.flush()
    resp = _read_json_line()
    if (
        resp.get("type") != "rpc_result"
        or resp.get("token") != _TOKEN
        or str(resp.get("id")) != str(_rpc_seq)
    ):
        raise RuntimeError("Invalid RPC response frame")
    if not resp.get("ok", False):
        raise RuntimeError(str(resp.get("error") or "Bridge request failed"))
    return resp.get("output")

init = _read_json_line()
if init.get("type") != "init" or init.get("token") != _TOKEN:
    raise RuntimeError("Invalid init frame")
inputs = init.get("inputs", {{}})
if not isinstance(inputs, dict):
    raise RuntimeError("inputs must be an object")
_rpc_seq = 0

class _ToolProxy:
    def run(self, name, **kwargs):
        return _rpc_call({{"kind": "tool", "name": name, "kwargs": kwargs}})

    def run_safe(self, name, **kwargs):
        return _rpc_call({{"kind": "tool_safe", "name": name, "kwargs": kwargs}})

class _LLMProxy:
    def ask(
        self,
        prompt,
        temperature=0.2,
        model=None,
        provider_id=None,
        timeout_s=None,
        max_retries=0,
        retry_delay_s=1.0,
    ):
        return _rpc_call({{
            "kind": "llm",
            "prompt": prompt,
            "temperature": temperature,
            "model": model,
            "provider_id": provider_id,
            "timeout_s": timeout_s,
            "max_retries": max_retries,
            "retry_delay_s": retry_delay_s,
        }})

def get_path(path, data=None):
    if data is None:
        data = inputs
    if path is None:
        return None
    p = str(path).strip()
    if not p:
        return None
    if p == "$":
        return data
    if p.startswith("$."):
        p = p[2:]
    cur = data
    for part in p.split("."):
        if isinstance(cur, dict):
            if part in cur:
                cur = cur[part]
            else:
                return None
        elif isinstance(cur, list):
            try:
                idx = int(part)
            except Exception:
                return None
            if 0 <= idx < len(cur):
                cur = cur[idx]
            else:
                return None
        else:
            return None
    return cur

g = {{
    "inputs": inputs,
    "outputs": outputs,
    "get_path": get_path,
    "tool": _ToolProxy(),
    "llm": _LLMProxy(),
}}

_extra_site = {json.dumps(_WORKFLOW_SITE_PACKAGES, ensure_ascii=False)}
if _extra_site and os.path.isdir(_extra_site) and _extra_site not in sys.path:
    sys.path.insert(0, _extra_site)

buf = io.StringIO()
payload = {{"outputs": {{}}, "stdout": "", "error": None}}
try:
    with contextlib.redirect_stdout(buf):
        exec({json.dumps(code, ensure_ascii=False)}, g, g)
    out = g.get("outputs", {{}})
    if out is None:
        out = {{}}
    if not isinstance(out, dict):
        raise TypeError("`outputs` must be a dict")
    payload["outputs"] = out
except Exception as e:
    payload["error"] = {{
        "type": type(e).__name__,
        "message": str(e),
        "traceback": traceback.format_exc(),
    }}
finally:
    payload["stdout"] = buf.getvalue()

sys.stdout.write(json.dumps({{"type": "final", "token": _TOKEN, "payload": payload}}, ensure_ascii=False) + "\\n")
sys.stdout.flush()
"""
        return f"python3 -I -c {shlex.quote(wrapped)}"

    def _write_json_line(self, stream: TextIO, payload: Dict[str, Any]) -> None:
        stream.write(json.dumps(payload, ensure_ascii=False, default=str) + "\n")
        stream.flush()

    def _parse_json_line(self, raw_line: str) -> Optional[Dict[str, Any]]:
        text = (raw_line or "").strip()
        if not text:
            return None
        try:
            obj = json.loads(text)
        except Exception:
            return None
        return obj if isinstance(obj, dict) else None

    def _handle_rpc_request(self, *, msg: Dict[str, Any], token: str) -> Dict[str, Any]:
        req_id = str(msg.get("id") or "")
        if msg.get("token") != token:
            return {"type": "rpc_result", "token": token, "id": req_id, "ok": False, "error": "Invalid bridge token"}
        rpc = msg.get("rpc")
        if not isinstance(rpc, dict):
            return {"type": "rpc_result", "token": token, "id": req_id, "ok": False, "error": "Invalid RPC payload"}

        kind = str(rpc.get("kind") or "").strip().lower()
        try:
            if kind in ("tool", "tool_safe"):
                name = str(rpc.get("name") or "").strip()
                if not name:
                    raise RuntimeError("Tool name is required")
                if name == "run_workflow":
                    raise RuntimeError("Tool 'run_workflow' is not available inside workflow sandbox bridge")
                kwargs = rpc.get("kwargs", {})
                if not isinstance(kwargs, dict):
                    raise RuntimeError("Tool kwargs must be an object")
                registry = self.tool_registry or get_tool_registry()
                if kind == "tool_safe":
                    output = registry.run_safe(name, **kwargs)
                else:
                    output = registry.run(name, **kwargs)
                return {"type": "rpc_result", "token": token, "id": req_id, "ok": True, "output": output}

            if kind == "llm":
                prompt = rpc.get("prompt")
                if not isinstance(prompt, str) or not prompt.strip():
                    raise RuntimeError("LLM prompt is required")
                temperature_raw = rpc.get("temperature", 0.2)
                try:
                    temperature = float(temperature_raw)
                except Exception:
                    temperature = 0.2
                model = rpc.get("model")
                provider_id = rpc.get("provider_id")
                timeout_raw = rpc.get("timeout_s")
                max_retries_raw = rpc.get("max_retries", 0)
                retry_delay_raw = rpc.get("retry_delay_s", 1.0)
                if model is not None and not isinstance(model, str):
                    raise RuntimeError("LLM model must be a string when provided")
                if provider_id is not None and not isinstance(provider_id, str):
                    raise RuntimeError("LLM provider_id must be a string when provided")
                timeout_s = None
                if timeout_raw is not None:
                    try:
                        timeout_s = float(timeout_raw)
                    except Exception as exc:
                        raise RuntimeError("LLM timeout_s must be a number when provided") from exc
                try:
                    max_retries = int(max_retries_raw)
                except Exception as exc:
                    raise RuntimeError("LLM max_retries must be an integer when provided") from exc
                try:
                    retry_delay_s = float(retry_delay_raw)
                except Exception as exc:
                    raise RuntimeError("LLM retry_delay_s must be a number when provided") from exc
                output = get_lazy_llm().ask(
                    prompt,
                    temperature=temperature,
                    model=model,
                    provider_id=provider_id,
                    timeout_s=timeout_s,
                    max_retries=max_retries,
                    retry_delay_s=retry_delay_s,
                )
                return {"type": "rpc_result", "token": token, "id": req_id, "ok": True, "output": output}

            raise RuntimeError(f"Unknown RPC kind: {kind}")
        except Exception as e:
            return {"type": "rpc_result", "token": token, "id": req_id, "ok": False, "error": str(e)}


@dataclass
class PythonREPLRuntime(Runtime):
    _repl: Optional[object] = None

    def __post_init__(self) -> None:
        if self._repl is None:
            try:
                from langchain_experimental.utilities import PythonREPL  # type: ignore
            except Exception as e:
                raise RuntimeError(
                    "LangChain PythonREPL is not available. Install: langchain and langchain-experimental."
                ) from e
            self._repl = PythonREPL()

    def execute(self, code: str, inputs: Dict[str, Any]) -> Tuple[Dict[str, Any], str]:
        assert self._repl is not None
        marker = "__WF_OUTPUTS__:"
        inputs_json = json.dumps(inputs, ensure_ascii=False, default=str)
        wrapped = (
            f"import json as __json\n"
            f"inputs = __json.loads({inputs_json!r})\n"
            f"outputs = {{}}\n"
            f"{code}\n"
            f"print({marker!r} + __json.dumps(outputs, ensure_ascii=False, default=str))\n"
        )
        try:
            out = self._repl.run(wrapped)  # type: ignore[attr-defined]
            stdout = "" if out is None else str(out)
        except Exception as e:
            raise NodeExecutionError(node_id="<runtime>", message=str(e)) from e
        outputs: Dict[str, Any] = {}
        for line in stdout.splitlines():
            if line.startswith(marker):
                payload = line[len(marker):]
                try:
                    obj = json.loads(payload) if payload else {}
                except Exception:
                    obj = {}
                if isinstance(obj, dict):
                    outputs = obj
        cleaned_lines = [ln for ln in stdout.splitlines() if not ln.startswith(marker)]
        cleaned_stdout = "\n".join(cleaned_lines)
        if stdout.endswith("\n"):
            cleaned_stdout += "\n"
        return outputs, cleaned_stdout

    def reset(self) -> None:
        self._repl = None
        self.__post_init__()
