# API Service Test Manifest 设计文档

> **状态**：Draft，等待评审  
> **作者**：连通性测试重构小组  
> **范围**：`flocks` 后端、WebUI 工具页、`.flocks/plugins/tools/api/<provider>/` 插件目录约定  
> **关联分支**：`feat/ngtip-v5_1_5-connectivity-test`

---

## 1. 背景与动机

Flocks 当前对 API 服务（如 `ngtip_api_v5_1_5`、`onesig_api_v2_5_3_*`、`tdp_api_v3_3_10` 等）的"连通性测试"和"工具级测试"共用同一份 `ToolRegistry`，并通过启发式逻辑挑工具、猜参数。

### 1.1 服务级连通性测试现状

```2286:2336:flocks/flocks/server/routes/provider.py
            # Prefer simpler tools for connectivity testing.
            # Rank tools by required-parameter count (fewer = simpler);
            # prefer lightweight query/scan tools and avoid file/upload handlers.
            # ...
            login_probe_keywords = ("login", "whoami", "ping", "health")

            def _is_login_probe(t: ToolInfo) -> bool:
                ...

            def _tool_sort_key(t: ToolInfo) -> tuple[int, int, str]:
                required_count = sum(1 for p in t.parameters if p.required)
                name_lower = t.name.lower()
                if _is_login_probe(t):
                    priority = -1
                elif "ip" in name_lower:
                    priority = 0
                elif "url" in name_lower or "scan" in name_lower or "query" in name_lower:
                    priority = 1
                ...
```

```2338:2354:flocks/flocks/server/routes/provider.py
            def _string_candidates(param_name: str) -> list[str]:
                param_name_lower = param_name.lower()
                if "ip" in param_name_lower or param_name_lower == "resource":
                    return ["8.8.8.8"]
                if "domain" in param_name_lower:
                    return ["example.com"]
                if "hash" in param_name_lower or "sha256" in param_name_lower:
                    return ["657483b5bf67ef0cc2e2d21c68394d1f7fd35f9c0b6998f7b944dc4e5aa881f8"]
                ...
```

**问题**：

1. **职责混淆**：业务工具（给 LLM 调用的功能能力）被反过来当探针调用，每加一个新厂商都要 review 一次关键字表。
2. **无法预测**：以 `ngtip_query` 为例，启发式会自动喂出 `(action=query_hash, resource=8.8.8.8)` 这种伪非法请求，浪费配额且日志噪音大。
3. **不可声明**：维护者无法在插件目录里"显式说明这个 API 应该用什么探针"，配置只能改后端代码。

### 1.2 工具级测试现状

UI 中每个工具行都有一个 "测试 / 详情" 按钮（见 `ServiceDetailPanel.tsx` "工具" Tab）：

```949:961:flocks/webui/src/pages/Tool/components/ServiceDetailPanel.tsx
                    <th className="w-[120px] px-5 py-2.5 ... ">{t('detail.tableActions')}</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-gray-100">
                  {serviceTools.map((tool) => (
                    <tr key={tool.name} className="hover:bg-gray-50">
                      <td className="...">{tool.name}</td>
                      <td className="...">{tool.description}</td>
                      <td className="..."><EnabledBadge enabled={tool.enabled} /></td>
                      <td className="..."><button onClick={() => onSelectTool(tool)} ...>{t('detail.testDetail')}</button></td>
```

```366:380:flocks/webui/src/pages/Tool/index.tsx
  const handleTest = async () => {
    if (!selectedTool) return;
    if (!canDirectlyTestTool(selectedTool)) return;
    try {
      setTesting(true);
      setTestResult(null);
      const params = JSON.parse(testParams);
      const response = await toolAPI.test(selectedTool.name, params);
      setTestResult(response.data);
```

用户点 "测试 / 详情" 后，需要在抽屉里**手写 JSON 参数**，对 `ngtip_query`（5 个 action × 各自 schema）这种 dispatcher 工具尤其痛苦——没人能记住每条 action 的精确字段名。

### 1.3 两层测试的本质差异

| 层级 | 触发位置 | 目的 | 期望行为 |
|---|---|---|---|
| **L1 服务级** | 服务详情页 "测试连通性" 按钮 | 验证 base_url + 凭据可用 | 调用要廉价、无副作用、可预期 |
| **L2 工具级** | 工具行 "测试 / 详情" 按钮 | 验证某个具体工具是否能跑通 | 用户能一键填入合理样例，省去手写 JSON |

二者在**真正调用什么、用什么参数**这件事上需求一致，应该由插件作者（最懂业务）声明，而不是后端启发式。

---

## 2. 目标与非目标

### 2.1 In-scope

- **G1**：在每个 API 插件目录里支持一份 `_test.yaml`，由插件作者声明「连通性探针 = 某个已注册工具 + 一组参数」。
- **G2**：`_test.yaml` 同时承载工具级 fixtures，作为 UI "测试 / 详情" 的样例下拉数据源。
- **G3**：服务级连通性测试优先使用 `_test.yaml.connectivity`，未声明时透明回退到现有启发式（向后兼容）。
- **G4**：`_test.yaml` 沿用 `PluginLoader` 的 `_` 前缀约定，保证**绝不**进入 `ToolRegistry`、绝不暴露给 LLM。
- **G5**：以 `ngtip_v5_1_5` 作为首个示范厂商落地端到端流程。

### 2.2 Out-of-scope（Future Work，本设计不做）

- CI / pytest 集成（基于 `_test.yaml` 的回归套件）。
- `connectivity` 多探针级联兜底（先 A 失败再试 B）。
- `params` 模板化（如 `{{today}}`、`{{secret:X}}`）。
- 把 `_test.yaml` 也接入 MCP、Generated Tool 这两类（首版只覆盖 `tools/api/`）。
- 修改 `POST /api/tools/{name}/test` 现有行为。

---

## 3. 设计总览

```
┌──────────────────────────── flocks/.flocks/plugins/tools/api/ngtip_v5_1_5/ ────────────────────┐
│                                                                                                 │
│  _provider.yaml          → loader 跳过（已有约定，存放 service_id/version/credential schema）   │
│  _test.yaml              → loader 跳过（新增）：connectivity + fixtures                         │
│  ngtip.handler.py        → handler 实现（业务工具复用，零改动）                                  │
│  ngtip_query.yaml        → 业务工具，进 ToolRegistry                                           │
│  ngtip_platform.yaml     → 业务工具，进 ToolRegistry                                           │
└─────────────────────────────────────────────────────────────────────────────────────────────────┘

           ┌────────────── L1: 服务级连通性测试 ──────────────┐    ┌── L2: 工具级测试 ──┐
           │                                                    │    │                     │
   UI ──► /api/provider/{id}/test-credentials                   │    GET /api/tools/{n}/fixtures
           │                                                    │    │                     │
           ├─► load_test_manifest(provider_id)                  │    └─► load_test_manifest(...).fixtures[name]
           │     ┌─ connectivity 命中? ─┐                       │
           │     │ Yes                  │ No                    │      → 返回该工具的预置样例列表
           │     ▼                      ▼                       │
           │  ToolRegistry.execute(    现有启发式（保留）        │      UI ToolDetailDrawer 显示
           │    connectivity.tool,                              │      「选择样例 ▾」下拉
           │    **connectivity.params)                          │
           │     │                                              │
           │     ▼                                              │
           │  ToolResult                                        │
           │     │                                              │
           └─────┴──► save api_service_status cache             │
                                                                 │
                  ────────── ToolRegistry / Agent 永远看不到 _test.yaml ─────────
```

**核心思想**：`_test.yaml` 是一份纯**索引 / 元数据**——它**不引入新的可调用单元**，只声明「连通性测试 = 跑哪个已有工具 + 喂什么参数」。所以：

- 不需要插件作者写新的 Python `health()` 函数；
- 切换探针只需改 YAML 一行；
- LLM / Agent 永远只看见业务工具，看不到测试索引。

---

## 4. 详细设计

### 4.1 文件位置与命名约定

- 路径：`<plugin_dir>/_test.yaml`，与 `_provider.yaml` 同目录。
- 文件名以 `_` 开头，复用现有的 `PluginLoader` 跳过规则：

```96:104:flocks/flocks/plugin/loader.py
        if item.is_file() and item.suffix in _SUPPORTED_EXTENSIONS and not item.name.startswith("_"):
            results.append(str(item))
        elif (
            depth < max_depth
            and item.is_dir()
            and not item.name.startswith("_")
            and (depth > 0 or item.name not in exclude)
        ):
            _scan_recursive(item, results, depth + 1, max_depth, exclude)
```

> 文件名不要选 `test_*.yaml`、`*.test.yaml`，那些不会被自动跳过、可能误进 `ToolRegistry`。`_test.yaml` 是唯一安全选项。

### 4.2 `_test.yaml` Schema

```yaml
# Required: 与 _provider.yaml.service_id 一致，用于交叉校验
provider: ngtip_api

# ── L1：服务级连通性测试（必填顶层字段） ───────────────────────────
connectivity:
  # Required：已注册的工具名（即 ToolRegistry.list_tools() 中的 name），
  # 必须属于本 provider，否则启动期校验失败。
  tool: ngtip_query

  # Required：调用参数（dict），与 UI "测试 / 详情" 抽屉里手填的 JSON
  # 结构完全一致；空对象表示无参调用。
  params:
    action: query_ip
    resource: "8.8.8.8"

  # 注：当前 schema 总是把 ToolResult.success == True 作为通过条件。
  # 富断言（output_contains / status_code / …）尚未实现；写入未识别字段
  # 不会报错但会被忽略并打 warn 日志，等真有需求再加。

# ── L2：工具级测试样例（可选） ─────────────────────────────────────
fixtures:
  ngtip_query:                                         # key = tool name
    - label: "IP reputation lookup (8.8.8.8)"          # Required，默认 (英文)
      label_cn: "IP 信誉查询（8.8.8.8）"               # Optional，中文覆盖
      tags: [smoke, ip]                                # Optional，语义标签
      params:                                          # Required
        action: query_ip
        resource: "8.8.8.8"
      assert:                                          # Optional，回归断言（CI 用）
        success: true

    - label: "Domain compromise check"
      label_cn: "域名失陷检测"
      params: { action: query_dns, resource: "example.com" }

  ngtip_platform:
    - label: "Intel volume statistics (last year)"
      label_cn: "情报数量统计（最近一年）"
      params: { action: platform_intelligence_count }
```

**字段约束**：

| 字段 | 必填 | 约束 |
|---|---|---|
| `provider` | 是 | 字符串；与同目录 `_provider.yaml.service_id` 严格相等，否则加载警告 |
| `connectivity` | **强烈推荐** | dict；不写时整份 manifest 仅作 fixtures 使用，连通性测试回退启发式 |
| `connectivity.tool` | 是（若有 connectivity） | 字符串；启动期校验：必须存在且 `tool.provider == storage_key` |
| `connectivity.params` | 是（若有 connectivity） | dict；可为空 `{}` |
| `connectivity.success_when` | — | 当前版本不支持，写入会被忽略并 warn；保留键名供未来扩展 |
| `fixtures` | 否 | dict<tool_name, list<fixture>> |
| `fixtures[*].label` | 是 | 非空字符串，长度 ≤ 80；默认 UI 显示文案（约定为英文） |
| `fixtures[*].label_cn` | 否 | 非空字符串，长度 ≤ 80；WebUI 在 zh-* 语言下优先使用 |
| `fixtures[*].tags` | 否 | 字符串列表 |
| `fixtures[*].params` | 是 | dict |
| `fixtures[*].assert` | 否 | dict；首版仅识别 `success: bool` |

### 4.3 加载与发现

新增模块 `flocks/flocks/tool/probe_loader.py`：

```python
@dataclass(frozen=True)
class ConnectivitySpec:
    tool: str                 # tool name (registered)
    params: Dict[str, Any]
    # success_when intentionally omitted in v1; the probe always asserts
    # ToolResult.success == True. Adding the field later is forward-compatible.

@dataclass(frozen=True)
class Fixture:
    label: str                         # default (English) UI string
    params: Dict[str, Any]
    tags: Tuple[str, ...] = ()
    assertion: Dict[str, Any] = field(default_factory=dict)
    label_cn: Optional[str] = None     # optional zh-* override

@dataclass(frozen=True)
class TestManifest:
    provider_id: str          # storage_key, e.g. "ngtip_api_v5_1_5"
    plugin_dir: Path
    connectivity: Optional[ConnectivitySpec]
    fixtures: Dict[str, List[Fixture]]   # tool_name -> samples


def load_test_manifest(provider_id: str) -> Optional[TestManifest]: ...
def get_connectivity_spec(provider_id: str) -> Optional[ConnectivitySpec]: ...
def get_tool_fixtures(provider_id: str, tool_name: str) -> List[Fixture]: ...
def get_tool_fixtures_by_tool_name(tool_name: str) -> List[Fixture]: ...   # for /api/tools/{name}/fixtures
```

**发现机制**：

1. 通过 `flocks.config.api_versioning.discover_api_service_descriptors()` 取 `(storage_key, plugin_dir)` 列表（已存在 API）。
2. 对每个 `plugin_dir / "_test.yaml"`，存在则 `yaml.safe_load`。
3. 解析为 `TestManifest` 后缓存到 module-level `dict[storage_key, TestManifest]`。
4. **缓存失效**：跟随 `api_versioning` 的 `refresh=True` 重新发现；首版不实现热重载（重启即可），与现有 `_provider.yaml` 行为一致。

**启动期校验**（推荐放在 `ToolRegistry.init()` 完成之后）：

- `connectivity.tool` 必须是已注册工具，否则记 `log.warn("test_manifest.invalid_tool", ...)`，但**不**抛异常（避免一份坏 yaml 拖垮整个进程）。
- `connectivity.tool.provider` 必须等于 manifest 所在的 `storage_key`，否则记 warn。
- `fixtures` 中的 tool name 不存在时，仅 debug 日志，不阻塞。

### 4.4 服务级连通性测试链路改造

修改 `flocks/flocks/server/routes/provider.py:test_provider_credentials` 的 API service 分支（当前 L2240+）。**插入点**在已有的 `_set_api_service_tools_enabled` 调用之后、启发式 service_tools 排序之前：

```python
# 在 API service 分支内，加载工具列表后立即尝试 manifest
from flocks.tool.probe_loader import get_connectivity_spec

spec = get_connectivity_spec(provider_id)
if spec is not None:
    log.info("test_credentials.using_declared_probe", {
        "service": provider_id,
        "tool": spec.tool,
        "params": spec.params,
    })
    try:
        result = await ToolRegistry.execute(tool_name=spec.tool, **spec.params)
        latency = int((time.time() - start) * 1000)
        response = {
            "success": result.success,
            "message": (
                f"✅ 连通性测试成功（声明式探针：{spec.tool}）"
                if result.success else f"❌ 连通性测试失败：{result.error or 'Unknown'}"
            ),
            "latency_ms": latency,
            "tool_tested": spec.tool,
            "params_used": spec.params,
            "probe_source": "manifest",   # 新增字段，方便观测
        }
        await _save_api_service_status_if_configured(provider_id, response)
        return response
    except Exception as exc:
        # 声明式探针执行抛异常 → 视为 manifest 配置坏了，记 warn 后回退
        # 到既有启发式逻辑（不直接返回 manifest 失败），避免一份坏 yaml
        # 拖垮整个连通性测试。
        log.warning("test_credentials.declared_probe_exception_falling_back", {
            "service": provider_id, "tool": spec.tool, "error": str(exc),
        })
        # fall through

# 没有 manifest 或 manifest 抛异常 → 现有启发式
```

**关键设计点**：

1. **`probe_source: "manifest" | "heuristic"`** 字段，让前端 / 日志 / Grafana 能区分两种探针来源，便于度量迁移进度。
2. **业务失败 vs 异常的边界**：工具被成功调起、返回 `success=False`（如 apikey 错）就是连通性测试要发现的答案，不回退；只有 `ToolRegistry.execute` 抛异常（manifest 写错工具名 / registry 故障）才回退到启发式，保持向后兼容。
3. **状态写入用带守卫版本**：`_save_api_service_status_if_configured` 与启发式分支保持一致——未配置/已禁用时不污染 `flocks.json`。

### 4.5 工具级测试样例 API + UI 增强

#### 后端

新增路由 `flocks/flocks/server/routes/tool.py`：

```python
@router.get(
    "/{name}/fixtures",
    response_model=List[FixtureResponse],
    summary="List declared test fixtures for a tool",
)
async def list_tool_fixtures(name: str) -> List[FixtureResponse]:
    """Return predeclared test samples (params) for the given tool, parsed
    from the owning provider's _test.yaml. Returns [] if no manifest exists."""
    ...
```

`FixtureItemResponse` shape：
```python
class FixtureItemResponse(BaseModel):
    label: str                                  # 默认显示文案 (英文)
    label_cn: Optional[str] = None              # zh-* 语言下优先使用
    params: Dict[str, Any]
    tags: List[str] = []
    has_assertion: bool = False                 # 是否有 assert（CI 用，前端可隐藏）
```

#### 前端

`flocks/webui/src/api/tool.ts`：
```ts
export interface ToolFixture {
  label: string;
  label_cn?: string | null;
  params: Record<string, any>;
  tags: string[];
  has_assertion: boolean;
}

listFixtures: (name: string) =>
  client.get<ToolFixture[]>(`/api/tools/${name}/fixtures`),
```

`flocks/webui/src/pages/Tool/toolDisplay.ts` 提供 locale-aware 取值（与
`getLocalizedToolDescription` 同一惯例）：

```ts
export function getLocalizedFixtureLabel(
  fixture: Pick<ToolFixture, 'label' | 'label_cn'>,
  language: string,
): string {
  const cn = fixture.label_cn?.trim() || '';
  const en = fixture.label?.trim() || '';
  return language.toLowerCase().startsWith('zh') ? (cn || en) : (en || cn);
}
```

`flocks/webui/src/pages/Tool/index.tsx` 中两个 detail 组件
（API 工具用 `ToolDetailDrawer`，MCP 工具用 `MCPToolDetailPanel`）在测试参数
输入框上方各自渲染一个下拉。下拉**始终渲染**：加载中时禁用并显示
`toolDetail.updating`，无 fixture 时禁用并显示 `toolDetail.selectFixturePlaceholder`：

```tsx
<select
  value=""
  disabled={fixturesLoading || fixtures.length === 0}
  onChange={(e) => {
    const idx = parseInt(e.target.value, 10);
    if (!isNaN(idx) && fixtures[idx]) {
      onTestParamsChange(JSON.stringify(fixtures[idx].params, null, 2));
    }
  }}
>
  <option value="">
    {fixturesLoading ? t('toolDetail.updating') : t('toolDetail.selectFixturePlaceholder')}
  </option>
  {fixtures.map((f, idx) => (
    <option key={idx} value={idx}>
      {f.tags.includes('smoke') ? '✦ ' : ''}{getLocalizedFixtureLabel(f, i18n.language)}
    </option>
  ))}
</select>
```

> 进阶（同一 PR 或下个迭代）：再加一个「全部样例一键跑」按钮，依次执行所有 fixtures 并展示结果矩阵；这会大幅降低 onesig 这种 6+ 工具厂商的回归测试成本。

---

## 5. 边界、异常与诊断

| 场景 | 行为 | 日志/反馈 |
|---|---|---|
| 没有 `_test.yaml` | 透明回退到启发式 | 无（与现行为一致） |
| `_test.yaml` 语法错误 | 当作没有 manifest | `log.warn("test_manifest.parse_error", {...})` |
| `connectivity.tool` 不存在或不属于本 provider | 当作没有 connectivity（仅 fixtures 生效） | `log.warn("test_manifest.invalid_tool", {...})` |
| `connectivity.params` 缺字段或不合法 | 调用真实失败，按原样反馈给前端 | 调用栈日志 |
| 插件被 `enabled=false` 屏蔽 | `connectivity` 仍可调用（与启发式行为一致：参考现有 `_set_api_service_tools_enabled`） | 不做特殊处理 |
| `fixtures[tool_name]` 中 tool 不存在 | 该项被忽略 | `log.debug("test_manifest.fixture_orphaned", {...})` |
| 凭据未配置 | 现有 `if not api_key: return early` 优先生效，不进 manifest 分支 | 与现行为一致 |
| 多版本插件共存（`ngtip_v5_1_5`、`ngtip_v6_0_0`） | 各自独立的 `_test.yaml`，互不干扰 | manifest cache 按 storage_key 分隔 |

**特别注意**：`_test.yaml.provider` 字段必须等于 `_provider.yaml.service_id`。这是一道**人肉防错**：作者复制目录的时候很容易忘记改 service_id；启动期一对账即可发现。

---

## 6. 兼容性与迁移

### 6.1 向后兼容

- 旧插件（无 `_test.yaml`）行为完全不变。
- 现有路由 `POST /api/provider/{id}/test-credentials` / `GET /api-services/status` / `POST /api-services/refresh` 接口形状不变。
- `_save_api_service_status` 缓存 schema 仅**新增**字段（`probe_source`、`params_used`），不删除任何现有字段。

### 6.2 迁移计划

| 阶段 | 动作 | 涉及插件 |
|---|---|---|
| Phase 0 | 落地基础设施 + NGTIP 示范 | `ngtip_v5_1_5/` |
| Phase 1 | 高频厂商 follow-up | `tdp_v3_3_10`、`onesec_v2_8_2`、`onesig_v2_5_3_*`、`skyeye_v4_0_14_0_SP2` |
| Phase 2 | 写一个 `flocks probe --check-manifest` CLI 子命令，扫所有插件统计 manifest 覆盖率 | — |
| Phase 3 | 当覆盖率达到 80%+ 时，把启发式逻辑挪进 deprecated 子模块，加 `log.warn("heuristic_probe.used", ...)` 提醒维护者补 manifest | — |

注意：**不强制要求**所有插件都有 `_test.yaml`。启发式作为永久兜底保留——某些极简 provider（如已知只有一个无参 health 端点）写不写 manifest 收益相近。

---

## 7. 实施拆分（按 PR 切片）

### PR 1：`probe_loader` + manifest 接入（基础设施，无用户感知）
- 新增 `flocks/flocks/tool/probe_loader.py`
- 改 `flocks/flocks/server/routes/provider.py` 的 API service 分支，前置 manifest 命中逻辑
- 单元测试 + 集成测试（mock `ToolRegistry.execute`）
- 不动任何插件目录

### PR 2：NGTIP 示范落地
- 新增 `flocks/.flocks/plugins/tools/api/ngtip_v5_1_5/_test.yaml`
- 跑一遍真实 NGTIP 实例，确认 `probe_source: "manifest"` 生效

### PR 3：UI 工具级测试样例下拉
- 新增 `GET /api/tools/{name}/fixtures` 路由
- 改 `flocks/webui/src/api/tool.ts` 和 `ToolDetailDrawer`
- i18n 文案：`tool.detail.selectFixture` / `tool.detail.runAllFixtures`

### PR 4（可选，看 NGTIP 落地反馈）：高频厂商批量补 `_test.yaml`
- 一个 PR 一个厂商，按"被使用频率"排序

---

## 8. 测试策略

### 8.1 单元测试（`flocks/tests/tool/test_probe_loader.py`）

- ✅ 解析合法 YAML：`load_test_manifest("ngtip_api_v5_1_5")` 返回正确的 `TestManifest`。
- ✅ `provider` 字段不匹配 `_provider.yaml.service_id` → 加载成功但 warn 日志。
- ✅ `connectivity.tool` 不存在 → `get_connectivity_spec` 返回 None + warn。
- ✅ YAML 解析失败 → 返回 None，不抛异常。
- ✅ `fixtures` 中 tool 不存在 → 该条被丢弃但其它正常返回。
- ✅ 没有 `_test.yaml` 文件 → 返回 None。

### 8.2 集成测试（`flocks/tests/provider/test_test_credentials.py`，扩展）

- ✅ 有 manifest → 走声明式分支，`tool_tested == manifest.tool`，`probe_source == "manifest"`。
- ✅ 无 manifest → 走启发式分支（现有断言不变）。
- ✅ manifest 声明式探针抛异常 → response.success == False 且 message 包含「声明式探针」字样，**不**回退到启发式。
- ✅ NGTIP 端到端：mock NGTIP 200 响应 `{response_code: 0, data: {...}}` → `success=True`；mock 401 → `success=False`。

### 8.3 前端测试

- 后续 PR 3 落地时补：`ToolDetailDrawer` 在 fixtures 非空时渲染下拉，选中后 `testParams` state 更新为对应 `JSON.stringify`。

---

## 9. 风险与未决问题

### 9.1 已识别风险

| # | 风险 | 缓解 |
|---|---|---|
| R1 | 维护者可能在 `connectivity.params` 里写下会**写入**的请求（如 `add_intelligence`、`distribution_rules`），导致每次"测试连通性"都污染生产数据 | 文档强烈警告；启动期对 `tool.requires_confirmation == True` 的 connectivity 给 `log.warn("test_manifest.write_probe", ...)` 标记，但不阻塞 |
| R2 | `_test.yaml` 跟代码版本不匹配（API 升级了但 manifest 没更新） | 启动期校验 + 集成测试可发现；Phase 2 的 CLI 扫描器会进一步暴露 |
| R3 | `connectivity.params` 引用动态值（如时间戳）时无法表达 | 第一版接受局限；Future Work 引入模板 |
| R4 | `_test.yaml` 文件被打包到 Docker 镜像但用户的 `~/.flocks/plugins/...` 覆盖目录里没有 | 与现有 `_provider.yaml` 同样面临这个问题，不在本设计新增；走插件分发管道解决 |

### 9.2 待评审的设计选择

- **D1：`connectivity` 用单一探针还是探针列表（先 A 失败再试 B）？**  
  建议：**单一**。多探针级联回到了启发式的复杂度，违背"声明清晰、可读"的初衷。如果 A 失败就该让用户看到真实失败。

- **D2：`_test.yaml` 的 `provider` 字段是否冗余？**（因为它能从目录路径反推）  
  建议：**保留**。冗余字段可以做 sanity check，发现"复制目录忘改 service_id"这类错误。

- **D3：`fixtures` 是否支持跨工具的"组合测试用例"？**（如 "先 add，再 list，再 delete"）  
  建议：**第一版不支持**。这是 E2E 测试场景，用 pytest 写更合适，硬塞进 YAML 会迅速变成迷你 DSL。

- **D4：是否给 manifest 增加版本号（schema_version: 1）？**  
  建议：**加**，预留向前兼容空间。文件顶层增加 `schema_version: 1`，loader 默认 v1，未识别的 schema 警告并降级处理。

---

## 10. Future Work

按价值/紧迫程度排序：

1. **CLI 子命令 `flocks probe`**  
   - `flocks probe --service ngtip_api_v5_1_5` 跑 connectivity；
   - `flocks probe --all-fixtures` 跑所有 fixtures 并断言；
   - `flocks probe --check-manifest` 扫所有插件，输出覆盖率矩阵。

2. **pytest fixture / plugin**  
   把 `_test.yaml.fixtures[*].assert` 接入 pytest，让插件作者 `pytest tests/plugins/ngtip` 就能跑回归。

3. **UI "全部样例一键跑"**  
   工具详情抽屉里一键依次执行所有 fixtures，展示成功率矩阵。

4. **`params` 模板**  
   支持 `{{today}}` / `{{epoch_now}}` / `{{secret:X}}` 等占位符，应对动态参数。

5. **Manifest schema 校验工具**  
   `pre-commit` hook 调用 `python -m flocks.tool.probe_loader --validate <plugin_dir>`。

6. **跨插件类型支持**  
   把 manifest 思路延伸到 `tools/mcp/` 和 `tools/generated/`（首版只覆盖 `tools/api/`）。

---

## 附录 A：NGTIP 完整 `_test.yaml` 示例

```yaml
# flocks/.flocks/plugins/tools/api/ngtip_v5_1_5/_test.yaml
schema_version: 1
provider: ngtip_api

connectivity:
  tool: ngtip_query
  params:
    action: query_ip
    resource: "8.8.8.8"
  success_when:
    tool_result_success: true

fixtures:
  ngtip_query:
    - label: "IP 信誉查询（8.8.8.8）"
      tags: [smoke, ip]
      params: { action: query_ip, resource: "8.8.8.8" }
      assert: { success: true }

    - label: "域名失陷检测（example.com）"
      tags: [smoke, domain]
      params: { action: query_dns, resource: "example.com" }

    - label: "Hash 文件信誉（已知良性）"
      tags: [hash]
      params:
        action: query_hash
        resource: "657483b5bf67ef0cc2e2d21c68394d1f7fd35f9c0b6998f7b944dc4e5aa881f8"

    - label: "漏洞情报（按 CVE 查）"
      tags: [vuln]
      params: { action: query_vuln, vuln_id: "CVE-2024-3400" }

    - label: "IP 地理位置"
      tags: [location]
      params: { action: query_location, resource: "8.8.8.8" }

  ngtip_platform:
    - label: "情报数量统计（最近一年）"
      tags: [smoke, count]
      params: { action: platform_intelligence_count }

    - label: "态势情报订阅（最近 7 天）"
      tags: [subscription]
      params:
        action: platform_subscription
        report_time: "7d"

    - label: "行业攻击情报（最近 7 天）"
      tags: [industry]
      params:
        action: platform_industry_attack
        update_time: "7d"
```

---

## 附录 B：自我评审 checklist

- [x] 不引入新的"暴露给 LLM 的工具"——`_test.yaml` 严格不进 `ToolRegistry`。
- [x] 不需要插件作者写新的 Python 函数（`health()` 等），只写 YAML。
- [x] 旧插件无变更即可继续工作（启发式兜底）。
- [x] 探针来源可观测（`probe_source` 字段进 cache 与日志）。
- [x] schema 最小化：除 `provider` + `connectivity` 必需，其余全部可选。
- [x] 命名空间安全：`connectivity.tool` 必须属于本 provider，启动期校验。
- [x] 错误处理透明：声明式探针失败明确反馈，不悄悄回退。
- [x] 拆分友好：4 个 PR 互相解耦，每个都能独立 review / revert。
- [x] 预留扩展点：`success_when`、`assert`、`schema_version` 都为后续能力留空。
- [x] 文档可读：YAML 例子、流程图、表格三种载体说明同一件事。
