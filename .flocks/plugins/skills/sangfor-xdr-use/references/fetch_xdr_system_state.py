import argparse
import json
import re
import socket
import time
from pathlib import Path


BROWSER_DAEMON_PORT_FILE = Path.home() / ".flocks" / "browser" / "bu.port"


def send_cmd(sock, cmd):
    sock.sendall((json.dumps(cmd) + "\n").encode())
    data = b""
    while not data.endswith(b"\n"):
        chunk = sock.recv(8192)
        if not chunk:
            break
        data += chunk
    return json.loads(data)

def send_cmd_new_conn(port, cmd):
    sock = socket.socket()
    sock.settimeout(15)
    sock.connect(("127.0.0.1", port))
    result = send_cmd(sock, cmd)
    sock.close()
    return result

def find_xdr_tab(targets, xdr_url):
    host = xdr_url.replace("https://", "").replace("http://", "").rstrip("/")
    for t in targets:
        url = t.get("url", "")
        if host in url and "/apex-business/settings/run/state" in url:
            return t
    for t in targets:
        url = t.get("url", "")
        if host in url and t.get("type") == "page":
            return t
    return None

def parse_system_run_state(text):
    lines = text.split("\n")
    result = {
        "nodes": {"total": None, "healthy": None, "abnormal": None, "unavailable": None},
        "resources": {"cpu": None, "memory": None, "disk": None},
        "disk_monitor": {"system": None, "data": None, "cpu_temp": None},
        "io_network": {"disk_read": None, "disk_write": None, "io_read_latency": None, "io_write_latency": None, "net_recv": None, "net_send": None},
        "data_ingestion": {"throughput": None, "parse_rate": None, "log_limit": None, "log_total": None}
    }

    node_count = 0
    for line in text.split("\n"):
        line = line.strip()
        if re.match(r'^node\d+$', line):
            node_count += 1

    for i, line in enumerate(lines):
        line = line.strip()
        if "节点状态" in line:
            if i + 2 < len(lines):
                next_line = lines[i + 2].strip()
                if next_line.isdigit():
                    result["nodes"]["total"] = next_line
        if "状态总览" in line:
            for j in range(i + 1, min(i + 15, len(lines))):
                l = lines[j].strip()
                if l == "健康" and result["nodes"]["healthy"] is None:
                    if j + 1 < len(lines):
                        val = lines[j + 1].strip()
                        if val.isdigit():
                            result["nodes"]["healthy"] = val
                elif l == "异常" and result["nodes"]["abnormal"] is None:
                    if j + 1 < len(lines):
                        val = lines[j + 1].strip()
                        if val.isdigit():
                            result["nodes"]["abnormal"] = val
                elif l == "不可用" and result["nodes"]["unavailable"] is None:
                    if j + 1 < len(lines):
                        val = lines[j + 1].strip()
                        if val.isdigit():
                            result["nodes"]["unavailable"] = val
        if "系统盘监控状况" in line:
            val = lines[i + 1].strip() if i + 1 < len(lines) else None
            result["disk_monitor"]["system"] = val
        elif "数据盘监控状况" in line:
            val = lines[i + 1].strip() if i + 1 < len(lines) else None
            result["disk_monitor"]["data"] = val
        elif "CPU最高温度" in line:
            val = lines[i + 1].strip() if i + 1 < len(lines) else None
            result["disk_monitor"]["cpu_temp"] = val

    cpu_match = re.search(r"CPU使用趋势[^\d]*([\d.]+)\s*%", text)
    if cpu_match:
        result["resources"]["cpu"] = cpu_match.group(1) + "%"

    mem_match = re.search(r"内存使用趋势[^\d]*([\d.]+)\s*%", text)
    if mem_match:
        result["resources"]["memory"] = mem_match.group(1) + "%"

    disk_match = re.search(r"磁盘使用趋势[^\d]*([\d.]+)\s*%", text)
    if disk_match:
        result["resources"]["disk"] = disk_match.group(1) + "%"

    read_match = re.search(r"读取\s+([\d.]+)\s+MiB/s", text)
    if read_match:
        result["io_network"]["disk_read"] = read_match.group(1) + " MiB/s"
    write_match = re.search(r"写入\s+([\d.]+)\s+MiB/s", text)
    if write_match:
        result["io_network"]["disk_write"] = write_match.group(1) + " MiB/s"

    recv_match = re.search(r"接收\s+([\d.]+)\s+MiB/s", text)
    if recv_match:
        result["io_network"]["net_recv"] = recv_match.group(1) + " MiB/s"
    send_match = re.search(r"发送\s+([\d.]+)\s+MiB/s", text)
    if send_match:
        result["io_network"]["net_send"] = send_match.group(1) + " MiB/s"

    io_read_match = re.search(r"IO读取延迟[^\d]*([\d.]+)\s*ms", text)
    if io_read_match:
        result["io_network"]["io_read_latency"] = io_read_match.group(1) + " ms"
    io_write_match = re.search(r"IO写入延迟[^\d]*([\d.]+)\s*ms", text)
    if io_write_match:
        result["io_network"]["io_write_latency"] = io_write_match.group(1) + " ms"

    throughput_match = re.search(r"数据采集吞吐率[^\d]*([\d.]+)\s+MiB/s", text)
    if throughput_match:
        result["data_ingestion"]["throughput"] = throughput_match.group(1) + " MiB/s"

    parse_rate_match = re.search(r"数据解析速率[^\d]*([\d.]+)\s+条/s", text)
    if parse_rate_match:
        result["data_ingestion"]["parse_rate"] = parse_rate_match.group(1) + " 条/s"

    log_limit_match = re.search(r"授权日志上限[^\d]*([\d.]+)\s+亿条", text)
    if log_limit_match:
        result["data_ingestion"]["log_limit"] = log_limit_match.group(1) + " 亿条"

    log_total_match = re.search(r"日志接入总量[^\d]*([\d.]+)\s+亿条", text)
    if log_total_match:
        result["data_ingestion"]["log_total"] = log_total_match.group(1) + " 亿条"

    if node_count > 0 and result["nodes"]["healthy"] and result["nodes"]["healthy"].isdigit():
        if int(result["nodes"]["healthy"]) > node_count:
            result["nodes"]["total"] = result["nodes"]["healthy"]
        else:
            result["nodes"]["total"] = str(node_count)
    elif node_count > 0:
        result["nodes"]["total"] = str(node_count)

    return result

def main():
    parser = argparse.ArgumentParser(description="Fetch XDR system run state via CDP")
    parser.add_argument("--url", required=True, help="XDR URL (e.g. https://xdr.example.com/)")
    parser.add_argument("--wait", type=int, default=5, help="Wait seconds for page render (default: 5)")
    parser.add_argument("--raw", action="store_true", help="Print raw page text")
    args = parser.parse_args()

    port_file = BROWSER_DAEMON_PORT_FILE
    if not port_file.exists():
        print(f"ERROR: Browser daemon port file not found: {port_file}")
        print("Please run: flocks browser --setup")
        exit(1)

    port = int(port_file.read_text().strip())

    targets_result = send_cmd_new_conn(port, {"method": "Target.getTargets"})
    targets = targets_result.get("result", {}).get("targetInfos", [])

    xdr_tab = find_xdr_tab(targets, args.url)
    if not xdr_tab:
        target_url = args.url.rstrip("/") + "/#/apex-business/settings/run/state"
        print(f"XDR tab not found. Please open: {target_url}")
        exit(1)

    print(f"Found XDR tab: {xdr_tab['targetId']}")

    attach_result = send_cmd_new_conn(port, {
        "method": "Target.attachToTarget",
        "params": {"targetId": xdr_tab["targetId"], "flatten": True}
    })
    session_id = attach_result["result"]["sessionId"]

    time.sleep(args.wait)

    text_result = send_cmd_new_conn(port, {
        "method": "Runtime.evaluate",
        "params": {"expression": "document.body.innerText"},
        "session_id": session_id
    })
    page_text = text_result["result"]["result"]["value"]

    if args.raw:
        print(page_text)
        return

    result = parse_system_run_state(page_text)

    print("\n### XDR System Run State\n")
    print("#### Nodes Overview")
    print(f"| Metric | Value |")
    print(f"|--------|-------|")
    print(f"| Total Nodes | {result['nodes']['total'] or '-'} |")
    print(f"| Healthy | {result['nodes']['healthy'] or '-'} |")
    print(f"| Abnormal | {result['nodes']['abnormal'] or '-'} |")
    print(f"| Unavailable | {result['nodes']['unavailable'] or '-'} |")

    print("\n#### Resource Usage Trend")
    print(f"| Metric | Current |")
    print(f"|--------|---------|")
    print(f"| CPU Usage | {result['resources']['cpu'] or '-'} |")
    print(f"| Memory Usage | {result['resources']['memory'] or '-'} |")
    print(f"| Disk Usage | {result['resources']['disk'] or '-'} |")

    print("\n#### Disk Monitor")
    print(f"| Item | Status |")
    print(f"|------|--------|")
    print(f"| System Disk | {result['disk_monitor']['system'] or '-'} |")
    print(f"| Data Disk | {result['disk_monitor']['data'] or '-'} |")
    print(f"| CPU Max Temp | {result['disk_monitor']['cpu_temp'] or '-'} |")

    print("\n#### IO & Network")
    print(f"| Metric | Current |")
    print(f"|--------|---------|")
    print(f"| Disk Read | {result['io_network']['disk_read'] or '-'} |")
    print(f"| Disk Write | {result['io_network']['disk_write'] or '-'} |")
    print(f"| IO Read Latency | {result['io_network']['io_read_latency'] or '-'} |")
    print(f"| IO Write Latency | {result['io_network']['io_write_latency'] or '-'} |")
    print(f"| Net Receive | {result['io_network']['net_recv'] or '-'} |")
    print(f"| Net Send | {result['io_network']['net_send'] or '-'} |")

    print("\n#### Data Ingestion")
    print(f"| Metric | Value |")
    print(f"|--------|-------|")
    print(f"| Throughput | {result['data_ingestion']['throughput'] or '-'} |")
    print(f"| Parse Rate | {result['data_ingestion']['parse_rate'] or '-'} |")
    print(f"| Log Limit | {result['data_ingestion']['log_limit'] or '-'} |")
    print(f"| Log Total | {result['data_ingestion']['log_total'] or '-'} |")

if __name__ == "__main__":
    main()