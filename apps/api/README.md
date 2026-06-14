# apps/api — FastAPI + Celery 后端

`apps/api` 是积木建模工具的后端,提供 HTTP API、任务调度、3D 重建流水线与对象存储管理。

## 技术栈

- Python 3.11+
- FastAPI 0.110 + Pydantic 2
- SQLAlchemy 2 (async) + Alembic
- Celery 5 + Redis 7
- PostgreSQL 16
- MinIO (S3 兼容)
- Meshroom (AliceVision) + COLMAP + Open3D (3D 重建,后续接入)
- uv 0.4+ (包管理,workspace 模式)
- Ruff + mypy + pytest (代码质量 / 测试)

## 开发

参见根目录 [README.md](../../README.md) 的"快速开始"一节。简而言之:

```bash
# 1. 准备环境变量 (一次性)
./scripts/bootstrap.sh

# 2. 启动 dev 模式
docker compose \
  -f deploy/docker-compose.yml \
  -f deploy/docker-compose.dev.yml \
  --env-file deploy/.env \
  up
```

后端默认监听 <http://localhost:8000>。

## 目录结构

```
apps/api/
├── src/
│   ├── app/             # FastAPI app factory
│   ├── api/v1/          # 路由 (captures / ar-captures / parametric-blocks / jobs / assets / health)
│   ├── core/            # 配置 / 日志 / 异常
│   ├── db/              # SQLAlchemy 模型 + session
│   ├── models/          # Pydantic schema
│   ├── services/        # 业务逻辑
│   ├── workers/         # Celery 任务
│   ├── pipelines/       # 3D 处理 (meshroom / colmap / cleanup)
│   └── storage/         # MinIO / S3 客户端
├── alembic/             # 数据库迁移
├── tests/               # pytest 套件
├── pyproject.toml
├── uv.lock
├── Dockerfile           # 生产多阶段构建
└── Dockerfile.dev       # 开发模式 (挂载源码 + --reload)
```

## 当前 API 入口

| 路径                                                     | 用途                                                                     |
| -------------------------------------------------------- | ------------------------------------------------------------------------ |
| `POST /api/v1/captures`                                  | 多照片采集, 写 MinIO 原图并派发重建任务                                  |
| `GET /api/v1/captures` / `GET /api/v1/captures/{id}`     | 最近采集列表与采集详情                                                   |
| `GET /api/v1/captures/{id}/images`                       | 为某个采集的原图生成预签名 URL                                           |
| `POST /api/v1/ar-captures`                               | ARCore RGB/depth 识别; 低置信返回 `needs_measurement`                    |
| `POST /api/v1/parametric-blocks`                         | 5 个卡尺值生成参数化 GLB                                                 |
| `GET /api/v1/jobs/{id}` / `GET /api/v1/jobs/{id}/stream` | 任务状态与 SSE 进度                                                      |
| `GET /api/v1/assets/{id}`                                | 3D 产物下载 URL; `Accept: application/json` 返回 JSON, 否则 302 到 MinIO |

MinIO 预签名 URL 由 `S3_PUBLIC_ENDPOINT` 决定浏览器可访问 host。局域网手机调试时把它设成
`http://<LAN-IP>:9000`; 不要使用 `/minio` 路径代理。

具体实现详见 `docs/design.md` 第 5/6/7 节。
