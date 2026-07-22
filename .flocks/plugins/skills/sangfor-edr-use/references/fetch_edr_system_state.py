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

def find_edr_tab(targets, edr_url):
    host = edr_url.replace("https://", "").replace("http://", "").rstrip("/")
    for t in targets:
        url = t.get("url", "")
        if host in url and "#/index" in url:
            return t
    for t in targets:
        url = t.get("url", "")
        if host in url and t.get("type") == "page":
            return t
    return None

def parse_edr_status(text):
    result = {
        "device_status": {"cpu": None, "memory": None, "disk": None},
        "terminal_overview": {"total": None, "online": None, "offline": None, "other": None, "server": None, "pc": None},
        "compromised": {"count": None, "high_suspicious": None, "low_suspicious": None}
    }

    cpu_match = re.search(r'CPU[:：]\s*([\d.]+)%', text)
    if cpu_match:
        result["device_status"]["cpu"] = cpu_match.group(1) + "%"

    mem_match = re.search(r'内存[:：]\s*([\d.]+)%', text)
    if mem_match:
        result["device_status"]["memory"] = mem_match.group(1) + "%"

    disk_match = re.search(r'硬盘[:：]\s*([\d.]+)%', text)
    if disk_match:
        result["device_status"]["disk"] = disk_match.group(1) + "%"

    compromised_match = re.search(r'已失陷\s*(\d+)', text)
    if compromised_match:
        result["compromised"]["count"] = compromised_match.group(1)

    high_match = re.search(r'高可疑\s*(\d+)', text)
    if high_match:
        result["compromised"]["high_suspicious"] = high_match.group(1)

    low_match = re.search(r'低可疑\s*(\d+)', text)
    if low_match:
        result["compromised"]["low_suspicious"] = low_match.group(1)

    total_match = re.search(r'受管控终端\s*\n\s*(\d+)', text)
    if total_match:
        result["terminal_overview"]["total"] = total_match.group(1)
    else:
        total_match2 = re.search(r'(\d+)\s*\n\s*受管控终端', text)
        if total_match2:
            result["terminal_overview"]["total"] = total_match2.group(1)

    online_match = re.search(r'在线[:：]\s*(\d+)', text)
    if online_match:
        result["terminal_overview"]["online"] = online_match.group(1)

    offline_match = re.search(r'离线[:：]\s*(\d+)', text)
    if offline_match:
        result["terminal_overview"]["offline"] = offline_match.group(1)

    other_match = re.search(r'其它[:：]\s*(\d+)', text)
    if other_match:
        result["terminal_overview"]["other"] = other_match.group(1)

    server_match = re.search(r'服务器[:：]\s*(\d+)', text)
    if server_match:
        result["terminal_overview"]["server"] = server_match.group(1)

    pc_match = re.search(r'PC[:：]\s*(\d+)', text)
    if pc_match:
        result["terminal_overview"]["pc"] = pc_match.group(1)

    return result

def main():
    parser = argparse.ArgumentParser(description="Fetch EDR system status via CDP")
    parser.add_argument("--url", required=True, help="EDR URL (e.g. https://edr.example.com/)")
    parser.add_argument("--wait", type=int, default=3, help="Wait seconds for page render (default: 3)")
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

    edr_tab = find_edr_tab(targets, args.url)
    if not edr_tab:
        target_url = args.url.rstrip("/") + "/ui/#/index"
        print(f"EDR tab not found. Please open: {target_url}")
        exit(1)

    print(f"Found EDR tab: {edr_tab['targetId']}")

    attach_result = send_cmd_new_conn(port, {
        "method": "Target.attachToTarget",
        "params": {"targetId": edr_tab["targetId"], "flatten": True}
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

    result = parse_edr_status(page_text)

    print("\n### EDR Device Status\n")
    print("#### Device Status")
    print(f"| Metric | Value |")
    print(f"|--------|-------|")
    print(f"| CPU | {result['device_status']['cpu'] or '-'} |")
    print(f"| Memory | {result['device_status']['memory'] or '-'} |")
    print(f"| Disk | {result['device_status']['disk'] or '-'} |")

    print("\n#### Terminal Overview")
    print(f"| Metric | Value |")
    print(f"|--------|-------|")
    print(f"| Total | {result['terminal_overview']['total'] or '-'} |")
    print(f"| Online | {result['terminal_overview']['online'] or '-'} |")
    print(f"| Offline | {result['terminal_overview']['offline'] or '-'} |")
    print(f"| Other | {result['terminal_overview']['other'] or '-'} |")
    print(f"| Server | {result['terminal_overview']['server'] or '-'} |")
    print(f"| PC | {result['terminal_overview']['pc'] or '-'} |")

    print("\n#### Compromised Status")
    print(f"| Metric | Value |")
    print(f"|--------|-------|")
    print(f"| Compromised | {result['compromised']['count'] or '-'} |")
    print(f"| High Suspicious | {result['compromised']['high_suspicious'] or '-'} |")
    print(f"| Low Suspicious | {result['compromised']['low_suspicious'] or '-'} |")

if __name__ == "__main__":
    main()