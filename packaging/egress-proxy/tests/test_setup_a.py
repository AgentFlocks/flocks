"""setup-a.py 里不碰系统的那部分：参数文件解析、代理 URL 解析、网段归一化、三份渲染结果。
在开发机上跑：  <flocks venv>/bin/python -m pytest packaging/egress-proxy/tests/test_setup_a.py -q
（要改系统的 install / check / rollback 由 tests/e2e-centos9.sh 在容器里测）"""
import importlib.util
import pathlib

import pytest

HERE = pathlib.Path(__file__).resolve().parent
SPEC = importlib.util.spec_from_file_location('setup_a', HERE.parent / 'setup-a.py')
sa = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(sa)

try:
    import yaml
except ImportError:  # pragma: no cover
    yaml = None


def settings(**overrides):
    """不探测网络、不查用户的一组可渲染设置。"""
    values = {'PROXY_URL': 'http://10.0.0.5:3128', 'DNS_MODE': 'redir-host', 'DNS_SERVERS': '10.0.0.53',
              'CHECK_INTRANET_URL': 'http://web.corp.local:8080/'}
    values.update(overrides)
    s = sa.build_settings(values, probe_dns=False)
    s['marker'] = 'flocks-egress:test'
    return s


# ---------------------------------------------------------------- 参数文件

def test_parse_conf_quotes_comments_and_unknown_keys():
    text = ('# 注释\r\n'
            'PROXY_URL="http://u:p%40ss@10.0.0.5:3128"   # 引号里可以有 # 和空格\n'  # secret-guard: allow（测试夹具）
            "PROXY_PASSWORD='a\"b #c'\n"
            'DNS_MODE=fake-ip # 尾注释\n'
            'FOO_BAR="x"\n'
            'PATH=/evil\n'
            'garbage line\n')
    values, warnings = sa.parse_conf(text)
    assert values == {'PROXY_URL': 'http://u:p%40ss@10.0.0.5:3128', 'PROXY_PASSWORD': 'a"b #c', 'DNS_MODE': 'fake-ip'}  # secret-guard: allow（测试夹具）
    assert any('FOO_BAR' in w for w in warnings) and any('PATH' in w for w in warnings) and any('无法识别' in w for w in warnings)


def test_parse_conf_rejects_unbalanced_quote():
    with pytest.raises(sa.Fail):
        sa.parse_conf('PROXY_URL="http://x:1\n')


def test_render_conf_roundtrip_and_quote_choice():
    values = {'PROXY_URL': 'http://10.0.0.5:3128', 'PROXY_USER': 'u', 'PROXY_PASSWORD': 'p"w#1', 'DNS_MODE': 'auto', 'SCOPE': 'host'}
    text = sa.render_conf(values)
    assert "PROXY_PASSWORD='p\"w#1'" in text
    assert 'DNS_MODE' not in text and 'SCOPE' not in text          # 默认值不写
    back, warnings = sa.parse_conf(text)
    assert back == {'PROXY_URL': 'http://10.0.0.5:3128', 'PROXY_USER': 'u', 'PROXY_PASSWORD': 'p"w#1'} and not warnings
    with pytest.raises(sa.Fail):
        sa.render_conf({'PROXY_URL': 'http://x:1', 'PROXY_PASSWORD': 'both\'"quotes'})


# ---------------------------------------------------------------- 代理 URL

@pytest.mark.parametrize('url, scheme, ptype, tls, host, port, user, password', [
    ('http://10.0.0.5:3128', 'http', 'http', False, '10.0.0.5', 3128, '', ''),
    ('HTTPS://proxy.corp.local:8443/', 'https', 'http', True, 'proxy.corp.local', 8443, '', ''),
    ('socks5h://[fd00::5]:1080', 'socks5', 'socks5', False, 'fd00::5', 1080, '', ''),
    ('socks://u:p@10.0.0.5:1080', 'socks5', 'socks5', False, '10.0.0.5', 1080, 'u', 'p'),
    ('http://corp%5Cuser:p%40ss%3Aw%2Fd%25+x@10.0.0.5:3128', 'http', 'http', False, '10.0.0.5', 3128, 'corp\\user', 'p@ss:w/d%+x'),  # secret-guard: allow（测试夹具）
    ('http://u:p@ss@10.0.0.5:3128', 'http', 'http', False, '10.0.0.5', 3128, 'u', 'p@ss'),   # 没编码的 @ 也认（按最后一个 @ 切）
])
def test_parse_proxy_ok(url, scheme, ptype, tls, host, port, user, password):
    p = sa.parse_proxy(url)
    assert (p['scheme'], p['type'], p['tls'], p['host'], p['port'], p['user'], p['password']) == (scheme, ptype, tls, host, port, user, password)


@pytest.mark.parametrize('url, hint', [
    ('', '必填'), ('ftp://x:1', '不支持的协议'), ('http://10.0.0.5', '必须写端口'), ('http://10.0.0.5:99999', '端口不合法'),
    ('http://10.0.0.5:3128/path', '不能带路径'), ('http://bad_host:3128', '不是合法的域名或 IP'), ('http://u:p%zz@10.0.0.5:3128', '两位十六进制'),
    ('http://:secret@10.0.0.5:3128', '没有用户名'), ('socks5:10.0.0.5:1080', '端口不合法'),
])
def test_parse_proxy_errors(url, hint):
    with pytest.raises(sa.Fail) as info:
        sa.parse_proxy(url)
    assert hint in str(info.value)


def test_parse_proxy_explicit_credentials_override_and_masking():
    p = sa.parse_proxy('https://old:old@10.0.0.5:3128', user='new', password='n:w', tls_insecure=True)
    assert (p['user'], p['password'], p['tls_insecure']) == ('new', 'n:w', True)
    assert sa.proxy_display(p) == 'https://new:***@10.0.0.5:3128'
    assert sa.proxy_display(sa.parse_proxy('http://[::1]:3128')) == 'http://[::1]:3128'
    assert sa.parse_proxy('http://10.0.0.5:3128', tls_insecure=True)['tls_insecure'] is False   # 非 https 不存这个开关


# ---------------------------------------------------------------- 网段

def test_normalize_cidrs():
    v4, v6 = sa.normalize_cidrs(['10.1.2.3/8', '192.168.1.7', 'fd00::1', '172.16.0.0/12', '172.16.0.0/12'], 'X')
    assert v4 == ['10.0.0.0/8', '172.16.0.0/12', '192.168.1.7/32'] and v6 == ['fd00::1/128']
    for bad in (['0.0.0.0/0'], ['::/0'], ['10.0.0.256'], ['abc']):
        with pytest.raises(sa.Fail):
            sa.normalize_cidrs(bad, 'X')


def test_url_host_port():
    assert sa.url_host_port('https://www.baidu.com/') == ('www.baidu.com', 443)
    assert sa.url_host_port('http://web.corp.local:8080/x') == ('web.corp.local', 8080)
    with pytest.raises(sa.Fail):
        sa.url_host_port('ftp://x/')


# ---------------------------------------------------------------- 设置与渲染

def test_build_settings_redir_host_defaults():
    s = settings(DIRECT_CIDRS='203.0.113.128/25, 100.100.1.1', PROXY_URL='http://198.51.100.9:3128')
    assert s['dns_hijack'] is False and s['dns_mode_effective'] == 'redir-host'
    assert '203.0.113.128/25' in s['direct4'] and '100.100.1.1/32' in s['direct4']
    assert '198.51.100.9/32' in s['direct4']            # 代理自己的地址算直连
    assert set(sa.BUILTIN4) <= set(s['direct4']) and set(sa.BUILTIN6) <= set(s['direct6'])
    assert s['ports'] == {'redir': 7892, 'dns': 1053, 'mixed': 7890}
    assert s['probe_host'] == 'www.baidu.com'
    public = sa.settings_public(dict(s, proxy=sa.parse_proxy('http://u:secret@10.0.0.5:3128')))
    assert public['proxy']['password'] == '' and public['proxy']['has_password'] is True and 'secret' not in repr(public)


def test_build_settings_errors():
    for overrides, hint in ((dict(SCOPE='nope'), 'SCOPE'), (dict(DNS_MODE='x'), 'DNS_MODE'), (dict(PUBLIC_UDP='maybe'), 'PUBLIC_UDP'),
                            (dict(REDIR_PORT='7890'), '不能重复'), (dict(DNS_SERVERS='10.0.0.300'), 'DNS_SERVERS'),
                            (dict(DIRECT_DOMAINS='bad_domain'), 'DIRECT_DOMAINS'), (dict(SCOPE='user'), 'FLOCKS_USER'),
                            (dict(SCOPE='user', FLOCKS_USER=sa.ENGINE_USER), '回环'), (dict(ENGINE_LOG_LEVEL='loud'), 'ENGINE_LOG_LEVEL')):
        with pytest.raises(sa.Fail) as info:
            settings(**overrides)
        assert hint in str(info.value), overrides


def test_render_config_yaml_fake_ip_with_auth_https():
    s = settings(PROXY_URL='https://proxy.corp.local:8443', PROXY_USER='corp', PROXY_PASSWORD='p"w', PROXY_TLS_INSECURE='yes',
                 DNS_MODE='fake-ip', DIRECT_DOMAINS='corp.local,Intra.Example')
    text = sa.render_config_yaml(s)
    assert 'enhanced-mode: fake-ip' in text and 'listen: 0.0.0.0:1053' in text      # host 范围默认连容器一起接管，DNS 监听所有接口
    assert '"+.corp.local"' in text and '"+.intra.example"' in text and '"proxy.corp.local"' in text   # 代理主机名不能给 fake-ip
    assert 'type: http' in text and 'tls: true' in text and 'skip-cert-verify: true' in text
    assert 'username: "corp"' in text and 'password: "p\\"w"' in text
    assert 'DOMAIN-SUFFIX,corp.local,DIRECT' in text and text.rstrip().endswith('- MATCH,corp-proxy')
    assert 'password: <隐藏>' in sa.mask_yaml(text) and 'p\\"w' not in sa.mask_yaml(text)
    if yaml:
        doc = yaml.safe_load(text)
        assert doc['dns']['nameserver'] == ['10.0.0.53'] and doc['proxies'][0]['password'] == 'p"w'
        assert doc['rules'][-1] == 'MATCH,corp-proxy'
        assert [(l['type'], l['port'], l['listen']) for l in doc['listeners']] == [('redir', 7892, '0.0.0.0'), ('mixed', 7890, '127.0.0.1')]


def test_render_config_yaml_redir_host_socks5():
    s = settings(PROXY_URL='socks5://10.0.0.5:1080')
    text = sa.render_config_yaml(s)
    assert 'dns:\n  enable: false' in text and 'enhanced-mode' not in text and 'DOMAIN-SUFFIX' not in text
    assert 'type: socks5' in text and 'tls:' not in text and 'store-fake-ip: false' in text
    assert 'ports: [443, 465, 993, 8443]' in text          # 邮件渠道的 SMTPS / IMAPS 也嗅探 SNI，代理侧看到的是域名
    if yaml:
        assert yaml.safe_load(text)['dns'] == {'enable': False}


def test_render_rules_nft_host_scope():
    s = settings(PUBLIC_UDP='reject', DIRECT_CIDRS='203.0.113.0/24', ENGINE_DOWN='reject')
    text = sa.render_rules_nft(s, 989)
    assert 'table inet flocks_egress\ndelete table inet flocks_egress\ntable inet flocks_egress {' in text
    assert 'comment "flocks-egress:test"' in text
    assert 'meta skuid 989 return' in text and 'skuid !=' not in text
    assert '203.0.113.0/24' in text and '10.0.0.5/32' in text and 'fc00::/7' in text
    assert 'redirect to :7892' in text and 'meta l4proto udp counter reject' in text
    assert 'redirect to :1053' not in text          # DNS 接管不在常驻表里（容器那条 dnat 到内网 DNS 不算接管）
    allow = sa.render_rules_nft(settings(PUBLIC_UDP='allow', ENGINE_DOWN='reject'), 989)
    assert 'meta l4proto udp counter reject' not in allow and 'PUBLIC_UDP=allow' in allow


def test_render_engine_nft_modes():
    hijack = sa.render_engine_nft(settings(DNS_MODE='fake-ip', ENGINE_DOWN='reject'), 989)
    assert 'redirect to :1053' in hijack and 'priority dstnat - 10' in hijack and 'meta skuid 989 return' in hijack
    empty = sa.render_engine_nft(settings(ENGINE_DOWN='reject'), 989)
    assert 'chain' not in empty and 'table inet flocks_egress_engine {' in empty      # reject + redir-host：空表


def test_render_units_use_absolute_nft_and_link_both_services():
    rules, engine = sa.render_units('/usr/sbin/nft')
    assert 'ExecStart=/usr/sbin/nft -f /etc/flocks-egress/rules.nft' in rules and 'After=local-fs.target systemd-sysctl.service nftables.service' in rules
    assert 'ExecReload=/usr/bin/python3 /opt/flocks-egress/bin/flocks-egress _rules-reload' in rules
    assert 'ExecStop=-/usr/sbin/nft delete table inet flocks_egress\n' in rules and 'ExecStop=-/usr/sbin/nft delete table inet flocks_egress_engine' in rules
    assert 'try-reload-or-restart flocks-egress-rules.service' in sa.NFTABLES_DROPIN_TEMPLATE and '--no-block' in sa.NFTABLES_DROPIN_TEMPLATE
    assert 'ExecStartPre=+/usr/sbin/nft -f /etc/flocks-egress/engine.nft' in engine and 'PartOf=flocks-egress-rules.service' in engine
    assert 'User=flocks-egress' in engine and 'AmbientCapabilities=CAP_NET_ADMIN' in engine and '{nft}' not in engine + rules


def test_version_file_matches_script():
    assert (HERE.parent / 'VERSION').read_text().strip() == sa.VERSION


def test_local_addresses_join_direct_set_except_special_ranges(monkeypatch):
    monkeypatch.setattr(sa, 'local_addresses', lambda: {'127.0.0.1', '::1', 'fe80::1', '169.254.10.3', '198.18.0.9', '203.0.113.7', '10.9.8.7', 'fd00::7', 'not-an-ip'})
    s = settings()
    assert s['local_ips'] == ['10.9.8.7', '203.0.113.7', 'fd00::7']      # 回环 / 链路本地 / fake-ip 段 / 非法值都不算
    assert '203.0.113.7/32' in s['direct4'] and 'fd00::7/128' in s['direct6']
    text = sa.render_rules_nft(dict(s, engine_down='reject'), 989) + sa.render_engine_nft(s, 989)   # 集合在放分流的那张表里
    assert '203.0.113.7/32' in text


def test_containers_default_on_for_host_scope_and_rendering():
    s = settings(DNS_MODE='fake-ip', ENGINE_DOWN='reject')      # 用 reject 看常驻表里的完整分流链
    assert s['containers'] is True and s['uplink_bridges'] == []
    cfg = sa.render_config_yaml(s)
    # 透明代理口开到所有接口，普通 HTTP/SOCKS 口永远只在回环；不再用全局 mixed-port / redir-port / allow-lan
    assert 'type: redir' in cfg and 'listen: 0.0.0.0' in cfg and 'type: mixed' in cfg and 'listen: 127.0.0.1' in cfg
    assert 'allow-lan: false' in cfg and 'mixed-port' not in cfg and 'redir-port' not in cfg and 'lan-allowed-ips' not in cfg
    assert 'listen: 0.0.0.0:1053' in cfg
    rules = sa.render_rules_nft(s, 989)
    for needle in ('chain egress_input', 'meta iifkind "bridge" return', 'tcp dport { 7892, 1053 } counter drop',
                   'chain egress_prerouting', 'meta iifkind != "bridge" return', 'fib daddr type local return',
                   'chain egress_forward', 'meta l4proto udp counter reject'):
        assert needle in rules, needle
    assert '7890' not in rules.split('chain egress_input')[1].split('}')[0]       # 7890 只在回环，input 链不用管它
    assert rules.count('redirect to :7892') == 2          # output 链一处、prerouting 一处
    assert 'dnat ip to' not in rules                      # fake-ip 下容器 DNS 走 dns 表，不做 dnat
    dns = sa.render_engine_nft(s, 989)
    assert 'chain dns_prerouting' in dns and dns.count('redirect to :1053') == 4
    if yaml:
        doc = yaml.safe_load(cfg)
        assert doc['listeners'][0] == {'name': 'redir-in', 'type': 'redir', 'port': 7892, 'listen': '0.0.0.0'}
        assert doc['listeners'][1]['listen'] == '127.0.0.1'


def test_containers_redir_host_sends_public_container_dns_to_intranet_dns():
    s = settings(DNS_SERVERS='fd00::53, 10.0.0.53', ENGINE_DOWN='reject')
    rules = sa.render_rules_nft(s, 989)
    pre = rules.split('chain egress_prerouting')[1].split('chain egress_forward')[0]
    assert 'udp dport 53 counter dnat ip to 10.0.0.53' in pre and 'tcp dport 53 counter dnat ip to 10.0.0.53' in pre
    # dnat 必须排在内网放行之后：只改写发往公网 DNS 的查询，容器自建 DNS / 别的内网 DNS 不动
    assert pre.index('ip daddr @direct4 counter return') < pre.index('dnat ip to') < pre.index('redirect to :7892')
    assert 'chain' not in sa.render_engine_nft(s, 989)       # redir-host 的 dns 表仍是空表


def test_containers_uplink_bridges_are_excluded(monkeypatch):
    monkeypatch.setattr(sa, 'detect_uplink_bridges', lambda: ['br0', 'virbr0'])
    s = settings(DNS_MODE='fake-ip', ENGINE_DOWN='reject')
    assert s['uplink_bridges'] == ['br0', 'virbr0']
    rules = sa.render_rules_nft(s, 989)
    assert 'meta iifkind "bridge" iifname != { "br0", "virbr0" } return' in rules       # input 链：上联网桥不算容器网桥
    assert rules.count('iifname { "br0", "virbr0" } return') == 2                       # prerouting / forward 各一处
    assert 'iifname { "br0", "virbr0" } return' in sa.render_engine_nft(s, 989)
    assert 'iifname' not in sa.render_rules_nft(settings(DNS_MODE='fake-ip', CONTAINERS='no', ENGINE_DOWN='reject'), 989).replace('iifname "lo"', '')


def test_containers_off_keeps_loopback_only():
    s = settings(DNS_MODE='fake-ip', CONTAINERS='no', ENGINE_DOWN='reject')
    assert s['containers'] is False and s['uplink_bridges'] == []
    cfg = sa.render_config_yaml(s)
    assert cfg.count('    listen: 127.0.0.1\n') == 2 and 'listen: 0.0.0.0' not in cfg and 'listen: 127.0.0.1:1053' in cfg
    rules = sa.render_rules_nft(s, 989)
    assert 'iifkind' not in rules and 'egress_input' not in rules and rules.count('redirect to :7892') == 1
    assert 'dns_prerouting' not in sa.render_engine_nft(s, 989)
    with pytest.raises(sa.Fail):
        settings(CONTAINERS='maybe')


def test_engine_down_default_is_direct():
    assert settings()['engine_down'] == 'direct'
    assert sa.DEFAULTS['ENGINE_DOWN'] == 'direct'


def test_engine_down_direct_moves_policy_into_engine_table():
    s = settings(DNS_MODE='fake-ip')          # 默认就是 direct
    assert s['engine_down'] == 'direct'
    rules, engine = sa.render_rules_nft(s, 989), sa.render_engine_nft(s, 989)
    # 常驻表只剩引擎端口门禁；分流（含容器 prerouting/forward）、集合、DNS 接管全在随引擎启停的表里
    assert 'redirect to :7892' not in rules and 'chain egress_nat' not in rules and 'set direct4' not in rules
    assert 'chain egress_input' in rules and 'chain egress_prerouting' not in rules
    # fake-ip + direct：常驻表留一条占位地址的快速拒绝，引擎不在时程序手里的 198.18.x.x 不会挂到超时
    assert 'chain egress_fakeip_guard' in rules and 'chain egress_fakeip_forward' in rules and rules.count('ip daddr 198.18.0.0/15 meta l4proto tcp counter reject with tcp reset') == 2
    assert 'egress_fakeip' not in sa.render_rules_nft(settings(), 989)     # redir-host 没有占位地址，不需要
    assert engine.count('redirect to :7892') == 2 and 'chain egress_guard' in engine and 'set direct4' in engine
    assert 'chain egress_prerouting' in engine and 'chain egress_forward' in engine and 'chain dns_nat' in engine and 'chain dns_prerouting' in engine
    # reject：分流留在常驻表
    r2 = sa.render_rules_nft(settings(DNS_MODE='fake-ip', ENGINE_DOWN='reject'), 989)
    assert r2.count('redirect to :7892') == 2 and 'ENGINE_DOWN=reject' in r2
    with pytest.raises(sa.Fail):
        settings(ENGINE_DOWN='maybe')


def test_engine_down_direct_redir_host_without_containers():
    s = settings(CONTAINERS='no')
    rules, engine = sa.render_rules_nft(s, 989), sa.render_engine_nft(s, 989)
    assert 'chain' not in rules                      # 常驻表连门禁都没有（不接管容器），只是个占位的表
    assert engine.count('redirect to :7892') == 1 and 'dns_nat' not in engine


def test_system_proxy_detection_parsers(monkeypatch):
    assert sa.parse_proxy_from_env_lines(['# x', 'export http_proxy="http://10.0.0.5:3128"', 'https_proxy=http://10.0.0.6:3128']) == 'http://10.0.0.6:3128'   # https 优先
    assert sa.parse_proxy_from_env_lines(['http_proxy=$MY_PROXY']) is None            # 引用变量的不认
    assert sa.parse_proxy_from_dnf_conf('[main]\nproxy=http://10.0.0.5:3128\nproxy_username=u@corp\nproxy_password=p:w\n') == 'http://u%40corp:p%3Aw@10.0.0.5:3128'   # secret-guard: allow（单测假账号）
    assert sa.parse_proxy_from_dnf_conf('[main]\nproxy = 10.0.0.5:3128\n') == 'http://10.0.0.5:3128'
    assert sa.parse_proxy_from_dnf_conf('[main]\nproxy=_none_\n') is None and sa.parse_proxy_from_dnf_conf('[main]\n') is None
    for key in sa.PROXY_ENV_KEYS:
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv('HTTP_PROXY', 'http://10.0.0.9:8080')
    assert sa.detect_system_proxy() == ('http://10.0.0.9:8080', '环境变量 HTTP_PROXY')
    assert sa.parse_proxy(sa.parse_proxy_from_dnf_conf('proxy=http://10.0.0.5:3128\nproxy_username=u@corp\nproxy_password=p:w'))['password'] == 'p:w'   # secret-guard: allow（单测假账号）


def test_bare_host_port_means_http_proxy():
    """小白只写 IP:端口 也行，按 HTTP 代理算；协议写错仍然报错。"""
    assert sa.parse_proxy('10.0.0.5:3128') == sa.parse_proxy('http://10.0.0.5:3128')
    assert sa.parse_proxy('proxy.corp.local:3128')['host'] == 'proxy.corp.local'
    assert sa.parse_proxy('u:p@10.0.0.5:3128')['user'] == 'u'
    with pytest.raises(sa.Fail, match='必须写端口'):
        sa.parse_proxy('10.0.0.5')
    with pytest.raises(sa.Fail, match='不支持的协议'):
        sa.parse_proxy('ftp://10.0.0.5:21')


def test_wgetrc_lines_use_env_parser():
    assert sa.parse_proxy_from_env_lines(['# wgetrc', 'https_proxy = http://10.0.0.7:3128/', 'use_proxy = on']) == 'http://10.0.0.7:3128/'
    assert sa.parse_proxy('http://10.0.0.7:3128/')['port'] == 3128


def test_interactive_prompt(monkeypatch):
    monkeypatch.setattr(sa.sys.stdin, 'isatty', lambda: False)
    assert sa.ask_proxy_interactively() is None                        # 不是终端（管道 / 脚本）不问，交给上层报错

    monkeypatch.setattr(sa.sys.stdin, 'isatty', lambda: True)
    answers = iter(['ftp://x:1', '10.0.0.5:3128', 'corpuser'])         # 第一次写错协议，重问一次
    monkeypatch.setattr('builtins.input', lambda prompt='': next(answers))
    monkeypatch.setattr(sa.getpass, 'getpass', lambda prompt='': 'p@ss')   # secret-guard: allow（单测假账号）
    assert sa.ask_proxy_interactively() == {'PROXY_URL': '10.0.0.5:3128', 'PROXY_USER': 'corpuser', 'PROXY_PASSWORD': 'p@ss'}   # secret-guard: allow（单测假密码）

    answers = iter(['10.0.0.5:3128', ''])                              # 账号回车 = 不要认证，也不问密码
    monkeypatch.setattr('builtins.input', lambda prompt='': next(answers))
    monkeypatch.setattr(sa.getpass, 'getpass', lambda prompt='': pytest.fail('不该问密码'))   # secret-guard: allow（假 getpass，不是凭证）
    assert sa.ask_proxy_interactively() == {'PROXY_URL': '10.0.0.5:3128'}

    answers = iter([''])                                               # 地址直接回车 = 退出
    monkeypatch.setattr('builtins.input', lambda prompt='': next(answers))
    assert sa.ask_proxy_interactively() is None

    answers = iter(['bad', 'bad', 'bad'])                              # 连错三次放弃
    monkeypatch.setattr('builtins.input', lambda prompt='': next(answers))
    assert sa.ask_proxy_interactively() is None

    def ctrl_d(prompt=''):
        raise EOFError
    monkeypatch.setattr('builtins.input', ctrl_d)                      # Ctrl-D = 不装了，不能刷 traceback
    assert sa.ask_proxy_interactively() is None
    answers = iter(['10.0.0.5:3128', 'corpuser'])
    monkeypatch.setattr('builtins.input', lambda prompt='': next(answers))
    monkeypatch.setattr(sa.getpass, 'getpass', ctrl_d)                 # 密码那一问按 Ctrl-D 也一样   # secret-guard: allow（假 getpass，不是凭证）
    assert sa.ask_proxy_interactively() is None


def test_unusable_system_proxy_is_skipped_with_source(monkeypatch, tmp_path, capsys):
    """机器上现成的代理设置解析不了（比如 socks4://）：说明来源、忽略，继续走终端提问 / 报错，不能带着一条没头没尾的错误退出。"""
    monkeypatch.setattr(sa, 'CONF_PATH', tmp_path / 'none' / 'egress.conf')
    monkeypatch.setattr(sa, 'detect_system_proxy', lambda: ('socks4://u:secret@10.0.0.5:1080', '/etc/environment'))   # secret-guard: allow（单测假账号）
    monkeypatch.setattr(sa, 'ask_proxy_interactively', lambda: None)
    args = sa.build_parser().parse_args(['install', '--dry-run'])
    with pytest.raises(sa.Fail, match='不知道代理地址'):
        sa.collect_values(args, tmp_path, reconfigure=False)
    err = capsys.readouterr().err
    assert '/etc/environment' in err and 'socks4://***@10.0.0.5:1080' in err and '不支持的协议' in err and 'secret' not in err   # 账号密码不进日志

    monkeypatch.setattr(sa, 'detect_system_proxy', lambda: ('u:se/cret@proxy_srv:3128', '/etc/wgetrc'))   # secret-guard: allow（单测假账号）：没写 ://、密码带 /、主机带下划线
    with pytest.raises(sa.Fail, match='不知道代理地址'):
        sa.collect_values(args, tmp_path, reconfigure=False)
    err = capsys.readouterr().err
    assert '***@proxy_srv:3128' in err and 'se/cret' not in err

    monkeypatch.setattr(sa, 'ask_proxy_interactively', lambda: {'PROXY_URL': '10.0.0.6:3128'})
    values, text, source = sa.collect_values(args, tmp_path, reconfigure=False)
    assert values == {'PROXY_URL': '10.0.0.6:3128'} and source == '终端输入' and 'PROXY_URL="10.0.0.6:3128"' in text

