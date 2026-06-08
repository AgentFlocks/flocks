---
name: user-defined-page-builder
category: system
description: Guide users to create, develop, hide, or delete user-defined custom pages that appear in the WebUI left navigation under Home, with live preview and no restart required. Also guide development of page-scoped backend APIs through the User Defined Page Backend API Runtime when built-in APIs are insufficient. Trigger when the user asks to create, remove, or delete a custom page, user-defined page, dashboard, navigation tab, integrate custom APIs for a page, or sends messages such as "create a custom page", "delete custom page", "remove user-defined page", "创建自定义页面", "删除自定义页面", "用户自定义页面", "自定义页面", "左侧导航页面", "首页下面的页面", "页面数据来源", "自定义 API", or wants help understanding how custom pages work in Flocks.
---

# User Defined Page Builder

When the user wants to create **user-defined custom pages** (shown in the WebUI left navigation under **Home**), first explain the feature clearly, then guide them through creation and development.

## Core Principles

- **Language**: Detect the user's language from their messages or UI locale. Conduct the **entire conversation in the user's language** (Chinese or English). Do not switch languages mid-session.
- **Admin-required notice**: Creating, editing, hiding, deleting, importing, or exporting user-defined pages requires administrator privileges. Before starting any write workflow, remind the user that the operation must be performed by an admin. This skill does **not** verify the user's role; WebUI visibility and backend APIs enforce authorization.
- **Explain before acting**: If the user only asks what the feature is, explain fully before creating anything.
- **Confirm once**: Before creating, confirm `pageId` (lowercase English + hyphens), `title` (navigation label in the user's language), and optional `icon` (Lucide icon name).
- **User space only**: Read and write only under `~/.flocks/plugins/user_defined_pages/`.
- **Final location check**: After finishing any page development, verify that all user-defined page files are stored under `~/.flocks/plugins/user_defined_pages/<pageId>/`. They must **not** remain in the project code directories such as `webui/`, `flocks/`, `tests/`, or `docs/`.
- **SDK only**: Page code may import only `react` and `@flocks/user-defined-page-sdk` (`Card`, `api`, `useCurrentUser`).
- **Never write `dist/`**: Build artifacts are generated automatically.
- **Auth-aware**: All `/api/user-defined-pages/*` routes require authentication. Prefer **direct file writes** for Rex; use API Token only when calling HTTP from non-browser clients. Never embed tokens in page source.
- **Page-scoped backend**: When built-in `/api/*` is insufficient, use the User Defined Page Backend API Runtime design: page APIs live under the page directory and are exposed only at `/api/user-defined-pages/<pageId>/api/*`.

## Authentication

Flocks protects **all HTTP API paths by default** (including `/api/user-defined-pages/*`). Only bootstrap, static assets, and a few public endpoints are exempt. Understand who needs what credential:

### WebUI (browser)

- User must be **logged in** (session cookie `flocks_session`).
- The WebUI axios client sends cookies automatically (`withCredentials: true`).
- Navigation, page host, bundle loading, and in-page `api` calls all reuse this session — **no extra token setup** for end users.
- If the user is not logged in or the session expired, user-defined pages and related APIs return **401**.

### Rex / Agent (recommended: file writes, no HTTP auth)

When creating or editing pages, first remind the user that the operation requires admin privileges, then **write files directly** under `~/.flocks/plugins/user_defined_pages/<pageId>/`:

- No HTTP request → no API Token needed.
- The file watcher detects changes, rebuilds, and publishes SSE events automatically.
- This is the **preferred path** for Rex in chat sessions.
- This skill does not perform role verification; WebUI and backend API paths are responsible for enforcing admin-only management.

### Rex / Agent (optional: HTTP API)

Use the REST API only when file-editing access is unavailable or you need an explicit build trigger. Page management APIs require admin privileges. `curl`, Python `httpx`/`requests`, and other **non-browser** clients **must** carry an API Token — even on `127.0.0.1`.

**Token location**: `~/.flocks/config/.secret.json`, secret id `server_api_token`.

**Generate or rotate** (on the Flocks server):

```bash
flocks admin generate-api-token
```

**Configure on a remote client** (same token value):

```bash
flocks admin set-api-token --token <token>
```

**Read token in Python** (when Rex runs a script inside Flocks):

```python
from flocks.security import get_secret_manager
from flocks.server.auth import API_TOKEN_SECRET_ID

token = get_secret_manager().get(API_TOKEN_SECRET_ID)
```

**Request headers** (either works):

```text
Authorization: Bearer <token>
X-Flocks-API-Token: <token>
```

All `curl` examples in this skill use `Authorization: Bearer <token>` — substitute the real token from the secret store. Do **not** ask the user to paste the token into chat; read it from the secret file or use file writes instead.

API Token authenticates as a synthetic **admin service identity** (`api-token-service`). It is for automation, not for end-user page rendering.

### Custom page code (`@flocks/user-defined-page-sdk` `api`)

- The SDK `api` helper is the WebUI axios client — it sends the **logged-in user's session cookie**, not an API Token.
- Page code may call other `/api/*` endpoints (alerts, sessions, etc.) while the user is logged in.
- **Never** hardcode `server_api_token` or any secret inside `src/Page.tsx` or other page source; tokens would be exposed in the bundle.

### Explain to users (first reply / when asked)

**Chinese example**:

> 自定义页面相关接口都需要登录鉴权。普通用户可以查看和使用已发布页面，但创建、修改、隐藏、删除、导入或导出页面需要管理员权限。我（Rex）在开始这类写操作前会提醒需要管理员操作，通常直接读写 `~/.flocks/plugins/user_defined_pages/` 目录，不经过 HTTP。若用脚本调管理 API，需在服务端配置 `server_api_token` 并在请求头携带 Bearer Token。

**English example**:

> User-defined page APIs require authentication. Regular users can view and use published pages, but creating, editing, hiding, deleting, importing, or exporting pages requires admin privileges. I (Rex) remind the user before starting these write operations and usually read/write `~/.flocks/plugins/user_defined_pages/` directly without HTTP. Non-browser management API clients must send a Bearer API Token from `server_api_token` in `~/.flocks/config/.secret.json`.

## First Reply Must Cover

Explain these points in the user's language:

1. **What it is**: Custom React pages under the Home section of the left navigation — for alert dashboards, asset views, duty screens, etc.
2. **Where files live**: `~/.flocks/plugins/user_defined_pages/<pageId>/` in the user space, **not** in the project code directory.
3. **How it appears**: After creation, a nav item shows under Home; route is `/user-defined-pages/<pageId>`.
4. **How to develop**: Describe requirements in chat; you write `src/Page.tsx`; saving triggers auto-build; **no restart** required.
5. **Live updates**: Source changes rebuild automatically; open pages and navigation refresh via SSE.
6. **How to remove**: Tell the user both options below — hiding from nav (reversible) and permanently deleting the page directory.
7. **Authentication and authorization**: WebUI uses login session automatically. Regular users can use published pages. Creating/modifying pages requires admin privileges; Rex should remind the user before write operations but does not verify roles in this skill; scripts calling management APIs need `server_api_token` (see **Authentication** above).
8. **Data sources**: Built-in `/api/*` endpoints, page-scoped backend APIs (`/api/user-defined-pages/<pageId>/api/*`), or workflows (`/api/workflow/{id}/run`) (see **Backend Data & API Extension** below).
9. **Backup + restart/upgrade continuity**: Back up the full page directory and explain that Flocks scans/rebuilds pages from `~/.flocks/plugins/user_defined_pages/` after restart or upgrade.

Then ask whether the user already has a page idea. If they have an idea, remind them that creation requires admin privileges, then start creation. If they do not have an idea, offer 2–3 example scenarios.

## Page ID Rules

- Allowed: `a-z`, `0-9`, `-`
- Examples: `alert-dashboard`, `threat-overview`, `duty-screen`
- Disallowed: uppercase, spaces, CJK characters, underscores

## Directory Layout

```text
~/.flocks/plugins/user_defined_pages/<pageId>/
  manifest.json
  src/index.tsx
  src/Page.tsx
  dist/page.js        # auto-generated
  dist/meta.json      # auto-generated
  assets/             # optional
```

## Creation Options

### Option A — API (when HTTP is needed)

Requires a valid `server_api_token` (see **Authentication**). Rex should prefer Option B unless API is explicitly required.

```bash
curl -s -X POST http://127.0.0.1:8000/api/user-defined-pages \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer <token>" \
  -d '{"id":"alert-dashboard","title":"Alert Dashboard","icon":"BarChart3","order":100}'
```

Chinese example title: `"title":"告警看板"`.

### Option B — Write files directly (preferred for Rex)

Create under `~/.flocks/plugins/user_defined_pages/<pageId>/`:

**manifest.json**

```json
{
  "id": "alert-dashboard",
  "title": "Alert Dashboard",
  "route": "/user-defined-pages/alert-dashboard",
  "icon": "BarChart3",
  "order": 100,
  "enabled": true,
  "placement": "home.after",
  "entry": "src/index.tsx",
  "updatedAt": 0
}
```

**src/index.tsx**

```tsx
import Page from './Page';
export default Page;
```

**src/Page.tsx** — start from the template below.

## Page Template

Use the manifest `title` for the card heading. Keep in-page status text in the user's language.

```tsx
import { useEffect, useState } from 'react';
import { Card } from '@flocks/user-defined-page-sdk';

export default function Page() {
  const [ready, setReady] = useState(false);

  useEffect(() => {
    setReady(true);
  }, []);

  return (
    <Card title="Alert Dashboard">
      {ready ? 'Ready' : 'Loading...'}
    </Card>
  );
}
```

For Chinese pages, use Chinese copy inside the component, e.g. `{ready ? '页面已就绪' : '加载中...'}`.

## Development Flow

1. After scaffold creation, tell the user the nav label, route, and directory path.
2. Identify data sources — built-in `/api/*`, page-scoped backend APIs, workflows, or external systems that need server-side proxying.
3. Edit `src/Page.tsx` based on requirements (add more files under `src/` if needed).
4. On save, the system rebuilds automatically. If build fails, read `dist/meta.json` → `error` and fix.
5. Manual rebuild: `POST /api/user-defined-pages/<pageId>/build`
6. Before wrapping up, run a final location check: every page file created for the user must be under `~/.flocks/plugins/user_defined_pages/<pageId>/`; do not leave page source, API handlers, assets, or drafts in the repository code directories.

## Backup and Restore

Always provide this backup command in the first explanation and in the final wrap-up:

```bash
cp -a ~/.flocks/plugins/user_defined_pages/<pageId> ~/.flocks/workspace/outputs/<today>/<pageId>-backup
```

Restore by copying the backup directory back to `~/.flocks/plugins/user_defined_pages/<pageId>/`. After restart (or immediately if watcher is active), the page will be scanned and available again.

## Backend Data & API Extension

When a custom page needs backend logic or external data that built-in APIs do not provide, use a **page-scoped backend API runtime**.

### Design Principle

Do **not** register arbitrary global FastAPI routes such as `/api/my-dashboard/stats`.

The page backend should be scoped to the page namespace:

```text
/api/user-defined-pages/<pageId>/api/{path:path}
```

This keeps page APIs tied to page lifecycle, permissions, logs, hot reload, deletion, and future UI management.

### Architecture

```text
User Defined Page (src/Page.tsx)
  └─ SDK api ──► /api/user-defined-pages/<pageId>/api/*     (page-scoped backend)
              ├─► /api/workflow/{id}/run           (multi-step workflows)
              └─► /api/*                           (built-in Flocks APIs)
```

### Target Directory Layout

When a page needs backend code, add an `api/` directory inside that page:

```text
~/.flocks/plugins/user_defined_pages/<pageId>/
  manifest.json
  src/Page.tsx
  api/
    routes.yaml
    handlers.py
  dist/
    page.js
    meta.json
```

### Route Manifest

Use `api/routes.yaml` to declare the page API surface:

```yaml
routes:
  - method: GET
    path: /stats
    handler: handlers.get_stats
    timeoutMs: 5000

  - method: POST
    path: /ack
    handler: handlers.ack_alert
    timeoutMs: 10000
```

Rules:

- `path` must start with `/` and is always scoped under `/api/user-defined-pages/<pageId>/api`.
- `handler` points to a callable in `api/handlers.py`.
- Keep route count small and page-specific.
- Prefer read-only `GET` for dashboards; use `POST` for actions.
- Do not expose global admin operations from page APIs.

### Handler Code

Use `api/handlers.py` for server-side page logic:

```python
async def get_stats(ctx, request):
    # ctx exposes trusted server-side helpers such as:
    # ctx.user, ctx.page_id, ctx.secrets, ctx.logger
    return {
        "open": 12,
        "critical": 3,
    }

async def ack_alert(ctx, request):
    body = await request.json()
    alert_id = body.get("id")
    if not alert_id:
        return {"ok": False, "error": "missing alert id"}
    return {"ok": True, "id": alert_id}
```

Implementation expectations for the runtime:

- Route module loading is controlled by Flocks, not by arbitrary `include_router`.
- Handlers run as trusted local plugins, not as a security sandbox.
- Runtime enforces auth, page ID validation, route validation, request/response size limits, timeout, and structured error reporting.
- Secrets are read server-side through `ctx.secrets` or `get_secret_manager()`; never return secrets to the page.
- Watcher should monitor `api/routes.yaml` and `api/*.py`; API changes should hot-reload without restarting Flocks.
- API runtime errors should be visible in page diagnostics (for example `dist/meta.json` or a dedicated API meta file).

### Call from Page Code

The SDK `api` helper sends the logged-in user's session cookie:

```tsx
const res = await api.get('/api/user-defined-pages/alert-dashboard/api/stats');
```

If the SDK later provides a page helper, prefer:

```tsx
const res = await api.page.get('/stats');
```

Until `api.page` exists, use explicit `/api/user-defined-pages/<pageId>/api/*` paths.

### Other extension paths

| Need | Approach | Call from page |
|------|----------|----------------|
| Page-specific backend data | `api/routes.yaml` + `api/handlers.py` | `/api/user-defined-pages/<pageId>/api/*` |
| External REST API needed by one page | Page handler proxies it server-side | same |
| Local compute / file transform for one page | Page handler | same |
| Multi-step orchestration | Workflow under `~/.flocks/plugins/workflows/<id>/` | `POST /api/workflow/<id>/run` |
| Existing Flocks data | Built-in routes | `api.get/post('/api/...')` |
| Fully custom standalone server | Separate process | Prefer a page API proxy to avoid browser CORS and secret exposure |

### Management APIs (for Rex / scripts)

| Action | Method |
|--------|--------|
| List page API routes | `GET /api/user-defined-pages/<pageId>/api` |
| Call page API | `GET/POST/... /api/user-defined-pages/<pageId>/api/<path>` |
| Reload page API | `POST /api/user-defined-pages/<pageId>/api/reload` |
| Read page detail/build info | `GET /api/user-defined-pages/<pageId>` |

If these endpoints are not implemented yet, treat this section as the target design and implement the backend runtime before promising the feature as available.

### Limitations (tell users when relevant)

- Do not register user routes globally under `/api/custom/...`.
- Page API code is trusted local plugin code, not sandboxed untrusted code.
- Page code may only import `react` and `@flocks/user-defined-page-sdk` — backend logic lives in `api/handlers.py`, not in page TSX.
- Page API routes are page-scoped; deleting the page should remove its backend routes as well.

### Explain to users (when page needs custom data)

**Chinese example**:

> 如果内置 API 不够用，我们按页面专属后端 API 的设计来做：在 `~/.flocks/plugins/user_defined_pages/<pageId>/api/` 下定义 `routes.yaml` 和 `handlers.py`，后端统一暴露到 `/api/user-defined-pages/<pageId>/api/*`。密钥只在服务端读取，不会写进页面代码。

**English example**:

> When built-in APIs are insufficient, use the page-scoped backend API design: define `api/routes.yaml` and `api/handlers.py` under `~/.flocks/plugins/user_defined_pages/<pageId>/`, then expose them through `/api/user-defined-pages/<pageId>/api/*`. Secrets stay server-side.

## Useful APIs

| Action | Method |
|--------|--------|
| List | `GET /api/user-defined-pages?enabledOnly=true` |
| Detail | `GET /api/user-defined-pages/<pageId>` |
| Save source | `PUT /api/user-defined-pages/<pageId>` with `{"sourcePath":"src/Page.tsx","sourceContent":"..."}` |
| Update manifest | `PUT /api/user-defined-pages/<pageId>` with `{"manifest":{"title":"New Title","order":50}}` |
| Rebuild | `POST /api/user-defined-pages/<pageId>/build` |
| Hide from nav | `PUT /api/user-defined-pages/<pageId>` with `{"manifest":{"enabled":false}}` |
| Delete permanently | Remove `~/.flocks/plugins/user_defined_pages/<pageId>/` (see below) |

## Remove or Delete a Page

Always explain both approaches when the user asks how to delete, or proactively mention this in the first introduction and wrap-up.

### Option 1 — Hide from navigation (soft delete, reversible)

Update manifest so the page no longer appears in the left nav, but files are kept:

```bash
curl -s -X PUT http://127.0.0.1:8000/api/user-defined-pages/<pageId> \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer <token>" \
  -d '{"manifest":{"enabled":false}}'
```

Or edit `manifest.json` and set `"enabled": false`. Navigation updates automatically via SSE; **no restart** required.

To restore later, set `"enabled": true` again.

### Option 2 — Delete permanently (hard delete)

Remove the entire page directory under user space:

```bash
rm -rf ~/.flocks/plugins/user_defined_pages/<pageId>
```

Only do this **after confirming** with the user — this cannot be undone (unless they have backups). After deletion, the nav item disappears automatically; no restart required.

**Chinese phrasing example** (adapt to user's language):

> 如果不再需要这个页面，有两种方式：  
> 1. **从导航隐藏**：把 `manifest.json` 里的 `enabled` 设为 `false`，页面文件仍保留，以后可恢复；  
> 2. **彻底删除**：删除目录 `~/.flocks/plugins/user_defined_pages/<pageId>/`，导航标签会消失且无法恢复，请先确认再操作。

**English phrasing example**:

> To remove a page you have two options:  
> 1. **Hide from navigation** — set `"enabled": false` in the manifest (files kept, reversible);  
> 2. **Delete permanently** — remove `~/.flocks/plugins/user_defined_pages/<pageId>/` (irreversible; confirm with the user first).

When the user explicitly asks to delete a page, confirm which option they want before acting.

## Conversation Steps

### Step 1 — Understand needs

Ask about:
- Purpose (dashboard / list / form / screen)
- Navigation title (user's language)
- Suggested `pageId`
- Whether multiple nav pages are needed
- **Data sources** — built-in APIs, page-scoped backend APIs, workflows, or external systems that need server-side proxying

### Step 2 — Create scaffold

After confirming `pageId` and `title`, create the page and report nav name, route, and directory.

### Step 3 — Implement

Iterate on `src/Page.tsx`:
- Match WebUI Tailwind styling
- Use `api` for built-in `/api/*` data when available
- If built-in APIs are insufficient, design or implement page-scoped APIs under `api/routes.yaml` + `api/handlers.py`, then call `/api/user-defined-pages/<pageId>/api/*` from the page
- Tell the user to wait for hot reload after each save

### Step 4 — Wrap up

Before responding, perform a final location check and explicitly confirm that the page files are under `~/.flocks/plugins/user_defined_pages/<pageId>/`, not in the project code directory.

Summarize page ID, nav label, route, data sources used (built-in API / page API / workflow), the verified storage directory, how to keep editing via chat, how to **hide** (`"enabled": false`) or **permanently delete** (remove `~/.flocks/plugins/user_defined_pages/<pageId>/`), and how to add another page or extend data with page-scoped backend APIs.
Also include one concrete backup command and remind the user that restart/upgrade keeps pages because files are stored in user home and startup reconciliation rebuilds missing/old bundles.

## Rex-User Collaboration Loop

Use this loop to ensure Rex and the user co-develop the page with clear ownership and fast feedback.

### Responsibilities

- **User provides**: business goal, fields/metrics, expected interactions, visual style, and acceptance criteria.
- **Rex provides**: page scaffold/files, frontend implementation, optional page-scoped backend API (`api/routes.yaml` + `api/handlers.py`), build/runtime troubleshooting, and final storage verification.

### Iteration cadence (must follow)

For each iteration, Rex should:

1. Restate the current task in one sentence (what will change in this round).
2. Implement only the agreed slice (small increment; avoid large unreviewed rewrites).
3. Tell the user exactly what to verify in WebUI (route, interaction, expected data/result).
4. Wait for user feedback, then continue to the next slice.

### Creation completion checklist

Do not declare "done" until all are true:

- Nav item visible under Home with expected title/icon/order.
- Route works: `/user-defined-pages/<pageId>`.
- Frontend behavior matches user requirements.
- If custom backend is used, page API routes work under `/api/user-defined-pages/<pageId>/api/*`.
- Backup command provided.
- Hide/delete options provided.
- Final location check explicitly confirmed (`~/.flocks/plugins/user_defined_pages/<pageId>/` only).

### If the user is unsure what to provide

Rex should ask only the minimum needed in this order:

1. page purpose
2. title + pageId
3. data sources
4. key interactions
5. visual preference

## Troubleshooting

| Symptom | Action |
|---------|--------|
| Nav item missing | Check `manifest.enabled`; wait for build |
| Blank / error page | Check `GET /api/user-defined-pages/<id>` → `build.error` |
| Build failed | Fix TSX syntax; only import `react` and `@flocks/user-defined-page-sdk` |
| Changes not visible | Confirm file saved under `src/`; try `POST .../build` |
| 401 Unauthorized (WebUI) | User not logged in or session expired — re-login |
| 401 Unauthorized (curl/script) | Add `Authorization: Bearer <server_api_token>`; run `flocks admin generate-api-token` if missing |
| Page `api` calls fail | User must stay logged in; do not put API Token in page source |
| Page API 404 | Confirm `api/routes.yaml` path and page ID; reload page API runtime |
| Page API 500 | Check handler traceback / page API diagnostics; validate handler return shape |
| External API from page fails | Do not call third-party URLs directly from page code — proxy through page-scoped backend |
| Need custom `/api/foo` route | Do not add global routes — use `/api/user-defined-pages/<pageId>/api/foo` |

## Do Not

- Write pages into `webui/` or `flocks/` code directories
- Leave generated user page source, API handlers, assets, or drafts anywhere outside `~/.flocks/plugins/user_defined_pages/<pageId>/`
- Modify files under `dist/`
- Import non-whitelisted npm packages into page code
- Skip `pageId` format validation
- Hardcode `server_api_token` or other secrets in page source (`src/*.tsx`)
- Ask users to paste API tokens into chat
- Register global custom FastAPI routes or write backend logic into `src/Page.tsx`
- Call third-party APIs directly from page code with embedded secrets
- Write page backend files outside `~/.flocks/plugins/user_defined_pages/<pageId>/api/`
