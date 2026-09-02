# 阶段 17｜生产健康探针、上传开关与 ClamAV 准备

## 当前结论

项目作为个人面试与开源作品，当前选择生产面试演示模式。新文件上传在生产环境关闭，暂不创建持续计费的 ClamAV、私网负载均衡、持久文件系统和镜像仓库资源。

代码仍保留完整上传模式、ClamAV 客户端、失败关闭逻辑和可部署的容器配置。开源使用者可以在自己的基础设施中选择启用。

> 开源脱敏说明：本阶段没有发布新云端版本，也没有创建 ClamAV 云资源。旧验收环境只作为历史记录，公开文档不披露应用、修订、函数和公网入口标识。

## 已完成的安全边界

- `UPLOADS_ENABLED` 控制新报价和证据文件是否可上传，默认值为 `true`。
- 生产环境设置 `UPLOADS_ENABLED=false` 时，允许使用 `UPLOAD_MALWARE_SCAN_MODE=disabled`，也不要求 `CLAMAV_HOST`。
- 关闭后，报价导入和证据上传在读取或存储文件前返回 HTTP 503 与 `FILE_UPLOADS_DISABLED`。
- `GET /api/v1/session` 返回 `capabilities.uploads_enabled`，前端根据该能力隐藏或禁用上传操作并展示演示模式说明。
- 上传关闭时，内部和非生产就绪详情中的 `malware_scanner` 为 `mode=disabled`、`status=not_applicable`，不会因 ClamAV 缺席降级；生产公开响应仍只返回总体 `status`。
- 生产环境设置 `UPLOADS_ENABLED=true` 时，启动校验仍强制要求 `UPLOAD_MALWARE_SCAN_MODE=clamav` 和非空 `CLAMAV_HOST`。
- ClamAV 不可达时，上传失败且 `/health/ready` 返回 503；`/health` 仍只表示 API 进程存活。
- 生产就绪响应只公开必要状态，不返回桶名、对象键、备份校验值和全局队列数量。
- 前后端健康响应使用 `Cache-Control: no-store`。

## 生产面试演示配置

基础配置如下。认证、SMTP、对象存储等凭据仍需通过部署 Secret 提供。

```env
APP_ENV=production
SEED_DEMO_ENABLED=false
UPLOADS_ENABLED=false
UPLOAD_MALWARE_SCAN_MODE=disabled
AI_ENABLED=false
AUTH_COOKIE_SECURE=true
AUTH_ALLOW_DEMO_HEADER=false
```

veFaaS 单实例环境可从仓库根目录执行以下命令。脚本会核对远端已存在的 Secret 名称，然后增量写入非敏感配置。

```bash
python3 scripts/configure_vefaas_backend_env.py \
  --app-id <backend-app-id> \
  --bucket <private-bucket-name> \
  --mode portfolio
```

脚本不会从远端导出 Secret 值，也不会用本地文件整批覆盖远端配置。缺少必需的远端 Secret 名称时，脚本会停止。

## 完整上传模式

完整模式的目标拓扑保持如下。

```text
FastAPI 后端
  └→ 同地域私网
       └→ 私网 TCP 负载均衡 3310
            └→ 单实例 ClamAV 容器
                 ├→ /tmp 临时运行目录
                 └→ /mnt/clamav-db 持久特征库
```

`clamd` 协议没有应用层认证和加密，3310 不能使用公网负载均衡。首版规格使用 1 vCPU、4 GiB 内存和至少 5 GiB 持久空间。容器细节和验收命令见 [`infra/clamav/README.md`](../infra/clamav/README.md)。

付费资源应在创建前核对地域、规格、数量、计费方式与最坏月费。开源仓库不绑定某个现存 VPC、子网、安全组、负载均衡或镜像仓库。

完整模式配置脚本命令如下。`CLAMAV_HOST` 等本机非密钥值放在被 Git 忽略的 `.env.production.local`。

```bash
python3 scripts/configure_vefaas_backend_env.py \
  --app-id <backend-app-id> \
  --bucket <private-bucket-name> \
  --mode full
```

`full` 模式当前会同时开启 AI 解析，脚本会要求远端已存在 `DASHSCOPE_API_KEY` 这一 Secret 名称。

## 发布验收顺序

1. 确认 ClamAV 特征库成功更新，特征库目录在容器重启后仍保留。
2. 检查 TCP 健康与应用 `PING`，确认 3310 没有公网入口。
3. 使用正常样本、EICAR 测试样本和超限样本验收扫描结果。
4. 临时停止 ClamAV，确认上传失败且就绪检查返回 503。
5. 执行 `cd backend && .venv/bin/python rescan_source_files.py --include-skipped`，只有 `ok: true`、`skipped: 0`、`error: 0` 时继续。
6. 把后端 `/health/ready` 和前端 `/health` 配置为平台探针，限制就绪路径的访问来源或配置限流。
7. 复验登录、上传、外部确认、邀请、导出、删除和备份恢复后再切换流量。

## 本地验证证据

- 后端上传开关、生产启动校验、关闭时短路和 Session 能力返回已通过定向测试。
- 前端会读取 Session 能力，在面试演示模式隐藏或禁用上传入口。
- ClamAV 镜像已在 `linux/amd64`、只读根文件系统、1 vCPU、4 GiB 内存和 512 MiB `/tmp` 的本地容器中完成概念验证。
- 干净文件、EICAR 测试样本、30 MiB 上限、特征库更新、持久化和远程管理命令限制结果符合预期。

本地容器烟测不代表已完成云端生产验收。真实 PDF、Office 与归档文件的扫描耗时、特征库告警和云网络隔离仍需要在目标环境重新测试。

## 参考

- [veFaaS 函数网络与健康配置](https://www.volcengine.com/docs/6662/1206174)
- [veFaaS 微服务容器部署](https://www.volcengine.com/docs/6662/1338568)
- [veFaaS 私网 CLB TCP 访问](https://www.volcengine.com/docs/6662/1923772)
- [ClamAV Docker 文档](https://docs.clamav.net/manual/Installing/Docker.html)
- [ClamD 协议](https://docs.clamav.net/manual/Usage/ClamdProtocol.html)
