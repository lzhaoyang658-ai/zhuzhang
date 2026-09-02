# 阶段 13 技术开发文档：TOS 持久化与云备份验收

## 1. 阶段目标

为后端应用接入最小权限私有 TOS，将原文件、导出制品和 SQLite 备份从函数实例本地目录迁移到可持久化的云存储，并完成真实环境恢复验收。

> 开源脱敏说明：本文保留验收方法与结果，运行身份、策略名、桶名、应用标识和精确恢复点已脱敏。

## 2. IAM 权限边界

- 运行时身份：`<storage-runtime-identity>`。
- 自定义策略：`<private-bucket-policy>`。
- 目标桶：`<private-bucket>`。
- 用户仅绑定上述一个自定义策略，当前只有一组启用中的 Access Key。
- 策略仅允许所需的桶探测/列表、对象读写删除和分片中止操作，不包含 IAM、计费或其他云资源权限。

Access Key 密文只存在 `<backend-app>` 的 veFaaS 环境变量中，未写入仓库、文档或部署输出。

## 3. 线上配置

```env
SOURCE_STORAGE_BACKEND=s3
ARTIFACT_STORAGE_BACKEND=s3
DATABASE_BACKUP_ENABLED=true
DATABASE_BACKUP_INTERVAL_SECONDS=300
```

TOS 对象使用 `AES256` 服务端加密，下载链接使用限时短签，桶仍保持私有访问。

## 4. 发布与验收结果

- 当时的后端稳定修订承接 100% 流量，发布状态为 done。
- `GET /health/ready`：HTTP 200，数据库 available，报价与导出队列 healthy。
- 随机对象写入成功，读回内容与原文字节一致。
- 限时预签名链接可正常下载私有对象。
- 测试对象删除成功，桶中不保留验收垃圾。
- SQLite 快照已上传到固定备份键，再独立下载到临时文件执行 `PRAGMA integrity_check`，结果为 `ok`，包含 28 张表。
- 快照上传后再次执行滚动发布，全新实例在本地数据库不存在时从 TOS 恢复后正常启动并通过健康检查，完成真实冷启动恢复演练。
- 备份对象使用 AES256 加密，内容长度非零，并含备份时间元数据。
- 前端首页、登录页和认证代理均返回 HTTP 200，未登录项目请求继续返回 HTTP 401。
- 本地后端回归：33 项测试通过。

## 5. 故障降级

`production_bootstrap.py` 在恢复云备份失败时会记录异常并继续启动 API，防止 TOS 或 IAM 短暂异常导致全站无法启动。该分支已有自动化回归测试。

## 6. 密钥轮换流程

1. 在 `<storage-runtime-identity>` 下创建第二组 Access Key。
2. 将新 AK/SK 注入 `<backend-app>` 的 `ARTIFACT_STORAGE_ACCESS_KEY_ID` 和 `ARTIFACT_STORAGE_SECRET_ACCESS_KEY`。
3. 发布新修订，重复写入、读取、删除和备份验收。
4. 新密钥验收成功后，先禁用旧密钥并观察运行日志。
5. 确认无回退需求后删除旧密钥，始终只保留一组启用中凭据。

建议每 90 天轮换一次，或在任何可能泄露后立即轮换。
