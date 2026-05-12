"""Build workflow.json for tdp_alert_pull_dedup.

Run: python _build_workflow.py

Reads the pull_dedup_loop node code from _node_pull_dedup_loop.py and
serializes a fully-valid workflow.json next to it.
"""

from __future__ import annotations

import json
import os

HERE = os.path.dirname(os.path.abspath(__file__))


def read_code(name: str) -> str:
    with open(os.path.join(HERE, name), "r", encoding="utf-8") as f:
        return f.read()


workflow = {
    "name": "tdp_alert_pull_dedup",
    "description": (
        "Long-running TDP alert puller + deduper. Each iteration calls the "
        "tdp_log_search tool to fetch attack-level HTTP alerts in a moving time "
        "window, normalizes them, filters non-HTTP/scan noise, deduplicates via "
        "URI-normalized 5-gram MinHash LSH (persistent across iterations / runs), "
        "and appends enriched alerts to JSONL files under "
        "~/.flocks/workflows/tdp_alert_pull_dedup/<YYYY-MM-DD>/alerts_NNN.jsonl. "
        "A persistent time cursor at ~/.flocks/workflows/tdp_alert_pull_dedup/cursor.json "
        "guarantees no gaps and no overlap across restarts."
    ),
    "description_cn": (
        "长时间运行的 TDP 告警拉取 + 去重 Pipeline。单个 python 节点内 while 循环：每轮调用 "
        "tdp_log_search 拉取一个时间窗口内的攻击级 HTTP 告警 → 归一化 → 过滤（去扫描/非HTTP）→ "
        "URI 归一化 + 5-gram MinHash LSH 去重（持久化 LSH 状态，跨轮次/跨进程共享）→ 追加写入 "
        "~/.flocks/workflows/tdp_alert_pull_dedup/<YYYY-MM-DD>/alerts_NNN.jsonl，每文件 10,000 条上限。"
        "时间游标 ~/.flocks/workflows/tdp_alert_pull_dedup/cursor.json 持久化，重启可无重叠续拉。"
        "通过 pull_interval_s / max_iterations / max_runtime_s 控制循环节奏与停止条件。"
    ),
    "start": "pull_dedup_loop",
    "nodes": [
        {
            "id": "pull_dedup_loop",
            "type": "python",
            "description": (
                "持续拉取 TDP 告警的主循环节点。内部 while 循环执行：调用 tdp_log_search → 归一化 → "
                "过滤 → LSH 去重 → 写盘。time_from 来自持久化游标（首次回退 initial_lookback_s 秒），"
                "time_to=当前时间，保证窗口连续无重叠。"
                "停止条件：max_iterations / max_runtime_s 任一达到即返回；外部取消（如 SIGINT 或节点超时）也会优雅退出。"
            ),
            "code": read_code("_node_pull_dedup_loop.py"),
        }
    ],
    "edges": [],
    "metadata": {
        "node_timeout_s": 2592000,
        "sampleInputs": {
            "pull_interval_s": 60,
            "initial_lookback_s": 300,
            "max_iterations": 0,
            "max_runtime_s": 0,
            "batch_size": 1000,
            "net_data_types": ["attack"],
            "sql": "threat.level = 'attack'",
            "assets_group": [],
            "filter_enabled": True,
            "dedup_enabled": True,
            "threshold": 0.7,
            "strict_fields": ["sip", "dip"],
            "lsh_fields": ["req_http_url", "req_body", "rsp_body"],
            "max_field_len": 500,
            "max_dedup_keys": 100000,
            "reset_cursor": False,
            "log_progress_every": 1,
            "_comment_runtime": (
                "node_timeout_s 默认 30 天（2,592,000s），适合长时间持续运行；"
                "若想短跑测试，把 max_iterations 调小或设 max_runtime_s 即可。"
            ),
            "_comment_path": (
                "输出落盘根目录：~/.flocks/workflows/tdp_alert_pull_dedup/<YYYY-MM-DD>/alerts_NNN.jsonl；"
                "时间游标：~/.flocks/workflows/tdp_alert_pull_dedup/cursor.json；"
                "LSH 持久化：~/.flocks/workflows/tdp_alert_pull_dedup/lsh_state_np128_th{int(threshold*100)}.pkl"
            ),
        },
    },
}

with open(os.path.join(HERE, "workflow.json"), "w", encoding="utf-8") as f:
    json.dump(workflow, f, ensure_ascii=False, indent=2)

print(f"wrote {os.path.join(HERE, 'workflow.json')}")
