# 筑账｜装修预算与增项管家

筑账是一个面向家庭装修的预算、报价、增减项、验收和付款记录工具。项目用一条可追溯的时间线连起报价比较、合同基线、变更确认、证据和正式档案导出。

当前实现基线见 [装修预算与增项管家 PRD V1.1](renovation-budget-change-manager-prd-v1.1.md)。项目代码使用 [MIT License](LICENSE)，ClamAV 等第三方组件的声明见 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)。

## 开源与演示状态

本仓库提供可本地运行的完整代码，不承诺长期可用的公共演示服务。曾用于联调的云端环境只作为历史验收记录，公开文档已移除入口、资源 ID、桶名和 IAM 标识。

面试演示推荐关闭新文件上传。这不需要部署 ClamAV，预算、增减项、验收、付款记录、风险和档案等主要流程仍可演示。如果公网环境要接收任意用户文件，应启用完整模式并接入 ClamAV。

| 运行模式 | 新文件上传 | ClamAV | 适用场景 | 增量扫描费用 |
| --- | --- | --- | --- | --- |
| 本地开发 | 开启 | 可关闭 | 本机使用可信测试样本 | 0 |
| 生产面试演示 | 关闭 | 不需要 | 公网展示主要业务流程 | 0 |
| 完整生产 | 开启 | 必需 | 真实用户上传报价与证据 | 依部署平台计费 |

## 主要能力

- 邮箱验证码登录、HttpOnly 会话、CSRF 保护、设备撤销和近期登录校验
- 多项目中心、三步建项、预算分类、资金上限与风险预留金
- CSV、XLSX、PDF 和图片报价解析，报价比较、人工校对与合同基线
- 增减项内部批准、外部确认、验收、付款事实和证据关联
- A1 至 A8 风险预警、站内通知和可选邮件摘要
- 家庭协作、项目归档、7 天延迟删除和撤销
- 正式 PDF、CSV、附件与清单组成的项目档案，支持分卷与 SHA-256 校验
- 报价和档案任务的租约回收、重试、死信、人工重排与 Worker 心跳

## 技术结构

| 部分 | 技术 |
| --- | --- |
| 前端 | Next.js 16、React 19、TypeScript、Tailwind CSS、GSAP |
| 后端 | FastAPI、SQLAlchemy、Alembic、Pydantic |
| 本地存储 | SQLite 和私有文件目录 |
| 生产扩展 | PostgreSQL、S3 兼容私有对象存储、独立 Worker |
| 可选 AI | 千问文本与视觉模型，Tesseract 本地回退 |
| 可选扫描 | 独立 `clamd` 容器，后端通过 TCP `INSTREAM` 调用 |

## 本地免费开发

### 环境要求

- Python 3.11 或更高版本
- Node.js 20.9 或更高版本与 npm
- 可选的 Poppler 和 Tesseract OCR 系统依赖
- 可选的 Docker，只有本地验证 ClamAV 时才需要

### 1. 准备配置

先从代码托管页面复制仓库 URL。干净克隆后，在仓库根目录创建本地 `.env`；以下命令把目录统一命名为 `zhuzhang`。

macOS 与 Linux

```bash
git clone https://github.com/lzhaoyang658-ai/zhuzhang.git zhuzhang
cd zhuzhang
cp .env.example .env
```

Windows PowerShell

```powershell
git clone https://github.com/lzhaoyang658-ai/zhuzhang.git zhuzhang
cd zhuzhang
Copy-Item .env.example .env
```

默认配置使用本地 SQLite、本地文件目录和开发验证码投递。不需要云账号、模型 API Key 或 ClamAV。

### 2. 启动后端

macOS 与 Linux

```bash
cd backend
python3.11 --version
python3.11 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
alembic upgrade head
python run.py
```

如果已安装的是 Python 3.12 或 3.13，请把上面的 `python3.11` 替换为对应命令；不要使用低于 3.11 的系统 Python。

Windows PowerShell

```powershell
cd backend
py -3.11 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
alembic upgrade head
python run.py
```

后端地址为 `http://127.0.0.1:8001`，接口文档为 `http://127.0.0.1:8001/api/docs`。

OCR 是可选依赖。macOS 可执行 `brew install poppler tesseract tesseract-lang`，Ubuntu 或 Debian 可安装 `poppler-utils tesseract-ocr tesseract-ocr-chi-sim`。Windows 需要安装 Poppler 和 Tesseract 并加入 `PATH`。没有 OCR 时，CSV、XLSX 和带文本层的 PDF 仍可使用，扫描件会给出回退提示。

### 3. 启动前端

新开一个终端。

```bash
cd frontend
npm ci
npm run dev
```

访问 `http://localhost:3001`。本地默认使用 `AUTH_DELIVERY_MODE=development`，验证码只返回给本地登录页。使用任意开发邮箱登录后即可建项。

默认不注入演示项目。如果需要内置演示账本，只能在空的本地开发库中临时设置 `SEED_DEMO_ENABLED=true`。数据库与上传文件位于 `backend/data/`，不应提交到 Git。

## 生产面试演示模式

公网演示不需要接收文件时，在部署 Secret 或环境变量中明确关闭上传。

```env
APP_ENV=production
SEED_DEMO_ENABLED=false
UPLOADS_ENABLED=false
UPLOAD_MALWARE_SCAN_MODE=disabled
AUTH_SECRET=<至少32位的独立随机密钥>
AUTH_DELIVERY_MODE=smtp
AUTH_COOKIE_SECURE=true
AUTH_ALLOW_DEMO_HEADER=false
SMTP_HOST=smtp.example.com
SMTP_PORT=465
SMTP_USERNAME=<部署Secret>
SMTP_PASSWORD=<部署Secret>
SMTP_FROM_EMAIL=noreply@example.com
SMTP_USE_SSL=true
```

关闭后，报价导入和证据上传接口在读取或存储文件前返回 HTTP 503 与 `FILE_UPLOADS_DISABLED`。`GET /api/v1/session` 通过 `capabilities.uploads_enabled` 告诉前端隐藏或禁用上传操作。就绪检查将扫描器标为 `not_applicable`，不会因为没有 ClamAV 而降级。

`AUTH_SECRET`、SMTP 凭据、模型 Key 和对象存储凭据只能放在部署 Secret，不能提交到 Git。`AUTH_SECRET` 需要在多次发布之间保持稳定，否则现有会话会失效。

仓库的 veFaaS 单实例配置脚本要求同时指定应用、私有桶和模式。它会先核对远端 Secret 名称，再增量写入非敏感配置，不会导出或覆盖远端 Secret 值。

```bash
python3 scripts/configure_vefaas_backend_env.py \
  --app-id <backend-app-id> \
  --bucket <private-bucket-name> \
  --mode portfolio
```

## 完整生产上传

真实用户需要上传报价和证据时，启用上传并接入私网 ClamAV。

```env
UPLOADS_ENABLED=true
UPLOAD_MALWARE_SCAN_MODE=clamav
CLAMAV_HOST=<私网ClamAV地址>
CLAMAV_PORT=3310
CLAMAV_TIMEOUT_SECONDS=10
CLAMAV_READINESS_TIMEOUT_SECONDS=2
```

生产环境启用上传时，缺少 ClamAV 配置会导致后端拒绝启动。`clamd` 的 TCP 协议没有应用层认证与加密，3310 只能在私网中开放。容器构建、持久化、本地烟测和云部署清单见 [ClamAV 部署说明](infra/clamav/README.md)。

veFaaS 完整模式从仓库根目录执行以下命令。`CLAMAV_HOST` 等本机非密钥配置放在被 Git 忽略的 `.env.production.local`，凭据需要先在部署平台的 Secret 中配置。

```bash
python3 scripts/configure_vefaas_backend_env.py \
  --app-id <backend-app-id> \
  --bucket <private-bucket-name> \
  --mode full
```

当前 `full` 模式会同时启用扫描件 AI 解析，因此远端还需要已配置的 `DASHSCOPE_API_KEY`。`portfolio` 模式会把 AI 与上传一起关闭。

发布后至少验收以下路径。

1. 正常样本可上传和回读。
2. EICAR 测试样本被拒绝。
3. 扫描服务不可用时，上传失败且就绪检查返回 503。
4. `cd backend && .venv/bin/python rescan_source_files.py --include-skipped` 的结果满足 `ok: true`、`skipped: 0` 和 `error: 0`。

ClamAV `PING` 只能证明进程响应，还需要监控病毒特征库的更新时间与失败记录。

## 可选的 AI 解析

本地表格和带文本层 PDF 始终优先使用确定性解析。扫描件可选调用千问视觉模型，模型服务失败时回退到 Tesseract。

```env
AI_ENABLED=true
DASHSCOPE_API_KEY=<仅写入本机或部署Secret>
```

模型输出不会直接建立合同基线，使用者必须校对金额、单位和归类。

## 报价测试样本

以下可选命令适用于 macOS、Linux 或 Git Bash。

```bash
bash scripts/fetch_quote_samples.sh
backend/.venv/bin/python scripts/generate_synthetic_quotes.py
```

公开 PDF 只保存在被 Git 忽略的本地数据目录。可提交的清单只记录来源、摘要与使用边界。三份住宅报价和基准答案都是合成数据，详见 [报价解析样本集](samples/quote_corpus/README.md)。

## 验证

macOS 与 Linux

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

Windows PowerShell

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

GitHub Actions 工作流已放在 `.github/workflows/`，它们使用临时数据库和测试配置，不依赖仓库 Secret。

## Worker、对象存储与数据库

本地默认使用 SQLite 与内嵌执行，不需要额外进程。小规模单实例部署也可使用 SQLite，但后端必须固定为一个实例，并配置经过恢复演练的远端备份。

生产多实例应使用 PostgreSQL，并将报价与档案任务切换为独立 Worker。

```env
DATABASE_URL=postgresql+psycopg://app_user:password@postgres:5432/renovation_budget
QUOTE_EXECUTION_MODE=worker
EXPORT_EXECUTION_MODE=worker
HEALTH_REQUIRE_WORKERS=true
```

```bash
cd backend
source .venv/bin/activate
python quote_worker.py
python export_worker.py --purge-expired
```

也可以用 `WORKER_QUEUES=quote,export python worker_service_bootstrap.py` 在一个小型常驻服务中消费两类队列。生产私有文件可切换到 S3 兼容对象存储，具体变量见 `.env.example`。

## 当前边界

- 平台只记录付款事实，不发起或托管装修款。
- 报告不构成质量鉴定、价格审定、法律意见或付款建议。
- 扫描 PDF 和图片的结构化结果仍需人工校对。系统不会在低置信度时猜测金额。
- 本地 SQLite 与私有文件目录只适合开发和隔离测试，不应直接作为公网多实例方案。
- 完整 ClamAV 部署会带来持续费用和运维工作，它是开源使用者自行选择的生产能力。

## 文档与参与

- [技术适配声明](docs/技术适配声明.md)
- [历史阶段技术文档](docs/)
- [贡献指南](CONTRIBUTING.md)
- [安全政策](SECURITY.md)
- [第三方软件声明](THIRD_PARTY_NOTICES.md)

阶段 12 至 17 文档保留了单实例云部署、对象存储、备份恢复、真实链路 UAT 与 ClamAV 准备过程。公开版使用占位符或通用资源名，不提供仍可能有效的云端标识。
