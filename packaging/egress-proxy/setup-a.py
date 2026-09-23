#!/usr/bin/env python3
"""flocks-egress：机器 A 的整机出网接管（内网直连、公网经客户已有的 HTTP / HTTPS / SOCKS5 代理）。

    sudo bash setup-a.sh install --proxy http://10.0.0.5:3128                       # 客户代理不认证
    sudo bash setup-a.sh install --proxy http://10.0.0.5:3128 --proxy-user u        # 认证：密码交互输入（或 --proxy-password）
    sudo bash setup-a.sh install --config ./egress.conf                              # 用参数文件
    sudo flocks-egress check | status | reconfigure | rollback                       # 装好后的日常命令

只依赖 CentOS 9 / RHEL 9 系自带的 python3、nftables、systemd；flocks 与机器上其他程序不需要任何配置。
导入本模块不会改动系统。
"""
import argparse
import getpass
import hashlib
import ipaddress
import json
import os
import platform
import pwd
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.parse
import uuid
from pathlib import Path

VERSION = '2.0.0'
TOOL = 'flocks-egress'
ENGINE_USER = 'flocks-egress'
PREFIX = Path('/opt/flocks-egress')
BIN_DIR = PREFIX / 'bin'
INSTALLED_SELF = BIN_DIR / 'flocks-egress'
ENGINE_BIN = BIN_DIR / 'mihomo'
ETC = Path('/etc/flocks-egress')
CONF_PATH = ETC / 'egress.conf'
SETTINGS_PATH = ETC / 'settings.json'
CONFIG_YAML = ETC / 'config.yaml'
RULES_NFT = ETC / 'rules.nft'
ENGINE_NFT = ETC / 'engine.nft'
STATE = Path('/var/lib/flocks-egress')
LAST_CHECK = STATE / 'last-check.json'
UNITS = Path('/etc/systemd/system')
RULES_UNIT = 'flocks-egress-rules.service'
ENGINE_UNIT = 'flocks-egress.service'
TABLE = 'flocks_egress'
ENGINE_TABLE = 'flocks_egress_engine'
LEGACY_TABLE, LEGACY_NFT = 'flocks_egress_dns', ETC / 'dns.nft'     # 2.0.0 早期构建用的名字，装过的开发机上要顺手清掉
FAKEIP_RANGE = '198.18.0.1/16'
FAKEIP_BLOCK = '198.18.0.0/15'      # fake-ip 占位地址所在的保留段（RFC 2544），用来识别 / 拦截占位地址
SBIN_LINKS = (Path('/usr/sbin/flocks-egress'), Path('/usr/local/sbin/flocks-egress'))
NFTABLES_DROPIN = UNITS / 'nftables.service.d' / 'flocks-egress.conf'

# 内置直连网段：RFC1918、回环、链路本地、CGNAT、组播、保留段；IPv6 的回环、ULA、链路本地、组播
BUILTIN4 = ['0.0.0.0/8', '10.0.0.0/8', '100.64.0.0/10', '127.0.0.0/8', '169.254.0.0/16',
            '172.16.0.0/12', '192.168.0.0/16', '224.0.0.0/4', '240.0.0.0/4']
BUILTIN6 = ['::1/128', 'fc00::/7', 'fe80::/10', 'ff00::/8']

CONF_KEYS = ('PROXY_URL', 'PROXY_USER', 'PROXY_PASSWORD', 'PROXY_TLS_INSECURE',
             'DIRECT_CIDRS', 'DIRECT_DOMAINS', 'DNS_MODE', 'DNS_SERVERS',
             'SCOPE', 'FLOCKS_USER', 'CONTAINERS', 'PUBLIC_UDP', 'ENGINE_DOWN', 'ENGINE_LOG_LEVEL',
             'CHECK_PUBLIC_URLS', 'CHECK_INTRANET_URL', 'REDIR_PORT', 'DNS_PORT', 'MIXED_PORT')
DEFAULTS = {'DNS_MODE': 'auto', 'SCOPE': 'host', 'CONTAINERS': 'auto', 'PUBLIC_UDP': 'reject', 'ENGINE_DOWN': 'direct', 'ENGINE_LOG_LEVEL': 'info',
            'CHECK_PUBLIC_URLS': 'https://www.baidu.com/', 'REDIR_PORT': '7892', 'DNS_PORT': '1053',
            'MIXED_PORT': '7890', 'PROXY_TLS_INSECURE': 'no'}
DOMAIN_RE = re.compile(r'^(?=.{1,253}$)[A-Za-z0-9]([A-Za-z0-9-]*[A-Za-z0-9])?(\.[A-Za-z0-9]([A-Za-z0-9-]*[A-Za-z0-9])?)*$')


class Fail(Exception):
    """给用户看的错误：只打印一行，不打印回溯。"""


def log(msg):
    print('[flocks-egress] ' + msg, flush=True)


def warn(msg):
    print('[flocks-egress] 警告: ' + msg, file=sys.stderr, flush=True)


def now_str():
    return time.strftime('%Y-%m-%d %H:%M:%S')


def demote(uid):
    """subprocess 的 preexec_fn：切到指定用户（只在 root 下调用）。"""
    pw = pwd.getpwuid(uid)

    def _pre():
        os.setgroups([])
        os.setgid(pw.pw_gid)
        os.setuid(uid)
    return _pre


CLEAN_ENV = {'PATH': '/usr/sbin:/usr/bin:/sbin:/bin', 'HOME': '/', 'LANG': 'C.UTF-8'}


def run(args, *, data=None, check=True, timeout=60, uid=None, env=None):
    """跑一条外部命令。uid 给定时以该用户身份跑（清空环境变量，避免继承 root 的 http_proxy 之类）。
    stdin 传密码时用 data，不要把密码放进 args（会进 ps）。"""
    pre = demote(uid) if uid is not None and uid != os.geteuid() else None
    if env is None and uid is not None:
        env = dict(CLEAN_ENV)
    try:
        result = subprocess.run(args, input=data, text=True, capture_output=True, timeout=timeout,
                                check=False, preexec_fn=pre, env=env)
    except FileNotFoundError:
        raise Fail('缺少命令 ' + str(args[0]))
    except subprocess.TimeoutExpired:
        if check:
            raise Fail('命令超时: ' + ' '.join(str(a) for a in args[:3]))
        return subprocess.CompletedProcess(args, 124, '', 'timeout')
    if check and result.returncode:
        raise Fail('命令失败: ' + ' '.join(str(a) for a in args[:4]) + '\n' + result.stderr.strip()[-600:])
    return result


def which(name):
    return shutil.which(name, path='/usr/sbin:/usr/bin:/sbin:/bin:' + os.environ.get('PATH', ''))


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, 'rb') as stream:
        for block in iter(lambda: stream.read(1 << 20), b''):
            h.update(block)
    return h.hexdigest()


def write_private(path, content, mode=0o600, owner=None, group=None):
    """原子写入：先写临时文件再 rename，替换前保留上一版为 .prev。"""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        shutil.copy2(str(path), str(path) + '.prev')
    tmp = path.with_name(path.name + '.tmp')
    fd = os.open(str(tmp), os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, mode)
    with os.fdopen(fd, 'w') as stream:
        stream.write(content)
    os.chmod(str(tmp), mode)
    if owner is not None or group is not None:
        shutil.chown(str(tmp), user=owner, group=group)
    os.replace(str(tmp), str(path))


def arch_name():
    machine = platform.machine()
    return {'x86_64': 'amd64', 'amd64': 'amd64', 'aarch64': 'arm64', 'arm64': 'arm64'}.get(machine, machine)


# ---------------------------------------------------------------- 参数文件

def parse_conf(text, source='egress.conf'):
    """KEY=VALUE 的参数文件。带引号的值引号里什么都行，不带引号的到第一个「空白 #」为止；未知键忽略并警告；不执行任何代码。"""
    values, warnings = {}, []
    for lineno, raw in enumerate(text.splitlines(), 1):
        line = raw.rstrip('\r')
        if not line.strip() or line.lstrip().startswith('#'):
            continue
        m = re.match(r'^\s*([A-Z][A-Z0-9_]*)\s*=\s*(.*)$', line)
        if not m:
            warnings.append('%s:%d 忽略无法识别的行' % (source, lineno))
            continue
        key, value = m.group(1), m.group(2)
        if key not in CONF_KEYS:
            warnings.append('%s:%d 忽略未知参数 %s（拼写检查一下）' % (source, lineno, key))
            continue
        q = re.match(r'^"([^"]*)"\s*(#.*)?$', value) or re.match(r"^'([^']*)'\s*(#.*)?$", value)
        if q:
            value = q.group(1)
        elif value[:1] in ('"', "'"):
            raise Fail('%s:%d %s 的引号没闭合或引号后面有多余内容' % (source, lineno, key))
        else:
            value = re.split(r'\s#', value, 1)[0].rstrip()
        values[key] = value
    return values, warnings


def quote_conf(value):
    if '\n' in value or '\r' in value:
        raise Fail('参数值不能包含换行')
    if '"' not in value:
        return '"%s"' % value
    if "'" not in value:
        return "'%s'" % value
    raise Fail('参数值不能同时包含单引号和双引号（密码这类请用 URL 编码写进 PROXY_URL）')


def render_conf(values):
    lines = ['# flocks-egress 参数（setup-a.py v%s 写入，%s）。每行 KEY="VALUE"。' % (VERSION, now_str()),
             '# 改完执行 sudo flocks-egress reconfigure 生效；全部可用参数见随包的 egress.conf.example。']
    for key in CONF_KEYS:
        if key in values and values[key] != '' and values[key] != DEFAULTS.get(key):
            lines.append('%s=%s' % (key, quote_conf(values[key])))
        elif key == 'PROXY_URL':
            lines.append('%s=%s' % (key, quote_conf(values.get(key, ''))))
    return '\n'.join(lines) + '\n'


def split_list(value):
    return [item for item in re.split(r'[,;\s]+', value or '') if item]


def is_ip(text):
    try:
        ipaddress.ip_address(text)
        return True
    except ValueError:
        return False


def parse_proxy(url, user=None, password=None, tls_insecure=False):
    """PROXY_URL → dict(scheme, type, tls, host, port, user, password, tls_insecure)。user/password 给定时覆盖 URL 里的。"""
    if not url:
        raise Fail('PROXY_URL 必填：客户代理的地址，如 http://10.0.0.5:3128、socks5://10.0.0.5:1080；带账号 http://用户:密码@10.0.0.5:3128')
    if re.search(r'%(?![0-9A-Fa-f]{2})', url):
        raise Fail('PROXY_URL 里 % 后面必须是两位十六进制（密码里的字面量 % 请写成 %25，@ 写成 %40）')
    if '://' not in url:
        url = 'http://' + url          # 只写了 IP:端口 就按 HTTP 代理算
    try:
        parts = urllib.parse.urlsplit(url)
    except ValueError as error:
        raise Fail('PROXY_URL 格式不对: ' + str(error))
    schemes = {'http': ('http', 'http', False), 'https': ('https', 'http', True),
               'socks5': ('socks5', 'socks5', False), 'socks5h': ('socks5', 'socks5', False), 'socks': ('socks5', 'socks5', False)}
    scheme = parts.scheme.lower()
    if scheme not in schemes:
        raise Fail('PROXY_URL 不支持的协议 %s（支持 http / https / socks5）' % (scheme or '(空)'))
    if parts.path not in ('', '/') or parts.query or parts.fragment:
        raise Fail('PROXY_URL 只能是 协议://[用户:密码@]主机:端口，不能带路径或参数')
    host = parts.hostname
    if not host:
        raise Fail('PROXY_URL 缺少主机地址')
    try:
        port = parts.port
    except ValueError:
        raise Fail('PROXY_URL 的端口不合法')
    if port is None:
        raise Fail('PROXY_URL 必须写端口，如 http://10.0.0.5:3128')
    if not is_ip(host) and not DOMAIN_RE.match(host):
        raise Fail('PROXY_URL 的主机部分不是合法的域名或 IP: %s（带下划线之类的主机名请直接填 IP）' % host)
    url_user = urllib.parse.unquote(parts.username) if parts.username is not None else ''
    url_pass = urllib.parse.unquote(parts.password) if parts.password is not None else ''  # secret-guard: allow（变量名含 password，不是凭证）
    if user:
        url_user = user
    if password:
        url_pass = password
    if url_pass and not url_user:
        raise Fail('填了代理密码却没有用户名')
    if '\n' in url_user + url_pass or '\r' in url_user + url_pass:
        raise Fail('代理用户名 / 密码不能包含换行')
    norm_scheme, ptype, tls = schemes[scheme]
    return {'scheme': norm_scheme, 'type': ptype, 'tls': tls, 'host': host, 'port': port,
            'user': url_user, 'password': url_pass, 'tls_insecure': bool(tls_insecure) if tls else False}  # secret-guard: allow（变量名含 password，不是凭证）


def proxy_display(proxy, mask=True):
    auth = ''
    if proxy['user']:
        auth = proxy['user'] + (':***' if mask and proxy['password'] else (':' + proxy['password'] if proxy['password'] else '')) + '@'
    host = '[%s]' % proxy['host'] if ':' in proxy['host'] else proxy['host']
    return '%s://%s%s:%d' % (proxy['scheme'], auth, host, proxy['port'])


def yes_no(value, key):
    v = (value or 'no').strip().lower()
    if v in ('yes', 'y', 'true', '1', 'on'):
        return True
    if v in ('no', 'n', 'false', '0', 'off', ''):
        return False
    raise Fail('%s 只能是 yes 或 no: %s' % (key, value))


def parse_port(value, key):
    if not re.match(r'^[0-9]{1,5}$', value or '') or not 1 <= int(value) <= 65535:
        raise Fail('%s 不是合法端口: %s' % (key, value))
    return int(value)


def normalize_cidrs(items, key):
    nets4, nets6 = [], []
    for item in items:
        try:
            if '/' in item:
                net = ipaddress.ip_network(item, strict=False)
            else:
                addr = ipaddress.ip_address(item)
                net = ipaddress.ip_network(item + ('/32' if addr.version == 4 else '/128'))
        except ValueError:
            raise Fail('%s 里有不合法的网段: %s' % (key, item))
        if net.prefixlen == 0:
            raise Fail('%s 不能包含 %s（那会把全部流量当成内网）' % (key, item))
        (nets4 if net.version == 4 else nets6).append(str(net))
    return sorted(set(nets4), key=ipaddress.ip_network), sorted(set(nets6), key=ipaddress.ip_network)


def url_host_port(url):
    parts = urllib.parse.urlsplit(url)
    if parts.scheme not in ('http', 'https') or not parts.hostname:
        raise Fail('不是合法的 http(s) 地址: ' + url)
    try:
        port = parts.port
    except ValueError:
        raise Fail('地址端口不合法: ' + url)
    return parts.hostname, port or (443 if parts.scheme == 'https' else 80)


# ---------------------------------------------------------------- 本机环境探测

def read_resolv_conf():
    try:
        return Path('/etc/resolv.conf').read_text(errors='replace')
    except OSError:
        return ''


def detect_dns_servers():
    """/etc/resolv.conf 的 nameserver；127.0.0.53/54（systemd-resolved）换成它的上游；去掉链路本地地址。"""
    found = []
    for line in read_resolv_conf().splitlines():
        m = re.match(r'^\s*nameserver\s+(\S+)', line)
        if not m:
            continue
        server = m.group(1).split('%')[0]
        if server in ('127.0.0.53', '127.0.0.54'):
            if which('resolvectl'):
                out = run(['resolvectl', 'dns'], check=False).stdout
                for token in re.findall(r'\S+', out):
                    token = token.split('%')[0]  # secret-guard: allow（变量名含 password，不是凭证）
                    if is_ip(token) and token not in found:
                        found.append(token)
            continue
        if not is_ip(server):
            continue
        if ipaddress.ip_address(server).is_link_local:
            continue
        if server not in found:
            found.append(server)
    return found


def detect_search_domains():
    found = []
    for line in read_resolv_conf().splitlines():
        m = re.match(r'^\s*(search|domain)\s+(.+)$', line)
        if not m:
            continue
        for d in m.group(2).split():
            d = d.rstrip('.').lower()
            if DOMAIN_RE.match(d) and d not in found:
                found.append(d)
    if which('hostname'):
        d = run(['hostname', '-d'], check=False).stdout.strip().rstrip('.').lower()
        if d and DOMAIN_RE.match(d) and d not in found:
            found.append(d)
    return found


def local_addresses():
    addrs = set()
    if which('ip'):
        for line in run(['ip', '-o', 'addr', 'show'], check=False).stdout.splitlines():
            parts = line.split()
            if len(parts) >= 4:
                addrs.add(parts[3].split('/')[0].split('%')[0])
    return addrs


def detect_uplink_bridges():
    """本机当上联口用的网桥（默认路由挂在上面，或成员口不是 veth：物理网卡、bond、vlan、虚机 tap）。
    这类网桥上进来的是局域网 / 虚机的包，不能按容器流量接管，否则局域网主机会被当成容器、还能碰到引擎端口。"""
    if not which('ip'):
        return []
    bridges = []
    for line in run(['ip', '-o', 'link', 'show', 'type', 'bridge'], check=False).stdout.splitlines():
        parts = line.split(':')
        if len(parts) >= 2:
            bridges.append(parts[1].strip().split('@')[0])
    if not bridges:
        return []
    default_devs = set()
    for family in ('-4', '-6'):
        default_devs |= set(re.findall(r'\bdev (\S+)', run(['ip', family, 'route', 'show', 'default'], check=False).stdout))
    uplinks = []
    for bridge in bridges:
        if bridge in default_devs:
            uplinks.append(bridge)
            continue
        members = run(['ip', '-d', '-o', 'link', 'show', 'master', bridge], check=False).stdout.splitlines()
        if any(not re.search(r'\bveth\b', line) for line in members):
            uplinks.append(bridge)
    return uplinks


def engine_uid():
    try:
        return pwd.getpwnam(ENGINE_USER).pw_uid
    except KeyError:
        return None


# ---------------------------------------------------------------- 两个探测子进程
# 以引擎用户身份跑（透明代理已在运行时 root 的 DNS / TCP 会被自己接管，引擎用户是规则里放过的那个），
# 代码通过 python3 -c 传入，不依赖脚本文件对该用户可读；密码走 stdin，不进 ps。

DNS_PROBE_SRC = r'''
import json, socket, struct, sys, time
def build(name, qid):
    labels = b"".join(bytes([len(p)]) + p.encode("idna") for p in name.strip(".").split("."))
    return struct.pack(">HHHHHH", qid, 0x0100, 1, 0, 0, 0) + labels + b"\x00" + struct.pack(">HH", 1, 1)
def skip_name(buf, pos):
    while True:
        if pos >= len(buf): raise ValueError("truncated")
        n = buf[pos]
        if n == 0: return pos + 1
        if n & 0xC0 == 0xC0: return pos + 2
        pos += 1 + n
def parse(buf, qid):
    if len(buf) < 12: raise ValueError("short")
    rid, flags, qd, an, _, _ = struct.unpack(">HHHHHH", buf[:12])
    if rid != qid: raise ValueError("id")
    pos = 12
    for _ in range(qd): pos = skip_name(buf, pos) + 4
    addrs = []
    for _ in range(an):
        pos = skip_name(buf, pos)
        rtype, _, _, rdlen = struct.unpack(">HHIH", buf[pos:pos + 10]); pos += 10
        if rtype == 1 and rdlen == 4: addrs.append(socket.inet_ntoa(buf[pos:pos + 4]))
        pos += rdlen
    return flags & 0xF, addrs
def query(name, server, timeout=3.0):
    qid = 0x4242
    fam = socket.AF_INET6 if ":" in server else socket.AF_INET
    deadline = time.monotonic() + timeout
    with socket.socket(fam, socket.SOCK_DGRAM) as s:
        s.sendto(build(name, qid), (server, 53))
        while True:
            left = deadline - time.monotonic()
            if left <= 0: raise socket.timeout("timeout")
            s.settimeout(left)
            data, _ = s.recvfrom(4096)
            if len(data) >= 2 and struct.unpack(">H", data[:2])[0] == qid: return parse(data, qid)
name, servers = sys.argv[1], sys.argv[2:]
notes = []
for server in servers:
    result = None; err = None
    for _ in range(2):
        try: result = query(name, server); break
        except Exception as e: err = e
    if result is None:
        notes.append("%s: 无响应 (%s)" % (server, err)); continue
    rcode, addrs = result
    real = [a for a in addrs if not (a.startswith("198.18.") or a.startswith("198.19."))]
    if rcode == 0 and real:
        print(json.dumps({"ok": True, "server": server, "addrs": real, "detail": "%s: %s -> %s" % (server, name, ", ".join(real))})); sys.exit(0)
    notes.append("%s: rcode=%d 无 A 记录%s" % (server, rcode, "（只有 fake-ip，查询被本机代理接管）" if addrs and not real else ""))
print(json.dumps({"ok": False, "detail": "; ".join(notes) or "没有可用的 DNS 服务器"})); sys.exit(1)
'''

PROXY_PROBE_SRC = r'''
import base64, json, socket, ssl, struct, sys
cfg = json.loads(sys.stdin.read())
host, port, target, tport = cfg["host"], cfg["port"], cfg["target"], cfg["target_port"]
def out(ok, stage, detail, status=None):
    print(json.dumps({"ok": ok, "stage": stage, "detail": detail, "status": status})); sys.exit(0 if ok else 1)
def recv_exact(sock, n):
    buf = b""
    while len(buf) < n:
        chunk = sock.recv(n - len(buf))
        if not chunk: raise ConnectionError("对端提前关闭连接")
        buf += chunk
    return buf
try:
    sock = socket.create_connection((host, port), timeout=6)
except socket.gaierror as e:
    out(False, "resolve", "解析代理主机名 %s 失败: %s" % (host, e))
except OSError as e:
    out(False, "connect", "连不上代理 %s:%d: %s" % (host, port, e.strerror or e))
sock.settimeout(8)
try:
    if cfg["tls"]:
        ctx = ssl.create_default_context()
        if cfg["tls_insecure"]:
            ctx.check_hostname = False; ctx.verify_mode = ssl.CERT_NONE
        try:
            sock = ctx.wrap_socket(sock, server_hostname=host)
        except ssl.SSLCertVerificationError as e:
            out(False, "tls", "代理的 TLS 证书校验失败: %s（自签证书：导入 CA 到 /etc/pki/ca-trust/source/anchors 后 update-ca-trust，或 PROXY_TLS_INSECURE=yes）" % e.verify_message)
        except ssl.SSLError as e:
            out(False, "tls", "与代理建立 TLS 失败: %s（这个端口确定是 https 代理？）" % e)
    if cfg["type"] == "http":
        req = "CONNECT %s:%d HTTP/1.1\r\nHost: %s:%d\r\nUser-Agent: flocks-egress/probe\r\n" % (target, tport, target, tport)
        if cfg["user"]:
            token = base64.b64encode(("%s:%s" % (cfg["user"], cfg["password"])).encode("utf-8")).decode("ascii")  # secret-guard: allow（变量名含 password，不是凭证）
            req += "Proxy-Authorization: Basic %s\r\n" % token
        req += "\r\n"
        sock.sendall(req.encode("utf-8"))
        data = b""
        while b"\r\n\r\n" not in data and len(data) < 65536:
            chunk = sock.recv(4096)
            if not chunk: break
            data += chunk
        line = data.split(b"\r\n", 1)[0].decode("latin-1", "replace")
        parts = line.split(" ", 2)
        if len(parts) < 2 or not parts[1].isdigit():
            out(False, "connect_target", "代理返回的不是 HTTP 应答（%r）：这个端口是 HTTP 代理吗？" % line[:80])
        code = int(parts[1])
        if code == 200:
            out(True, "ok", "CONNECT %s:%d → %d" % (target, tport, code), code)
        if code in (407, 401):
            out(False, "auth", "代理要求认证 / 账号密码不对（HTTP %d）%s" % (code, "" if cfg["user"] else "：请加 --proxy-user / --proxy-password"), code)
        if code == 403:
            out(False, "acl", "代理拒绝 CONNECT %s:%d（HTTP 403，多半是 ACL 不放行该目标或端口）" % (target, tport), code)
        out(False, "target", "代理接受了连接但访问 %s:%d 失败（HTTP %d %s）" % (target, tport, code, parts[2] if len(parts) > 2 else ""), code)
    else:
        methods = b"\x00\x02" if cfg["user"] else b"\x00"
        sock.sendall(b"\x05" + bytes([len(methods)]) + methods)
        ver, method = recv_exact(sock, 2)
        if ver != 5:
            out(False, "connect_target", "对端不是 SOCKS5 代理（版本字节 %d）" % ver)
        if method == 0xFF:
            out(False, "auth", "SOCKS5 代理不接受我们的认证方式%s" % ("" if cfg["user"] else "（它要求账号密码：请加 --proxy-user / --proxy-password）"))
        if method == 2:
            u = cfg["user"].encode("utf-8"); p = cfg["password"].encode("utf-8")
            sock.sendall(b"\x01" + bytes([len(u)]) + u + bytes([len(p)]) + p)
            _, status = recv_exact(sock, 2)
            if status != 0:
                out(False, "auth", "SOCKS5 账号密码不对（状态 %d）" % status)
        t = target.encode("idna")
        sock.sendall(b"\x05\x01\x00\x03" + bytes([len(t)]) + t + struct.pack(">H", tport))
        _, rep, _, atyp = recv_exact(sock, 4)
        if rep == 0:
            out(True, "ok", "SOCKS5 CONNECT %s:%d → 成功" % (target, tport), rep)
        reasons = {1: "代理内部错误", 2: "规则不允许", 3: "网络不可达", 4: "主机不可达", 5: "连接被拒", 6: "TTL 过期", 7: "命令不支持", 8: "地址类型不支持"}
        out(False, "acl" if rep == 2 else "target", "SOCKS5 CONNECT %s:%d 失败: %s" % (target, tport, reasons.get(rep, "REP=%d" % rep)), rep)
except (OSError, ConnectionError, ValueError) as e:
    out(False, "io", "与代理通信出错: %s" % e)
'''


def probe_runner_uid():
    """探测子进程用哪个身份跑：引擎用户存在就用它（透明代理运行中不会被自己接管），否则 root（还没装过，没有规则）。"""
    return engine_uid()


def dns_probe(name, servers, uid=None):
    """问指定 DNS 服务器能否解析 name 的 A 记录（不经系统解析器）。返回 dict(ok, addrs, detail)。uid 不给就按 probe_runner_uid()。"""
    result = run([sys.executable, '-c', DNS_PROBE_SRC, name] + list(servers), check=False, timeout=45,
                 uid=probe_runner_uid() if uid is None else uid)
    try:
        return json.loads(result.stdout.strip().splitlines()[-1])
    except (ValueError, IndexError):
        return {'ok': False, 'detail': (result.stderr.strip() or '探测子进程异常')[-300:]}


def proxy_probe(proxy, target, target_port):
    """真的经代理 CONNECT 一次目标。返回 dict(ok, stage, detail, status)。"""
    payload = json.dumps({'host': proxy['host'], 'port': proxy['port'], 'type': proxy['type'], 'tls': proxy['tls'],
                          'tls_insecure': proxy['tls_insecure'], 'user': proxy['user'], 'password': proxy['password'],
                          'target': target, 'target_port': target_port})
    result = run([sys.executable, '-c', PROXY_PROBE_SRC], data=payload, check=False, timeout=45, uid=probe_runner_uid())
    try:
        return json.loads(result.stdout.strip().splitlines()[-1])
    except (ValueError, IndexError):
        return {'ok': False, 'stage': 'io', 'detail': (result.stderr.strip() or '探测子进程异常')[-300:], 'status': None}


# ---------------------------------------------------------------- 参数 → 设置

def build_settings(values, *, probe_dns=True):
    """校验参数文件的键值，补默认值、探测 DNS 模式，得到渲染所需的一切（settings.json 的内容，不含密码）。"""
    v = dict(DEFAULTS)
    v.update({k: val for k, val in values.items() if val is not None})
    s = {'version': VERSION, 'created': now_str()}

    s['proxy'] = parse_proxy(v.get('PROXY_URL', ''), v.get('PROXY_USER') or None, v.get('PROXY_PASSWORD') or None,
                             yes_no(v.get('PROXY_TLS_INSECURE'), 'PROXY_TLS_INSECURE'))

    scope = v['SCOPE']
    if scope not in ('host', 'user'):
        raise Fail('SCOPE 只能是 host 或 user: ' + scope)
    s['scope'] = scope
    s['flocks_user'], s['flocks_uid'] = None, None
    if scope == 'user':
        name = v.get('FLOCKS_USER', '')
        if not name:
            raise Fail('SCOPE=user 时必须填 FLOCKS_USER（运行 flocks 的系统用户）')
        if name == ENGINE_USER:
            raise Fail('FLOCKS_USER 不能是 %s（那是代理引擎自己的用户，会形成回环）' % ENGINE_USER)
        try:
            s['flocks_uid'] = pwd.getpwnam(name).pw_uid
        except KeyError:
            raise Fail('系统里没有用户 %s，请先创建或改 FLOCKS_USER' % name)
        s['flocks_user'] = name
        if s['flocks_uid'] == 0:
            warn('FLOCKS_USER 是 root：效果接近 SCOPE=host，所有 root 进程的公网流量都会被接管')
    elif v.get('FLOCKS_USER'):
        warn('SCOPE=host 接管整台机器，FLOCKS_USER=%s 不起作用' % v['FLOCKS_USER'])
    containers = v['CONTAINERS']
    if containers not in ('auto', 'yes', 'no'):
        raise Fail('CONTAINERS 只能是 auto / yes / no: ' + containers)
    # 容器（Docker bridge 等网桥）来的流量没有 uid 可认，user 范围下默认不接管
    s['containers'] = (scope == 'host') if containers == 'auto' else containers == 'yes'
    if s['containers'] and scope == 'user':
        warn('CONTAINERS=yes 且 SCOPE=user：网桥来的容器流量分不出用户，所有容器的公网流量都会被接管')
    s['uplink_bridges'] = detect_uplink_bridges() if s['containers'] else []

    extra4, extra6 = normalize_cidrs(split_list(v.get('DIRECT_CIDRS')), 'DIRECT_CIDRS')
    s['extra_direct'] = extra4 + extra6
    notes = []

    domains = []
    for d in split_list(v.get('DIRECT_DOMAINS')):
        d = d.rstrip('.').lower()
        if not DOMAIN_RE.match(d):
            raise Fail('DIRECT_DOMAINS 里有不合法的域名: ' + d)
        if d not in domains:
            domains.append(d)
    s['auto_domains'] = [d for d in detect_search_domains() if d not in domains]
    s['direct_domains'] = domains + s['auto_domains']

    dns_mode = v['DNS_MODE']
    if dns_mode not in ('auto', 'fake-ip', 'redir-host'):
        raise Fail('DNS_MODE 只能是 auto / fake-ip / redir-host: ' + dns_mode)
    s['dns_mode'] = dns_mode
    servers = split_list(v.get('DNS_SERVERS'))
    for server in servers:
        if not is_ip(server):
            raise Fail('DNS_SERVERS 里有不合法的地址: ' + server)
    s['dns_servers_configured'] = bool(servers)
    if not servers:
        servers = detect_dns_servers()
        if not servers:
            raise Fail('DNS_SERVERS 为空，且 /etc/resolv.conf 里找不到可用的 DNS，请填 DNS_SERVERS')
        notes.append('DNS_SERVERS 未填写，使用系统当前 DNS: ' + ', '.join(servers))
    s['dns_servers'] = servers

    # 代理自己的地址也算直连（root 直接 curl -x 它时不必绕进引擎）：写的是域名就问内网 DNS 要 A 记录
    proxy_ips = []
    if is_ip(s['proxy']['host']):
        proxy_ips = [s['proxy']['host']]
    elif probe_dns:
        result = dns_probe(s['proxy']['host'], servers)
        if result.get('ok'):
            proxy_ips = result['addrs']
            notes.append('代理主机名 %s 解析为 %s，加入直连集合' % (s['proxy']['host'], ', '.join(proxy_ips)))
        else:
            warn('内网 DNS 解析不了代理主机名 %s（%s），它没有加进直连集合；引擎自己解析时也可能失败' % (s['proxy']['host'], result.get('detail')))
    s['proxy_ips'] = proxy_ips
    # 本机自己的地址也直连（A 用公网地址时，进程连本机对外地址不该绕去代理）；回环 / 链路本地 / 组播已内置，fake-ip 段不算
    local_ips = []
    for addr in sorted(local_addresses()):
        try:
            ip = ipaddress.ip_address(addr)
        except ValueError:
            continue
        if ip.is_loopback or ip.is_link_local or ip.is_multicast or ip.is_unspecified or ip in ipaddress.ip_network(FAKEIP_BLOCK):
            continue
        local_ips.append(str(ip))
    s['local_ips'] = local_ips
    # 内网 DNS 服务器也直连：redir-host 模式不接管 DNS，别让「公网 UDP 拒绝」误伤一台公网地址的 DNS
    s['direct4'], s['direct6'] = normalize_cidrs(BUILTIN4 + BUILTIN6 + extra4 + extra6 + proxy_ips + servers + local_ips, 'DIRECT_CIDRS')

    if v['PUBLIC_UDP'] not in ('reject', 'allow'):
        raise Fail('PUBLIC_UDP 只能是 reject 或 allow: ' + v['PUBLIC_UDP'])
    s['public_udp'] = v['PUBLIC_UDP']
    if v['ENGINE_DOWN'] not in ('reject', 'direct'):
        raise Fail('ENGINE_DOWN 只能是 direct（引擎不在时直连兜底，默认）或 reject（引擎不在时公网立刻被拒）: ' + v['ENGINE_DOWN'])
    s['engine_down'] = v['ENGINE_DOWN']
    if v['ENGINE_LOG_LEVEL'] not in ('debug', 'info', 'warning', 'error', 'silent'):
        raise Fail('ENGINE_LOG_LEVEL 只能是 debug/info/warning/error/silent: ' + v['ENGINE_LOG_LEVEL'])
    s['log_level'] = v['ENGINE_LOG_LEVEL']
    ports = {key: parse_port(v[key.upper() + '_PORT'], key.upper() + '_PORT') for key in ('redir', 'dns', 'mixed')}
    if len(set(ports.values())) != 3:
        raise Fail('REDIR_PORT / DNS_PORT / MIXED_PORT 不能重复')
    s['ports'] = ports

    s['check_public_urls'] = split_list(v.get('CHECK_PUBLIC_URLS')) or split_list(DEFAULTS['CHECK_PUBLIC_URLS'])
    for url in s['check_public_urls']:
        url_host_port(url)
    s['check_intranet_url'] = v.get('CHECK_INTRANET_URL') or None
    if s['check_intranet_url']:
        url_host_port(s['check_intranet_url'])

    # DNS 模式：auto = 问一下内网 DNS 能不能解析公网域名。能 → redir-host（不碰 DNS，内网/外网纯按真实 IP 分）；
    # 不能 → fake-ip（接管 DNS，公网域名给占位地址，域名交给代理去解析；内网域名靠 search 域 / DIRECT_DOMAINS 识别）
    probe_host = url_host_port(s['check_public_urls'][0])[0]
    if is_ip(probe_host):
        probe_host = 'www.baidu.com'
    s['probe_host'] = probe_host
    if dns_mode == 'auto':
        if not probe_dns:
            s['dns_mode_effective'], s['dns_mode_reason'] = 'fake-ip', '未探测，保守用 fake-ip'
        else:
            result = dns_probe(probe_host, servers)
            if result.get('ok'):
                s['dns_mode_effective'] = 'redir-host'
                s['dns_mode_reason'] = '内网 DNS 能解析公网域名（%s），不接管 DNS，内网/外网按真实 IP 区分' % result.get('detail', '')
            else:
                s['dns_mode_effective'] = 'fake-ip'
                s['dns_mode_reason'] = '内网 DNS 解析不了公网域名 %s（%s），接管 DNS：公网域名用 fake-ip 占位交给代理解析，内网域名靠 search 域 / DIRECT_DOMAINS' % (probe_host, result.get('detail', ''))
        notes.append('DNS_MODE=auto → %s：%s' % (s['dns_mode_effective'], s['dns_mode_reason']))
    else:
        s['dns_mode_effective'], s['dns_mode_reason'] = dns_mode, 'egress.conf 指定'
    s['dns_hijack'] = s['dns_mode_effective'] == 'fake-ip'

    if s['dns_hijack'] and scope == 'host':
        local = local_addresses()
        for server in servers:
            if ipaddress.ip_address(server).is_loopback or server in local:
                raise Fail('fake-ip 模式且 SCOPE=host 时 DNS_SERVERS 不能是本机地址（%s）：本机 DNS 转发器往上游发的查询也会被接管，引擎解析内网域名会绕回自己。'
                           '请把 DNS_SERVERS 填成真正的内网 DNS 服务器 IP；内网 DNS 能解析公网域名的话可改 DNS_MODE=redir-host（不碰 DNS）；或改 SCOPE=user' % server)
    if s['auto_domains'] and s['dns_hijack']:
        notes.append('自动加入内网域名后缀（来自 resolv.conf search 域 / 本机域名）: ' + ' '.join(s['auto_domains']))
    s['notes'] = notes
    return s


def settings_public(s):
    """写进 settings.json 的部分：不带密码。"""
    out = json.loads(json.dumps(s))
    out['proxy'] = dict(s['proxy'], password='', has_password=bool(s['proxy']['password']))  # secret-guard: allow（变量名含 password，不是凭证）
    out['proxy_display'] = proxy_display(s['proxy'])
    return out


# ---------------------------------------------------------------- 渲染：引擎配置 / nft 规则 / systemd 单元

def ystr(value):
    """YAML 里的字符串一律用 JSON 双引号形式（JSON 标量是合法 YAML）。"""
    return json.dumps(value, ensure_ascii=False)


def render_config_yaml(s):
    p, ports = s['proxy'], s['ports']
    lines = [
        '# flocks-egress 引擎配置 —— setup-a.py v%s 根据 egress.conf 渲染，%s' % (VERSION, now_str()),
        '# 手工改动会在下次 reconfigure 时被覆盖（上一版保留为 config.yaml.prev）。要改参数请改 egress.conf。',
        '# dns-mode: %s' % s['dns_mode_effective'],
        'mode: rule',
        'log-level: %s' % s['log_level'],
        'ipv6: false',
        'allow-lan: false',
        'bind-address: 127.0.0.1',
        '# 入口分开写：透明代理口%s，普通 HTTP/SOCKS 口只在回环上（对外不开放代理）' % ('监听所有接口，网桥转来的容器连接也能进来；来源由 nft input 链限定为本机与容器网桥' if s.get('containers') else '与普通口都只在回环上'),
        'listeners:',
        '  - name: redir-in',
        '    type: redir',
        '    port: %d' % ports['redir'],
        '    listen: %s' % ('0.0.0.0' if s.get('containers') else '127.0.0.1'),
        '  - name: mixed-in',
        '    type: mixed',
        '    port: %d' % ports['mixed'],
        '    listen: 127.0.0.1',
        'unified-delay: true',
        'tcp-concurrent: false',
        'find-process-mode: "off"',
        'geodata-mode: false',
        'geo-auto-update: false',
        'profile:',
        '  store-selected: false',
        '  store-fake-ip: %s' % ('true' if s['dns_hijack'] else 'false'),
        '',
        '# 从 TLS SNI / HTTP Host 里嗅探真实域名：纯 IP 目标也能以域名形式交给代理 CONNECT（465 / 993 是邮件渠道的 SMTPS / IMAPS）',
        'sniffer:',
        '  enable: true',
        '  force-dns-mapping: true',
        '  parse-pure-ip: true',
        '  override-destination: true',
        '  sniff:',
        '    HTTP:',
        '      ports: [80, 8080-8880]',
        '    TLS:',
        '      ports: [443, 465, 993, 8443]',
        '',
    ]
    if s['dns_hijack']:
        lines += [
            '# fake-ip：本机所有 DNS 查询被 nft 转到这里。命中 fake-ip-filter 的域名返回真实解析结果（问内网 DNS），其余给 198.18.x.x 占位',
            'dns:',
            '  enable: true',
            '  listen: %s:%d' % ('0.0.0.0' if s.get('containers') else '127.0.0.1', ports['dns']),
            '  ipv6: false',
            '  enhanced-mode: fake-ip',
            '  fake-ip-range: %s' % FAKEIP_RANGE,
            '  fake-ip-filter-mode: blacklist',
            '  fake-ip-filter:',
            '    - "localhost"',
            '    - "+.localhost"',
            '    - "+.local"',
            '    - "+.in-addr.arpa"',
            '    - "+.ip6.arpa"',
        ]
        for d in s['direct_domains']:
            lines.append('    - %s' % ystr('+.' + d))
        if not is_ip(p['host']):
            lines.append('    - %s' % ystr(p['host']))
        lines.append('  # 内网 DNS：解析内网域名、代理主机名；不配 fallback，引擎不会自己往公网发 DNS')
        lines.append('  nameserver:')
        lines += ['    - %s' % ystr(n) for n in s['dns_servers']]
        lines.append('  default-nameserver:')
        lines += ['    - %s' % ystr(n) for n in s['dns_servers']]
    else:
        lines += ['# redir-host：不接管 DNS，程序用系统解析器拿真实 IP；引擎不需要自己的 DNS', 'dns:', '  enable: false']
    lines += ['', 'proxies:', '  - name: corp-proxy', '    type: %s' % p['type'], '    server: %s' % ystr(p['host']), '    port: %d' % p['port']]
    if p['user']:
        lines.append('    username: %s' % ystr(p['user']))
    if p['password']:
        lines.append('    password: %s' % ystr(p['password']))
    if p['tls']:
        lines.append('    tls: true')
        lines.append('    skip-cert-verify: %s' % ('true' if p['tls_insecure'] else 'false'))
    lines += ['', '# 规则从上到下匹配：内网域名 / 内网网段直连，其余全部交给代理', 'rules:']
    if s['dns_hijack']:
        lines += ['  - DOMAIN-SUFFIX,%s,DIRECT' % d for d in s['direct_domains']]
    lines += ['  - IP-CIDR,%s,DIRECT,no-resolve' % c for c in s['direct4']]
    lines += ['  - IP-CIDR6,%s,DIRECT,no-resolve' % c for c in s['direct6']]
    lines.append('  - MATCH,corp-proxy')
    return '\n'.join(lines) + '\n'


def scope_rule(s, uid):
    if s['scope'] == 'host':
        return 'meta skuid %d return' % uid, '接管整台机器发出的流量，只放过引擎用户 %s（uid %d），避免回环' % (ENGINE_USER, uid)
    return 'meta skuid != %d return' % s['flocks_uid'], '只接管用户 %s（uid %d）发出的流量，其他用户一律不碰' % (s['flocks_user'], s['flocks_uid'])


def nft_sets(s):
    return [
        '    set direct4 {',
        '        type ipv4_addr',
        '        flags interval',
        '        auto-merge',
        '        elements = { %s }' % ', '.join(s['direct4']),
        '    }',
        '    set direct6 {',
        '        type ipv6_addr',
        '        flags interval',
        '        auto-merge',
        '        elements = { %s }' % ', '.join(s['direct6']),
        '    }',
    ]


def nft_policy_chains(s, uid):
    """本机进程的分流：output 链上 内网直连 / 公网 TCP 转引擎 / 公网 UDP、IPv6 处置。"""
    match_line, _ = scope_rule(s, uid)
    ports = s['ports']
    lines = [
        '    chain egress_nat {',
        '        type nat hook output priority dstnat; policy accept;',
        '        %s' % match_line,
        '        ip daddr @direct4 counter return',
        '        ip6 daddr @direct6 counter return',
        '        meta nfproto ipv4 meta l4proto tcp counter redirect to :%d' % ports['redir'],
        '    }',
        '    chain egress_guard {',
        '        type filter hook output priority filter; policy accept;',
        '        # 已建立连接的回包（含别人连进来的监听端口）不在此处理，只管新发起的',
        '        ct state established,related return',
        '        %s' % match_line,
        '        ip daddr @direct4 return',
        '        ip6 daddr @direct6 return',
        '        meta nfproto ipv6 meta l4proto tcp counter reject with tcp reset',
    ]
    if s['public_udp'] == 'reject':
        lines.append('        # 公网 UDP 快速拒绝（fake-ip 模式下 DNS 已先被 %s 表转走），QUIC 等会立刻回退到 TCP' % ENGINE_TABLE)
        lines.append('        meta l4proto udp counter reject')
    else:
        lines.append('        # PUBLIC_UDP=allow：公网 UDP 放行直连')
    lines.append('    }')
    return lines


def nft_container_filters(s):
    """容器链共用的两段：哪些接口算容器网桥（上联网桥除外）。"""
    uplinks = s.get('uplink_bridges') or []
    not_container = ['        meta iifkind != "bridge" return'] + (['        iifname { %s } return' % ', '.join(ystr(b) for b in uplinks)] if uplinks else [])
    bridge_only = '        meta iifkind "bridge"%s return' % (' iifname != { %s }' % ', '.join(ystr(b) for b in uplinks) if uplinks else '')
    return not_container, bridge_only


def nft_container_input_chain(s):
    """引擎端口的门禁：透明代理口（和 fake-ip 的 DNS 口）监听在所有接口上，只有本机和容器网桥能碰它。"""
    ports = s['ports']
    _, bridge_only = nft_container_filters(s)
    return [
        '    chain egress_input {',
        '        type filter hook input priority filter - 1; policy accept;',
        '        iifname "lo" return',
        bridge_only,
        '        tcp dport { %d, %d } counter drop' % (ports['redir'], ports['dns']),
        '        udp dport %d counter drop' % ports['dns'],
        '    }',
    ]


def nft_container_policy_chains(s):
    """容器（网桥）流量的分流：prerouting 转引擎 / forward 处置公网 UDP、IPv6。"""
    ports = s['ports']
    not_container, _ = nft_container_filters(s)
    lines = [
        '    # ---- 容器流量：Docker / podman 的 bridge 网络有自己的网络命名空间，包是从网桥转发出来的，不经 output 链，这里同样处理',
        '    chain egress_prerouting {',
        '        type nat hook prerouting priority dstnat; policy accept;',
    ] + not_container + [
        '        # 发给本机自己的（含 docker 发布出来的端口）不管',
        '        fib daddr type local return',
        '        ip daddr @direct4 counter return',
        '        ip6 daddr @direct6 counter return',
    ]
    if not s['dns_hijack']:
        dns4 = [n for n in s['dns_servers'] if ':' not in n]
        if dns4:
            lines += ['        # redir-host 不接管 DNS，但容器被 Docker 塞的可能是 8.8.8.8 之类公网 DNS（宿主 resolv.conf 只有回环地址时）：',
                      '        # 发往公网 DNS 的查询改送内网 DNS（内网目标在上面已放行，容器自己的 DNS 服务、别的内网 DNS 都不动）',
                      '        meta nfproto ipv4 udp dport 53 counter dnat ip to %s' % dns4[0],
                      '        meta nfproto ipv4 tcp dport 53 counter dnat ip to %s' % dns4[0]]
    lines += [
        '        meta nfproto ipv4 meta l4proto tcp counter redirect to :%d' % ports['redir'],
        '    }',
        '    chain egress_forward {',
        '        type filter hook forward priority filter; policy accept;',
    ] + not_container + [
        '        ct state established,related return',
        '        ip daddr @direct4 return',
        '        ip6 daddr @direct6 return',
        '        meta nfproto ipv6 meta l4proto tcp counter reject with tcp reset',
    ]
    if s['public_udp'] == 'reject':
        lines.append('        meta l4proto udp counter reject')
    lines.append('    }')
    return lines


def nft_dns_chains(s, uid):
    """fake-ip 模式的 DNS 接管：本机 53 转引擎；开了容器接管的话网桥来的 53 也转。"""
    match_line, _ = scope_rule(s, uid)
    ports = s['ports']
    lines = [
        '    chain dns_nat {',
        '        # 排在分流 nat 链前面：DNS 先转给引擎，其余再按分流规则处理',
        '        type nat hook output priority dstnat - 10; policy accept;',
        '        %s' % match_line,
        '        meta nfproto ipv4 udp dport 53 counter redirect to :%d' % ports['dns'],
        '        meta nfproto ipv4 tcp dport 53 counter redirect to :%d' % ports['dns'],
        '    }',
        '    chain dns_guard {',
        '        type filter hook output priority filter - 10; policy accept;',
        '        ct state established,related return',
        '        %s' % match_line,
        '        # 引擎只监听 IPv4：IPv6 DNS 直接拒绝，让解析器换用 IPv4 DNS（resolv.conf 需至少有一个 IPv4 DNS）',
        '        meta nfproto ipv6 udp dport 53 counter reject',
        '        meta nfproto ipv6 tcp dport 53 counter reject with tcp reset',
        '    }',
    ]
    if s.get('containers'):
        not_container, _ = nft_container_filters(s)
        lines += [
            '    chain dns_prerouting {',
            '        # 容器（网桥）发出的 DNS 也转给引擎',
            '        type nat hook prerouting priority dstnat - 10; policy accept;',
        ] + not_container + [
            '        meta nfproto ipv4 udp dport 53 counter redirect to :%d' % ports['dns'],
            '        meta nfproto ipv4 tcp dport 53 counter redirect to :%d' % ports['dns'],
            '    }',
        ]
    return lines


def render_rules_nft(s, uid):
    """常驻规则表：随 flocks-egress-rules.service 加载，引擎停了也在。
    ENGINE_DOWN=direct（默认）：分流规则在随引擎启停的 %s 表，这里只剩引擎端口门禁（和 fake-ip 占位地址的快速拒绝），引擎不在时一切直连兜底；
    ENGINE_DOWN=reject：分流规则放在这张表里，引擎不在时公网 TCP 转到没人听的端口 → 立刻被拒，不漏成直连。""" % ENGINE_TABLE
    _, scope_note = scope_rule(s, uid)
    ports = s['ports']
    fail_closed = s.get('engine_down', 'reject') == 'reject'
    lines = [
        '#!/usr/sbin/nft -f',
        '# flocks-egress 常驻规则 —— setup-a.py v%s 渲染，%s' % (VERSION, now_str()),
        '# %s：' % scope_note,
        '#   目标是内网地址（direct4 / direct6）→ 直连，不碰',
        '#   其余公网 TCP → 127.0.0.1:%d（本机引擎 → 代理）' % ports['redir'],
        '#   其余公网 UDP → %s；公网 IPv6 TCP → 拒绝（引擎只走 IPv4）' % s['public_udp'],
        '#   引擎不在时：%s' % ('公网 TCP 立刻被拒，不漏成直连（ENGINE_DOWN=reject）' if fail_closed else '分流规则随引擎一起卸掉，一切直连兜底（ENGINE_DOWN=direct，默认；规则在 %s 表里）' % ENGINE_TABLE),
        '# 先建再删再建，一个事务内完成，重复加载幂等。',
        'table inet %s' % TABLE,
        'delete table inet %s' % TABLE,
        'table inet %s {' % TABLE,
        '    comment %s' % ystr(s['marker']),
    ]
    if fail_closed:
        lines += nft_sets(s) + nft_policy_chains(s, uid)
        if s.get('containers'):
            lines += nft_container_input_chain(s) + nft_container_policy_chains(s)
    else:
        lines.append('    # ENGINE_DOWN=direct（默认）：分流规则见 %s 表（随引擎启停）；这里只留引擎端口的门禁' % ENGINE_TABLE)
        if s.get('containers'):
            lines += nft_container_input_chain(s)
        if s['dns_hijack']:
            # 引擎停掉的瞬间，程序手里已经解析到的 198.18.x.x 占位地址会被直接连出去、挂到超时；引擎在跑时这种包早在 nat 里改成了 127.0.0.1，不会碰到这里
            lines += [
                '    chain egress_fakeip_guard {',
                '        type filter hook output priority filter; policy accept;',
                '        ip daddr %s meta l4proto tcp counter reject with tcp reset' % FAKEIP_BLOCK,
                '    }',
            ]
            if s.get('containers'):
                not_container, _ = nft_container_filters(s)
                lines += ['    chain egress_fakeip_forward {', '        type filter hook forward priority filter; policy accept;'] + not_container + [
                    '        ip daddr %s meta l4proto tcp counter reject with tcp reset' % FAKEIP_BLOCK,
                    '    }',
                ]
    lines.append('}')
    return '\n'.join(lines) + '\n'


def render_engine_nft(s, uid):
    """随引擎进程加载 / 卸载的表：fake-ip 的 DNS 接管（引擎不在时 DNS 走原来的路，内网域名解析不受影响）；
    ENGINE_DOWN=direct 时分流规则也在这里。什么都不需要时是一张空表（保证 systemd 停止时的 delete 有对象）。"""
    fail_closed = s.get('engine_down', 'reject') == 'reject'
    lines = [
        '#!/usr/sbin/nft -f',
        '# flocks-egress 随引擎启停的规则 —— setup-a.py v%s 渲染，%s；随 %s 启停' % (VERSION, now_str(), ENGINE_UNIT),
        'table inet %s' % ENGINE_TABLE,
        'delete table inet %s' % ENGINE_TABLE,
        'table inet %s {' % ENGINE_TABLE,
        '    comment %s' % ystr(s['marker']),
    ]
    if not fail_closed:
        lines += nft_sets(s) + nft_policy_chains(s, uid)
        if s.get('containers'):
            lines += nft_container_policy_chains(s)
    if s['dns_hijack']:
        lines += nft_dns_chains(s, uid)
    if fail_closed and not s['dns_hijack']:
        lines.append('    # redir-host 模式且 ENGINE_DOWN=reject：这张表故意留空（保证 systemd 停止时的 delete 有对象）')
    lines.append('}')
    return '\n'.join(lines) + '\n'


RULES_UNIT_TEMPLATE = '''# flocks-egress 常驻 nft 规则表（ENGINE_DOWN=reject 时分流规则在这里；direct 时这里只有引擎端口门禁，分流随引擎服务启停）。
# 开机在网络起来之前加载；停掉它 = 引擎一起停、全部恢复直连。由 setup-a.py 安装；改参数请改 /etc/flocks-egress/egress.conf 后 sudo flocks-egress reconfigure
[Unit]
Description=Flocks egress rules (nftables): intranet direct, public TCP to local engine
Documentation=file:///opt/flocks-egress/README.md
DefaultDependencies=no
# nftables.service 启动时会 flush ruleset，必须排在它后面；在网络起来前就位
After=local-fs.target systemd-sysctl.service nftables.service
Before=network-pre.target network.target
Wants=network-pre.target

[Service]
Type=oneshot
RemainAfterExit=yes
ExecStart={nft} -f /etc/flocks-egress/rules.nft
# reload = 重新加载常驻表，引擎在跑就连 DNS 表一起补上（nftables.service 清空 ruleset 之后靠这个恢复，见 nftables.service.d 的 drop-in）
ExecReload=/usr/bin/python3 /opt/flocks-egress/bin/flocks-egress _rules-reload
ExecStop=-{nft} delete table inet flocks_egress_engine
ExecStop=-{nft} delete table inet flocks_egress

[Install]
WantedBy=multi-user.target
'''

ENGINE_UNIT_TEMPLATE = '''# flocks-egress 引擎（mihomo）：接住被 nft 转来的公网连接，交给客户的代理。以低权限用户运行，只保留 CAP_NET_ADMIN（透明代理监听需要）。
# 由 setup-a.py 安装；改参数请改 /etc/flocks-egress/egress.conf 后 sudo flocks-egress reconfigure
[Unit]
Description=Flocks egress engine (mihomo): public traffic via the customer's HTTP/SOCKS5 proxy
Documentation=file:///opt/flocks-egress/README.md
Requires=flocks-egress-rules.service
After=flocks-egress-rules.service network-online.target nss-lookup.target
Wants=network-online.target
PartOf=flocks-egress-rules.service

[Service]
Type=simple
User=flocks-egress
Group=flocks-egress
# DNS 接管表跟着引擎进程走（前缀 + 表示以 root 执行）：引擎停了 DNS 就恢复直连，内网域名解析不受影响
ExecStartPre=+{nft} -f /etc/flocks-egress/engine.nft
ExecStart=/opt/flocks-egress/bin/mihomo -d /var/lib/flocks-egress -f /etc/flocks-egress/config.yaml
ExecStopPost=-+{nft} delete table inet flocks_egress_engine
Restart=always
RestartSec=2
TimeoutStopSec=15

AmbientCapabilities=CAP_NET_ADMIN
CapabilityBoundingSet=CAP_NET_ADMIN
NoNewPrivileges=true
ProtectSystem=strict
ProtectHome=true
ReadWritePaths=/var/lib/flocks-egress
PrivateTmp=true
ProtectKernelTunables=true
ProtectKernelModules=true
ProtectKernelLogs=true
ProtectControlGroups=true
ProtectClock=true
ProtectHostname=true
RestrictNamespaces=true
RestrictRealtime=true
RestrictSUIDSGID=true
LockPersonality=true
SystemCallArchitectures=native
RestrictAddressFamilies=AF_INET AF_INET6 AF_UNIX AF_NETLINK
LimitNOFILE=1048576

[Install]
WantedBy=multi-user.target
'''


NFTABLES_DROPIN_TEMPLATE = '''# flocks-egress 装的 drop-in：nftables.service 启动 / reload 时会 flush ruleset（把本工具的表也清掉），
# 之后把表加回来。--no-block 是为了不和 nftables.service 自己的 job 互相等待；本工具的规则单元没在跑时什么都不做。
[Service]
ExecStartPost=-/usr/bin/systemctl --no-block try-reload-or-restart flocks-egress-rules.service
ExecReload=-/usr/bin/systemctl --no-block try-reload-or-restart flocks-egress-rules.service
'''


def render_units(nft_path):
    return RULES_UNIT_TEMPLATE.replace('{nft}', nft_path), ENGINE_UNIT_TEMPLATE.replace('{nft}', nft_path)


def cmd_rules_reload(_args=None):
    """flocks-egress-rules.service 的 ExecReload：重新加载常驻表；引擎服务在跑就把 DNS 表也加回来。"""
    require_root()
    nft = which('nft')
    if not nft:
        raise Fail('缺少 nft 命令')
    run([nft, '-f', str(RULES_NFT)])
    if systemctl_is('active', ENGINE_UNIT) == 'active' and ENGINE_NFT.is_file():
        run([nft, '-f', str(ENGINE_NFT)])
    drop_legacy_table(nft)
    return 0


def drop_legacy_table(nft):
    """早期 2.0.0 构建的 DNS 接管表叫 flocks_egress_dns：留着会在新表卸掉后继续把 53 转到没人听的端口，见到就删。"""
    if nft and nft_table_exists(nft, LEGACY_TABLE):
        run([nft, 'delete', 'table', 'inet', LEGACY_TABLE], check=False)
    try:
        LEGACY_NFT.unlink()
    except FileNotFoundError:
        pass


def mask_yaml(text):
    return re.sub(r'^(\s*password:).*$', r'\1 <隐藏>', text, flags=re.M)


# ---------------------------------------------------------------- 系统状态读取

def require_root():
    if sys.platform != 'linux' or os.geteuid() != 0:
        raise Fail('只能在 Linux 上以 root 执行（sudo bash setup-a.sh ... / sudo flocks-egress ...）；--help 可在任意系统查看')


def os_release():
    try:
        text = Path('/etc/os-release').read_text()
    except OSError:
        return {}
    return {k: v.strip('"') for k, v in (line.split('=', 1) for line in text.splitlines() if '=' in line)}


def systemctl_is(prop, unit):
    return run(['systemctl', 'is-' + prop, unit], check=False, timeout=30).stdout.strip()


def unit_installed(unit):
    return (UNITS / unit).exists()


def nft_table_exists(nft, table):
    return run([nft, 'list', 'table', 'inet', table], check=False).returncode == 0


def nft_table_marker(nft, table):
    """表存在时返回它的 comment（我们写的 marker），不存在返回 None。"""
    result = run([nft, '-j', 'list', 'table', 'inet', table], check=False)
    if result.returncode:
        return None
    try:
        for item in json.loads(result.stdout)['nftables']:
            if 'table' in item:
                return item['table'].get('comment', '')
    except (ValueError, KeyError):
        pass
    return ''


def nft_chain_counters(nft, table, chain):
    """chain 里带 counter 的规则：{规则特征: packets}。特征取集合名（@direct4）或 redirect/reject 关键字。"""
    result = run([nft, '-j', 'list', 'chain', 'inet', table, chain], check=False)
    counters = {}
    if result.returncode:
        return counters
    try:
        items = json.loads(result.stdout)['nftables']
    except (ValueError, KeyError):
        return counters
    for item in items:
        rule = item.get('rule')
        if not rule:
            continue
        key, packets = None, None
        for expr in rule.get('expr', []):
            if 'match' in expr and isinstance(expr['match'].get('right'), str) and expr['match']['right'].startswith('@'):
                key = expr['match']['right']
            if 'counter' in expr and isinstance(expr['counter'], dict):
                packets = expr['counter'].get('packets')
            if 'redirect' in expr:
                key = key or 'redirect'
            if 'reject' in expr:
                key = key or 'reject'
        if packets is not None:
            counters[key or 'rule%s' % rule.get('handle')] = packets
    return counters


def listening(proto, port, any_addr=False):
    """本机有没有 proto/port 的监听。默认只算回环 / 通配地址（引擎自己绑回环时具体地址的监听不冲突）；
    any_addr=True 时任何地址上的都算（引擎要绑 0.0.0.0 时，别的进程绑在具体地址同样会撞）。"""
    if not which('ss'):
        return None
    flag = '-lnt' if proto == 'tcp' else '-lnu'
    out = run(['ss', '-H', flag, 'sport = :%d' % port], check=False).stdout
    pattern = r'(^|\s)\S*:%d(\s|$)' % port if any_addr else r'(^|\s)(127\.0\.0\.1|0\.0\.0\.0|\*|\[::\]|\[::1\]):%d(\s|$)' % port
    return any(re.search(pattern, line) for line in out.splitlines())


def journal_tail(n=30, unit=ENGINE_UNIT):
    return run(['journalctl', '-u', unit, '--no-pager', '-o', 'cat', '-n', str(n)], check=False, timeout=30).stdout.splitlines()


def journal_since(since, unit=ENGINE_UNIT):
    return run(['journalctl', '-u', unit, '--no-pager', '-o', 'cat', '--since', since], check=False, timeout=30).stdout.splitlines()


def journal_cursor(unit=ENGINE_UNIT):
    """当前日志末尾的标记；之后用 journal_after() 只取新增的行（引擎每条连接一行，跑几个月的机器不能整段拉回来数行数）。
    正常拿 journal 游标；拿不到（该单元还没有任何日志）就退回「从此刻起」的秒级时间戳，不用宽松的时间窗，免得把上一项自己的日志算进来。"""
    out = run(['journalctl', '-u', unit, '--no-pager', '-o', 'cat', '-n', '0', '--show-cursor'], check=False, timeout=30).stdout
    m = re.search(r'^-- cursor: (\S+)', out, re.M)
    if m:
        return ('--after-cursor', m.group(1))
    return ('--since', '@%d' % int(time.time()))


def journal_after(marker, unit=ENGINE_UNIT):
    flag, value = marker
    return run(['journalctl', '-u', unit, '--no-pager', '-o', 'cat', flag, value], check=False, timeout=30).stdout.splitlines()


def load_installed_settings():
    try:
        return json.loads(SETTINGS_PATH.read_text())
    except (OSError, ValueError):
        return None


def script_dir():
    return Path(__file__).resolve().parent


def find_engine_binary(sdir):
    """交付包里的 vendor 二进制（校验 sha256），没有就沿用已安装的。返回 (路径, 来源)。"""
    arch = arch_name()
    candidate = sdir / 'vendor' / ('mihomo-linux-%s' % arch)
    sums = sdir / 'vendor' / 'SHA256SUMS'
    if candidate.is_file():
        if sums.is_file():
            expected = None
            for line in sums.read_text().splitlines():
                parts = line.split()
                if len(parts) == 2 and parts[1] == candidate.name:
                    expected = parts[0]
            if expected and sha256_file(candidate) != expected:
                raise Fail('vendor/%s 校验和不匹配，文件可能损坏或被替换' % candidate.name)
            if expected:
                log('引擎二进制校验和正确（%s）' % candidate.name)
        return candidate, 'vendor'
    if ENGINE_BIN.is_file():
        return ENGINE_BIN, 'installed'
    raise Fail('缺少引擎二进制 vendor/mihomo-linux-%s（交付包里应自带；开发机上执行 tools/fetch-mihomo.sh 下载）' % arch)


# ---------------------------------------------------------------- 安装 / 重配置

SETTING_FLAGS = ('proxy', 'proxy_user', 'proxy_password', 'proxy_tls_insecure', 'direct_cidr', 'direct_domain',
                 'dns_mode', 'dns', 'scope', 'flocks_user', 'containers', 'public_udp', 'engine_down', 'check_public_url', 'check_intranet_url')


def flags_given(args):
    return any(getattr(args, name, None) for name in SETTING_FLAGS)


PROXY_ENV_KEYS = ('https_proxy', 'HTTPS_PROXY', 'http_proxy', 'HTTP_PROXY', 'all_proxy', 'ALL_PROXY')


def parse_proxy_from_env_lines(lines):
    """从 KEY=VALUE / export KEY=VALUE 形式的行里找代理地址（/etc/environment、/etc/profile.d/*.sh）。"""
    found = {}
    for raw in lines:
        m = re.match(r'^\s*(?:export\s+)?(https_proxy|HTTPS_PROXY|http_proxy|HTTP_PROXY|all_proxy|ALL_PROXY)\s*=\s*(.+?)\s*$', raw)
        if not m:
            continue
        value = m.group(2).strip()
        if value[:1] in ('"', "'") and value[-1:] == value[:1]:
            value = value[1:-1]
        if value and '$' not in value:
            found[m.group(1)] = value
    for key in PROXY_ENV_KEYS:
        if key in found:
            return found[key]
    return None


def parse_proxy_from_dnf_conf(text):
    """dnf.conf / yum.conf 的 proxy= / proxy_username= / proxy_password=。"""
    conf = {}
    for raw in text.splitlines():
        m = re.match(r'^\s*(proxy|proxy_username|proxy_password)\s*=\s*(.*?)\s*$', raw)
        if m and m.group(2):
            conf[m.group(1)] = m.group(2)
    url = conf.get('proxy')
    if not url or url.lower() in ('_none_', 'none'):
        return None
    if conf.get('proxy_username'):
        parts = urllib.parse.urlsplit(url if '://' in url else 'http://' + url)
        userinfo = urllib.parse.quote(conf['proxy_username'], safe='') + (':' + urllib.parse.quote(conf.get('proxy_password', ''), safe='') if conf.get('proxy_password') else '')
        url = urllib.parse.urlunsplit((parts.scheme, userinfo + '@' + parts.netloc, parts.path, parts.query, parts.fragment))
    return url if '://' in url else 'http://' + url


def detect_system_proxy():
    """机器上已有的代理设置：当前环境变量、/etc/environment、/etc/profile.d/*.sh、dnf / yum 的 proxy=、/etc/wgetrc。返回 (地址, 来源) 或 None。
    内网机器多半为了 dnf / curl 早就配过代理，拿来当默认值，装的时候就不用再敲一遍。"""
    for key in PROXY_ENV_KEYS:
        if os.environ.get(key):
            return os.environ[key], '环境变量 ' + key
    candidates = [Path('/etc/environment')] + sorted(Path('/etc/profile.d').glob('*.sh')) if Path('/etc/profile.d').is_dir() else [Path('/etc/environment')]
    for path in candidates:
        try:
            url = parse_proxy_from_env_lines(path.read_text(errors='replace').splitlines())
        except OSError:
            continue
        if url:
            return url, str(path)
    for path in (Path('/etc/dnf/dnf.conf'), Path('/etc/yum.conf')):
        try:
            url = parse_proxy_from_dnf_conf(path.read_text(errors='replace'))
        except OSError:
            continue
        if url:
            return url, str(path)
    try:   # wgetrc 的 `https_proxy = http://…` 与环境变量同名，同一个解析器就够
        url = parse_proxy_from_env_lines(Path('/etc/wgetrc').read_text(errors='replace').splitlines())
    except OSError:
        url = None
    if url:
        return url, '/etc/wgetrc'
    return None


def ask_proxy_interactively():
    """没给地址、机器上也找不到时，在终端上问三句：地址、账号（可空）、密码。返回键值或 None（不是终端）。"""
    if not sys.stdin.isatty():
        return None
    print('没有找到代理地址，请按提示输入。')
    try:
        for _ in range(3):
            url = input('代理地址（例如 10.0.0.5:3128，直接回车退出）：').strip()
            if not url:
                return None
            try:
                parse_proxy(url)
                break
            except Fail as error:
                print('  地址不对：%s' % error)
        else:
            return None
        values = {'PROXY_URL': url}
        user = input('代理账号（没有就直接回车）：').strip()
        if user:
            values['PROXY_USER'] = user
            values['PROXY_PASSWORD'] = getpass.getpass('代理密码（输入不回显）：')
    except EOFError:        # Ctrl-D：当作不装了，别刷 traceback
        print()
        return None
    return values


def values_from_flags(args, base):
    """命令行参数覆盖到参数文件的键值上。"""
    values = dict(base)
    if args.proxy:
        values['PROXY_URL'] = args.proxy
        # 换了代理地址就不再沿用旧文件里的账号
        values.pop('PROXY_USER', None)
        values.pop('PROXY_PASSWORD', None)
        values.pop('PROXY_TLS_INSECURE', None)
    if args.proxy_user:
        values['PROXY_USER'] = args.proxy_user
    if args.proxy_password:
        values['PROXY_PASSWORD'] = args.proxy_password
    if args.proxy_user and not args.proxy_password:
        url_has_password = False
        try:
            url_has_password = bool(urllib.parse.urlsplit(values.get('PROXY_URL', '')).password)  # secret-guard: allow（变量名含 password，不是凭证）
        except ValueError:
            pass
        if not url_has_password and not values.get('PROXY_PASSWORD'):
            if not sys.stdin.isatty():
                raise Fail('给了 --proxy-user 但没有密码：加 --proxy-password，或把密码写进 PROXY_URL')
            try:
                values['PROXY_PASSWORD'] = getpass.getpass('代理用户 %s 的密码（输入不回显）: ' % args.proxy_user)
            except EOFError:
                raise Fail('没有输入密码：加 --proxy-password，或把密码写进 PROXY_URL')
    if args.proxy_tls_insecure:
        values['PROXY_TLS_INSECURE'] = 'yes'
    if args.direct_cidr:
        values['DIRECT_CIDRS'] = ','.join(args.direct_cidr)
    if args.direct_domain:
        values['DIRECT_DOMAINS'] = ','.join(args.direct_domain)
    if args.dns_mode:
        values['DNS_MODE'] = args.dns_mode
    if args.dns:
        values['DNS_SERVERS'] = ','.join(args.dns)
    if args.scope:
        values['SCOPE'] = args.scope
    if args.flocks_user:
        values['FLOCKS_USER'] = args.flocks_user
    if args.containers:
        values['CONTAINERS'] = args.containers
    if args.public_udp:
        values['PUBLIC_UDP'] = args.public_udp
    if args.engine_down:
        values['ENGINE_DOWN'] = args.engine_down
    if args.check_public_url:
        values['CHECK_PUBLIC_URLS'] = ','.join(args.check_public_url)
    if args.check_intranet_url:
        values['CHECK_INTRANET_URL'] = args.check_intranet_url
    return values


def read_conf_file(path):
    try:
        text = Path(path).read_text()
    except OSError as error:
        raise Fail('读不了参数文件 %s: %s' % (path, error.strerror))
    values, warnings = parse_conf(text, str(path))
    for w in warnings:
        warn(w)
    return values, text


def collect_values(args, sdir, reconfigure):
    """决定本次的参数来自哪里。返回 (键值, 要写进 /etc 的参数文件正文或 None, 来源说明)。"""
    local_conf = sdir / 'egress.conf'
    if reconfigure:
        if not CONF_PATH.is_file():
            raise Fail('还没安装过（没有 %s）；请先 install' % CONF_PATH)
        values, _ = read_conf_file(CONF_PATH)
        return values, None, str(CONF_PATH)
    if getattr(args, 'proxy_positional', None):
        if args.proxy and args.proxy != args.proxy_positional:
            raise Fail('代理地址给了两个：%s 和 --proxy %s' % (args.proxy_positional, args.proxy))
        args.proxy = args.proxy_positional
    if args.config:
        if flags_given(args):
            raise Fail('--config 和 --proxy 等参数二选一')
        values, text = read_conf_file(args.config)
        return values, text, str(args.config)
    if flags_given(args):
        base = read_conf_file(CONF_PATH)[0] if CONF_PATH.is_file() else {}
        values = values_from_flags(args, base)
        return values, render_conf(values), '命令行参数' + ('（其余沿用 %s）' % CONF_PATH if base else '')
    if CONF_PATH.is_file():
        values, _ = read_conf_file(CONF_PATH)
        if local_conf.is_file() and local_conf.resolve() != CONF_PATH and local_conf.read_text() != CONF_PATH.read_text():
            warn('已安装的 %s 与 %s 内容不同，本次使用前者；要用后者请加 --config %s' % (CONF_PATH, local_conf, local_conf))
        return values, None, str(CONF_PATH) + '（已安装，按它重新配置）'
    if local_conf.is_file():
        values, text = read_conf_file(local_conf)
        return values, text, str(local_conf)
    found = detect_system_proxy()
    if found:
        url, source = found
        try:
            shown = proxy_display(parse_proxy(url))
        except Fail as error:
            warn('机器上 %s 里的代理设置 %s 用不了（%s），忽略它' % (source, re.sub(r'^([A-Za-z][\w+.-]*://)?.*@', lambda m: (m.group(1) or '') + '***@', url), error))   # 解析不了没法走 proxy_display，贪心遮到最后一个 @（密码里有 / 或 @、没写 :// 都能遮住）
        else:
            log('没给代理地址，用机器上已有的代理设置：%s（来自 %s）；要用别的加 --proxy' % (shown, source))
            values = {'PROXY_URL': url}
            return values, render_conf(values), '机器已有的代理设置（%s）' % source
    values = ask_proxy_interactively()
    if values:
        return values, render_conf(values), '终端输入'
    raise Fail('不知道代理地址（机器上也没有现成的代理设置）。用法: sudo bash setup-a.sh install --proxy http://<代理IP>:<端口> [--proxy-user 用户 --proxy-password 密码]\n'
               '  或者 cp egress.conf.example egress.conf 填好后 sudo bash setup-a.sh install')


def preflight(dry_run):
    require_root()
    rel = os_release()
    os_id, os_ver = rel.get('ID', '?'), rel.get('VERSION_ID', '?')
    if os_id not in ('centos', 'rhel', 'rocky', 'almalinux', 'ol', 'anolis', 'openEuler', 'kylin', 'uos', 'tencentos', 'alinux'):
        warn('未识别的发行版 %s，脚本按 CentOS / RHEL 9 系编写，继续尝试' % os_id)
    if not str(os_ver).startswith('9'):
        warn('系统版本 %s，目标是 9.x，其他版本没验证过' % os_ver)
    log('系统: %s %s，架构: %s，python %s' % (os_id, os_ver, platform.machine(), platform.python_version()))
    if not Path('/run/systemd/system').is_dir():
        if dry_run:
            warn('systemd 未在运行（dry-run 继续）')
        else:
            raise Fail('systemd 未在运行，本工具依赖 systemd 管理服务')
    nft = which('nft')
    if not nft:
        raise Fail('缺少 nft 命令，请先 dnf install -y nftables')
    if not which('ss'):
        warn('缺少 ss 命令（iproute），端口检查会跳过')
    if which('getenforce') and run(['getenforce'], check=False).stdout.strip() == 'Enforcing':
        log('SELinux Enforcing：无需调整，引擎以 unconfined_service_t 运行')
    return nft


def check_conflicts(nft, s, installed):
    """装之前看现场：残留的表 / 单元、端口占用、会清空 ruleset 的 nftables.service。"""
    if installed is None:
        leftovers = [t for t in (TABLE, ENGINE_TABLE, LEGACY_TABLE) if nft_table_exists(nft, t)]
        leftovers += [u for u in (RULES_UNIT, ENGINE_UNIT) if unit_installed(u)]
        if NFTABLES_DROPIN.exists():
            leftovers.append(str(NFTABLES_DROPIN))
        if leftovers:
            raise Fail('检测到上次安装的残留（%s）但没有 %s：先执行 sudo flocks-egress rollback 清理，再安装' % (', '.join(leftovers), SETTINGS_PATH))
        wanted = [('tcp', s['ports']['redir']), ('tcp', s['ports']['mixed'])]
        if s['dns_hijack']:
            wanted += [('tcp', s['ports']['dns']), ('udp', s['ports']['dns'])]
    else:
        # 重配置：只查这次改了的端口（没改的那些正被自己的引擎占着）
        old = installed.get('ports', {})
        wanted = []
        for proto, key in (('tcp', 'redir'), ('tcp', 'mixed'), ('tcp', 'dns'), ('udp', 'dns')):
            if key == 'dns' and not s['dns_hijack']:
                continue
            if s['ports'][key] != old.get(key):
                wanted.append((proto, s['ports'][key]))
        if installed.get('marker') and nft_table_exists(nft, TABLE):
            marker = nft_table_marker(nft, TABLE)
            if marker and marker != installed['marker']:
                raise Fail('nft 表 inet %s 不是本工具装的（comment 不匹配），未改动。请先人工确认那张表是谁的' % TABLE)
    busy = [(proto, port) for proto, port in wanted if listening(proto, port, any_addr=bool(s.get('containers')))]
    if busy:
        raise Fail('本机 127.0.0.1 的端口已被占用: %s。改 egress.conf 里的 REDIR_PORT / DNS_PORT / MIXED_PORT' % ', '.join('%s/%d' % b for b in busy))
    if systemctl_is('enabled', 'nftables.service') == 'enabled' or systemctl_is('active', 'nftables.service') == 'active':
        warn('机器启用了 nftables.service，它 start / restart / reload 时会清空整个 ruleset；本工具会给它装一个 drop-in（%s）在那之后把表加回来，装完请用 flocks-egress check 确认' % NFTABLES_DROPIN)
    if which('firewall-cmd') and systemctl_is('active', 'firewalld') == 'active':
        log('firewalld 在运行：本工具用独立的 nft 表 inet %s，与 firewalld 互不影响' % TABLE)


def cleanup_dir(path, names):
    """只删自己写进去的文件，再删空目录（不做递归删除）。"""
    for name in names:
        try:
            (Path(path) / name).unlink()
        except FileNotFoundError:
            pass
    try:
        os.rmdir(str(path))
    except OSError:
        warn('临时目录 %s 里有未预期的文件，保留' % path)


def stage_engine_binary(bin_src):
    """把引擎二进制先拷到安装目录旁边的 mihomo.tmp（自检用它、装的时候 rename 成正式文件）。
    包解压在 /tmp 之类挂了 noexec 的目录时，vendor 里的二进制直接跑不了，从这里跑就行。"""
    BIN_DIR.mkdir(parents=True, exist_ok=True)
    staged = ENGINE_BIN.with_name('mihomo.tmp')
    shutil.copy2(str(bin_src), str(staged))
    os.chmod(str(staged), 0o755)
    return staged


def render_and_validate(s, uid, nft, bin_src, tmp, dry_run=False):
    cfg, rules, dns = render_config_yaml(s), render_rules_nft(s, uid), render_engine_nft(s, uid)
    files = {'config.yaml': cfg, 'rules.nft': rules, 'engine.nft': dns}
    for name, content in files.items():
        (tmp / name).write_text(content)
    if bin_src is None:
        warn('没有引擎二进制，跳过引擎自检')
    else:
        home = tmp / 'home'
        home.mkdir()
        log('用引擎自检配置...')
        result = None
        try:
            try:
                result = run([str(bin_src), '-t', '-d', str(home), '-f', str(tmp / 'config.yaml')], check=False, timeout=60)
            except PermissionError:
                if dry_run:
                    warn('%s 所在目录禁止执行程序（挂载了 noexec），dry-run 跳过引擎自检；正式安装会先把它拷到 %s 再自检' % (bin_src, BIN_DIR))
                else:
                    staged = stage_engine_binary(bin_src)
                    log('%s 所在目录禁止执行程序（noexec），改用拷到 %s 的副本自检' % (bin_src.parent, staged))
                    try:
                        result = run([str(staged), '-t', '-d', str(home), '-f', str(tmp / 'config.yaml')], check=False, timeout=60)
                    except PermissionError:
                        raise Fail('%s 也禁止执行程序（noexec），引擎装到这里跑不起来；请让管理员给 /opt 去掉 noexec，或联系技术支持' % BIN_DIR)
        finally:
            cleanup_dir(home, ['cache.db'])
        if result is not None and result.returncode:
            raise Fail('渲染出的 config.yaml 没通过引擎自检:\n' + mask_yaml((result.stdout + result.stderr).strip()[-800:]))
    log('用 nft 自检规则...')
    for name in ('rules.nft', 'engine.nft'):
        result = run([nft, '-c', '-f', str(tmp / name)], check=False)
        if result.returncode:
            raise Fail('渲染出的 %s 没通过 nft -c 检查:\n%s' % (name, result.stderr.strip()[-800:]))
    return files


def start_services(nft, s):
    run(['systemctl', 'daemon-reload'])
    run(['systemctl', 'enable', RULES_UNIT, ENGINE_UNIT], check=False)
    drop_legacy_table(nft)
    # 规则表能 reload 就 reload（一个事务内替换，不会有规则空窗）；引擎重启
    run(['systemctl', 'reload-or-restart', RULES_UNIT], timeout=100)
    run(['systemctl', 'restart', ENGINE_UNIT], timeout=100)
    for _ in range(20):
        if systemctl_is('active', ENGINE_UNIT) == 'active' and listening('tcp', s['ports']['redir']):
            break
        time.sleep(0.5)
    if systemctl_is('active', ENGINE_UNIT) != 'active':
        print('\n'.join(journal_tail(30)), file=sys.stderr)
        raise Fail('引擎服务没起来，日志见上（journalctl -u %s）。恢复原状: sudo flocks-egress rollback' % ENGINE_UNIT)
    if not nft_table_exists(nft, TABLE):
        raise Fail('nft 表 inet %s 没有加载，看 journalctl -u %s' % (TABLE, RULES_UNIT))


def cmd_install(args, reconfigure=False):
    nft = preflight(args.dry_run)
    sdir = script_dir()
    log('%s v%s %s开始' % (TOOL, VERSION, '重配置' if reconfigure else '安装'))
    values, conf_text, source_desc = collect_values(args, sdir, reconfigure)
    log('参数来源: ' + source_desc)
    installed = load_installed_settings()
    s = build_settings(values)
    for note in s['notes']:
        log(note)
    s['marker'] = (installed or {}).get('marker') or 'flocks-egress:' + uuid.uuid4().hex
    check_conflicts(nft, s, installed)

    if args.skip_proxy_test:
        warn('--skip-proxy-test：没有测试代理 %s 是否可用' % proxy_display(s['proxy']))
    else:
        log('测试代理 %s：经它 CONNECT %s:443 ...' % (proxy_display(s['proxy']), s['probe_host']))
        probe = proxy_probe(s['proxy'], s['probe_host'], 443)
        if probe.get('ok'):
            log('代理可用: ' + probe['detail'])
        elif probe.get('stage') == 'target':
            warn('代理接受了连接，但经它访问 %s:443 失败：%s。继续安装；若 %s 在客户网络本就不可达可忽略，否则装完用 flocks-egress check 再看'
                 % (s['probe_host'], probe['detail'], s['probe_host']))
        else:
            raise Fail('代理测试失败：%s\n  代理地址: %s\n  请核对地址、端口、账号密码后重试；确实要跳过测试加 --skip-proxy-test' % (probe.get('detail'), proxy_display(s['proxy'])))

    uid = engine_uid()
    if uid is None:
        if args.dry_run:
            uid = 65534
            log('dry-run：引擎用户 %s 尚未创建，规则里先用占位 uid %d' % (ENGINE_USER, uid))
        else:
            run(['useradd', '--system', '--shell', '/sbin/nologin', '--home-dir', str(STATE), '--no-create-home', ENGINE_USER])
            uid = engine_uid()
            log('已创建系统用户 %s（uid %d）' % (ENGINE_USER, uid))
    s['engine_uid'] = uid

    try:
        bin_src, bin_kind = find_engine_binary(sdir)
    except Fail:
        if not args.dry_run:
            raise
        bin_src, bin_kind = None, 'none'
    if bin_kind == 'installed':
        log('本目录没有 vendor 二进制，沿用已安装的 %s' % ENGINE_BIN)
    tmp = Path(tempfile.mkdtemp(prefix='flocks-egress.'))
    try:
        files = render_and_validate(s, uid, nft, bin_src, tmp, dry_run=args.dry_run)
        if args.dry_run:
            print('\n===== config.yaml =====\n' + mask_yaml(files['config.yaml']))
            print('===== rules.nft =====\n' + files['rules.nft'])
            print('===== engine.nft =====\n' + files['engine.nft'])
            log('--dry-run 结束，未改动系统')
            return 0
    finally:
        cleanup_dir(tmp, ['config.yaml', 'rules.nft', 'engine.nft'])

    # 目录与文件
    for path, mode, owner, group in ((PREFIX, 0o755, 'root', 'root'), (BIN_DIR, 0o755, 'root', 'root'),
                                     (ETC, 0o750, 'root', ENGINE_USER), (STATE, 0o750, ENGINE_USER, ENGINE_USER)):
        path.mkdir(parents=True, exist_ok=True)
        os.chmod(str(path), mode)
        shutil.chown(str(path), user=owner, group=group)
    for child in STATE.iterdir():
        shutil.chown(str(child), user=ENGINE_USER, group=ENGINE_USER)
    if bin_src != ENGINE_BIN:
        tmp_bin = stage_engine_binary(bin_src)   # 自检阶段可能已经拷过一份；再拷一次也就 60 MB，不复用可能是上次失败留下的旧副本
        os.replace(str(tmp_bin), str(ENGINE_BIN))
        log('引擎二进制已安装到 %s' % ENGINE_BIN)
    self_src = Path(__file__).resolve()
    if self_src != INSTALLED_SELF.resolve():
        write_private(INSTALLED_SELF, self_src.read_text(), 0o755)
    for name in ('README.md', 'egress.conf.example', 'THIRD_PARTY_NOTICES.md'):
        src = sdir / name
        if src.is_file() and src.resolve() != (PREFIX / name).resolve():
            write_private(PREFIX / name, src.read_text(), 0o644)
    (PREFIX / 'VERSION').write_text(VERSION + '\n')
    for link in SBIN_LINKS:
        # sudo 的 secure_path 默认不含 /usr/local/sbin，所以 /usr/sbin 也放一份
        if link.is_symlink() or link.exists():
            link.unlink()
        link.parent.mkdir(parents=True, exist_ok=True)
        link.symlink_to(INSTALLED_SELF)
    if conf_text is not None:
        write_private(CONF_PATH, conf_text, 0o600, 'root', 'root')
    else:
        os.chmod(str(CONF_PATH), 0o600)
    write_private(SETTINGS_PATH, json.dumps(settings_public(s), ensure_ascii=False, indent=2) + '\n', 0o600, 'root', 'root')
    write_private(CONFIG_YAML, files['config.yaml'], 0o640, 'root', ENGINE_USER)
    write_private(RULES_NFT, files['rules.nft'], 0o644, 'root', 'root')
    write_private(ENGINE_NFT, files['engine.nft'], 0o644, 'root', 'root')
    unit_rules, unit_engine = render_units(nft)
    write_private(UNITS / RULES_UNIT, unit_rules, 0o644, 'root', 'root')
    write_private(UNITS / ENGINE_UNIT, unit_engine, 0o644, 'root', 'root')
    write_private(NFTABLES_DROPIN, NFTABLES_DROPIN_TEMPLATE, 0o644, 'root', 'root')
    if which('restorecon'):
        run(['restorecon', '-R', str(PREFIX), str(ETC), str(STATE), str(UNITS / RULES_UNIT), str(UNITS / ENGINE_UNIT), str(NFTABLES_DROPIN.parent)], check=False)

    if args.no_start:
        run(['systemctl', 'daemon-reload'])
        run(['systemctl', 'enable', RULES_UNIT, ENGINE_UNIT], check=False)
        log('--no-start：已安装并 enable，未启动。手工启动: systemctl start %s' % ENGINE_UNIT)
        return 0
    start_services(nft, s)
    scope_desc = '整台机器（除引擎用户 %s）' % ENGINE_USER if s['scope'] == 'host' else '用户 %s（uid %d）' % (s['flocks_user'], s['flocks_uid'])
    log('服务已启动。接管范围: %s%s；代理: %s；DNS 模式: %s；引擎不在时: %s' % (
        scope_desc, ('，含容器网桥流量' + ('（上联网桥 %s 除外）' % ', '.join(s['uplink_bridges']) if s.get('uplink_bridges') else '')) if s.get('containers') else '',
        proxy_display(s['proxy']), s['dns_mode_effective'], '公网立刻被拒' if s['engine_down'] == 'reject' else '直连兜底'))
    log('运行验收: flocks-egress check')
    print()
    rc = cmd_check(argparse.Namespace(quick=False, evidence=None))
    if rc:
        warn('安装完成，但验收有 FAIL 项（见上）。修正参数后 sudo flocks-egress reconfigure；要恢复原状 sudo flocks-egress rollback')
        return 1
    log('安装完成：这台机器访问公网已经走 %s，内网照常直连；flocks 照常使用，不用改任何配置。' % proxy_display(s['proxy']))
    log('以后有问题先跑 sudo flocks-egress check，把输出发给技术支持；要撤掉就 sudo flocks-egress rollback')
    return 0


# ---------------------------------------------------------------- 回退 / 状态

def cmd_rollback(_args=None):
    require_root()
    nft = which('nft')
    ts = time.strftime('%Y%m%d%H%M%S')
    for unit in (ENGINE_UNIT, RULES_UNIT):
        if unit_installed(unit):
            run(['systemctl', 'disable', '--now', unit], check=False, timeout=100)
    log('服务已停止并取消自启')
    if nft:
        for table in (ENGINE_TABLE, TABLE, LEGACY_TABLE):
            if nft_table_exists(nft, table):
                run([nft, 'delete', 'table', 'inet', table], check=False)
        log('nft 表已删除，出网流量恢复安装前的走法')
    if engine_uid() is not None and which('pkill'):
        run(['pkill', '-u', ENGINE_USER, '-x', 'mihomo'], check=False)
    for base in (UNITS / ENGINE_UNIT, UNITS / RULES_UNIT, NFTABLES_DROPIN):
        for suffix in ('', '.prev', '.tmp'):
            try:
                base.with_name(base.name + suffix).unlink()
            except FileNotFoundError:
                pass
    try:
        os.rmdir(str(NFTABLES_DROPIN.parent))
    except OSError:
        pass
    run(['systemctl', 'daemon-reload'], check=False)
    for link in SBIN_LINKS:
        if link.is_symlink():
            link.unlink()
    # 程序文件：只删自己放进去的那几个，再删空目录（不做递归删除）
    for name in ('bin/mihomo', 'bin/flocks-egress', 'README.md', 'VERSION', 'egress.conf.example', 'THIRD_PARTY_NOTICES.md'):
        for suffix in ('', '.prev', '.tmp'):
            try:
                (PREFIX / (name + suffix)).unlink()
            except FileNotFoundError:
                pass
    for path in (BIN_DIR, PREFIX):
        try:
            os.rmdir(str(path))
        except OSError:
            if path.exists():
                warn('%s 非空，保留（里面有不是本工具安装的文件）' % path)
    for name in ('cache.db', 'last-check.json'):
        try:
            (STATE / name).unlink()
        except FileNotFoundError:
            pass
    try:
        os.rmdir(str(STATE))
    except OSError:
        if STATE.exists():
            warn('%s 非空，保留' % STATE)
    if ETC.exists():
        archive = ETC.with_name(ETC.name + '.retired-' + ts)
        ETC.rename(archive)
        log('配置目录已改名保留为 %s（含 egress.conf，确认不需要后可自行删除）' % archive)
    if engine_uid() is not None:
        run(['userdel', ENGINE_USER], check=False)
        log('系统用户 %s 已删除' % ENGINE_USER)
    log('已恢复机器原来的出网方式。flocks 本身未受影响；若客户网络封了直连出口，公网请求会开始失败，这是预期行为')
    return 0


def cmd_status(_args=None):
    require_root()
    s = load_installed_settings()
    if s is None:
        raise Fail('未安装（没有 %s）' % SETTINGS_PATH)
    nft = which('nft')
    print('flocks-egress v%s  安装于 %s  代理 %s  范围 %s%s  DNS 模式 %s%s' % (
        s.get('version'), s.get('created'), s.get('proxy_display'),
        '整机' if s.get('scope') == 'host' else '用户 %s' % s.get('flocks_user'), '+容器' if s.get('containers') else '',
        s.get('dns_mode_effective'), '（auto 探测）' if s.get('dns_mode') == 'auto' else ''))
    print('  引擎不在时: %s' % ('公网 TCP 立刻被拒（ENGINE_DOWN=reject）' if s.get('engine_down', 'reject') == 'reject' else '直连兜底（ENGINE_DOWN=direct）'))
    for unit in (RULES_UNIT, ENGINE_UNIT):
        print('  %-30s %s / %s' % (unit, systemctl_is('active', unit), systemctl_is('enabled', unit)))
    if nft:
        policy_table = TABLE if s.get('engine_down', 'reject') == 'reject' else ENGINE_TABLE
        for table in (TABLE, ENGINE_TABLE):
            if nft_table_exists(nft, table):
                counters = nft_chain_counters(nft, table, 'egress_nat') if table == policy_table else {}
                if s.get('dns_hijack') and table == ENGINE_TABLE:
                    counters.update({'dns_' + k: v for k, v in nft_chain_counters(nft, table, 'dns_nat').items()})
                print('  nft 表 inet %-18s 存在  %s' % (table, '  '.join('%s=%s' % kv for kv in sorted(counters.items()))))
            else:
                print('  nft 表 inet %-18s 不存在%s' % (table, '（引擎没在跑？）' if table == ENGINE_TABLE else '（异常）'))
    ports = s.get('ports', {})
    print('  监听 127.0.0.1: tcp/%s=%s tcp/%s=%s%s' % (
        ports.get('redir'), listening('tcp', ports.get('redir', 0)), ports.get('mixed'), listening('tcp', ports.get('mixed', 0)),
        ' udp/%s=%s' % (ports.get('dns'), listening('udp', ports.get('dns', 0))) if s.get('dns_hijack') else ''))
    try:
        last = json.loads(LAST_CHECK.read_text())
        print('  上次验收 %s: PASS %d / FAIL %d / WARN %d' % (last.get('time'), last.get('pass', 0), last.get('fail', 0), last.get('warn', 0)))
    except (OSError, ValueError):
        print('  还没有跑过验收（flocks-egress check）')
    return 0


# ---------------------------------------------------------------- 验收

def pad(text, width):
    """按终端显示宽度补空格（中文算 2 列），让验收输出对齐。"""
    import unicodedata
    cols = sum(2 if unicodedata.east_asian_width(ch) in ('W', 'F') else 1 for ch in text)
    return text + ' ' * max(0, width - cols)


class Report:
    def __init__(self):
        self.items = []

    def _add(self, result, name, detail):
        self.items.append({'name': name, 'result': result, 'detail': detail})
        print('  %-5s %s %s' % (result, pad(name, 28), detail))

    def ok(self, name, detail=''):
        self._add('PASS', name, detail)

    def bad(self, name, detail=''):
        self._add('FAIL', name, detail)

    def warn(self, name, detail=''):
        self._add('WARN', name, detail)

    def count(self, result):
        return sum(1 for item in self.items if item['result'] == result)


def is_fakeip(addr):
    return bool(re.match(r'^198\.1[89]\.', addr or ''))


def resolve4(host, uid):
    out = run(['getent', 'ahostsv4', host], check=False, timeout=20, uid=uid).stdout
    for line in out.splitlines():
        parts = line.split()
        if parts:
            return parts[0]
    return ''


def curl_code(url, uid, timeout=20):
    """返回 (http 状态码或空, 错误摘要)。"""
    result = run(['curl', '-sS', '-m', str(timeout), '-o', '/dev/null', '-w', '%{http_code}', url], check=False, timeout=timeout + 10, uid=uid)
    code = result.stdout.strip()
    return (code if re.match(r'^[1-5][0-9][0-9]$', code) else ''), result.stderr.strip().replace('\n', ' ')[:160]


def cmd_check(args):
    require_root()
    s = load_installed_settings()
    if s is None:
        raise Fail('未安装（没有 %s）' % SETTINGS_PATH)
    nft = which('nft')
    if not nft:
        raise Fail('缺少 nft 命令')
    quick = bool(getattr(args, 'quick', False))
    if not quick and not which('curl'):
        warn('缺少 curl 命令，只做本机状态检查（相当于 --quick）；dnf install -y curl-minimal 后再跑完整验收')
        quick = True
    ports = s['ports']
    hijack = bool(s.get('dns_hijack'))
    # 密码不在 settings.json 里，探测代理要从 egress.conf 重新读
    proxy = None
    try:
        values = dict(DEFAULTS)
        values.update(read_conf_file(CONF_PATH)[0])
        proxy = parse_proxy(values.get('PROXY_URL', ''), values.get('PROXY_USER') or None, values.get('PROXY_PASSWORD') or None,
                            yes_no(values.get('PROXY_TLS_INSECURE'), 'PROXY_TLS_INSECURE'))
    except Fail as error:
        warn('egress.conf 读取失败，跳过代理探测: %s' % error)
    uid_engine = engine_uid()
    if s['scope'] == 'host':
        client_uid, client_desc = 0, '本机(root)'
        other_uid, other_desc = uid_engine, '引擎用户 %s' % ENGINE_USER
        scope_desc = '整台机器（除 %s）' % ENGINE_USER
        expect_scope_line = 'meta skuid %s return' % uid_engine
    else:
        client_uid, client_desc = s['flocks_uid'], '%s 用户' % s['flocks_user']
        other_uid, other_desc = 0, 'root'
        scope_desc = '用户 %s（uid %d）' % (s['flocks_user'], s['flocks_uid'])
        expect_scope_line = 'meta skuid != %d return' % s['flocks_uid']
    report = Report()
    print('flocks-egress 验收  %s  接管范围=%s%s  代理=%s  DNS模式=%s%s  引擎不在时=%s' % (
        now_str(), scope_desc, '+容器' if s.get('containers') else '', s.get('proxy_display'), s['dns_mode_effective'], '（auto 探测）' if s['dns_mode'] == 'auto' else '',
        '公网拒绝' if s.get('engine_down', 'reject') == 'reject' else '直连兜底'))
    print()

    # 1. 两个服务
    for unit, label in ((RULES_UNIT, '规则服务'), (ENGINE_UNIT, '引擎服务')):
        active, enabled = systemctl_is('active', unit), systemctl_is('enabled', unit)
        if active == 'active':
            since = run(['systemctl', 'show', '-p', 'ActiveEnterTimestamp', '--value', unit], check=False).stdout.strip()
            report.ok(label, '%s active 自 %s%s' % (unit, since, '' if enabled == 'enabled' else '（未 enable！systemctl enable %s）' % unit))
        else:
            report.bad(label, '%s 状态 %s：systemctl status %s 查看原因' % (unit, active, unit))
        if enabled != 'enabled':
            report.warn(label + '开机自启', '%s 未 enable' % unit)

    # 2. 监听端口
    wanted = [('tcp', ports['redir'], '透明代理'), ('tcp', ports['mixed'], 'HTTP代理')]
    if hijack:
        wanted += [('tcp', ports['dns'], 'DNS'), ('udp', ports['dns'], 'DNS')]
    missing = ['%s/%d(%s)' % (proto, port, name) for proto, port, name in wanted if listening(proto, port) is False]
    if missing:
        report.bad('监听端口', '缺少: ' + ' '.join(missing))
    else:
        report.ok('监听端口', '%s' % ('透明代理口 tcp/%d%s 在所有接口（只放本机与容器网桥），HTTP 代理口 tcp/%d 只在 127.0.0.1' % (ports['redir'], (' 与 DNS 口 %d' % ports['dns']) if hijack else '', ports['mixed'])
                              if s.get('containers') else '127.0.0.1 上 ' + ' '.join('%s/%d' % (proto, port) for proto, port, _ in wanted)))

    # 3. nft 规则。分流规则在哪张表取决于 ENGINE_DOWN：reject 在常驻表，direct 在随引擎启停的表
    fail_closed = s.get('engine_down', 'reject') == 'reject'
    policy_table = TABLE if fail_closed else ENGINE_TABLE
    if nft_table_exists(nft, TABLE):
        marker = nft_table_marker(nft, TABLE)
        problems = []
        if marker != s.get('marker'):
            problems.append('comment 不是本安装的 marker（被别的东西替换过？）')
        text = run([nft, 'list', 'table', 'inet', policy_table], check=False).stdout
        if expect_scope_line not in text:
            problems.append('范围规则不是「%s」（SCOPE / FLOCKS_USER 改过？重跑 flocks-egress reconfigure）' % expect_scope_line)
        if 'redirect to :%d' % ports['redir'] not in text:
            problems.append('缺少到 :%d 的 redirect' % ports['redir'])
        fallback = not fail_closed and not nft_table_exists(nft, ENGINE_TABLE)
        if problems and fallback:
            report.warn('nft 分流规则', 'ENGINE_DOWN=direct：引擎不在，分流表 inet %s 已随之卸掉，现在是直连兜底状态' % ENGINE_TABLE)
        elif problems:
            report.bad('nft 分流规则', '；'.join(problems))
        else:
            report.ok('nft 分流规则', '表 inet %s 存在，范围: %s%s' % (policy_table, scope_desc, '' if fail_closed else '（ENGINE_DOWN=direct，随引擎启停）'))
    else:
        fallback = False
        report.bad('nft 分流规则', '常驻表 inet %s 不存在（规则服务没起来，或 nftables.service 清空过 ruleset：systemctl restart %s）' % (TABLE, RULES_UNIT))
    if s.get('containers'):
        text = run([nft, 'list', 'table', 'inet', policy_table], check=False).stdout + run([nft, 'list', 'table', 'inet', TABLE], check=False).stdout
        bridges = [line.split(':')[1].strip().split('@')[0] for line in run(['ip', '-o', 'link', 'show', 'type', 'bridge'], check=False).stdout.splitlines() if ':' in line]
        uplinks = s.get('uplink_bridges') or []
        now_uplinks = detect_uplink_bridges()
        if set(now_uplinks) != set(uplinks):
            report.warn('容器流量接管', '网络拓扑变了：安装时的上联网桥是 %s，现在是 %s——重跑 flocks-egress reconfigure，否则新上联网桥上的局域网流量会被当成容器' % (
                ', '.join(uplinks) or '（无）', ', '.join(now_uplinks) or '（无）'))
        elif 'chain egress_prerouting' in text and 'meta iifkind "bridge"' in text and 'chain egress_input' in text:
            report.ok('容器流量接管', '网桥来的公网 TCP 同样转引擎；当前网桥接口: %s%s' % (
                ', '.join(b for b in bridges if b not in uplinks) or '暂无（Docker 起来后自动覆盖）',
                '；上联网桥不算容器: %s' % ', '.join(uplinks) if uplinks else ''))
        else:
            report.bad('容器流量接管', '缺少 egress_prerouting / egress_input 链（引擎没起来？或重跑 flocks-egress reconfigure）')
    if hijack:
        dns_text = run([nft, 'list', 'table', 'inet', ENGINE_TABLE], check=False).stdout
        if 'redirect to :%d' % ports['dns'] in dns_text:
            report.ok('nft DNS 接管规则', '表 inet %s 存在，53 → 127.0.0.1:%d' % (ENGINE_TABLE, ports['dns']))
        else:
            report.bad('nft DNS 接管规则', '表 inet %s 缺失或没有 DNS redirect（引擎服务没起来？）' % ENGINE_TABLE)
    elif not nft_table_exists(nft, ENGINE_TABLE):
        report.warn('nft 引擎表', '随引擎启停的表 inet %s 不存在（引擎服务没起来？）' % ENGINE_TABLE)

    # 4. 代理可用（直接从本机经代理 CONNECT 一次，不经过引擎）
    if proxy is not None and not quick:
        probe = proxy_probe(proxy, s['probe_host'], 443)
        if probe.get('ok'):
            report.ok('代理可用', '%s：%s' % (proxy_display(proxy), probe['detail']))
        elif probe.get('stage') == 'target':
            report.warn('代理可用', '%s 能连、认证通过，但经它访问 %s:443 失败：%s' % (proxy_display(proxy), s['probe_host'], probe['detail']))
        else:
            report.bad('代理可用', '%s：%s' % (proxy_display(proxy), probe.get('detail')))

    # 5. DNS
    probe_host = s['probe_host']
    if hijack:
        # 查 3 次：极偶然会和别的进程同一时刻的 DNS 查询发生连接跟踪五元组碰撞而拿到真实 IP（无害，TCP 仍会进引擎），不能凭一次就判失败
        results = [resolve4(probe_host, client_uid) for _ in range(3)]
        fakes = sum(1 for ip in results if is_fakeip(ip))
        if fakes == 3:
            report.ok('DNS 接管(%s)' % client_desc, '%s → %s（fake-ip，3/3）' % (probe_host, results[0]))
        elif fakes >= 1:
            report.warn('DNS 接管(%s)' % client_desc, '%s 3 次结果 %s，%d/3 为 fake-ip；偶发真实 IP 是连接跟踪碰撞，再跑一次确认' % (probe_host, results, fakes))
        else:
            report.bad('DNS 接管(%s)' % client_desc, '%s 3 次结果 %s，期望 198.18.x.x' % (probe_host, results))
        if other_uid is not None:
            # 直接问 DNS_SERVERS（引擎自己就是这么解析的），不走系统解析器：本机若有 dnsmasq / systemd-resolved 转发器，
            # 转发器那一跳被接管是正常的，不代表引擎会回环
            probe = dns_probe(probe_host, s['dns_servers'], uid=other_uid)
            detail = probe.get('detail') or ''
            if probe.get('ok'):
                report.ok('例外用户 DNS 不接管', '%s 直接问 %s → %s（真实 IP）' % (other_desc, ', '.join(s['dns_servers']), ', '.join(probe['addrs'])))
            elif '接管' in detail:
                report.bad('例外用户 DNS 不接管', '%s 直接问 DNS_SERVERS 拿到的是 fake-ip（会回环！）：%s' % (other_desc, detail))
            elif 'rcode=' in detail:
                # auto 选到 fake-ip 的机器就是这样：内网 DNS 本来就解析不了公网域名，应答是真实 DNS 给的，不是 fake-ip
                report.ok('例外用户 DNS 不接管', '%s 直接问 DNS_SERVERS 得到真实 DNS 的应答、不是 fake-ip（%s；内网 DNS 本就不解析公网域名）' % (other_desc, detail))
            else:
                report.warn('例外用户 DNS 不接管', '%s 直接问 DNS_SERVERS 无响应（%s），判不了' % (other_desc, detail))
    else:
        ip = resolve4(probe_host, client_uid)
        if ip and not is_fakeip(ip):
            report.ok('DNS 不接管(redir-host)', '%s → %s（真实 IP，来自系统解析器）' % (probe_host, ip))
        else:
            report.bad('DNS 不接管(redir-host)', '%s → %r；redir-host 模式要求内网 DNS 能解析公网域名，否则改 DNS_MODE=fake-ip 或 auto' % (probe_host, ip))
    if s.get('check_intranet_url'):
        host, _ = url_host_port(s['check_intranet_url'])
        if not is_ip(host):
            ip = resolve4(host, client_uid)
            if ip and not is_fakeip(ip):
                report.ok('内网域名真实解析', '%s → %s' % (host, ip))
            else:
                report.bad('内网域名真实解析', '%s → %r（应为真实 IP；fake-ip 模式下要把它的后缀加进 DIRECT_DOMAINS）' % (host, ip))

    if quick:
        print('\n--quick：跳过代理探测与公网 / 内网实际访问测试')
    else:
        # 引擎只在 info / debug 级别给每条连接记一行日志；warning 以上就只能看 nft 的 redirect 计数
        log_ok = s.get('log_level', 'info') in ('debug', 'info')

        def redirect_pkts():
            return nft_chain_counters(nft, policy_table, 'egress_nat').get('redirect', 0)

        # 6. 公网走代理：被接管方发请求 → nft redirect 计数要涨；日志级别够的话引擎日志里还要有「--> 主机:端口 ... corp-proxy」
        for url in s['check_public_urls']:
            host, port = url_host_port(url)
            if fallback:
                code, err = curl_code(url, client_uid)
                report.warn('公网走代理', '%s 引擎不在、ENGINE_DOWN=direct 兜底：直连 %s，未经 B' % (url, 'http=%s' % code if code else '失败 ' + err))
                continue
            cursor, before_pkts = journal_cursor(), redirect_pkts()
            code, err = curl_code(url, client_uid)
            time.sleep(1)
            after_pkts = redirect_pkts()
            hit = log_ok and any('--> %s:%d' % (host, port) in line and 'corp-proxy' in line for line in journal_after(cursor))
            if log_ok:
                # 日志能逐条对上这次连接：以日志为准，计数只用来做 detail 里的补充
                if code and hit:
                    report.ok('公网走代理', '%s http=%s，引擎日志: %s:%d → corp-proxy' % (url, code, host, port))
                elif code:
                    report.bad('公网走代理', '%s http=%s 但引擎日志里没有 %s:%d → corp-proxy（流量没被接管？）' % (url, code, host, port))
                elif hit:
                    report.bad('公网走代理', '%s 已进引擎并交给 corp-proxy，但请求失败: %s —— 多半是代理拒绝 / 认证错，看 journalctl -u %s' % (url, err, ENGINE_UNIT))
                else:
                    report.bad('公网走代理', '%s 请求失败且未进引擎: %s' % (url, err))
            elif code and after_pkts > before_pkts:
                # warning 以上没有逐条日志，只能看 redirect 计数有没有涨（别的进程也会推高它，所以只当正向证据）
                report.ok('公网走代理', '%s http=%s，redirect 计数 %d → %d（ENGINE_LOG_LEVEL=%s 没有逐条连接日志）' % (url, code, before_pkts, after_pkts, s.get('log_level')))
            elif code:
                report.bad('公网走代理', '%s http=%s 但 redirect 计数没涨 %d → %d（流量没被接管？）' % (url, code, before_pkts, after_pkts))
            else:
                report.bad('公网走代理', '%s 请求失败: %s（redirect 计数 %d → %d；ENGINE_LOG_LEVEL=%s 下判不了是否进了引擎，可临时改回 info 再 check）' % (url, err, before_pkts, after_pkts, s.get('log_level')))
        # 7. 内网直连：nft 直连计数增加、引擎日志没有它
        if s.get('check_intranet_url'):
            host, port = url_host_port(s['check_intranet_url'])
            cursor = journal_cursor()
            before_pkts = nft_chain_counters(nft, policy_table, 'egress_nat').get('@direct4', 0)
            code, err = curl_code(s['check_intranet_url'], client_uid, timeout=10)
            time.sleep(1)
            after_pkts = nft_chain_counters(nft, policy_table, 'egress_nat').get('@direct4', 0)
            hit = log_ok and any('--> %s:%d' % (host, port) in line for line in journal_after(cursor))
            if fallback:
                if code:
                    report.ok('内网直连', '%s http=%s（引擎不在、direct 兜底中，直连计数不可用）' % (s['check_intranet_url'], code))
                else:
                    report.bad('内网直连', '%s 请求失败: %s（引擎不在、direct 兜底中；内网服务本身可达吗？）' % (s['check_intranet_url'], err))
            elif code and not hit and after_pkts > before_pkts:
                report.ok('内网直连', '%s http=%s，%s直连计数 %d → %d' % (s['check_intranet_url'], code, '未进引擎，' if log_ok else '', before_pkts, after_pkts))
            elif hit:
                report.bad('内网直连', '%s 进了引擎：目标 %s 不在直连网段（补 DIRECT_CIDRS）%s' % (s['check_intranet_url'], host, '，fake-ip 模式下域名后缀补 DIRECT_DOMAINS' if hijack else ''))
            else:
                report.bad('内网直连', '%s http=%r 直连计数 %d → %d（内网服务本身可达吗？%s）' % (s['check_intranet_url'], code, before_pkts, after_pkts, err))
        else:
            report.warn('内网直连', '未设置 CHECK_INTRANET_URL，跳过（egress.conf 里填一个内网 HTTP 地址可自动验证）')
        # 8. 例外用户不被接管：它访问公网不该出现在引擎日志（host 范围下引擎用户若被接管就是回环）
        if other_uid is not None:
            host, port = url_host_port(s['check_public_urls'][0])
            if log_ok:
                cursor = journal_cursor()
                curl_code(s['check_public_urls'][0], other_uid, timeout=6)
                time.sleep(1)
                hit = any('--> %s:%d' % (host, port) in line for line in journal_after(cursor))
                if hit:
                    report.bad('例外用户不被接管', '%s 的连接也进了引擎，规则 uid 匹配有问题' % other_desc)
                else:
                    report.ok('例外用户不被接管', '%s 访问 %s 未进引擎' % (other_desc, host))
            else:
                report.warn('例外用户不被接管', 'ENGINE_LOG_LEVEL=%s 没有逐条连接日志，判不了；要验证就临时改回 info 再 reconfigure' % s.get('log_level'))

    # 9. 引擎近期错误
    errors = [line for line in journal_since('-10 min') if 'level=error' in line]
    if errors:
        report.warn('引擎近 10 分钟有 error', '%d 条：journalctl -u %s -p err --since -10min' % (len(errors), ENGINE_UNIT))
    else:
        report.ok('引擎近 10 分钟无 error', '')

    npass, nfail, nwarn = report.count('PASS'), report.count('FAIL'), report.count('WARN')
    print('\nPASS %d / FAIL %d / WARN %d' % (npass, nfail, nwarn))
    evidence = {'tool': TOOL, 'version': VERSION, 'time': now_str(), 'host': platform.node(), 'arch': platform.machine(),
                'scope': s['scope'], 'dns_mode': s['dns_mode_effective'], 'proxy': s.get('proxy_display'), 'quick': quick,
                'items': report.items, 'pass': npass, 'fail': nfail, 'warn': nwarn}
    payload = json.dumps(evidence, ensure_ascii=False, indent=2) + '\n'
    try:
        STATE.mkdir(parents=True, exist_ok=True)
        LAST_CHECK.write_text(payload)
    except OSError as error:
        warn('写不了 %s: %s' % (LAST_CHECK, error.strerror))
    if getattr(args, 'evidence', None):
        Path(args.evidence).write_text(payload)
        log('验收记录已写入 ' + args.evidence)
    return 1 if nfail else 0


# ---------------------------------------------------------------- 入口

def build_parser():
    parser = argparse.ArgumentParser(prog='flocks-egress', description='机器 A 整机出网接管：内网直连，公网经客户已有的 HTTP / HTTPS / SOCKS5 代理（flocks 与其他程序零配置）',
                                     epilog='首次安装: sudo bash setup-a.sh install --proxy http://<代理IP>:<端口> [--proxy-user 用户 --proxy-password 密码]')
    parser.add_argument('--version', action='version', version='%s %s' % (TOOL, VERSION))
    sub = parser.add_subparsers(dest='action', metavar='命令')
    install = sub.add_parser('install', help='安装或按新参数重装（幂等）')
    install.add_argument('proxy_positional', nargs='?', metavar='URL', help='代理地址，等价于 --proxy，可以只写 IP:端口（按 HTTP 代理）；都不给就用机器上已有的代理设置（环境变量 / /etc/environment / dnf.conf / wgetrc），再没有就在终端上问')
    install.add_argument('--proxy', metavar='URL', help='客户代理地址：http://IP:端口、https://IP:端口、socks5://IP:端口，可内嵌 用户:密码@')
    install.add_argument('--proxy-user', metavar='USER', help='代理用户名（代理要认证时）')
    install.add_argument('--proxy-password', metavar='PASS', help='代理密码；不给且有 --proxy-user 时交互输入')
    install.add_argument('--proxy-tls-insecure', action='store_true', help='https 代理用自签证书时跳过证书校验')
    install.add_argument('--direct-cidr', action='append', metavar='CIDR', help='额外算作内网直连的网段，可重复（RFC1918 等已内置）')
    install.add_argument('--direct-domain', action='append', metavar='DOMAIN', help='内网域名后缀，可重复（只在 fake-ip 模式用到；resolv.conf 的 search 域自动加入）')
    install.add_argument('--dns-mode', choices=['auto', 'redir-host', 'fake-ip'], help='默认 auto：探测内网 DNS 能否解析公网域名')
    install.add_argument('--dns', action='append', metavar='IP', help='内网 DNS 服务器，可重复（默认取 /etc/resolv.conf）')
    install.add_argument('--scope', choices=['host', 'user'], help='host 整机（默认）；user 只接管 --flocks-user 的进程')
    install.add_argument('--flocks-user', metavar='NAME', help='--scope user 时运行 flocks 的系统用户')
    install.add_argument('--containers', choices=['auto', 'yes', 'no'], help='是否连本机 Docker / podman 容器（bridge 网络）的流量一起接管；auto = host 范围接管、user 范围不接管')
    install.add_argument('--public-udp', choices=['reject', 'allow'], help='发往公网的 UDP：reject 立即拒绝（默认）/ allow 放行直连')
    install.add_argument('--engine-down', choices=['direct', 'reject'], help='引擎进程不在时：direct 分流规则随引擎卸掉、直连兜底（默认）/ reject 公网 TCP 立刻被拒、不漏成直连')
    install.add_argument('--check-public-url', action='append', metavar='URL', help='验收用的公网地址，可重复（默认 https://www.baidu.com/）')
    install.add_argument('--check-intranet-url', metavar='URL', help='验收用的一个内网 HTTP 地址（证明直连）')
    install.add_argument('--config', metavar='FILE', help='用参数文件（egress.conf）代替上面的参数')
    install.add_argument('--dry-run', action='store_true', help='只渲染并自检配置，不改动系统')
    install.add_argument('--no-start', action='store_true', help='安装并 enable，但不启动')
    install.add_argument('--skip-proxy-test', action='store_true', help='不在安装前测试代理是否可用')
    reconf = sub.add_parser('reconfigure', help='改了 /etc/flocks-egress/egress.conf 之后重新渲染并重启')
    reconf.add_argument('--dry-run', action='store_true')
    reconf.add_argument('--skip-proxy-test', action='store_true')
    check = sub.add_parser('check', help='验收：服务、规则、代理、DNS、公网走代理、内网直连')
    check.add_argument('--quick', action='store_true', help='只查本机状态，不发网络请求')
    check.add_argument('--evidence', metavar='FILE', help='把验收结果另存为 JSON')
    sub.add_parser('status', help='当前状态一览')
    sub.add_parser('rollback', help='停服务、删规则、删程序文件，配置目录改名保留；机器恢复原出网方式')
    sub.add_parser('uninstall', help='同 rollback')
    sub.add_parser('_rules-reload')   # flocks-egress-rules.service 的 ExecReload 用，不对人
    return parser


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)
    if not args.action:
        parser.print_help()
        return 2
    for name in SETTING_FLAGS + ('config', 'dry_run', 'no_start', 'skip_proxy_test', 'quick', 'evidence'):
        if not hasattr(args, name):
            setattr(args, name, None)
    try:
        if args.action == 'install':
            return cmd_install(args)
        if args.action == 'reconfigure':
            return cmd_install(args, reconfigure=True)
        return {'check': cmd_check, 'status': cmd_status, 'rollback': cmd_rollback, 'uninstall': cmd_rollback,
                '_rules-reload': cmd_rules_reload}[args.action](args)
    except Fail as error:
        print('[flocks-egress] 错误: ' + str(error), file=sys.stderr)
        return 1
    except (KeyboardInterrupt, EOFError):
        print('\n[flocks-egress] 已中断', file=sys.stderr)
        return 130


if __name__ == '__main__':
    sys.exit(main())
