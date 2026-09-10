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
        if seq == 0:
            return {"phase": "not_observed"}
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


def descriptor_sample(pid):
    counts = {"socket": 0, "pipe": 0, "anon_inode": 0, "other": 0}
    seen = 0
    try:
        with os.scandir(f"/proc/{pid}/fd") as entries:
            for entry in entries:
                seen += 1
                if seen > 2048:
                    break
                try:
                    target = os.readlink(entry.path)
                    category = next((name for name in ("socket", "pipe", "anon_inode")
                                     if target.startswith(name + ":")), "other")
                    counts[category] += 1
                except OSError:
                    pass
    except OSError:
        return {"unavailable": True}
    # Do not record filenames, remote addresses or socket inodes.
    return {"counts_sample": counts, "truncated": seen > 2048}


def http_socket_sample(pid):
    result = {"port": 5173, "namespace_states_sample": {}, "listener_rx_queue": []}
    for table in ("tcp", "tcp6"):
        try:
            content = read_small(f"/proc/{pid}/net/{table}", 131072)
            if len(content) >= 131072:
                result["table_truncated"] = True
            for line in content.splitlines()[1:1025]:
                fields = line.split()
                if len(fields) < 5 or int(fields[1].rsplit(":", 1)[1], 16) != 5173:
                    continue
                state = fields[3]
                states = result["namespace_states_sample"]
                states[state] = states.get(state, 0) + 1
                if state == "0A":
                    result["listener_rx_queue"].append(int(fields[4].split(":")[1], 16))
        except (OSError, ValueError, IndexError):
            continue
    return result


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
                           **({**thread_sample(args.pid), "file_descriptors": descriptor_sample(args.pid),
                               "http_sockets": http_socket_sample(args.pid)} if index % 3 == 0 else {})})
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
