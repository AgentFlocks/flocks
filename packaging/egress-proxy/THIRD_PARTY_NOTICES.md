# 第三方组件说明

本包里随附的第三方软件：

| 组件 | 版本 | 许可证 | 来源 | 用途 |
|---|---|---|---|---|
| mihomo（原 Clash.Meta） | v1.19.31 | GPL-3.0 | https://github.com/MetaCubeX/mihomo/releases/tag/v1.19.31 | `vendor/mihomo-linux-amd64`，机器 A 上的透明代理引擎，由 `setup-a.py` 装到 `/opt/flocks-egress/bin/mihomo`，作为独立进程运行 |

mihomo 以未修改的官方发行二进制形式随附，以独立进程方式被 systemd 拉起，与 flocks 之间只有网络层交互，不构成 flocks 的衍生作品。按 GPL-3.0 要求，其源代码可在上述地址获取（对应 tag `v1.19.31`）；需要离线源码副本时，请联系交付方索取。

`setup-a.py` 只用 Python 标准库。nftables、systemd、python3 为 CentOS / RHEL 9 系统自带组件，本包不随附。
