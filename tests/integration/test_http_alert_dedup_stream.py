#!/usr/bin/env python3
# NOTE: standalone manual integration test — not a pytest test, run directly with python3.
"""
手动集成测试工具：流式模拟脚本，逐条读取 tdp_logs.json，逐条 POST 到 http_alert_dedup 的
/invoke 接口，汇总去重结果写入 output 文件。需要 flocks 服务运行，http_alert_dedup 工作流已发布。

用法:
    python3 scripts/stream_tdp_invoke.py [--input FILE] [--batch-size N] [--delay SEC] [--output FILE]

默认:
    --input   ~/Downloads/tdp_logs.json
    --batch-size 1      每次发送的告警条数（1 = 严格逐条）
    --delay   0.0       每批次之间的间隔秒数（模拟流速）
    --output  ~/.flocks/workspace/outputs/<timestamp>_tdp_invoke.jsonl
"""

import argparse
import json
import os
import sys
import time
import urllib.request
import urllib.error
from datetime import datetime
from pathlib import Path

API_URL = "http://127.0.0.1:8000/api/workflow-center/http_alert_dedup/invoke"
API_KEY = "Yw5WQxIL2bgDSL1RH0XO4yolu30GYrQ9bsfLHSmWVfk"
WORKFLOW_INPUTS_BASE = {
    "source_log_type": "tdp",
    "filter_enabled": True,
    "dedup_enabled": True,
    "threshold": 0.7,
}


def post_invoke(alerts: list) -> dict:
    payload = json.dumps({"inputs": {**WORKFLOW_INPUTS_BASE, "alerts": alerts}}).encode()
    req = urllib.request.Request(
        API_URL,
        data=payload,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {API_KEY}",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as e:
        body = e.read().decode(errors="replace")
        return {"error": f"HTTP {e.code}: {body}", "status": "FAILED"}
    except Exception as e:
        return {"error": str(e), "status": "FAILED"}


def default_output_path() -> Path:
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_dir = Path.home() / ".flocks" / "workspace" / "outputs" / datetime.now().strftime("%Y-%m-%d")
    out_dir.mkdir(parents=True, exist_ok=True)
    return out_dir / f"{ts}_tdp_invoke.jsonl"


def main():
    parser = argparse.ArgumentParser(description="流式模拟：逐条将 TDP 告警发送至 /invoke")
    parser.add_argument("--input", default=str(Path.home() / "Downloads" / "tdp_logs.json"),
                        help="输入 JSON 文件路径（list 格式）")
    parser.add_argument("--batch-size", type=int, default=1,
                        help="每次请求发送的告警条数，默认 1（逐条流式）")
    parser.add_argument("--delay", type=float, default=0.0,
                        help="每批之间等待秒数，默认 0（无延迟）")
    parser.add_argument("--output", default=None,
                        help="输出 JSONL 文件路径，默认写入 ~/.flocks/workspace/outputs/")
    args = parser.parse_args()

    input_path = Path(args.input).expanduser()
    if not input_path.exists():
        print(f"[ERROR] 文件不存在: {input_path}", file=sys.stderr)
        sys.exit(1)

    with open(input_path, "r", encoding="utf-8") as f:
        records = json.load(f)

    if isinstance(records, dict):
        records = records.get("data", records.get("alerts", records.get("logs", [])))
    if not isinstance(records, list):
        print("[ERROR] 文件格式错误：期望顶层为 JSON 数组", file=sys.stderr)
        sys.exit(1)

    total = len(records)
    batch_size = max(1, args.batch_size)
    output_path = Path(args.output).expanduser() if args.output else default_output_path()
    output_path.parent.mkdir(parents=True, exist_ok=True)

    print(f"[stream] 输入: {input_path}  共 {total} 条")
    print(f"[stream] batch_size={batch_size}  delay={args.delay}s")
    print(f"[stream] 输出: {output_path}")
    print(f"[stream] API: {API_URL}")
    print("-" * 60)

    summary = {
        "total_input": total,
        "total_batches": 0,
        "total_unique": 0,
        "total_deduped": 0,
        "total_filtered_out": 0,
        "failed_batches": 0,
        "started_at": datetime.now().isoformat(),
    }

    with open(output_path, "w", encoding="utf-8") as out_f:
        batch_idx = 0
        for start in range(0, total, batch_size):
            batch = records[start: start + batch_size]
            batch_idx += 1
            t0 = time.time()
            result = post_invoke(batch)
            elapsed = round(time.time() - t0, 3)

            status = result.get("status", "UNKNOWN")
            outputs = result.get("outputs", {})
            stats = outputs.get("stats", {})

            log_entry = {
                "batch": batch_idx,
                "record_start": start,
                "record_end": start + len(batch) - 1,
                "status": status,
                "elapsed_ms": round(elapsed * 1000),
                "unique_alerts": len(outputs.get("unique_alerts", [])),
                "deduped_alerts": len(outputs.get("deduped_alerts", [])),
                "stats": stats,
                "error": result.get("error"),
                "dedup_summary": outputs.get("dedup_summary", ""),
            }
            out_f.write(json.dumps(log_entry, ensure_ascii=False) + "\n")
            out_f.flush()

            summary["total_batches"] += 1
            if status == "SUCCEEDED":
                summary["total_unique"] += log_entry["unique_alerts"]
                summary["total_deduped"] += log_entry["deduped_alerts"]
                summary["total_filtered_out"] += stats.get("filter_removed_count", 0)
            else:
                summary["failed_batches"] += 1

            progress = f"{start + len(batch)}/{total}"
            indicator = "✓" if status == "SUCCEEDED" else "✗"
            print(
                f"  {indicator} batch {batch_idx:4d} [{progress:>9s}]  "
                f"unique={log_entry['unique_alerts']:3d}  "
                f"deduped={log_entry['deduped_alerts']:3d}  "
                f"{elapsed*1000:.0f}ms"
                + (f"  ERR: {result.get('error','')[:60]}" if status != "SUCCEEDED" else "")
            )

            if args.delay > 0 and start + batch_size < total:
                time.sleep(args.delay)

    summary["finished_at"] = datetime.now().isoformat()

    print("-" * 60)
    print(f"[done] 批次总数:   {summary['total_batches']}")
    print(f"[done] 失败批次:   {summary['failed_batches']}")
    print(f"[done] 累计输入:   {summary['total_input']}")
    print(f"[done] 累计过滤掉: {summary['total_filtered_out']}")
    print(f"[done] 累计去重后: {summary['total_unique']}")
    print(f"[done] 累计去重前: {summary['total_deduped']}")
    print(f"[done] 输出文件:   {output_path}")

    # 末尾追加一行汇总
    with open(output_path, "a", encoding="utf-8") as out_f:
        out_f.write(json.dumps({"_summary": summary}, ensure_ascii=False) + "\n")


if __name__ == "__main__":
    main()
