"""Standard-library-only helpers shared with the independent Linux observer."""

import json
import os
import struct
import time
from pathlib import Path


SCHEMA = 1
GC_RECORD = struct.Struct("<QQQdQQQ")  # sequence, phase, generation, monotonic, tid, collected, uncollectable
MAX_LOG_BYTES = 8 * 1024 * 1024


def root_dir():
    return Path(os.environ.get("FLOCKS_ROOT") or Path.home() / ".flocks").expanduser()


def config_path():
    return root_dir() / "memory-diagnostics.json"


def read_small(path, limit=16384):
    with open(path, "r", encoding="utf-8", errors="replace") as stream:
        return stream.read(limit)


def process_sample(pid, proc=Path("/proc")):
    """No smaps/heap walk, command line, environment, or business data."""
    base = proc / str(pid)
    result = {"pid": pid}
    fields = read_small(base / "stat").rsplit(")", 1)[1].split()
    result.update(state=fields[0], minor_faults=int(fields[7]), major_faults=int(fields[9]),
                  user_ticks=int(fields[11]), system_ticks=int(fields[12]), start_ticks=int(fields[19]))
    for line in read_small(base / "status").splitlines():
        key, _, value = line.partition(":")
        if key in {"VmRSS", "VmSwap", "VmSize", "VmHWM", "RssAnon", "RssFile", "RssShmem", "Threads"}:
            result[key] = int(value.split()[0])
    for name in ("io", "wchan"):
        try:
            result[name] = read_small(base / name, 2048).strip()
        except OSError:
            pass
    return result


class Journal:
    """One writer per file; bounded retention and JSON line size; no logging recursion."""

    def __init__(self, path, max_bytes=MAX_LOG_BYTES, backups=2):
        self.path = Path(path)
        self.max_bytes = max_bytes
        self.backups = backups

    def write(self, event):
        self._append(self._encode(event))

    @staticmethod
    def _encode(event):
        line = json.dumps({"schema": SCHEMA, "time": time.time(), **event}, ensure_ascii=True,
                          separators=(",", ":")) + "\n"
        if len(line) > 512 * 1024:
            line = json.dumps({"type": "record_dropped", "reason": "oversized_record"}) + "\n"
        return line

    def write_many(self, events):
        # The caller supplies a bounded batch; avoid one open/stat per workflow event.
        batch = "".join(self._encode(event) for event in events)
        if batch:
            self._append(batch)

    def _append(self, line):
        if self.path.exists() and self.path.stat().st_size + len(line) > self.max_bytes:
            for index in range(self.backups, 0, -1):
                source = self.path if index == 1 else Path(f"{self.path}.{index - 1}")
                if source.exists():
                    source.replace(Path(f"{self.path}.{index}"))
        fd = os.open(self.path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
        with os.fdopen(fd, "a", encoding="utf-8") as stream:
            stream.write(line)
