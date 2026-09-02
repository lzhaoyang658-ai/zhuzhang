# ClamAV 容器与部署说明

这个目录提供筑账完整生产模式所需的 `clamd` 容器适配。面试演示可以设置 `UPLOADS_ENABLED=false`，不需要构建或部署本容器。

ClamAV 使用 GPLv2，镜像分发注意事项见仓库根目录的 [第三方软件声明](../../THIRD_PARTY_NOTICES.md)。

## 为什么需要适配镜像

官方 ClamAV 镜像会在多个目录写入运行文件和病毒特征库。当容器使用只读根文件系统，平台又只提供小容量临时目录时，直接运行官方镜像容易在特征库更新或重启时失败。

本适配将不同类型的写入分开。

- `/mnt/clamav-db` 保存需要跨重启保留的病毒特征库。
- `/tmp/clamav` 保存 socket、PID 和扫描临时文件。
- 配置和启动脚本在构建时写入只读镜像层。
- `freshclam` 每天检查一次更新，更新后用 `SIGUSR2` 请求 `clamd` 重载特征库。
- 远程 `SHUTDOWN`、`RELOAD` 和 `STATS` 命令已关闭，版本查询和扫描命令保留。

ClamAV 官方说明容器建议至少配置 3 GiB 内存，4 GiB 更合适，同时建议为病毒特征库准备约 5 GiB 磁盘空间。特征库重载期间会短时使用更多内存，不应用空闲内存数据作为下调规格的唯一依据。详见 [ClamAV Docker 文档](https://docs.clamav.net/manual/Installing/Docker.html) 和 [系统需求](https://docs.clamav.net/Introduction.html#recommended-system-requirements)。

## 目录文件

| 文件 | 用途 |
| --- | --- |
| `Dockerfile` | 固定官方 ClamAV 1.5 基础镜像摘要并安装配置 |
| `clamd.conf` | TCP、扫描上限、资源和管理命令限制 |
| `freshclam.conf` | 特征库更新与重载回调 |
| `start-clamav.sh` | 初始化持久目录、启动和监督两个进程 |
| `healthcheck.sh` | 向 IPv4 本地端口发送 `zPING` 并检查 `PONG` |
| `reload-clamd.sh` | 特征库更新后安全发送 `SIGUSR2` |

## 本地构建

从仓库根目录执行。云端目标使用 `linux/amd64`，Apple Silicon 上会通过 Docker 模拟运行，启动和扫描可能比原生 amd64 慢。

```bash
docker build \
  --platform linux/amd64 \
  --tag zhuzhang-clamav:local \
  infra/clamav

docker volume create zhuzhang-clamav-db

docker run --detach \
  --name zhuzhang-clamav-local \
  --platform linux/amd64 \
  --read-only \
  --cpus 1 \
  --memory 4g \
  --memory-swap 4g \
  --tmpfs /tmp:rw,noexec,nosuid,size=512m \
  --volume zhuzhang-clamav-db:/mnt/clamav-db \
  --publish 127.0.0.1:3310:3310 \
  zhuzhang-clamav:local
```

首次启动要加载数百万条特征，需要等待健康检查通过。

```bash
docker inspect \
  --format '{{json .State.Health.Status}}' \
  zhuzhang-clamav-local

docker exec zhuzhang-clamav-local \
  /usr/local/bin/clamav-healthcheck
```

后端本地联调配置如下。

```env
UPLOADS_ENABLED=true
UPLOAD_MALWARE_SCAN_MODE=clamav
CLAMAV_HOST=127.0.0.1
CLAMAV_PORT=3310
```

验收完成后删除本地烟测容器和特征库数据卷。数据卷删除不可恢复，请先确认名称只属于本次烟测。

```bash
docker stop --timeout 10 zhuzhang-clamav-local
docker rm zhuzhang-clamav-local
docker volume rm zhuzhang-clamav-db
```

## 云端部署清单

完整生产模式可以部署到其他支持容器、持久卷和私网负载均衡的平台。以 veFaaS 为例，创建付费资源前应重新核对当前地域的可用性和价格。

- 使用 `linux/amd64` 镜像，先推送到同地域私有镜像仓库。
- 扫描实例使用 1 vCPU 和 4 GiB 内存，首版保持一个实例。
- 将持久文件系统挂载到 `/mnt/clamav-db`，预留至少 5 GiB。
- 为 `/tmp` 提供 512 MiB 可写临时空间，根文件系统保持只读。
- 允许 `freshclam` 访问官方特征库站点，同时限制其他不必要的公网访问。
- 使用私网 TCP 负载均衡提供稳定地址，3310 不对公网开放。
- 安全组、网络 ACL 和私网路由只允许应用网络与平台健康检查流量访问 3310。
- 平台健康检查至少覆盖 TCP 连接，应用就绪检查继续使用 `PING`。
- 配置特征库更新失败和过期告警。`PING` 不能代替特征库新鲜度监控。

`clamd` 协议本身不提供认证和加密。如果部署平台无法提供可控的私网路径，不应将该端口直接发布到公网。协议说明见 [ClamD Protocol](https://docs.clamav.net/manual/Usage/ClamdProtocol.html)。

多个扫描实例共享一个可写特征库目录时，不应让每个实例都独立运行 `freshclam`。首版使用单实例可避免这个冲突，代价是更新或重启期间上传会按失败关闭策略暂时被拒绝。后续需要高可用时，应改为单独更新器和只读扫描实例。

## 已完成的本地烟测

2026-09-02 在 ARM64 Docker Desktop 主机上，以 `linux/amd64`、1 vCPU、4 GiB 内存、只读根文件系统和 512 MiB `/tmp` 完成了一次本地概念验证。

- `clamd` 在模拟环境中约 17 秒进入可扫描状态。
- 后端 `ClamAVScanner` 对干净文件和 EICAR 测试样本的判定正确。
- 扫描上限为 30 MiB，与应用单文件上限一致。
- 远程 `SHUTDOWN`、`RELOAD` 和 `STATS` 被拒绝，容器仍保持健康。
- 特征库从当时的版本 28108 更新到 28110，重启后持久卷中的新版本仍可用。
- 空闲内存约为 965 MiB。特征库更新和重载存在峰值，生产规格仍保持 4 GiB。
- 与上传安全和部署安全相关的后端定向测试共 46 项通过。

这些结果证明容器与当前后端协议兼容，不代表已完成特定云平台的生产验收。上线前还需要使用真实 PDF、Office 和归档文件测量扫描时间，验证特征库告警，并完成服务停机时的失败关闭测试。
