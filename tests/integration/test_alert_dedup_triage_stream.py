#!/usr/bin/env python3
# NOTE: standalone manual integration test — not a pytest test, run directly with python3.
"""
手动集成测试工具：两阶段流式 pipeline（dedup → triage）

逐条读取 ~/Downloads/tdp_logs.json，对每条告警：
  1) 调用 http_alert_dedup（POST /workflow-center/http_alert_dedup/invoke）
     - 返回 unique_alerts 为空 -> 被过滤掉，跳过 triage
     - 返回 unique_alerts[0].dedup_key_already_exists == True -> 跨批次重复，跳过 triage
     - 否则 -> 视为"首次出现的可分析告警"，转 step 2
  2) 调用 tdp_alert_triage（POST /workflow-center/<id>/invoke）
     - 把原始告警作为 alert_data 传入，触发 LLM 研判流水线（测绘/CVE/payload 并行）

输出 JSONL 到 ~/.flocks/workspace/outputs/<date>/，每条记录包含：
    {batch, alert_index, dedup: {...}, triage: {verdict, risk, title, report_path} | None, reason}
末尾追加一行 _summary 汇总。

用法:
    python3 scripts/stream_pipeline_dedup_triage.py [--input FILE] [--limit N] [--delay SEC]

如果只想跑一小批做端到端验证：
    python3 scripts/stream_pipeline_dedup_triage.py --limit 3 --triage-limit 1
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

# ---------- API endpoints ----------
# dedup: via main server proxy (records UI metrics)
DEDUP_URL  = "http://127.0.0.1:8000/api/workflow-center/http_alert_dedup/invoke"
DEDUP_KEY  = "Yw5WQxIL2bgDSL1RH0XO4yolu30GYrQ9bsfLHSmWVfk"

# triage: call the published service directly (avoids 30 s proxy timeout)
TRIAGE_URL = "http://127.0.0.1:19001/invoke"
TRIAGE_KEY = "8e23f1ad036c4f73960925923d04e9a1edf8fcaf3d6b4461b5d2ced7e0956267"

DEDUP_BASE_INPUTS = {
    "source_log_type": "tdp",
    "filter_enabled": True,
    "dedup_enabled": True,
    "threshold": 0.7,
}


def _post(url: str, api_key: str, payload: dict, timeout: int) -> tuple[dict, int]:
    """POST JSON to a flocks /invoke endpoint; return (response_dict, elapsed_ms)."""
    # Services use X-API-Key; main proxy uses Authorization: Bearer.
    if "127.0.0.1:8000" in url:
        auth_header = {"Authorization": f"Bearer {api_key}"}
    else:
        auth_header = {"X-API-Key": api_key}
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json", **auth_header},
        method="POST",
    )
    t0 = time.time()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read()), round((time.time() - t0) * 1000)
    except urllib.error.HTTPError as e:
        body = e.read().decode(errors="replace")[:500]
        return {"status": "FAILED", "error": f"HTTP {e.code}: {body}"}, round((time.time() - t0) * 1000)
    except Exception as e:
        return {"status": "FAILED", "error": str(e)}, round((time.time() - t0) * 1000)


def call_dedup(alert: dict, timeout: int) -> tuple[dict, int]:
    payload = {"inputs": {**DEDUP_BASE_INPUTS, "alerts": [alert]}}
    return _post(DEDUP_URL, DEDUP_KEY, payload, timeout)


def call_triage(alert: dict, timeout: int) -> tuple[dict, int]:
    """Call triage service directly on port 19001 — no proxy timeout."""
    payload = {"inputs": {"alert_data": alert}}
    return _post(TRIAGE_URL, TRIAGE_KEY, payload, timeout)


def default_output_path() -> Path:
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_dir = Path.home() / ".flocks" / "workspace" / "outputs" / datetime.now().strftime("%Y-%m-%d")
    out_dir.mkdir(parents=True, exist_ok=True)
    return out_dir / f"{ts}_pipeline_dedup_triage.jsonl"


def main() -> None:
    p = argparse.ArgumentParser(description="Streaming pipeline: dedup -> triage")
    p.add_argument("--input", default=str(Path.home() / "Downloads" / "tdp_logs.json"),
                   help="Input JSON file (top-level list)")
    p.add_argument("--limit", type=int, default=0,
                   help="Process only the first N alerts (0 = all)")
    p.add_argument("--triage-limit", type=int, default=0,
                   help="Stop after triggering N successful triage runs (0 = unlimited). "
                        "Useful to avoid burning LLM credits during smoke tests.")
    p.add_argument("--delay", type=float, default=0.0,
                   help="Delay (seconds) between alerts")
    p.add_argument("--dedup-timeout", type=int, default=60)
    p.add_argument("--triage-timeout", type=int, default=600)
    p.add_argument("--output", default=None, help="Output JSONL path")
    args = p.parse_args()

    src = Path(args.input).expanduser()
    if not src.exists():
        print(f"[ERROR] input not found: {src}", file=sys.stderr)
        sys.exit(1)

    with open(src, "r", encoding="utf-8") as f:
        records = json.load(f)
    if isinstance(records, dict):
        records = records.get("data", records.get("alerts", records.get("logs", [])))
    if not isinstance(records, list):
        print("[ERROR] expected top-level list", file=sys.stderr)
        sys.exit(1)

    if args.limit > 0:
        records = records[: args.limit]

    out_path = Path(args.output).expanduser() if args.output else default_output_path()
    out_path.parent.mkdir(parents=True, exist_ok=True)

    total = len(records)
    print(f"[stream] input: {src}  count={total}")
    print(f"[stream] output: {out_path}")
    print(f"[stream] dedup:  {DEDUP_URL}")
    print(f"[stream] triage: {TRIAGE_URL}")
    print("-" * 80)

    summary = {
        "total_input": total,
        "dedup_success": 0,
        "dedup_failed": 0,
        "filtered_out": 0,
        "duplicate_skipped": 0,
        "triage_invoked": 0,
        "triage_success": 0,
        "triage_failed": 0,
        "verdict_counts": {},
        "started_at": datetime.now().isoformat(),
    }

    with open(out_path, "w", encoding="utf-8") as f_out:
        for i, alert in enumerate(records):
            entry = {"alert_index": i, "alert_id": alert.get("id") or alert.get("uuid"),
                     "threat_name": (alert.get("threat") or {}).get("name", ""),
                     "src_ip": alert.get("attacker"), "dst_ip": alert.get("victim")}

            # ---------- step 1: dedup ----------
            dr, dms = call_dedup(alert, args.dedup_timeout)
            ds = dr.get("status", "UNKNOWN")
            if ds != "SUCCEEDED":
                summary["dedup_failed"] += 1
                entry["dedup"] = {"status": ds, "elapsed_ms": dms, "error": dr.get("error", "")[:300]}
                entry["reason"] = "dedup_failed"
                entry["triage"] = None
                f_out.write(json.dumps(entry, ensure_ascii=False) + "\n"); f_out.flush()
                print(f"  [{i+1:3d}/{total}] ✗ dedup FAILED  ({dms}ms)  {dr.get('error','')[:80]}")
                continue

            summary["dedup_success"] += 1
            outs = dr.get("outputs", {})
            stats = outs.get("stats", {})
            ua = outs.get("unique_alerts", [])
            entry["dedup"] = {"status": ds, "elapsed_ms": dms,
                              "filter_removed": stats.get("filter_removed_count", 0),
                              "after_filter": stats.get("after_filter_count", 0),
                              "unique_alerts": len(ua),
                              "lsh_clusters": stats.get("lsh_total_clusters"),
                              "lsh_dedup_keys": stats.get("lsh_total_dedup_keys")}

            if not ua:
                summary["filtered_out"] += 1
                entry["reason"] = "filtered_out"
                entry["triage"] = None
                f_out.write(json.dumps(entry, ensure_ascii=False) + "\n"); f_out.flush()
                print(f"  [{i+1:3d}/{total}] - dedup OK  ({dms:>4d}ms)  filtered_out (kept=0)")
                continue

            already = bool(ua[0].get("dedup_key_already_exists"))
            entry["dedup"]["dedup_key"] = ua[0].get("dedup_key")
            entry["dedup"]["dedup_key_already_exists"] = already

            if already:
                summary["duplicate_skipped"] += 1
                entry["reason"] = "duplicate_skipped"
                entry["triage"] = None
                f_out.write(json.dumps(entry, ensure_ascii=False) + "\n"); f_out.flush()
                print(f"  [{i+1:3d}/{total}] - dedup OK  ({dms:>4d}ms)  duplicate (key={ua[0].get('dedup_key','')[:8]})")
                continue

            # ---------- step 2: triage (only first-seen unique alerts) ----------
            if args.triage_limit > 0 and summary["triage_success"] >= args.triage_limit:
                entry["reason"] = "triage_limit_reached"
                entry["triage"] = None
                f_out.write(json.dumps(entry, ensure_ascii=False) + "\n"); f_out.flush()
                print(f"  [{i+1:3d}/{total}] - dedup OK  ({dms:>4d}ms)  triage limit reached, skip")
                continue

            summary["triage_invoked"] += 1
            tr, tms = call_triage(alert, args.triage_timeout)
            ts_ = tr.get("status", "UNKNOWN")
            if ts_ != "SUCCEEDED":
                summary["triage_failed"] += 1
                entry["reason"] = "triage_failed"
                entry["triage"] = {"status": ts_, "elapsed_ms": tms, "error": tr.get("error", "")[:300]}
                f_out.write(json.dumps(entry, ensure_ascii=False) + "\n"); f_out.flush()
                print(f"  [{i+1:3d}/{total}] ✗ dedup OK + triage FAILED  ({dms}+{tms}ms)  {tr.get('error','')[:80]}")
                continue

            summary["triage_success"] += 1
            tout = tr.get("outputs", {})
            verdict = tout.get("attack_verdict", "unknown")
            summary["verdict_counts"][verdict] = summary["verdict_counts"].get(verdict, 0) + 1
            entry["reason"] = "triage_done"
            entry["triage"] = {"status": ts_, "elapsed_ms": tms,
                               "attack_verdict": verdict,
                               "risk_level": tout.get("risk_level"),
                               "report_title": tout.get("report_title"),
                               "report_path": tout.get("report_path")}
            f_out.write(json.dumps(entry, ensure_ascii=False) + "\n"); f_out.flush()
            print(f"  [{i+1:3d}/{total}] ✓ dedup OK + triage OK  ({dms}+{tms}ms)  "
                  f"verdict={verdict}  title={(tout.get('report_title') or '')[:30]}")

            if args.delay > 0:
                time.sleep(args.delay)

        summary["finished_at"] = datetime.now().isoformat()
        f_out.write(json.dumps({"_summary": summary}, ensure_ascii=False) + "\n")

    print("-" * 80)
    print(f"[done] dedup_success / failed   : {summary['dedup_success']} / {summary['dedup_failed']}")
    print(f"[done] filtered_out             : {summary['filtered_out']}")
    print(f"[done] duplicate_skipped        : {summary['duplicate_skipped']}")
    print(f"[done] triage_invoked           : {summary['triage_invoked']}")
    print(f"[done] triage_success / failed  : {summary['triage_success']} / {summary['triage_failed']}")
    print(f"[done] verdict_counts           : {summary['verdict_counts']}")
    print(f"[done] output                   : {out_path}")


if __name__ == "__main__":
    main()
