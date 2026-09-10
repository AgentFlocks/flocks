"""Build/install/restore the narrowly scoped memory diagnostic overlay.

Build needs git on the developer machine; install/restore need only Python's
standard library on the release-ZIP machine. All edits are validated before any
source is replaced; custom knowledge routes in app.py are preserved.
"""

import argparse
import ast
import difflib
import hashlib
import json
import os
import shutil
import subprocess
import tempfile
import zipfile
from datetime import datetime
from pathlib import Path


BASE = "5678bddef031900e51f43cd34e4eea098d6b3bd3"
HOOKS = ("flocks/server/app.py", "flocks/workflow/runner.py", "flocks/workflow/engine.py",
         "flocks/workflow/llm.py", "flocks/workflow/repl_runtime.py", "flocks/ingest/syslog/listener.py")


def sha(data):
    return hashlib.sha256(data).hexdigest()


def atomic_write(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    mode = path.stat().st_mode & 0o777 if path.exists() else 0o644
    fd, name = tempfile.mkstemp(prefix=".memory-diag-", dir=path.parent)
    temporary = Path(name)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
        temporary.chmod(mode)
        temporary.replace(path)
    finally:
        if temporary.exists():
            temporary.unlink()


def exact_target(root, name):
    path = root / name
    if not name.startswith("flocks/") or not path.resolve().is_relative_to(root.resolve()):
        raise ValueError(f"Unsafe target: {name}")
    return path


def build(root, output):
    changes = []
    for name in HOOKS:
        before = subprocess.check_output(["git", "show", f"{BASE}:{name}"], cwd=root)
        after = (root / name).read_bytes()
        old_lines = before.decode().splitlines(keepends=True)
        new_lines = after.decode().splitlines(keepends=True)
        replacements = []
        matcher = difflib.SequenceMatcher(None, old_lines, new_lines, autojunk=False)
        for group in matcher.get_grouped_opcodes(3):
            edits = [item for item in group if item[0] != "equal"]
            start, end = edits[0], edits[-1]
            for context in (3, 8, 16, 32):
                old = "".join(old_lines[max(0, start[1] - context):end[2] + context])
                new = "".join(new_lines[max(0, start[3] - context):end[4] + context])
                if before.decode().count(old) == 1:
                    break
            else:
                raise ValueError(f"Non-unique patch context: {name}")
            replacements.append({"before": old, "after": new})
        changes.append({"path": name, "base_sha256": sha(before), "replacements": replacements})
    manifest = {"schema": 1, "base_commit": BASE, "changes": changes, "new_files": {}}
    output.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(output, "x", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.write(Path(__file__), "install.py")
        for path in sorted((root / "flocks/diagnostics").glob("*.py")):
            name = path.relative_to(root).as_posix()
            data = path.read_bytes()
            manifest["new_files"][name] = sha(data)
            archive.writestr("payload/" + name, data)
        archive.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2))
    print(output)


def prepare(root, bundle):
    manifest = json.loads((bundle / "manifest.json").read_text())
    planned = {}
    for change in manifest["changes"]:
        name = change["path"]
        path = exact_target(root, name)
        data = path.read_bytes()
        # app.py may contain the known extra knowledge router or other independent
        # local routes. Apply only unique contexts there, never overwrite the file.
        if name != "flocks/server/app.py" and sha(data) != change["base_sha256"]:
            raise ValueError(f"基线不匹配，未修改任何文件：{name}")
        text = data.decode("utf-8")
        if "flocks.diagnostics.memory" in text:
            raise ValueError(f"已存在诊断补丁，请勿重复安装：{name}")
        for replacement in change["replacements"]:
            if text.count(replacement["before"]) != 1:
                raise ValueError(f"上下文不唯一或代码不同，未修改任何文件：{name}")
            text = text.replace(replacement["before"], replacement["after"], 1)
        ast.parse(text, filename=name)
        planned[name] = (data, text.encode("utf-8"))
    for name, expected in manifest["new_files"].items():
        path = exact_target(root, name)
        if path.exists():
            raise ValueError(f"目标已存在，未修改任何文件：{name}")
        data = (bundle / "payload" / name).read_bytes()
        if sha(data) != expected:
            raise ValueError(f"补丁文件校验失败：{name}")
        ast.parse(data, filename=name)
        planned[name] = (None, data)
    return planned


def install(root, bundle):
    planned = prepare(root, bundle)
    data_root = Path(os.environ.get("FLOCKS_ROOT") or Path.home() / ".flocks").expanduser()
    backup = data_root / "diagnostic-backups" / datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    backup.mkdir(parents=True, mode=0o700, exist_ok=False)
    records = {}
    for name, (before, after) in planned.items():
        if before is not None:
            path = backup / "original" / name
            atomic_write(path, before)
        records[name] = {"before": sha(before) if before is not None else None, "after": sha(after)}
    atomic_write(backup / "restore.json", json.dumps({"root": str(root.resolve()), "files": records}).encode())
    written = []
    try:
        for name, (_, after) in planned.items():
            atomic_write(exact_target(root, name), after)
            written.append(name)
    except Exception:
        for name in reversed(written):
            before = planned[name][0]
            path = exact_target(root, name)
            if before is None:
                saved = backup / "failed-install" / name
                saved.parent.mkdir(parents=True, exist_ok=True)
                path.replace(saved)
            else:
                atomic_write(path, before)
        raise
    print(f"诊断补丁已安装，未改变配置、套件、数据库或运行中的进程。备份：{backup}")
    print("下一步：执行诊断 enable，再运行 flocks restart --server-only。")


def restore(backup):
    manifest = json.loads((backup / "restore.json").read_text())
    root = Path(manifest["root"])
    planned = {}
    for name, hashes in manifest["files"].items():
        path = exact_target(root, name)
        if sha(path.read_bytes()) != hashes["after"]:
            raise ValueError(f"安装后代码又有变化，拒绝覆盖：{name}")
        original = backup / "original" / name
        data = original.read_bytes() if hashes["before"] is not None else None
        if data is not None and sha(data) != hashes["before"]:
            raise ValueError(f"备份校验失败：{name}")
        planned[name] = data
    for name, data in planned.items():
        target = exact_target(root, name)
        if data is None:
            saved = backup / "removed-diagnostics" / name
            saved.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(target), str(saved))
        else:
            atomic_write(target, data)
    print("已恢复原代码；诊断新增文件移至备份目录，可恢复。请执行 flocks restart --server-only。")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    pack = commands.add_parser("build")
    pack.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    pack.add_argument("--output", type=Path, required=True)
    deploy = commands.add_parser("install")
    deploy.add_argument("--root", type=Path, required=True)
    deploy.add_argument("--bundle", type=Path, default=Path(__file__).resolve().parent)
    undo = commands.add_parser("restore")
    undo.add_argument("--backup", type=Path, required=True)
    args = parser.parse_args()
    try:
        if args.command == "build":
            build(args.root.resolve(), args.output)
        elif args.command == "install":
            install(args.root.resolve(), args.bundle)
        else:
            restore(args.backup)
    except (OSError, ValueError, SyntaxError) as error:
        parser.exit(1, f"操作未完成：{error}\n")


if __name__ == "__main__":
    main()
