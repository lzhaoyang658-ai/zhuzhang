# 阶段 12 技术开发文档：火山引擎临时 HTTPS 部署

## 1. 阶段目标

本阶段将 Next.js 前端和 FastAPI 后端分别发布到火山引擎 veFaaS，通过 API 网关提供临时 HTTPS 公网地址，完成 QQ SMTP、安全会话和前后端代理的真实环境验收。

此环境的目的是个人联调和小范围验收，不是最终多实例生产架构。

> 开源脱敏说明：本文是 2026-08-31 的历史验收复盘。公开版不提供仍可能有效的访问入口、资源 ID、桶名、网关名和 IAM 标识，也不保证该环境继续运行。

## 2. 云端资源

- 后端应用：`<backend-app>`，应用和函数 ID 已脱敏。
- 前端应用：`<frontend-app>`，应用和函数 ID 已脱敏。
- 网关：`<api-gateway>`，两个应用使用独立 Service/Route。
- 私有桶：`<private-bucket>`，北京地域、私有访问、开启版本控制和 TOS 服务端加密。

访问地址：

```text
Frontend <historical-frontend-url-redacted>
Backend  <historical-backend-url-redacted>
```

## 3. 运行规格与费用边界

后端使用 1024 MiB、500 milli-CPU，最小和最大实例都为 1。这样可以避免当前本地 SQLite 因缩容到零频繁重建，也将本阶段的保留实例成本限制在个人预算内。

前端使用 1024 MiB、500 milli-CPU，最小实例 0、最大实例 1，按需冷启动以节省预算。没有开通 Token Plan，也不依赖 Token Plan 完成部署。

## 4. 后端发布适配

- 运行时：`native-python3.12/v1`。
- 构建：`pip install -r requirements.txt -t .`。
- 启动：`python production_bootstrap.py`。
- 冷启动前创建 SQLite 父目录，再执行 Alembic 迁移。
- 报价解析和档案生成暂用 embedded 执行，健康检查不强制要求独立 Worker。

## 5. 前端发布适配

Next.js 使用 standalone 输出。veFaaS 打包会忽略 `node_modules` 目录，因此构建时将 standalone 依赖目录改名为 `runtime_modules`，启动时使用：

```bash
NODE_PATH=runtime_modules node server.js
```

后端 HTTPS 地址在 Next.js 构建阶段通过 `BACKEND_URL` 注入。浏览器统一访问前端同源 `/api/v1/*`，由 Next.js 代理到 FastAPI，避免跨域 Cookie 和 CSRF 配置变复杂。

## 6. 安全配置

- 生产会话使用独立随机 `AUTH_SECRET`。
- Cookie 只在 HTTPS 传输，关闭演示 Header 登录。
- QQ SMTP 授权码和千问 API Key 只存在 veFaaS 环境变量中，不写入 Git。
- 报价扫描件模型与普通文本模型使用北京地域兼容接口。

## 7. 验收结果

- 后端 `GET /health/ready`：HTTP 200，数据库 available，两类 embedded 队列 healthy。
- 前端 `/` 与 `/login`：HTTP 200。
- 前端 `/api/v1/auth/status`：HTTP 200，同源代理正常。
- 未登录请求 `/api/v1/projects`：HTTP 401，认证边界正常。
- 公网验证码请求：HTTP 202，`delivery=smtp`，QQ 邮箱链路正常。
- 本地后端回归：32 项测试全部通过。
- 本地前端生产构建：编译、TypeScript 和静态页生成全部通过。

## 8. 当前边界与下一阶段

已完成以下云端权限配置：

- 创建最小权限自定义策略 `<private-bucket-policy>`，仅允许对 `<private-bucket>` 执行读取、写入、删除、分片终止和桶列表/探测操作。
- 创建只信任 veFaaS 的服务角色 `<vefaas-storage-role>`。
- 将策略绑定到角色，并将角色仅绑定到 `<backend-app>`。

真实发布验证表明，当前 veFaaS `native-python3.12/v1` 运行时不会将该函数角色的临时 AK/SK 提供给应用环境或 boto3 默认凭据链。实例内对 TOS 发起探测请求时返回 `NoCredentialsError`，因此不能仅凭函数角色完成应用层 S3 兼容访问。

启动脚本已增加备份恢复失败的降级保护：TOS/IAM 短暂异常会记录日志，但不再阻断 API 启动。为保证线上上传和导出不会因缺少凭据立即失败，稳定版环境保持为：

```env
SOURCE_STORAGE_BACKEND=local
ARTIFACT_STORAGE_BACKEND=local
DATABASE_BACKUP_ENABLED=false
```

阶段 13 当时创建了独立运行时 IAM 身份 `<storage-runtime-identity>`，只绑定 `<private-bucket-policy>`，并将唯一一组 Access Key 仅注入 `<backend-app>` 的 veFaaS 环境。当时验收配置切换为：

```env
SOURCE_STORAGE_BACKEND=s3
ARTIFACT_STORAGE_BACKEND=s3
DATABASE_BACKUP_ENABLED=true
```

上传、短签下载、删除、SQLite 快照上传和独立下载完整性验收已全部通过。详细结果和密钥轮换流程见阶段 13 文档。
