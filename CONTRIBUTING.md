# 贡献指南

感谢你关注筑账。项目当前由个人维护，小而清楚的 Pull Request 更容易被复核。

## 适合贡献的内容

- 可复现的缺陷修复
- 补齐现有流程的测试与可访问性改进
- 不改变业务语义的性能和工程改进
- 能够说明当前行为的文档修正

大型功能、数据模型改动和公开 API 变更建议先开 Issue，说明用户问题、范围与兼容性影响。

## 本地准备

先按 [README](README.md#本地免费开发) 完成干净克隆与环境初始化。请不要使用真实装修文件做公开测试数据。需要报价样本时，优先使用 `samples/quote_corpus/` 的合成资料或自行生成的虚构数据。

## 开发流程

1. 从最新默认分支创建短期分支。
2. 只修改解决当前问题所需的文件。
3. 修改数据库模型时提供 Alembic 迁移，不在启动过程中手工改表。
4. 行为改动需要配套测试，配置变更同步修改 `.env.example` 和文档。
5. 提交前执行本地检查，然后查看 `git diff` 与 `git status --short`。

## 本地检查

macOS 和 Linux 可以执行以下命令。

```bash
cd backend
source .venv/bin/activate
python -m pytest
python -m compileall -q app alembic *.py

cd ../frontend
npm test
npm run typecheck
npm run lint
npm run build
```

Windows PowerShell 使用以下命令。

```powershell
cd backend
.\.venv\Scripts\Activate.ps1
python -m pytest
python -m compileall -q app alembic
Get-ChildItem -File *.py | ForEach-Object { python -m py_compile $_.FullName }

cd ../frontend
npm test
npm run typecheck
npm run lint
npm run build
```

## Pull Request 清单

- 说明问题、修改方法和验证结果。
- 标出数据库、配置、权限或部署兼容性变化。
- 前端界面变动提供必要的截图，不展示真实邮箱、地址和项目数据。
- 不提交 `.env`、Access Key、SMTP 授权码、API Key、真实上传数据和云端资源标识。
- 确认新依赖的许可证与本项目的分发方式兼容，并在需要时更新 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)。

## 安全问题

漏洞不走普通 Issue 和 Pull Request。请按 [SECURITY.md](SECURITY.md) 私下报告。

## 许可

项目代码使用 [MIT License](LICENSE)。提交贡献表示你有权提交这些内容，并同意在项目许可证下分发所提交的内容。如果贡献包含第三方代码或资产，请在 Pull Request 中说明来源和许可证。
