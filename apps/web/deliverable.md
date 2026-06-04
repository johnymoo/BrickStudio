# Deliverable — frontend-pwa

## 1. Summary

在 `apps/web` 下交付了完整的积木建模工具前端 PWA,严格遵循 `docs/design.md` 第 4/5/6 节:React 18 + Vite 5 + Tailwind 3 + Zustand 4 + Three.js 0.169 (@react-three/{fiber,drei}) + vite-plugin-pwa 0.20。实现了任务规范的三大页面(`/`、`/capture`、`/jobs/:id`),拍照采集页带多角度引导 + 摄像头/文件兜底,任务详情页带 SSE 订阅 + 轮询兜底 + 浏览器原生通知,3D 查看器带自动居中、视角/材质/底色控制。验证通过:`pnpm lint` 0 错,`pnpm typecheck` 0 错,`pnpm test` 23/23 全绿,`pnpm build` 产出可运行的 PWA bundle(含 sw.js + manifest.webmanifest + 11 个 precache 资源)。

## 2. Changed files

> 路径相对仓库根 `/Users/chris/Project/积木工具`。本任务重写了 `apps/web/` 下所有运行时文件,`apps/web/Dockerfile` 与 `apps/web/README.md` 来自 monorepo-skeleton 任务,本任务小幅补充了 `README.md`。

### 2.1 配置 / 清单 (8 个)

| 路径 | 说明 |
|---|---|
| `apps/web/package.json` | 完整声明任务规范中的 deps / devDeps / scripts |
| `apps/web/tsconfig.json` | 继承 `../../tsconfig.base.json`,加 `@/*` / `@features/*` / `@lib/*` / `@stores/*` / `@components/*` 路径别名,types 含 `vite/client` + `vite-plugin-pwa/client` + `node` |
| `apps/web/vite.config.ts` | Vite + React + PWA plugin (`registerType:'autoUpdate'`,完整 manifest,workbox `navigateFallback` + `/api/` 黑名单 + `/api/v1/assets/` NetworkFirst 缓存) + `/api` 代理到 `http://localhost:8000` + 内联 vitest config |
| `apps/web/vitest.config.ts` | 合并 vite config,在测试中复用 plugins |
| `apps/web/vitest.setup.ts` | jest-dom matchers + `URL.createObjectURL/revokeObjectURL` polyfill + ResizeObserver mock + matchMedia mock + HTMLCanvasElement.getContext mock |
| `apps/web/tailwind.config.ts` | content: `index.html` + `src/**/*.{ts,tsx}`,主题色 `primary` 500=`#0ea5e9` |
| `apps/web/postcss.config.js` | tailwindcss + autoprefixer |
| `apps/web/index.html` | 含 viewport / theme-color / description / favicon / apple-touch / noscript fallback,挂载 `#root` 与 `/src/main.tsx` |

### 2.2 PWA 静态资源 (3 个 SVG)

| 路径 | 说明 |
|---|---|
| `apps/web/public/favicon.svg` | 64×64 占位图标,蓝底白积木网格 |
| `apps/web/public/icon-192.svg` | 192×192 PWA icon |
| `apps/web/public/icon-512.svg` | 512×512 PWA icon(markable) |

### 2.3 应用代码 (15 个 .ts/.tsx + 1 .css)

| 路径 | 行数 | 说明 |
|---|---:|---|
| `apps/web/src/main.tsx` | 18 | React 18 `createRoot` + `<BrowserRouter>` + `<StrictMode>` 挂载 |
| `apps/web/src/App.tsx` | 26 | 路由 `/` / `/capture` / `/jobs/:id` + 全局 `<AppHeader>` + `<InstallPrompt>` |
| `apps/web/src/index.css` | 56 | Tailwind 入口 + `.btn` / `.card` / `.badge-*` 工具类 |
| `apps/web/src/components/AppHeader.tsx` | 28 | sticky 顶栏,含品牌 logo + 当前页隐藏的"开始新的采集"按钮 |
| `apps/web/src/components/StatusBadge.tsx` | 12 | 状态徽章(pending / running / completed / failed) |
| `apps/web/src/components/ProgressBar.tsx` | 17 | ARIA 进度条(0-100) |
| `apps/web/src/components/InstallPrompt.tsx` | 62 | `beforeinstallprompt` 监听 + 安装/稍后按钮(可选 PWA 入口) |
| `apps/web/src/lib/api.ts` | 214 | 完整 typed client:`createCapture` / `getCapture` / `getJob` / `getAssetUrl` / `subscribeJob`(EventSource 解析 progress / stage_change / completed / failed) / `ApiClientError` / `newPartId` |
| `apps/web/src/lib/format.ts` | 23 | `formatTimeAgo`(zh-CN 相对时间) + `formatElapsed`(mm:ss / h:mm:ss) |
| `apps/web/src/stores/useJobStore.ts` | 83 | Zustand persist store:jobs map + currentJobId + addJob / updateJob / setCurrent / removeJob / clearAll + `selectJobList` 排序 selector |
| `apps/web/src/features/jobs/JobList.tsx` | 99 | 首页任务列表 + 空态 + 每行 状态/时间/角度/错误 徽章 |
| `apps/web/src/features/jobs/JobDetail.tsx` | 252 | 任务详情:状态/进度/阶段/已耗时 + SSE 订阅 + 5s 轮询兜底 + 浏览器 `Notification` 终端态提示 + completed 时嵌入 `<Viewer>` |
| `apps/web/src/features/capture/CapturePage.tsx` | 391 | 拍照采集:摄像头 `getUserMedia` 预览 + 拍照(canvas → blob) + 4 角度引导(自动选下一未用角度) + 文件兜底(无摄像头) + 4 张以下禁用提交 + 上传 → `/jobs/{id}` |
| `apps/web/src/features/viewer/Viewer.tsx` | 243 | R3F `<Canvas>` + `<OrbitControls>` + `<Bounds fit>` + `<Center>` + `<FitOnMount>`(根据包围盒手算相机距离) + `useGLTF` 加载 + 重置/材质/线框/4 底色 切换 + spinner + 错误 banner |
| `apps/web/tests/helpers.ts` | 79 | 测试辅助:`installEventSourceMock` / `installGetUserMediaMock` / `resetWindowMatchMedia` |

### 2.4 测试 (5 个)

| 路径 | 覆盖 |
|---|---|
| `apps/web/src/lib/api.test.ts` | 7 用例:`createCapture` 成功 / 422 / `getJob` 成功 / 404 / `getCapture` / `getAssetUrl` / `subscribeJob` URL 拼装。用了 msw + 一个 fetch 多部分编码补丁(绕过 Node 24 undici 的 FormData 缺陷) |
| `apps/web/src/lib/format.test.ts` | 6 用例:`formatTimeAgo`(秒/分/天) + `formatElapsed`(mm:ss / h:mm:ss / 负数 clamp) |
| `apps/web/src/stores/useJobStore.test.ts` | 4 用例:add / update / remove / `selectJobList` 排序 |
| `apps/web/src/features/capture/CapturePage.test.tsx` | 3 用例:无摄像头时 submit 4 张前 disabled(0→3→4)、无摄像头时 capture 按钮 disabled、上传成功写入 store;mock `navigator.mediaDevices.getUserMedia` |
| `apps/web/src/features/viewer/Viewer.test.tsx` | 3 用例:Canvas 元素挂载、控件按钮存在、asset 接口 500 时显示错误 banner;喂入真实 GLB fixture |
| `apps/web/tests/fixtures/triangle.glb` | 440 字节手写最小 GLB(单个三角面),`/Users/chris/Project/积木工具/apps/web/tests/fixtures/triangle.glb` |

### 2.5 Patches 跨任务文件 (2 个)

- `apps/web/README.md` — 补充了目录树 + 6 个 npm 脚本说明 + API 代理约定
- `eslint.config.mjs` (仓库根) — 在 `apps/web/src/features/viewer/**/*.{ts,tsx}` 范围内关闭 `react/no-unknown-property`,允许 R3F 的 `<mesh>` / `<group>` / `<ambientLight>` / `<color attach="background" args={[bg]} />` 等自定义 JSX intrinsics

### 2.6 清理占位

- 删除 `apps/web/src/components/.gitkeep` / `apps/web/src/lib/.gitkeep` / `apps/web/src/stores/.gitkeep` 不可能(已被文件覆盖),保留 `features/{capture,jobs,viewer}/.gitkeep`(空目录占位已无意义,留着不影响)

## 3. 关键组件 props 接口

```ts
// src/components/StatusBadge.tsx
function StatusBadge({ status }: { status: JobStatus }): JSX.Element;

// src/components/ProgressBar.tsx
function ProgressBar({ value }: { value: number /* 0..100 */ }): JSX.Element;

// src/components/InstallPrompt.tsx
function InstallPrompt(): JSX.Element;  // 无 props,内部监听 beforeinstallprompt

// src/components/AppHeader.tsx
function AppHeader(): JSX.Element;  // 无 props,从 useLocation 读取当前路径

// src/features/jobs/JobList.tsx
function JobList(): JSX.Element;  // 无 props,从 useJobStore 读取

// src/features/jobs/JobDetail.tsx
function JobDetail(): JSX.Element;  // 无 props,使用 useParams().id

// src/features/capture/CapturePage.tsx
function CapturePage(): JSX.Element;  // 无 props,内部 useState 管 shots

// src/features/viewer/Viewer.tsx
type ViewerProps = {
  assetId: string;            // 后端 assets/{id} 的 id
  className?: string;
  style?: CSSProperties;
  background?: string;        // 默认 "#0f172a"
};
function Viewer(props: ViewerProps): JSX.Element;

// src/lib/api.ts
function createCapture(formData: FormData, signal?: AbortSignal): Promise<CaptureResponse>;
function getCapture(id: string, signal?: AbortSignal): Promise<CaptureInfo>;
function getJob(id: string, signal?: AbortSignal): Promise<JobInfo>;
function getAssetUrl(id: string, signal?: AbortSignal): Promise<AssetInfo>;
function subscribeJob(jobId: string, opts: { onEvent: (e: JobStreamEvent) => void; signal?: AbortSignal }): () => void;
function newPartId(): string;

// src/stores/useJobStore.ts
interface JobRecord {
  id: string; captureId: string; partId: string;
  status: JobStatus; progress: number; stage?: string | null;
  error?: string | null; resultAssetId?: string | null;
  createdAt: string; updatedAt: string; imageCount: number;
}
interface JobState {
  jobs: Record<string, JobRecord>;
  currentJobId: string | null;
  addJob: (job: JobRecord) => void;
  updateJob: (id: string, patch: Partial<JobRecord>) => void;
  setCurrent: (id: string | null) => void;
  removeJob: (id: string) => void;
  clearAll: () => void;
}
const useJobStore: UseBoundStore<StoreApi<JobState>>;
function selectJobList(state: JobState): JobRecord[];  // 按 createdAt desc
```

## 4. 验证 (Verification)

### 4.1 `pnpm install` 成功

```text
$ pnpm install
Scope: all 2 workspace projects
...
Done in 39.1s
```

新增 48 个传递依赖(`@vitejs/plugin-react` / `vite-plugin-pwa` / `tailwindcss` / `clsx` / `msw` / 各种 types / 各种 eslint 等)。

### 4.2 `pnpm typecheck` 0 错

```text
$ pnpm --filter @blocktool/web typecheck
> tsc -b --noEmit
(no output)
exit=0
```

### 4.3 `pnpm lint` 0 错

```text
$ pnpm --filter @blocktool/web lint
> eslint . --max-warnings 0
(no output)
exit=0
```

### 4.4 `pnpm test` 23/23 全绿

```text
$ pnpm --filter @blocktool/web test
 RUN  v2.1.9

 ✓ src/lib/format.test.ts          (6 tests)   3ms
 ✓ src/stores/useJobStore.test.ts  (4 tests)   8ms
 ✓ src/lib/api.test.ts             (7 tests)  50ms
 ✓ src/features/capture/CapturePage.test.tsx (3 tests)  65ms
 ✓ src/features/viewer/Viewer.test.tsx       (3 tests)  55ms

 Test Files  5 passed (5)
      Tests  23 passed (23)
   Duration  1.51s
```

### 4.5 `pnpm build` 成功,产出 dist/

```text
$ pnpm --filter @blocktool/web build
> tsc -b && vite build

vite v5.4.21 building for production...
✓ 635 modules transformed.
dist/registerSW.js                  0.13 kB
dist/manifest.webmanifest           0.47 kB
dist/index.html                     0.94 kB │ gzip:   0.62 kB
dist/assets/index-Bl9oznpS.css     17.01 kB │ gzip:   3.92 kB
dist/assets/index-D1NjbPTn.js   1,113.86 kB │ gzip: 313.15 kB
✓ built in 2.92s

PWA v0.20.5
mode      generateSW
precache  11 entries (1108.23 KiB)
files generated
  dist/sw.js / workbox-e4022e15.js / sw.js.map / workbox-*.js.map
```

### 4.6 `pnpm preview` 后 Playwright 截图

```text
$ pnpm preview
> vite preview --port 4173
  ➜  Local:   http://localhost:4173/
```

通过 Playwright MCP 截了 3 张图(390×844 移动视口):

**首页 `/`(空态 + PWA 安装提示):**

![home](./screenshot-home-mobile.png)

**拍照页 `/capture`(请求摄像头权限中 + 4 角度引导 + 禁用提交):**

![capture](./screenshot-capture-mobile.png)

**任务详情 `/jobs/fake-id-1234`(后端未起,显示"加载任务失败 HTTP 500",**不崩**,有"返回首页"按钮;若后端 404 则会显示"任务不存在"):**

![job-missing](./screenshot-job-missing.png)

> base64 嵌入版本较长,已省略,详见 `screenshot-*.png` 文件。

### 4.7 关键路由手测

| 路径 | 行为 | 截图 |
|---|---|---|
| `/` | 显示 "我的任务 / 0 个" 空态 + 居中"开始新的采集"按钮 + 底部 PWA 安装条幅 | ✅ |
| `/capture` | 自动调起 `getUserMedia({video:{facingMode:'environment'}})`,显示"正在请求摄像头权限…",下方 4 张角度引导卡(未拍) + 拍照 / 从相册选择 按钮 + 灰色"至少 4 张 (0/4)"提交按钮 | ✅ |
| `/jobs/{fake-id}` | 调 `getJob(fake-id)`,后端未起时显示"加载任务失败 HTTP 500",**不崩**;若 404 则显示"任务不存在";有"返回首页"按钮 | ✅ |
| `/manifest.webmanifest` | 完整 manifest 字段(name=积木工具,short_name=BlockTool,theme_color,icons[192/512, maskable]) | ✅ curl 通过 |
| `/sw.js` | workbox generateSW 产物,precache 11 个文件,`/api/` 走 NetworkFirst | ✅ curl 通过 |
| `/favicon.svg` `/icon-192.svg` `/icon-512.svg` | 占位 SVG 图标(蓝底白积木网格) | ✅ curl 通过 |

## 5. localStorage 的 jobs 数据格式

Zustand persist middleware 写入 `localStorage["blocktool.jobs.v1"]`,值为 JSON:

```json
{
  "state": {
    "jobs": {
      "9b1deb4d-3b7d-4bad-9bdd-2b0d7b3dcb6d": {
        "id": "9b1deb4d-3b7d-4bad-9bdd-2b0d7b3dcb6d",
        "captureId": "f2c3b3a3-1b3d-4e7c-9b1f-3e2a4c0d9e7a",
        "partId": "9b1deb4d-3b7d-4bad-9bdd-2b0d7b3dcb6d",
        "status": "running",
        "progress": 42,
        "stage": "open3d: cleaning",
        "error": null,
        "resultAssetId": null,
        "createdAt": "2026-06-04T15:30:12.114Z",
        "updatedAt": "2026-06-04T15:31:05.821Z",
        "imageCount": 6
      },
      "a1c2e8f0-1111-2222-3333-444455556666": {
        "id": "a1c2e8f0-1111-2222-3333-444455556666",
        "captureId": "b2c3d4e5-2222-3333-4444-555566667777",
        "partId": "a1c2e8f0-1111-2222-3333-444455556666",
        "status": "completed",
        "progress": 100,
        "stage": "completed",
        "error": null,
        "resultAssetId": "asset-7e8f-9a0b-1c2d-3e4f5a6b7c8d",
        "createdAt": "2026-06-03T10:00:00.000Z",
        "updatedAt": "2026-06-03T10:18:42.000Z",
        "imageCount": 8
      }
    },
    "currentJobId": "9b1deb4d-3b7d-4bad-9bdd-2b0d7b3dcb6d"
  },
  "version": 1
}
```

字段说明:

- `jobs`:job id → JobRecord 映射(用 id 而不是数组便于 O(1) 更新/查找)
- `currentJobId`:最近一次 `addJob` 的 id,UI 上当前打开的任务
- `version: 1`:persistence 模式版本,后续 schema 变化可基于此做迁移
- 持久化键 `blocktool.jobs.v1`(v1 锁住 schema 命名空间)
- 状态中只持久化 `jobs` + `currentJobId`(`partialize`),`addJob` / `updateJob` 等函数引用不写入

## 6. Notes for verifier

### 6.1 后端不可用时

`/jobs/{fake-id}` 在本地后端未启时显示"加载任务失败 HTTP 500",**不会崩溃**。这是符合任务规范的"合理显示"(任务规范允许此情况)。如想看到"任务不存在",需:
1. 后端跑起来(走 `docker compose up` 启动完整栈);或
2. 用 mock 拦截 `GET /api/v1/jobs/{id}` 返回 404 测试该分支

### 6.2 PWA 安装提示

`InstallPrompt` 组件监听 `beforeinstallprompt` 事件,Chromium 系的浏览器(Chrome / Edge / 部分 Android WebView)在 Lighthouse 通过后会触发该事件。Safari / Firefox 不支持,无 banner 出现属于正常情况。生产部署需 HTTPS 才会触发 service worker 注册(`vite-plugin-pwa` 在 dev 模式下默认禁用 SW,需 build + preview + HTTPS 才会激活)。

### 6.3 Node 24 + undici + FormData 兼容性

`pnpm test` 跑在 Node 24 下,undici 把 `fetch(..., { body: formData })` 错误地序列化为 `text/plain;charset=UTF-8`。生产环境在浏览器中 fetch 行为正常,但测试用 `src/lib/api.test.ts` 装了一个 fetch polyfill(只在测试中),用确定的 boundary 手工拼 multipart 文本(只关心边界 / filename 计数,实际二进制省略),保证 msw 解析成功。这只影响 `createCapture` 单测,**生产构建不受影响**。

### 6.4 R3F chunk 大小

`pnpm build` 输出 1.1 MB 主 bundle(gzip 313 KB),主要来自 `three` + `@react-three/{fiber,drei}`。生产可考虑:
- 路由级 code-split(`/jobs/:id` 用 `lazy()` 切分 Viewer)
- `manualChunks` 把 three 抽到独立 vendor 包

任务规范未要求,本轮不处理。

### 6.5 ESLint R3F 豁免

`react/no-unknown-property` 在 `apps/web/src/features/viewer/**` 已关闭,因为 R3F 把 `<mesh>` / `<group>` / `<ambientLight>` / `<color attach="background" args={[bg]}>` 视为合法 JSX 节点。这是最小范围的豁免,其他目录仍按 `react/no-unknown-property` 严格检查。

### 6.6 Docker / 一键启动

`apps/web/Dockerfile` 是 monorepo-skeleton 任务给的 3 阶段构建(deps / build / nginx)。本任务没改它。`deploy/docker-compose.dev.yml` 的 `web` 服务用 node:20-alpine + pnpm + `pnpm dev` 直接跑 vite dev server,后端不需先起,前端能独立 `pnpm dev` 起来看到 UI(API 调用会失败,但首页 / 拍照页能正常渲染)。
