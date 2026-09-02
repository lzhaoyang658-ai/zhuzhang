# 第 1 阶段技术开发文档｜Alpha 核心闭环

> 配套文档：《装修预算与增项管家 PRD V1.0》《AI Agent 产品 Vibe Coding 通用技术栈手册 V2.1》与《技术适配声明》。

## 一、阶段目标

- 交付一套本地可运行、响应式的业主端 Web 产品。
- 打通“合同基线 → 增减项确认 → 预算刷新 → 验收与付款检查 → 付款记录 → 时间线/导出”。
- 金额口径与状态变化全部由服务端确定性计算；关键事件追加审计。
- 明确不做：Beta 的图片/PDF AI 识别、三报价智能匹配、正式认证、多人权限、购买、邮件与生产部署。

## 二、技术适配摘要

- 采用纵向切片；后端使用 FastAPI/SQLite/SQLAlchemy，前端使用 Next.js/TypeScript/Tailwind。
- CSV/XLSX 导入、基础证据文件和 PDF/CSV/ZIP 导出按需求引入。
- 本地演示账号只用于 Alpha 体验，不等价于生产认证。

## 三、技术栈与模型

- Python 3.11、FastAPI、Pydantic、SQLAlchemy、Alembic、pytest。
- Next.js、React、TypeScript、Tailwind CSS、ESLint。
- 本阶段不调用模型；报价解析服务接口为 Beta AI/OCR 留出扩展点。

## 四、环境与配置

- 后端默认 `http://127.0.0.1:8001`；前端默认 `http://localhost:3001`。
- `DATABASE_URL`、`UPLOAD_DIR`、`CORS_ORIGINS` 从环境读取。
- 无需 API Key。

## 五、数据、资产与状态

- 金额以整数分保存，时间以 UTC 保存并在界面按 Asia/Shanghai 展示。
- 项目、基线、增减项、付款节点、验收、付款、证据和审计事件都包含 `project_id`。
- 增减项支持草稿、待确认、修订中、已批准、已拒绝、已撤销、已实施、已验收、已结算。
- 证据文件使用随机对象键；用户原文件名仅保存在元数据中。

## 六、API 设计摘要

- `GET/POST /api/v1/projects`：项目列表与创建。
- `GET /api/v1/projects/{id}/dashboard`：预算、风险、节点和时间线聚合。
- `POST /api/v1/projects/{id}/baseline`：建立新基线版本。
- `GET/POST /api/v1/projects/{id}/changes`：增减项列表与创建。
- `POST /api/v1/changes/{id}/actions/{action}`：受控状态转换。
- `GET/POST /api/v1/projects/{id}/milestones`：付款计划。
- `POST /api/v1/milestones/{id}/acceptances`：记录验收。
- `GET /api/v1/milestones/{id}/payment-check`、`POST /api/v1/milestones/{id}/payments`：付款前检查与付款记录。
- `GET /api/v1/projects/{id}/timeline`：统一事实时间线。
- `POST /api/v1/projects/{id}/export`：生成基础项目档案。

## 七、验收界面

- 正式 Web 产品的第一条纵向切片，不是一次性调试页。
- 桌面端提供侧边导航与多栏总览；移动端按风险、预算、节点、快捷操作、时间线排序，并使用底部导航。
- 关键写操作使用表单确认；风险同时使用文字、图标和颜色表达。

## 八、测试要求

- 自动化：预算公式、基线为零、增减项状态机、拒绝不入账、付款冲正/作废、验收风险、幂等写入。
- 工程：后端 pytest；前端 TypeScript、ESLint、生产构建。
- 浏览器：桌面与手机宽度；核心交互、空/错误状态、控制台和网络请求。

## 九、产品经理验收清单

- [ ] 打开总览，确认资金上限、基线、批准预算、待审批风险和预测结算口径清晰。
- [ ] 新建一个增项并发送确认，确认待审批风险随即变化。
- [ ] 将增项批准，确认金额从待审批风险转入已批准预算。
- [ ] 查看付款节点，在缺少验收时看到付款风险。
- [ ] 记录验收后再记录付款，确认首页累计已付款更新且文案为“已记录付款”。
- [ ] 在手机宽度下完成上述主要操作。
- [ ] 查看时间线并下载基础项目档案。

## 十、风险与待确认项

- 本地演示身份、同步导出和本地文件存储不得直接当作生产方案。
- AI 发布门槛需要 Beta 的真实样本集和真实模型才能验证。
- 需要产品经理决定的问题：无。

## 十一、交接给下一阶段

- Beta 直接复用 API、金额服务、状态机、审计事件和响应式信息架构。
- 在保持人工确认边界的前提下扩展 AI/OCR、多人权限、异步导出和通知。
