# apps/web — 移动 Web PWA (React + Vite + Three.js)

`apps/web` 是积木建模工具的前端 PWA,负责手机端拍照采集、任务状态展示和 3D 模型查看。

## 技术栈

- React 18 + TypeScript 5
- Vite 5 + vite-plugin-pwa
- TailwindCSS 3
- Zustand 4 (状态管理)
- Three.js 0.169 + @react-three/fiber + @react-three/drei (3D 查看器)
- Vitest (单测)

## 开发

参见根目录 [README.md](../../README.md) 的"快速开始"一节。简而言之:

```bash
# 1. 准备环境变量 (一次性)
./scripts/bootstrap.sh

# 2. 启动 dev 模式 (前端 + 后端 + 依赖,带 HMR)
docker compose \
  -f deploy/docker-compose.yml \
  -f deploy/docker-compose.dev.yml \
  --env-file deploy/.env \
  up
```

然后访问 <http://localhost:5173>。

## 目录结构

```
apps/web/
├── src/
│   ├── features/        # 按业务功能划分
│   │   ├── capture/     # 拍照采集 (CapturePage + tests)
│   │   ├── captures/    # 采集详情, 显示 needs_measurement 指引和原图
│   │   ├── parametric/  # 4 步卡尺参数化建模 wizard
│   │   ├── viewer/      # 3D 查看器 (Viewer + tests)
│   │   └── jobs/        # 任务状态 (JobList + JobDetail)
│   ├── components/      # 通用 UI 组件 (AppHeader / StatusBadge / ProgressBar / InstallPrompt)
│   ├── lib/             # API client (api.ts) + 工具 (format.ts) + tests
│   ├── stores/          # zustand stores (useJobStore + tests)
│   ├── App.tsx          # 路由 (/, /parametric, /jobs/:id, /captures/:id)
│   ├── main.tsx         # 启动 React + BrowserRouter
│   └── index.css        # Tailwind + 组件 utility class
├── public/              # PWA 静态资源 (favicon.svg / icon-192.svg / icon-512.svg)
├── tests/               # 跨特性 fixtures (triangle.glb) + helpers
├── package.json
├── tsconfig.json        # 继承 ../../tsconfig.base.json
├── vite.config.ts       # PWA plugin + /api → :8000 代理 + vitest config
├── tailwind.config.ts
├── postcss.config.js
├── vitest.config.ts
├── vitest.setup.ts      # jsdom polyfills (URL.createObjectURL / matchMedia / ResizeObserver)
└── Dockerfile
```

## 命令

| 命令 | 作用 |
|---|---|
| `pnpm dev` | 启动 Vite dev server (5173) |
| `pnpm build` | tsc -b + vite build → `dist/` |
| `pnpm preview` | vite preview,跑构建产物 (4173) |
| `pnpm lint` | ESLint flat config (--max-warnings 0) |
| `pnpm typecheck` | tsc -b --noEmit (0 错) |
| `pnpm test` | vitest run (23 用例) |

## API 约定

所有路径都走 `/api/v1/...`,Vite dev server 把它代理到 `http://localhost:8000`。
生产环境由 Caddy 转发(`/api/*` → `api:8000`)。

前端 `request()` 默认发送 `Accept: application/json`。因此 `getAssetUrl()` 会从
`GET /api/v1/assets/{id}` 拿 JSON envelope, 再把其中的 MinIO 预签名 URL 交给
Three.js loader。直接在浏览器打开 asset endpoint 仍可走 302 下载。

首页数据来源有两类:
- 本地 `useJobStore` (`blocktool.jobs.v1`) 保存当前浏览器提交的 job, 包括 `/parametric` 生成的 job。
- 后端 `GET /api/v1/captures?limit=20` 补充显示 AR 识别失败且需要测量的采集, 点击进入 `/captures/:id` 查看原图和 fallback 指引。
