# 第三方软件声明

本文档记录仓库中需要单独关注的第三方软件。它不是完整的依赖许可证清单，也不构成法律意见。发布或再分发前，请根据实际制品重新检查依赖、许可证文本和源码提供方式。

## ClamAV

- 项目与版权方 [ClamAV by Cisco Talos](https://github.com/Cisco-Talos/clamav)
- 许可证 [GNU General Public License Version 2](https://github.com/Cisco-Talos/clamav/blob/main/COPYING.txt)
- 官方许可说明 [ClamAV Introduction](https://docs.clamav.net/Introduction.html#license)

筑账后端不链接 `libclamav`，也不把 ClamAV 源码编译进应用。完整模式部署中，FastAPI 应用与独立的 `clamd` 进程通过 TCP `INSTREAM` 协议交换扫描请求和结果。应用代码按仓库根目录的 MIT License 分发，ClamAV 仍保留其自身的 GPLv2 许可声明。

`infra/clamav/Dockerfile` 以官方 ClamAV 镜像为基础，再加入本项目的配置和启动脚本。如果你构建并向他人分发这个镜像或其他包含 ClamAV 的派生制品，需要根据 GPLv2 和制品中其他组件的许可证履行相应义务。这通常涉及保留版权与许可证文本，并以许可证允许的方式提供相应源码。具体要求取决于你如何修改、交付和分发制品，必要时请寻求专业意见。

ClamAV 病毒特征库也有自身的使用与分发条件。长期运行、镜像发布或离线分发时，请同时检查 ClamAV 官方文档与镜像中的声明。

## 其他依赖

Python 与 Node.js 依赖列在 `backend/requirements.txt`、`frontend/package.json` 和锁定文件中。这些组件分别适用各自的许可证。仓库的 MIT License 不会替换第三方组件的许可条款。
