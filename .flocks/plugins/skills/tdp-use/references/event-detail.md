# 事件详情查看与分析

列表页通常只展示摘要。无论是威胁事件页还是日志调查页，若要确认攻击是否成功、读取原始报文或分析 payload，必须继续进入详情。

## 操作路径

**第一步：点击事件 / 告警表格行进入详情**

```bash
flocks browser -c '
attach_tab("<TARGET_ID>")
js("document.querySelectorAll(\"tbody tr\")[0]?.click()")
wait(1.0)
print(page_info())
print(js("document.body.innerText.slice(0, 2000)"))
'
```

**第二步：若页面有“查看详情”链接，继续进入告警明细列表**

```bash
flocks browser -c '
attach_tab("<TARGET_ID>")
js("""
Array.from(document.querySelectorAll("a"))
  .find(a => a.textContent?.includes("查看详情"))
  ?.click()
""")
wait(1.0)
print(page_info())
'
```

**第三步：滚动到“威胁明细”表格，点击具体告警行查看原始报文 / PCAP**

```bash
flocks browser -c '
attach_tab("<TARGET_ID>")
js("window.scrollTo(0, document.body.scrollHeight)")
wait(0.8)
js("document.querySelectorAll(\"tbody tr\")[0]?.click()")
wait(1.0)
print(js("document.body.innerText.slice(0, 2500)"))
'
```

## 告警详情分析要点

| 检查项 | 说明 |
|--------|------|
| **攻击结果** | success/failed，确认攻击是否成功 |
| **攻击者/受害者IP** | 确定攻击方向 |
| **威胁名称** | 具体的攻击手法 |
| **URL 路径** | 是否有异常路径（如 `/config/mac/list`） |
| **User-Agent** | 是否伪装 |
| **PCAP/原始报文** | 查看攻击 payload 特征 |

> 加密请求体只需分析其存在性和大小，不需要完整输出。

## 重要原则

- 列表摘要不足以支撑结论，调查和溯源场景中必须进入详情。
- 一个事件包含多条告警时，要抽查关键告警明细，不能只看第一条。
- 数据不足时继续深挖，不基于摘要推断结论。
