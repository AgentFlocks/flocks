"""Run by file path: never import flocks, acquire its GIL, or call its HTTP API."""

import argparse
import os
import time
from pathlib import Path

from common import GC_RECORD, Journal, process_sample, read_small


def system_sample(pid):
    result = {}
    for name in ("meminfo", "pressure/memory"):
        try:
            result[name] = read_small(Path("/proc") / name, 8192)
        except OSError:
            pass
    # v2 cgroup values may be unavailable in some mount namespaces.
    try:
        group = next(line[3:] for line in read_small(f"/proc/{pid}/cgroup").splitlines()
                     if line.startswith("0::"))
        if ".." not in Path(group).parts:
            base = Path("/sys/fs/cgroup") / group.lstrip("/")
            for name in ("memory.current", "memory.max", "memory.swap.current", "memory.events"):
                try:
                    result[name] = read_small(base / name, 1024).strip()
                except OSError:
                    pass
    except (OSError, StopIteration):
        pass
    return result


def gc_sample(directory):
    try:
        with open(directory / "gc-state.bin", "rb") as stream:
            first = stream.read(GC_RECORD.size)
            stream.seek(0)
            second = stream.read(GC_RECORD.size)
        if first != second or len(first) != GC_RECORD.size:
            return {"inconsistent": True}
        seq, phase, generation, when, tid, collected, uncollectable = GC_RECORD.unpack(first)
        if seq % 2:
            return {"inconsistent": True}
        return {"sequence": seq, "phase": "start" if phase == 1 else "stop",
                "generation": generation, "native_tid": tid, "collected": collected,
                "uncollectable": uncollectable, "age_s": max(0, time.monotonic() - when)}
    except OSError:
        return None


def thread_sample(pid):
    rows = []
    children = set()
    scanned = 0
    try:
        with os.scandir(f"/proc/{pid}/task") as entries:
            for entry in entries:
                scanned += 1
                if scanned > 256:
                    break
                try:
                    base = Path(entry.path)
                    fields = read_small(base / "stat", 2048).rsplit(")", 1)[1].split()
                    if (fields[0] in {"D", "R"} or entry.name == str(pid)) and len(rows) < 32:
                        rows.append({"tid": int(entry.name), "state": fields[0],
                                     "major_faults": int(fields[9]), "wchan": read_small(base / "wchan", 128)})
                    if len(children) < 32:
                        children.update(int(value) for value in read_small(base / "children", 256).split()[:8])
                except (OSError, ValueError, IndexError):
                    continue
    except OSError:
        pass
    child_rows = []
    for child in sorted(children)[:32]:
        try:
            child_rows.append(process_sample(child))
        except (OSError, ValueError, IndexError):
            pass
    return {"threads": rows, "task_scan_truncated": scanned > 256, "direct_children_sample": child_rows}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--pid", type=int, required=True)
    parser.add_argument("--start-ticks", type=int, required=True)
    parser.add_argument("--directory", type=Path, required=True)
    parser.add_argument("--seconds", type=int, default=10800)
    args = parser.parse_args()
    journal = Journal(args.directory / "os.jsonl")
    deadline = time.monotonic() + min(max(args.seconds, 1), 21600)
    journal.write({"type": "observer_start", "observer_pid": os.getpid(), "target_pid": args.pid})
    index = 0
    reason = "time_limit"
    while time.monotonic() < deadline:
        try:
            sample = process_sample(args.pid)
            if sample["start_ticks"] != args.start_ticks or sample["state"] == "Z":
                reason = "target_exited_or_reused"
                break
            if (args.directory / "STOP").exists():
                reason = "operator_stop"
                break
            journal.write({"type": "os_sample", "process": sample, "system": system_sample(args.pid),
                           "gc": gc_sample(args.directory),
                           **(thread_sample(args.pid) if index % 3 == 0 else {})})
            index += 1
        except (FileNotFoundError, ProcessLookupError):
            reason = "target_exited"
            break
        except (OSError, ValueError, IndexError) as error:
            # Disk failure ends this observer, never restarts or signals the backend.
            try:
                journal.write({"type": "observer_error", "error_type": type(error).__name__})
            except OSError:
                return
        time.sleep(5)
    journal.write({"type": "observer_stop", "reason": reason})


if __name__ == "__main__":
    main()
