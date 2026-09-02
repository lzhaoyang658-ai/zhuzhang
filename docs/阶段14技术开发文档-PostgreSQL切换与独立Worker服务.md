# 阶段 14 技术开发文档：PostgreSQL 切换与独立 Worker 服务

## 1. 本阶段完成内容

- 新增 `backend/database_cutover.py`，用于把当前 SQLite 业务数据一次性复制到空 PostgreSQL，并逐表校验行数。
- 新增 `backend/app/worker_service.py` 和 `backend/worker_service_bootstrap.py`，将报价解析与档案导出从 API 进程移到一个独立常驻服务。
- Worker 提供 `/health` 存活探针和 `/ready` 就绪探针；两条队列分别运行在线程中，一条队列的异常不会终止另一条。
- 新增 `MAINTENANCE_MODE`。数据库切换窗口开启后，API 的 POST、PATCH、PUT、DELETE 统一返回 503 和 `Retry-After`，防止复制期间继续产生新写入。
- 报价与档案仍通过数据库租约领取，保持现有自动重试、死信与心跳模型不变。

## 2. 为什么暂不直接购买 RDS

火山引擎云数据库 PostgreSQL 当前为本地盘高可用架构，创建时需要一个 Primary 和一个 Secondary 节点，费用按两个节点的规格与存储相加计算。按量实例虽然可以随时释放，但购买前要求账户现金余额与代金券合计不低于 100 元。当前项目预算上限为 0 至 100 元，因此本阶段不自动创建付费数据库资源。

官方依据：

- [按量计费购买要求](https://www.volcengine.com/docs/6438/79231)
- [PostgreSQL 计费项与双节点计算方式](https://www.volcengine.com/docs/6438/79224)
- [CreateDBInstance 的主备节点与 VPC 参数](https://www.volcengine.com/docs/6438/1159933)
- [PostgreSQL 白名单默认拒绝规则](https://www.volcengine.com/docs/6438/1257674)

实际单价和最低可售规格仍以创建实例页面为准，不能只用文档示例估算生产月账单。

## 3. 目标部署拓扑

```text
公网 HTTPS
    |
frontend-app
    |
backend-app ---------- 私有对象存储
    |                    |
    +---- PostgreSQL ----+
              |
      worker-app
      quote + export
```

- API 和 Worker 接入与 PostgreSQL 相同的 VPC、子网和安全组。
- PostgreSQL 白名单只加入该 VPC 网段或精确的运行网段，禁止使用 `0.0.0.0/0`。
- Worker 不创建公网 API Gateway 触发器，只保留平台内部健康探针。
- 小规模阶段一个 0.5C/1G Worker 同时消费两条队列；任务量增长后可用 `WORKER_QUEUES` 拆成两个函数。

## 4. 数据切换步骤

1. 保留当前 SQLite→TOS 自动备份，并手动确认最新备份可恢复。
2. 创建空 PostgreSQL 实例、应用数据库和最小权限账号。
3. 给 API/Worker 接入相同 VPC，并验证 TCP 5432 连通。
4. 把 API 的 `MAINTENANCE_MODE` 设置为 `true`，确认写请求返回 `MAINTENANCE_MODE`。
5. 从最新 SQLite 文件执行：

```bash
cd backend
SOURCE_DATABASE_URL=sqlite:///./data/app.db \
DATABASE_URL='postgresql+psycopg://app_user:***@private-host:5432/renovation_budget' \
.venv/bin/python database_cutover.py
```

6. 工具先对目标库执行 `alembic upgrade head`，目标业务表非空则立即停止；复制在目标事务内完成，最后逐表比较源/目标行数。
7. 把 API 和 Worker 的 `DATABASE_URL` 切到 PostgreSQL；API 设置 `QUOTE_EXECUTION_MODE=worker`、`EXPORT_EXECUTION_MODE=worker`、`HEALTH_REQUIRE_WORKERS=true`。
8. Worker 设置 `WORKER_REQUIRE_POSTGRESQL=true`，确认 `/ready` 为 200 且 API `/health/ready` 两条队列都有有效心跳。
9. 关闭 `MAINTENANCE_MODE`，完成登录、项目总览、创建增项、报价任务与档案任务的端到端验收。
10. SQLite 文件只作为切换前归档保留，不再作为线上活动数据库。

## 5. Worker 配置

```env
DATABASE_URL=postgresql+psycopg://...
WORKER_QUEUES=quote,export
WORKER_REQUIRE_POSTGRESQL=true
WORKER_FAILURE_BACKOFF_SECONDS=5
WORKER_SHUTDOWN_SECONDS=10
QUOTE_EXECUTION_MODE=worker
EXPORT_EXECUTION_MODE=worker
```

veFaaS 启动命令：

```bash
python worker_service_bootstrap.py
```

生产 Worker 还需复用 API 已有的 TOS、通义千问和制品存储环境变量。数据库密码、TOS 密钥和模型 Key 均只通过部署 Secret 注入。

## 6. 已完成验收

- 全量后端自动化：40 项通过。
- SQLite→空库复制、行数校验和非空目标拒绝测试通过。
- 双队列 Worker 真实进程启动成功，`/health` 返回 200，`/ready` 返回 200。
- `worker_heartbeats` 中同时出现 quote/export 两个 idle 心跳。
- SIGINT 下 Uvicorn lifespan 正常结束，两条 Worker 线程完成优雅停机。

## 7. 尚未执行的付费步骤

- 创建火山 RDS PostgreSQL 实例。
- 配置 VPC、子网、安全组与 PostgreSQL 白名单。
- 生产数据复制、API 数据库切换和独立 Worker 函数发布。
- 在真实 PostgreSQL 上执行并发领取、强制终止、租约回收与连接池容量验收。

这些步骤需要先确认控制台显示的实例价格及可接受的持续月费用。
