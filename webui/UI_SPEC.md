# Flocks WebUI 界面规范（v1.4，已停用）

> 2026-10-10 起界面以 flocksdev 的 dev 分支为准：沿用 dev 的写法（Tailwind 默认色板类加 index.css 的 `.dark` 兼容表、lucide-react 图标），不再按本文件。`scripts/ui_spec_check.py` 已从 CI 移除。`src/styles/tokens.css` 里的语义变量只给 v2 新增、dev 没有的界面和 Hub 插件页面用，取值已对齐 dev 的配色。下面的内容只作历史参考。

所有新界面都按这份规范写，已有页面逐步对齐。设计源是 `webui/design/flocks-ui-spec-v1.4.html`（浅色 / 深色并排的组件样板，直接用浏览器打开），这里只写开发要遵守的那部分。token 落在代码里：`src/styles/tokens.css`（CSS 变量）、`tailwind.config.js`（语义类）、`src/styles/index.css`（全局基线与 `ui-*` 组件类），`scripts/ui_spec_check.py` 负责拦截新增的不合规写法。

## 目录

- [一句话版](#一句话版)
- [Token 怎么用](#token-怎么用)
- [颜色](#颜色)
- [字体与字号](#字体与字号)
- [间距、圆角、尺寸](#间距圆角尺寸)
- [图标](#图标)
- [组件](#组件)
- [页面布局](#页面布局)
- [状态与反馈](#状态与反馈)
- [深色模式](#深色模式)
- [键盘与可达性](#键盘与可达性)
- [提交前检查](#提交前检查)
- [Pro overlay 的额外约束](#pro-overlay-的额外约束)

## 一句话版

颜色只用语义 token（`bg-surface` / `text-fg-secondary` / `border-line` / `bg-action` / `text-success` …），不写 `gray-500`、`red-600`、十六进制；字号只用 `text-sm`（14/22）、`text-xs`（12/18）和三档标题；控件高度 36（紧凑 32 / 大号 40），圆角 8（卡片和弹层 12，标签 4）；图标用 `@tabler/icons-react`，16 / 20 / 24 / 32 四档；主操作是品牌红，AI 相关是紫，情报 / 接口 / 连接是青；状态永远"文字 + 底色 + 图标"一起给，禁用、空态、加载、异常都要说明原因并给恢复入口。

## Token 怎么用

`tokens.css` 在 `:root` 定义浅色值，在 `.dark` 覆盖深色值，变量名和设计稿一致（`--bg-canvas`、`--text-primary`、`--status-successBg` …）。Tailwind 把它们暴露成语义类，切主题时自动跟着变，不需要写 `dark:` 前缀：

| 用途 | 类 | 变量 |
|---|---|---|
| 页面底、面板、次级块、悬停 / 选中 | `bg-canvas` `bg-surface` `bg-subtle` `bg-hover` `bg-selected` | `--bg-*` |
| 正文、次要、弱化、占位、链接、禁用文字 | `text-fg` `text-fg-secondary` `text-fg-muted` `text-fg-placeholder` `text-fg-link` `text-fg-disabled` | `--text-*` `--disabled-text` |
| 分隔线、控件边框 | `border-line` `border-line-control` | `--border-subtle` `--border-control` |
| 主操作及 hover / pressed、按钮文字、危险操作 | `bg-action` `bg-action-hover` `bg-action-pressed` `text-action-fg` `bg-action-danger` | `--action-*` |
| 功能状态（文字 / 底色成对） | `text-success` + `bg-success-soft`，同理 `warning` `danger` `info` | `--status-*` |
| 分类强调 | `text-ai` + `bg-ai-soft`，`text-data` + `bg-data-soft`，`text-brand` + `bg-brand-soft` | `--accent-*` |
| 焦点环、禁用底、开关 | `outline-focus` `bg-disabled` `bg-switch-on` | `--focus-ring` `--disabled-bg` `--switch-*` |
| 圆角 | `rounded-tag`(4) `rounded-segment`(6) `rounded-control`(8) `rounded-card`(12) `rounded-overlay`(12) `rounded-pill` | `--radius-*` |
| 尺寸 | `h-control-sm`(32) `h-control`(36) `h-control-lg`(40) `min-h-touch`(48) `w-sidebar`(208) `h-topbar`(44) | `--control-*` `--layout-*` |
| 弹层宽度、阴影、动效 | `max-w-modal`(560) `max-w-drawer`(480) `max-w-onboarding`(768) `shadow-overlay` `duration-fast`(120) `duration-overlay`(240) | `--overlay-*` `--shadow-overlay` `--motion-*` |

语义类不支持透明度修饰（`bg-surface/50` 不会生效）。规范里透明只用于浮层阴影，需要半透明遮罩时用 `bg-black/40`。

历史代码不用逐行改就已经落在规范色上，靠的是两层兼容（都在 `tailwind.config.js` 和 `index.css` 末尾的"历史控件对齐"段里，新代码别依赖它们）：

- 色板并入：`gray` / `zinc` → slate（规范浅色中性色），`rose` → red，`yellow` / `orange` → amber，`emerald` / `lime` → green，`purple` / `fuchsia` → violet，`indigo` / `sky` → blue，`teal` → cyan；这些色系的 `*-100` 落到 `*-50`，老徽章 `bg-green-100 text-green-800` 就是规范的成功色对。`rounded-md` → 8，`rounded-2xl` / `3xl` → 12，`shadow-sm` / `shadow` → 无（卡片只靠边框），`shadow-md` 及以上 → 浮层阴影。深色由 `.dark` 覆盖表接管。
- 控件对齐：输入类控件的 `border-gray-300` 一律显示为 `border.control`，聚焦只留品牌红边框；深色填充的"黑按钮"显示为次要按钮，绿 / 蓝 / 紫 / 琥珀填充的按钮显示为品牌红主操作，带 `aria-selected` / `aria-pressed` / `aria-current` 的显示为分段选中态；`disabled` 按钮用禁用底 / 字；`role="switch"` 的轨道开关开启为语义绿。所以给分段控件和分页当前项加对 aria 属性，样式就对了。

## 颜色

浅色 / 深色的 36 个色值（按设计稿）：

| Token | 浅色 | 深色 | 用途 |
|---|---|---|---|
| bg.canvas | #F8FAFC | #252C35 | 页面底 |
| bg.surface | #FFFFFF | #303842 | 面板、卡片、输入框 |
| bg.subtle | #F1F5F9 | #39434F | 次级块、标签底、表头 |
| bg.hover / bg.selected | #E2E8F0 | #46515E | 悬停、选中 |
| text.primary | #0F172A | #D7DEE8 | 正文 |
| text.secondary | #475569 | #BCC6D0 | 说明、次要信息 |
| text.muted | #64748B | #9AA7B4 | 弱化、时间戳 |
| text.placeholder | #94A3B8 | #9AA7B4 | 占位 |
| text.link | #1D4ED8 | #BFDBFE | 链接 |
| border.subtle | #E2E8F0 | #4A5563 | 分隔线、卡片边 |
| border.control | #64748B | #9AA7B4 | 输入框、次要按钮边框 |
| action.primary / hover / pressed | #DC2626 / #B91C1C / #991B1B | 同左 | 主操作（品牌红），文字 #FFFFFF |
| action.danger | #B91C1C | 同左 | 删除等危险操作，文字 #FFFFFF |
| focus.ring | #1D4ED8 | #93C5FD | 键盘焦点环 |
| status.success / Bg | #166534 / #F0FDF4 | #86EFAC / #14332A | 已完成、连接成功 |
| status.warning / Bg | #92400E / #FFFBEB | #FCD34D / #392D13 | 待确认、需要关注 |
| status.danger / Bg | #B91C1C / #FEF2F2 | #FCA5A5 / #3B2028 | 失败、异常 |
| status.info / Bg | #1E40AF / #EFF6FF | #93C5FD / #1E3A5F | 提示、运行信息 |
| accent.ai / Bg | #6D28D9 / #F5F3FF | #C4B5FD / #352B4B | 模型、Rex、智能体 |
| accent.data / Bg | #0E7490 / #ECFEFF | #67E8F9 / #173C46 | 情报、接口、连接 |
| accent.brand / Bg | #B91C1C / #FEF2F2 | #FCA5A5 / #3B2028 | 品牌标识、选中对号 |
| disabled.bg / text | #F1F5F9 / #64748B | #303842 / #778393 | 禁用 |
| switch.on | #047857 | #34D399 | 开关开启 |
| shadow.overlay | 0 12px 32px rgba(15,23,42,.16) | 0 12px 32px rgba(0,0,0,.4) | 只用于浮层 |

几条硬规则：类别色（紫 / 青）用来区分资源类型，不表达风险等级；状态色不单独出现，要搭图标和文字；文字可读性不能靠透明度，深色模式下不要用 `opacity` 压暗文字，改用 `text-fg-muted`。

## 字体与字号

字体栈 `Inter, system-ui, -apple-system, "PingFang SC", "Microsoft YaHei", sans-serif`，日志和代码用 `font-mono`（`ui-monospace, SFMono-Regular, Consolas`）。中文交给系统回退，不设字距。

| 层级 | 字号 / 行高 / 字重 | 类 |
|---|---|---|
| 页面标题 | 24 / 32 · 700 | `text-title-page` 或 `text-2xl font-bold` |
| 区块标题 | 20 / 28 · 600 | `text-title-section` 或 `text-xl font-semibold` |
| 卡片、弹窗标题 | 16 / 24 · 600 | `text-title-card` 或 `text-base font-semibold` |
| 正文 | 14 / 22 · 400 | `text-sm`（默认） |
| 辅助说明 | 12 / 18 · 400 | `text-xs` |

`text-sm` / `text-xs` 的行高已经改成 22 / 18，不要再用 `leading-*` 手调；指标数字用 28 / 36 加 `tabular-nums`。不要用 `text-[13px]` 这类任意值。

## 间距、圆角、尺寸

间距 4px 步长：图标与文字 6–8，同组元素 8–12，卡片内边距 16，常规区块 24，大分组 32。Tailwind 默认刻度就是 4 的倍数，`gap-2` / `p-4` / `space-y-6` 直接用。

圆角：标签 4（`rounded-tag`）、分段内项 6（`rounded-segment`）、按钮 / 输入框 / 下拉 8（`rounded-control`，等于 `rounded-lg`）、卡片和弹层 12（`rounded-card` / `rounded-overlay`，等于 `rounded-xl`）、胶囊 `rounded-pill`。不要用 `rounded-2xl` 及以上。

控件高度：默认 36（`h-control`），紧凑 32，大号 40；触控目标至少 44，移动端 48（`min-h-touch`）。边框 1px。侧边栏 208 宽，顶栏 44 高。

动效 120 / 180 / 240ms，缓动 `cubic-bezier(0.2, 0, 0, 1)`（`transition-colors` 默认已经是 180ms 这条曲线）。

## 图标

统一用 `@tabler/icons-react`（Tabler Outline，24 网格、stroke 2，MIT，随包内嵌，不走 CDN），四档尺寸 16 内联 / 20 控件 / 24 导航 / 32 特征，用 `className="h-4 w-4"` 之类控制，不改 stroke。同一操作区不混别的图标集。

规范指定的语义图标：

| 含义 | 组件 | 含义 | 组件 |
|---|---|---|---|
| 工作区 | `IconLayoutDashboard` | 搜索 | `IconSearch` |
| 智能体 | `IconRobot` | 筛选 | `IconFilter` |
| AI 对话 | `IconMessageCircle` | 排序 | `IconArrowsSort` |
| 威胁情报 | `IconShieldCheck` | 文件与报告 | `IconFileText` |
| 数据资源 | `IconDatabase` | 成功 | `IconCircleCheck` |
| 模型配置 | `IconCpu` | 警告 | `IconAlertTriangle` |
| 服务连接 | `IconPlug` | 错误 | `IconCircleX` |
| 访问凭据 | `IconKey` | 系统设置 | `IconSettings` |

纯图标按钮必须有 `aria-label`（或 `title`），命中区不小于 44px。图标颜色跟随文字 token，不单独上色。

## 组件

`index.css` 里的 `ui-*` 类就是下面这些规格，新界面直接用；写自定义组件时按同样的尺寸。

按钮（`.ui-btn`）：高 36、内边距 6×12、圆角 8、字号 14 字重 500、图标与文字间距 8。主要操作 `.ui-btn-primary`（品牌红底白字，hover #B91C1C，pressed #991B1B），次要操作默认样式（白底、`border-control` 边、hover `bg-hover`），危险 `.ui-btn-danger`，幽灵 `.ui-btn-ghost`，纯图标 `.ui-btn-icon`（36×36）。禁用态用 `disabled` 属性：底 `disabled.bg`、字 `disabled.text`、`cursor-not-allowed`，不要只降透明度。加载中保留按钮尺寸，显示"保存中…"并禁用。一个操作区只有一个主要按钮。

输入框（`.ui-input` / `.ui-textarea` / `.ui-select`）：高 36、圆角 8、`border-control` 边、内边距 0 12、字号 14；多行最小高 84、行高 22；聚焦是单层品牌红边框（全局已处理，不要再加 `focus:ring`）；错误态边框 `status.danger` 并在下方 4px 处给 12px 红字说明，保留用户输入；只读 / 不可用要有标识文字。字段标签 `.ui-label` 14/500，必填加 `.ui-label-required`（红色 *），说明 `.ui-hint` 12px。

下拉选择：触发器同输入框（36，紧凑 32 / 大号 40）；菜单圆角 12、离触发器 8、最多 280 高可滚动、宽度不小于触发器；选项至少 36 高，正文 14/20、说明 12/18；选中项只在文字前显示品牌红对号，悬停和键盘定位用 `bg-hover`；支持搜索、分组、禁用项和"未找到"空态；键盘 Enter / Space 展开，方向键移动，Enter 选择，Esc 收起并把焦点还给触发器。多选显示已选数量并提供清空。

Tab 与分段：分类 Tab（`.ui-tabs` / `.ui-tab`）高 44，选中项底部 2px 品牌红、文字 600；同一对象的状态视图用分段控件（`.ui-segmented`）：外框 `bg-subtle` 圆角 8 内边距 4，内项 32 高圆角 6，选中 `bg-selected` 600；都支持方向键、Home、End。列表里的分类 Tab 留在列表上下文里，切换分类重置类型和页码但保留搜索词。

开关（`.ui-switch`）：轨道 32×18，滑块 12，文字间距 8；开启是语义绿滑块居右，关闭是中性灰居左；禁用保留当前值不响应；旁边一定有文字说明状态，不只靠颜色。

列表与表格（`.ui-table`）：主信息 14，辅助 12；表头 `bg-subtle` 12px；行 hover `bg-hover`；筛选条：搜索 + 状态 + 类型 + 排序，条件间隔 12，搜索和下拉高 36；有已选条件时显示条件标签和"重置筛选"；筛选后回第一页；空结果给"没有匹配的资源 / 尝试调整关键词"和清除筛选按钮；分页显示"1 / N"。

卡片（`.ui-card`）：内边距 16、间距 16、圆角 12、`border-line` 边；只有可操作的卡才加 hover（`.ui-card-interactive`）和 focus；可选卡选中时（`.ui-card-selected`）边框 `focus.ring` + 内描边 + `bg-selected`，并保留文字标志（"已选择"）。指标卡数字 28/36 `tabular-nums`，下方一行 12px 趋势说明；连接卡显示"3 / 3 · 全部连接正常"这类状态；任务卡带 6px 进度条。

AI 对话窗口：正文 14/22，消息间距 24，气泡内边距 12×16、圆角 12、`bg-subtle` + `border-line`；Rex 消息带头像和时间，工具调用区展示名称、状态、耗时、摘要；回答状态分正常完成 / 生成中 / 模型不可用，错误用 `bg-danger-soft` + `text-danger` 的错误框；输入区最小高 84，Enter 发送、Shift+Enter 换行，发送按钮是主操作。

新人引导弹窗：宽 768、内边距 24、顶边 2px 品牌红强调；三步（配置模型 → 配置情报 → Rex 对话）用步骤条，已验证实线、未验证虚线；校验失败在字段下方说明原因并保留输入、提供重试；API Key 默认掩码。

弹窗与抽屉（`.ui-modal` / `.ui-drawer`）：弹窗宽 560、圆角 12；抽屉宽 480、贴右全高；标题 20/28，内容内边距 24，标题与正文间距 16，内容与操作区 24，底部按钮间距 8、右对齐、取消在左主要在右；内容长时只中间滚动，标题和操作区固定；支持 Esc、关闭按钮、点遮罩关闭，关闭后焦点回到触发按钮；窄屏改全宽。

## 页面布局

侧边栏 208 宽、顶栏 44 高；页头（标题 24/32 + 一句说明 + 右侧主操作）→ 16 → 指标卡（桌面四列，窄屏两列）→ 16 → 主辅分栏（桌面 8:4，窄屏单列）。内容区左右留 24，窄屏 16；375px 视口不允许横向滚动，固定底栏不能挡内容。

## 状态与反馈

成功 / 警告 / 错误 / 信息四种反馈都用"文字色 + 底色 + 图标"三件套（`.ui-badge-*`），toast 同理。空态（`.ui-empty`）：图标 + 一句"为什么是空的" + 一个动作按钮（"完成首次分析后，报告将展示在这里 / 开始分析"）。加载：占位保留布局高度，不让内容跳动。异常：保留已有内容和资源名称，给"重试"入口，不整块消失。禁用：说明原因（"尚未配置设备"），不只降透明度。用户取消的操作静默处理。

## 深色模式

主题由 `html.dark` 切换（`ThemeToggle`），token 在 `.dark` 里覆盖，用语义类的代码不需要任何 `dark:` 前缀。深色下品牌红、AI 紫、数据青换成浅一档（#FCA5A5 / #C4B5FD / #67E8F9）以保证对比度，底色换深色版；控件边框用 #9AA7B4。老代码里 `dark:bg-zinc-800` 这类写法由 `index.css` 的 `.dark` 覆盖表映射到规范色，能跑，但新代码别再写。

## 键盘与可达性

所有可交互元素都能 Tab 到；键盘焦点环 2px `focus.ring` 外扩 2px（全局 `:focus-visible` 已处理，别用 `outline-none` 去掉）；输入框聚焦是品牌红边框。弹层要做焦点约束和关闭后焦点返回。触控目标最小 44px。文字对比度不低于 4.5:1（设计稿的 56 组配色已校验，新增配色自己跑一遍）。状态不只靠颜色，禁用、加载、错误都配文字。

## 提交前检查

```sh
cd webui
npm run lint && npm run build          # 类型 + 构建
python3 ../scripts/ui_spec_check.py    # 规范检查：裸色板 / 十六进制 / lucide / 任意字号 / 大圆角
python3 ../scripts/ui_spec_check.py --update            # 只在有意减少存量时更新基线（基线上升会被拒绝）
```

`ui_spec_check.py` 按文件记录存量（`scripts/ui_spec_baseline.json`），新文件出现任何命中、老文件命中数增加都会失败，CI 里是阻断步骤。改老页面时顺手把命中数往下降，基线只允许减不允许加。

界面改动验收时截图浅色 + 深色、桌面 1280 + 移动 375 各一张，逐元素对照本规范；带截图的测试报告是界面类任务完成的门槛。

## Pro overlay 的额外约束

`flockspro/webui-overlay` 和开源源码一起编译（`scripts/build-webui.sh`），构建时 `tailwind.config.js` 会把 `WEBUI_EXTENSION_REGISTRY` 所在目录一起扫，所以 overlay 里可以直接用语义类和 `ui-*` 类。overlay 自带的 CSS（`.flockspro-*`）只能引用 `tokens.css` 的变量，不写字面颜色。移动端页面同样遵守本规范，触控目标按 48。
