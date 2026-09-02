# 报价解析样本集

本目录记录 Beta 报价识别开发使用的样本来源与生成方法。公开文件只下载到
`backend/data/sample_quotes/public/`，该目录已被 Git 忽略；仓库中不重新分发第三方文件。

## 样本构成

| 样本 | 类型 | 用途 | 隐私处理 |
| --- | --- | --- | --- |
| 岳阳家庭居室装饰装修合同示范文本 | 文本 PDF、空白报价表 | 表头识别、页码定位、中文 PDF 文本层 | 官方空白表单，无已填写个人信息 |
| 青岛家居装饰装修合同示范文本 | 文本 PDF、字段规范 | 材料、品牌、规格、损耗和工艺字段设计 | 官方空白表单，无已填写个人信息 |
| 深圳龙岗公开招标控制价清单 | 扫描 PDF、多页复杂表格 | OCR 降级、扫描件识别、失败提示 | 仅用于本地测试；不进入演示数据和仓库 |
| 三份虚构住宅报价 | XLSX + CSV + 基准 JSON | 三报价导入、匹配、差异和金额评测 | 全部名称与数据均为合成 |

公开来源没有被假定为开放许可证。下载脚本仅为本地研发复现提供原网址和摘要校验，
如需把原文件随产品或训练集再分发，必须另行核实授权。个人网盘、模板售卖站和没有
LICENSE 的 GitHub 真实报价文件均不纳入。

## 生成与下载

在仓库根目录执行：

```bash
bash scripts/fetch_quote_samples.sh
backend/.venv/bin/python scripts/generate_synthetic_quotes.py
```

生成后目录应包含：

- `backend/data/sample_quotes/public/*.pdf`
- `backend/data/sample_quotes/synthetic/vendor-a.xlsx`
- `backend/data/sample_quotes/synthetic/vendor-b.xlsx`
- `backend/data/sample_quotes/synthetic/vendor-c.csv`
- `backend/data/sample_quotes/synthetic/vendor-a-scan.jpg`
- `backend/data/sample_quotes/synthetic/ground-truth.json`

## 评测边界

- 官方空白表用于验证结构，不计入条目召回率。
- 扫描型公开清单用于鲁棒性和失败模式测试，不把其中公司名称、印章或签名写入应用演示数据。
- 发布指标以合成基准答案和后续获得明确授权的人工标注真实样本共同计算。
- AI/OCR 结果始终进入“待校对”，不能自动激活合同基线。
