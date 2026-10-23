# Flocks WebUI

Flocks AI Native SecOps Platform 的 Web 用户界面。

## 快速开始

### 安装依赖

```bash
bun install
```

如果你本地已经使用 Node.js / npm，也可以继续执行 `npm install`。

### 开发模式

```bash
bun run dev
```

访问 http://localhost:5173

### 构建生产版本

```bash
bun run build
```

### 预览生产版本

```bash
bun run preview
```

## 技术栈

- **React 19** - UI 框架
- **TypeScript 5.9** - 类型安全
- **Vite 7** - 构建工具
- **TailwindCSS 3** - CSS 框架
- **React Router 7** - 路由管理
- **Zustand** - 状态管理
- **Axios** - HTTP 客户端
- **@xyflow/react** - 工作流 DAG 可视化
- **Lucide React** - 图标库

## 项目结构

```
src/
├── api/           # API 客户端
├── components/    # 公共组件
├── pages/         # 页面组件
├── hooks/         # 自定义 Hooks
├── stores/        # 状态管理
├── types/         # TypeScript 类型
├── utils/         # 工具函数
├── constants/     # 常量
├── config/        # 配置
└── styles/        # 样式文件
```

## 开发规范

- 使用 TypeScript 进行开发
- 遵循 ESLint 规则
- 使用 Prettier 格式化代码
- 组件使用函数式组件 + Hooks
- 样式使用 TailwindCSS

## API 代理配置

开发环境下，API 请求默认代理到 `http://127.0.0.1:8000`，也可通过 `VITE_API_BASE_URL` 覆盖：

- `/api/*` -> `${VITE_API_BASE_URL:-http://127.0.0.1:8000}/api/*`
- `/event` -> `${VITE_API_BASE_URL:-http://127.0.0.1:8000}/event`

## 环境变量

复制 `.env.example` 为 `.env` 并配置：

```bash
VITE_API_BASE_URL=http://127.0.0.1:8000
VITE_WS_BASE_URL=ws://127.0.0.1:8000
```

如果后端改为 `9000`，对应设置为：

```bash
VITE_API_BASE_URL=http://127.0.0.1:9000
VITE_WS_BASE_URL=ws://127.0.0.1:9000
```
