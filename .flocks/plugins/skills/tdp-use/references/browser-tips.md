# TDP 浏览器操作详细技巧

## 1. 导航操作

### 推荐方式：直接拼接 URL 跳转

TDP 是 SPA 应用，直接跳转 URL 比点击菜单更稳定：

```bash
flocks browser -c '
tid = new_tab("https://<tdp-domain>/dashboard", activate=True)
wait_for_load()
print(tid)
print(page_info())
print(js("document.body.innerText.slice(0, 1200)"))
'
```

### 继续使用已有 tab

```bash
flocks browser -c '
attach_tab("<TARGET_ID>")
print(page_info())
'
```

---

## 2. 页面滚动

> ⚠️ 必须使用 JavaScript 滚动；滚动后再重新读取页面状态。

```bash
flocks browser -c '
attach_tab("<TARGET_ID>")
print(js("document.body.scrollHeight"))
js("window.scrollTo(0, document.body.scrollHeight)")
wait(1.0)
print(page_info())
print(js("document.body.innerText.slice(0, 1600)"))
'
```

分步滚动：

```bash
flocks browser -c '
attach_tab("<TARGET_ID>")
js("window.scrollBy(0, 1000)")
wait(0.8)
print(js("document.body.innerText.slice(0, 1600)"))
'
```

滚动到指定元素：

```bash
flocks browser -c '
attach_tab("<TARGET_ID>")
js("""
const el = document.evaluate(
  '//*[contains(text(),"目标文字")]',
  document,
  null,
  XPathResult.FIRST_ORDERED_NODE_TYPE,
  null
).singleNodeValue;
if (el) el.scrollIntoView({block: "center"});
""")
wait(0.5)
print(page_info())
'
```

**判断是否需要继续滚动的依据**：
- 页面有大量空白
- 看到“查看更多”“加载更多”等链接
- 页面布局明显不完整（如被截断的表格）
- 还没有看到预期的数据列表

---

## 3. 动态元素点击

TDP 使用 React/Vue 构建，大量元素是 `<div>/<span>` + onClick。推荐顺序是：先 `page_info()`，再 `js(...)` 观察和点击；不要再依赖旧的 ref 编号。

### 方法一：XPath 文本定位（适合文本唯一的元素）

```bash
flocks browser -c '
attach_tab("<TARGET_ID>")
js("""
const el = document.evaluate(
  '//*[contains(text(),"Redis")]',
  document,
  null,
  XPathResult.FIRST_ORDERED_NODE_TYPE,
  null
).singleNodeValue;
if (el) el.click();
""")
wait(0.8)
print(page_info())
'
```

### 方法二：表格行点击（告警列表、事件列表）

```bash
flocks browser -c '
attach_tab("<TARGET_ID>")
js("document.querySelectorAll(\"tbody tr\")[0]?.click()")
wait(1.0)
print(js("document.body.innerText.slice(0, 2000)"))
'
```

点击第 N 条数据行：

```bash
flocks browser -c '
attach_tab("<TARGET_ID>")
row_idx = 2
print(js(f"document.querySelectorAll(\"tbody tr\")[{row_idx}]?.click(); true"))
wait(1.0)
print(page_info())
'
```

### 方法三：遍历所有元素（适合折叠面板、Tab 切换、动态按钮）

```bash
flocks browser -c '
attach_tab("<TARGET_ID>")
js("""
for (const el of document.querySelectorAll("*")) {
  const txt = el.textContent?.trim();
  if (txt?.includes("目标文本")) {
    const btn = el.querySelector("button, svg, [role=button]") || el;
    btn.click();
    break;
  }
}
""")
wait(1.0)
print(js("document.body.innerText.slice(0, 2000)"))
'
```

### 方法四：查找链接（“查看详情”等文字链接）

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

关闭弹窗：

```bash
flocks browser -c '
attach_tab("<TARGET_ID>")
js("document.querySelector(\".ant-modal-close\")?.click()")
wait(0.5)
print(page_info())
'
```

### 定位策略选择表

| 场景 | 推荐方式 |
|------|----------|
| 文本唯一的元素 | XPath `contains(text(),'关键词')` |
| 多个相似元素，需精确匹配 | `textContent` + 过滤逻辑 |
| 父元素含多个子按钮 | 先定位父，再 `querySelector('button, svg, [role=button]')` |
| class 名动态变化 | 用标签名、文本、DOM 结构，不依赖 class |
| 表格数据行 | `querySelectorAll('tbody tr')[index].click()` |
| 文字链接 | `Array.from(querySelectorAll('a')).find(...)` |

---

## 4. 调试技巧（定位不到元素时）

```bash
flocks browser -c '
attach_tab("<TARGET_ID>")
print(js("""
Array.from(document.querySelectorAll("*"))
  .filter(el => {
    const txt = el.textContent?.trim();
    return txt && txt.includes("目标文本") && txt.length < 50;
  })
  .slice(0, 20)
  .map(el => ({
    tag: el.tagName,
    className: el.className,
    text: el.textContent?.trim(),
    html: el.outerHTML.slice(0, 200),
  }))
"""))
'
```

查看页面所有链接和按钮：

```bash
flocks browser -c '
attach_tab("<TARGET_ID>")
print(js("""
Array.from(document.querySelectorAll("a, button"))
  .slice(0, 100)
  .map(el => ({
    tag: el.tagName,
    text: el.textContent?.trim()?.slice(0, 30),
    href: el.href || "",
  }))
"""))
'
```

检查表格行数：

```bash
flocks browser -c '
attach_tab("<TARGET_ID>")
print(js("""
(() => {
  const rows = document.querySelectorAll("tbody tr");
  return {
    rowCount: rows.length,
    firstRowHtml: rows[0]?.outerHTML?.slice(0, 200) || "",
  };
})()
"""))
'
```

---

## 5. 截图保存

```bash
flocks browser -c '
attach_tab("<TARGET_ID>")
capture_screenshot("/tmp/tdp-shot.png", max_dim=1800)
print("/tmp/tdp-shot.png")
'
```

完整页面截图：

```bash
flocks browser -c '
attach_tab("<TARGET_ID>")
capture_screenshot("/tmp/tdp-shot-full.png", full=True, max_dim=2200)
print("/tmp/tdp-shot-full.png")
'
```

---

## 6. 注意事项

- **等待时间**：页面交互后用 `wait(0.5 ~ 2.0)`，然后再读取 `page_info()` 或 `js(...)`。
- **文本匹配精度**：匹配词要足够精确，避免误触相似元素；`textContent` 包含子元素文本，注意嵌套层级。
- **获取页面内容**：优先用 `js("document.body.innerText.slice(...)")` 获取纯文本；需要结构化信息时，直接在页面内组装 JSON 返回。
- **每次页面变化后都重新观察**：点击、滚动、切 tab、弹窗打开/关闭后，之前读取到的 DOM 状态都可能失效。
