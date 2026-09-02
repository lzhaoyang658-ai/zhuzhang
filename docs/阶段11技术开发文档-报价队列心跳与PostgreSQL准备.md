# 阶段 11 技术开发文档：报价队列、Worker 心跳与 PostgreSQL 准备

## 1. 阶段目标

本阶段把报价解析从“API 进程内线程”扩展为可独立部署的持久任务 Worker，并为报价解析与项目档案两类后台任务建立统一心跳和健康视图。同时补齐 PostgreSQL 驱动与连接池配置，使现有单机开发方案具备迁往生产多实例部署的代码基础。

本阶段不改变报价人工校对流程，也不新增模型供应商。普通表格、文本 PDF、扫描件的既有解析策略保持不变。

## 2. 报价任务状态机

内部状态如下：

```text
queued -> running -> succeeded
   ^         |
   |         +-> queued（指数退避，自动重试）
   |         +-> dead_letter（达到最大次数）
   |
   +------------- 人工重新排队
```

- `queued`：等待 Worker；`next_attempt_at` 可表示尚未到期的延迟重试。
- `running`：任务已被 `lease_owner` 领取，`lease_expires_at` 是异常恢复边界。
- `succeeded`：解析草稿已写入，等待用户人工校对。
- `dead_letter`：达到重试上限，需要人工处理。现有前端接口仍返回兼容状态 `failed`，不引入界面回归。

默认最多尝试三次。失败等待时间为 `QUOTE_RETRY_BASE_SECONDS * 2^(attempt-1)`。Worker 崩溃或失联后，租约过期的运行任务可由其他 Worker 重新领取；条件更新防止同一任务被两个正常 Worker 同时成功领取。

## 3. 独立进程与本地兼容

开发环境默认：

```env
QUOTE_EXECUTION_MODE=embedded
```

API 继续用内嵌执行器处理任务，便于一条命令启动。生产环境配置为：

```env
QUOTE_EXECUTION_MODE=worker
```

并单独运行：

```bash
cd backend
source .venv/bin/activate
python quote_worker.py
```

`--once` 参数用于部署探针、验收脚本或一次性消费测试。

## 4. Worker 心跳与健康检查

`worker_heartbeats` 保存 Worker 身份、队列、空闲或忙碌状态、当前任务、累计处理数、累计失败数和最后心跳时间。执行耗时任务时，后台脉冲线程会定期刷新 `last_seen_at`；任务结束后 Worker 回到 `idle`。

健康接口分层：

- `GET /health`：仅做 API 存活检查，避免后台依赖短暂波动导致容器反复重启。
- `GET /health/ready`：检查数据库，并在 `HEALTH_REQUIRE_WORKERS=true` 时要求所有配置为 `worker` 的队列存在有效心跳。
- `GET /api/v1/task-health`：需要登录，仅汇总当前用户有权访问项目的报价和档案任务，不暴露其他项目数量。

任务健康视图包括有效 Worker 数、忙碌 Worker 数、排队数、延迟重试数、运行数、死信数和最早排队时间。过期七天以上的心跳在服务启动时清理。

## 5. PostgreSQL 生产准备

依赖新增 `psycopg[binary]`，URL 使用 SQLAlchemy 的 Psycopg 3 方言：

```env
DATABASE_URL=postgresql+psycopg://app_user:password@postgres:5432/renovation_budget
```

数据库引擎统一开启连接存活预检，并对非 SQLite 数据库开放以下配置：

- `DATABASE_POOL_SIZE`
- `DATABASE_MAX_OVERFLOW`
- `DATABASE_POOL_RECYCLE_SECONDS`
- `DATABASE_CONNECT_TIMEOUT_SECONDS`

密码只应由部署平台 Secret 注入。上线前需要在真实 PostgreSQL 环境执行 `alembic upgrade head`，并完成多 Worker 并发领取、强制终止 Worker、租约回收和连接容量压测。开发机的驱动加载验证不能替代目标环境验收。

## 6. 数据库迁移

迁移版本：`e83b4d9a6f12`

新增报价任务字段：

- `lease_owner`
- `lease_expires_at`
- `next_attempt_at`

新增 `worker_heartbeats` 表及队列、最后心跳索引。迁移是增量操作，不删除项目、报价或档案数据。

## 7. 配置清单

```env
QUOTE_EXECUTION_MODE=worker
QUOTE_JOB_LEASE_SECONDS=3600
QUOTE_JOB_MAX_ATTEMPTS=3
QUOTE_RETRY_BASE_SECONDS=30
QUOTE_WORKER_POLL_SECONDS=2
WORKER_HEARTBEAT_SECONDS=5
WORKER_HEARTBEAT_STALE_SECONDS=20
HEALTH_REQUIRE_WORKERS=true
```

租约应显著长于单份最大报价解析时间；心跳过期阈值应至少是心跳间隔的三倍，避免短暂调度抖动导致假告警。

## 8. 验收范围

自动化测试覆盖：

- API 存活、就绪与登录态任务健康接口。
- 报价租约领取、过期回收、自动重试、死信和人工重新排队。
- Worker 心跳计数与项目范围隔离。
- PostgreSQL URL 的连接池和超时参数。
- 原有预算、风险、通知、权限、导出和对象存储回归。

此外使用隔离 SQLite 数据库启动真实 API 与 `quote_worker.py --once`，完成报价上传、独立进程领取、解析成功和心跳落库。生产 PostgreSQL 的网络、备份、主从和云厂商连接上限不在本地验收范围内。
