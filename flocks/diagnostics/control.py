"""Release ZIP friendly CLI. Run: .venv/bin/python flocks/diagnostics/control.py --help.

Does not import the application, require HTTP responsiveness, sudo, pip or git.
"""

import argparse
import hashlib
import json
import os
import sys
import tarfile
import time
from datetime import datetime
from pathlib import Path

from common import config_path, process_sample, read_small, root_dir


SOURCE_FILES = (
    "flocks/server/app.py", "flocks/workflow/runner.py", "flocks/workflow/engine.py",
    "flocks/workflow/llm.py", "flocks/workflow/repl_runtime.py", "flocks/workflow/_async_runtime.py",
    "flocks/workflow/store.py", "flocks/ingest/syslog/listener.py", "flocks/ingest/syslog/manager.py",
    "flocks/server/routes/knowledge.py", "flocks/diagnostics/memory.py", "flocks/diagnostics/sidecar.py",
    "flocks/diagnostics/common.py", "flocks/diagnostics/control.py",
)


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(f".{os.getpid()}.tmp")
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2)
    temporary.replace(path)


def latest():
    value = json.loads(read_small(root_dir() / "memory-diagnostics-latest.json", 8192))
    directory = Path(value["directory"]).resolve()
    allowed = (root_dir() / "workspace" / "outputs").resolve()
    if not directory.is_relative_to(allowed) or directory.parent.name != "memory-diagnostics":
        raise ValueError("Capture directory is outside diagnostics outputs")
    return value, directory


def fingerprint():
    base = Path(__file__).resolve().parents[2]
    result = {"python": sys.version, "executable": sys.executable, "source_sha256": {}}
    for name in SOURCE_FILES:
        path = base / name
        if path.is_file() and path.stat().st_size < 4 * 1024 * 1024:
            result["source_sha256"][name] = hashlib.sha256(path.read_bytes()).hexdigest()
    workflows = root_dir() / "plugins" / "workflows"
    result["installed_workflow_sha256"] = {}
    if workflows.is_dir():
        # Hash only definitions, never execution history or inputs. Bounded directory scan.
        with os.scandir(workflows) as entries:
            for index, entry in enumerate(entries):
                if index >= 64:
                    result["installed_workflows_truncated"] = True
                    break
                definition = Path(entry.path) / "workflow.json"
                if definition.is_file() and definition.stat().st_size < 2 * 1024 * 1024:
                    result["installed_workflow_sha256"][entry.name[:96]] = hashlib.sha256(definition.read_bytes()).hexdigest()
    return result


def last_record(path):
    with path.open("rb") as stream:
        stream.seek(max(0, path.stat().st_size - 65536))
        lines = stream.read(65536).splitlines()
    for line in reversed(lines):
        try:
            record = json.loads(line)
            return {key: record[key] for key in ("type", "reason", "number", "time") if key in record}
        except (ValueError, UnicodeDecodeError):
            continue
    return {}


def collect(directory):
    # Copy a stable prefix through open descriptors; rotation cannot replace the
    # inode being read. Never glob/tar business outputs, databases or backend.log.
    write_json(directory / "collection.json", {"collected_at": time.time(), **fingerprint()})
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    target = directory.parent / f"flocks-memory-evidence-{stamp}.tar.gz"
    allowed = {"runtime.jsonl", "runtime.jsonl.1", "runtime.jsonl.2",
               "allocations.jsonl", "allocations.jsonl.1", "allocations.jsonl.2",
               "os.jsonl", "os.jsonl.1", "os.jsonl.2", "gc-state.bin", "collection.json", "markers.jsonl"}
    fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb") as output, tarfile.open(fileobj=output, mode="w:gz") as archive:
        for name in sorted(allowed):
            path = directory / name
            if not path.is_file() or path.is_symlink():
                continue
            try:
                with path.open("rb") as stream:
                    info = archive.gettarinfo(str(path), arcname=f"memory-evidence/{name}", fileobj=stream)
                    if info.size <= 9 * 1024 * 1024:
                        info.uid = info.gid = 0
                        info.uname = info.gname = ""
                        archive.addfile(info, stream)
            except FileNotFoundError:
                continue  # A rotation raced the initial open; later names still captured.
    return target


def main(argv=None):
    parser = argparse.ArgumentParser(description="Flocks 内存诊断：默认关闭，不依赖后端 API")
    commands = parser.add_subparsers(dest="command", required=True)
    enable = commands.add_parser("enable", help="下次后端启动开启诊断")
    enable.add_argument("--allocations", action="store_true", help="显式开启有开销的单帧分配追踪")
    enable.add_argument("--minutes", type=int, default=180, choices=range(1, 361), metavar="1..360")
    enable.add_argument("--trace-seconds", type=int, default=600, choices=range(10, 901), metavar="10..900")
    commands.add_parser("status", help="检查开关、采样时间和 PID，不请求后端")
    commands.add_parser("collect", help="仅打包最新诊断目录，后端卡死时也可运行")
    commands.add_parser("trace", help="对当前运行中的诊断采集请求一个有限分配追踪窗口")
    commands.add_parser("disable", help="关闭后续启动诊断；通知当前采集停止，不重启服务")
    mark = commands.add_parser("mark", help="标记复現阶段；不接受业务正文")
    mark.add_argument("phase", choices=("idle", "syslog-on", "denoise-on", "triage-on", "soc-open", "soc-closed", "growth", "before-restart"))
    args = parser.parse_args(argv)
    try:
        if args.command == "enable":
            write_json(config_path(), {"enabled": True, "allocations": args.allocations,
                                       "duration_s": args.minutes * 60, "trace_seconds": args.trace_seconds})
            print(f"已写入 {config_path()}。请执行 flocks restart --server-only 后再执行 status。")
            if args.allocations:
                print("分配追踪有 CPU/内存开销，最多 900 秒，达到安全阈值会提前停止；系统采样继续。")
            return
        if args.command == "disable":
            write_json(config_path(), {"enabled": False})
            try:
                _, directory = latest()
                (directory / "STOP").touch(mode=0o600, exist_ok=True)
            except (OSError, ValueError, KeyError):
                pass
            print("已关闭；当前进程可运行时将自行停止埋点，独立系统采样约 5 秒内停止。未重启服务。")
            return
        value, directory = latest()
        if args.command == "collect":
            print(f"请发送：{collect(directory)}")
        elif args.command == "trace":
            (directory / "TRACE").touch(mode=0o600, exist_ok=True)
            print("已请求追踪；需后端诊断线程仍在运行。超过 4 GiB 或已有追踪时不会重复开启。")
        elif args.command == "mark":
            path = directory / "markers.jsonl"
            if path.exists() and path.stat().st_size > 64 * 1024:
                raise ValueError("Marker size limit reached")
            fd = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
            with os.fdopen(fd, "a", encoding="utf-8") as stream:
                stream.write(json.dumps({"time": time.time(), "phase": args.phase}) + "\n")
            print(f"已标记：{args.phase}")
        else:
            result = {**value, "configured": json.loads(read_small(config_path(), 8192)),
                      "source": fingerprint(), "stop_requested": (directory / "STOP").exists()}
            for name in ("runtime.jsonl", "os.jsonl", "allocations.jsonl"):
                path = directory / name
                if path.exists():
                    result[name] = {"bytes": path.stat().st_size, "updated_seconds_ago": round(time.time() - path.stat().st_mtime, 1)}
                    result[name]["last_record"] = last_record(path)
            try:
                result["process_now"] = process_sample(int(value["pid"]))
                if value.get("start_ticks") is not None:
                    result["same_process"] = value["start_ticks"] == result["process_now"]["start_ticks"]
            except (OSError, ValueError, IndexError):
                result["process_now"] = "not available (exited or non-Linux)"
            print(json.dumps(result, ensure_ascii=False, indent=2))
    except (OSError, ValueError, KeyError) as error:
        print(f"诊断命令未完成：{error}。尚无采集记录时，请先 enable 并重启后端。", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
