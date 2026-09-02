from __future__ import annotations

import csv
import io
import re
import zipfile
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from xml.sax.saxutils import escape

from PIL import Image as PillowImage
from pypdf import PdfReader
from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.cidfonts import UnicodeCIDFont
from reportlab.platypus import (
    Image, KeepTogether, PageBreak, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle,
)
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.models import (
    AcceptanceRecord, AuditEvent, BaselineVersion, ChangeOrder, Evidence, PaymentMilestone,
    PaymentRecord, Project, Quote,
)
from app.services.budget import calculate_budget
from app.services.source_storage import get_source_storage


settings = get_settings()
REPORT_VERSION = "formal-v2"


@dataclass(frozen=True)
class ArchivePart:
    path: Path
    filename: str
    kind: str
    part_number: int


@dataclass(frozen=True)
class ArchiveBuild:
    parts: tuple[ArchivePart, ...]
    report_page_count: int

    @property
    def path(self) -> Path:
        return self.parts[0].path


@dataclass(frozen=True)
class SourceAttachment:
    path: Path
    member_name: str


def _within(value: date | datetime, date_from: date | None, date_to: date | None) -> bool:
    day = value.date() if isinstance(value, datetime) else value
    return (not date_from or day >= date_from) and (not date_to or day <= date_to)


def _safe_archive_name(value: str, fallback: str) -> str:
    name = Path(value).name.strip()
    name = re.sub(r"[\\/:*?\"<>|\x00-\x1f]", "_", name)
    return name[:180] or fallback


def _unique_member(prefix: str, name: str, used: set[str]) -> str:
    stem, suffix = Path(name).stem, Path(name).suffix
    candidate = f"{prefix}/{name}"
    counter = 2
    while candidate in used:
        candidate = f"{prefix}/{stem}-{counter}{suffix}"
        counter += 1
    used.add(candidate)
    return candidate


def _safe_source(root: Path, *parts: str) -> Path | None:
    if root == settings.upload_dir:
        return get_source_storage().ensure_local("/".join(parts))
    base = root.resolve()
    source = root.joinpath(*parts).resolve()
    try:
        source.relative_to(base)
    except ValueError:
        return None
    return source if source.is_file() else None


def _money(cents: int) -> str:
    return f"¥{cents / 100:,.2f}"


def _label(value: str) -> str:
    return {
        "approved": "已批准",
        "pending_confirmation": "待确认",
        "pending_revision": "待修订",
        "draft": "草稿",
        "rejected": "已拒绝",
        "void": "已作废",
        "passed": "通过",
        "passed_with_issues": "带问题通过",
        "failed": "未通过",
        "normal": "付款",
        "reversal": "冲正",
    }.get(value, value)


def _styles() -> dict[str, ParagraphStyle]:
    return {
        "cover_kicker": ParagraphStyle("cover_kicker", fontName="STSong-Light", fontSize=9, leading=13, textColor=colors.HexColor("#557267"), spaceAfter=12),
        "cover_title": ParagraphStyle("cover_title", fontName="STSong-Light", fontSize=28, leading=35, textColor=colors.HexColor("#173d32"), spaceAfter=18),
        "cover_subtitle": ParagraphStyle("cover_subtitle", fontName="STSong-Light", fontSize=10, leading=17, textColor=colors.HexColor("#65726c"), spaceAfter=28),
        "section": ParagraphStyle("section", fontName="STSong-Light", fontSize=19, leading=25, textColor=colors.HexColor("#173d32"), spaceBefore=3, spaceAfter=13),
        "subsection": ParagraphStyle("subsection", fontName="STSong-Light", fontSize=12, leading=17, textColor=colors.HexColor("#2e6655"), spaceBefore=14, spaceAfter=8),
        "body": ParagraphStyle("body", fontName="STSong-Light", fontSize=8.5, leading=14, textColor=colors.HexColor("#384a43"), wordWrap="CJK"),
        "small": ParagraphStyle("small", fontName="STSong-Light", fontSize=7.2, leading=11, textColor=colors.HexColor("#6d7973"), wordWrap="CJK"),
        "table": ParagraphStyle("table", fontName="STSong-Light", fontSize=7.2, leading=10.5, textColor=colors.HexColor("#263a32"), wordWrap="CJK"),
        "table_head": ParagraphStyle("table_head", fontName="STSong-Light", fontSize=7.2, leading=10, textColor=colors.white, alignment=TA_LEFT, wordWrap="CJK"),
        "metric": ParagraphStyle("metric", fontName="STSong-Light", fontSize=15, leading=20, textColor=colors.HexColor("#173d32"), alignment=TA_CENTER),
        "metric_label": ParagraphStyle("metric_label", fontName="STSong-Light", fontSize=7, leading=10, textColor=colors.HexColor("#6b7771"), alignment=TA_CENTER),
    }


def _paragraph(value: object, style: ParagraphStyle) -> Paragraph:
    text = "-" if value is None or value == "" else str(value)
    return Paragraph(escape(text).replace("\n", "<br/>"), style)


def _table(rows: list[list[object]], widths: list[float], styles: dict[str, ParagraphStyle], *, header: bool = True) -> Table:
    converted: list[list[object]] = []
    for row_index, row in enumerate(rows):
        style = styles["table_head"] if header and row_index == 0 else styles["table"]
        converted.append([cell if isinstance(cell, (Image, Paragraph)) else _paragraph(cell, style) for cell in row])
    table = Table(converted, colWidths=widths, repeatRows=1 if header else 0, hAlign="LEFT")
    commands = [
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 6),
        ("RIGHTPADDING", (0, 0), (-1, -1), 6),
        ("TOPPADDING", (0, 0), (-1, -1), 6),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
        ("GRID", (0, 0), (-1, -1), .35, colors.HexColor("#d5d9d2")),
    ]
    if header:
        commands.extend([
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#173d32")),
            ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f4f3ed")]),
        ])
    else:
        commands.extend([
            ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#f7f5ee")),
            ("BOX", (0, 0), (-1, -1), .6, colors.HexColor("#bfc8c1")),
        ])
    table.setStyle(TableStyle(commands))
    return table


def _page_chrome(canvas, doc) -> None:
    canvas.saveState()
    width, height = A4
    canvas.setStrokeColor(colors.HexColor("#d5d8d1"))
    canvas.setLineWidth(.4)
    canvas.line(18 * mm, height - 15 * mm, width - 18 * mm, height - 15 * mm)
    canvas.setFont("STSong-Light", 7)
    canvas.setFillColor(colors.HexColor("#68746e"))
    canvas.drawString(18 * mm, height - 11 * mm, "筑账 · 装修项目正式档案")
    canvas.drawRightString(width - 18 * mm, 10 * mm, f"第 {doc.page} 页")
    canvas.restoreState()


def _thumbnail(source: Path, max_width: float = 46 * mm, max_height: float = 31 * mm) -> Image | None:
    try:
        with PillowImage.open(source) as image:
            width, height = image.size
        ratio = min(max_width / width, max_height / height)
        return Image(str(source), width=width * ratio, height=height * ratio)
    except Exception:
        return None


def _build_report(
    project: Project,
    budget: dict,
    baselines: list[BaselineVersion],
    changes: list[ChangeOrder],
    milestones: list[PaymentMilestone],
    acceptances: list[AcceptanceRecord],
    payments: list[PaymentRecord],
    events: list[AuditEvent],
    evidence: list[Evidence],
    quotes: list[Quote],
    *,
    include_attachments: bool,
    date_from: date | None,
    date_to: date | None,
) -> bytes:
    pdfmetrics.registerFont(UnicodeCIDFont("STSong-Light"))
    styles = _styles()
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(
        buffer, pagesize=A4, rightMargin=18 * mm, leftMargin=18 * mm,
        topMargin=21 * mm, bottomMargin=17 * mm,
        title=f"{project.name} - 装修项目正式档案", author="筑账",
    )
    story: list[object] = []
    generated_at = datetime.now(timezone.utc)
    range_text = f"{date_from.isoformat() if date_from else '项目开始'} 至 {date_to.isoformat() if date_to else '生成时'}"

    story.extend([
        Spacer(1, 27 * mm),
        _paragraph("装修项目正式档案", styles["cover_kicker"]),
        _paragraph(project.name, styles["cover_title"]),
        _paragraph("将预算边界、合同版本、增减项、验收、付款与原始依据整理为可交接记录。", styles["cover_subtitle"]),
        _table([
            ["城市", project.city, "面积", f"{project.area_sqm}㎡（{project.area_basis}）"],
            ["装修方式", project.renovation_type, "项目状态", project.status],
            ["计划周期", f"{project.planned_start or '-'} 至 {project.planned_end or '-'}", "数据范围", range_text],
            ["生成时间", generated_at.strftime("%Y-%m-%d %H:%M UTC"), "报告版本", REPORT_VERSION],
        ], [26 * mm, 52 * mm, 26 * mm, 65 * mm], styles, header=False),
        Spacer(1, 44 * mm),
        _paragraph("重要说明", styles["subsection"]),
        _paragraph("本报告依据用户录入、上传和确认的数据自动整理，不构成工程质量鉴定、价格审定、法律意见或付款建议。发生争议时，应结合合同原件、现场事实和专业意见判断。", styles["body"]),
        PageBreak(),
        _paragraph("预算与合同边界", styles["section"]),
    ])

    metric_rows = [
        ["装修资金上限", "当前合同基线", "预测结算", "累计已付款"],
        [_money(budget["fund_limit_cents"]), _money(budget["baseline_cents"]), _money(budget["predicted_settlement_cents"]), _money(budget["paid_cents"])],
        ["风险预留", "已批准增减", "待确认风险", "剩余资金"],
        [_money(project.reserve_cents), _money(budget["approved_change_cents"]), _money(budget["pending_risk_cents"]), _money(budget["remaining_funds_cents"])],
    ]
    metric_data = []
    for row_index, row in enumerate(metric_rows):
        style = styles["metric_label"] if row_index % 2 == 0 else styles["metric"]
        metric_data.append([_paragraph(value, style) for value in row])
    metrics = Table(metric_data, colWidths=[42.25 * mm] * 4)
    metrics.setStyle(TableStyle([
        ("GRID", (0, 0), (-1, -1), .5, colors.HexColor("#d2d7d0")),
        ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#f7f4ea")),
        ("TOPPADDING", (0, 0), (-1, -1), 8),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
    ]))
    story.extend([metrics, _paragraph("合同基线版本", styles["subsection"])])
    baseline_rows: list[list[object]] = [["版本", "金额", "状态", "确认人", "确认时间", "变更原因"]]
    baseline_rows.extend([[f"V{item.version}", _money(item.amount_cents), "当前" if item.is_active else "历史", item.confirmed_by, item.confirmed_at.date().isoformat(), item.reason] for item in baselines])
    story.append(_table(baseline_rows or [["版本", "金额", "状态", "确认人", "确认时间", "变更原因"]], [14 * mm, 27 * mm, 18 * mm, 26 * mm, 27 * mm, 57 * mm], styles))

    story.extend([PageBreak(), _paragraph("增减项及确认状态", styles["section"])])
    change_rows: list[list[object]] = [["日期", "标题与原因", "类别 / 区域", "状态", "金额"]]
    for item in changes:
        amount = item.amount_cents if item.change_type == "increase" else -item.amount_cents
        confirmation = f"\n确认：{item.confirmation_name}（{item.confirmation_role or '-'}）" if item.confirmation_name else ""
        change_rows.append([item.proposed_on.isoformat(), f"{item.title}\n{item.reason}{confirmation}", f"{item.category}\n{item.area or '-'}", _label(item.status), _money(amount)])
    if len(change_rows) == 1:
        change_rows.append(["-", "所选范围内没有增减项", "-", "-", "-"])
    story.append(_table(change_rows, [24 * mm, 67 * mm, 29 * mm, 27 * mm, 27 * mm], styles))

    story.extend([PageBreak(), _paragraph("付款节点与验收问题", styles["section"])])
    acceptance_by_milestone: dict[str, list[AcceptanceRecord]] = {}
    for item in acceptances:
        acceptance_by_milestone.setdefault(item.milestone_id, []).append(item)
    payment_by_milestone: dict[str, list[PaymentRecord]] = {}
    for item in payments:
        payment_by_milestone.setdefault(item.milestone_id, []).append(item)
    milestone_rows: list[list[object]] = [["付款节点", "计划日期", "计划金额", "有效实付", "最新验收", "未关闭问题"]]
    for milestone in milestones:
        latest = sorted(acceptance_by_milestone.get(milestone.id, []), key=lambda item: item.created_at)[-1] if acceptance_by_milestone.get(milestone.id) else None
        paid = sum((-item.amount_cents if item.record_type == "reversal" else item.amount_cents) for item in payment_by_milestone.get(milestone.id, []))
        milestone_rows.append([
            f"{milestone.name}\n{milestone.condition}", milestone.planned_date.isoformat(), _money(milestone.planned_amount_cents),
            _money(paid), f"{latest.accepted_on}\n{_label(latest.result)}" if latest else "尚无验收", str(latest.open_issues if latest else 0),
        ])
    story.append(_table(milestone_rows, [49 * mm, 24 * mm, 27 * mm, 27 * mm, 31 * mm, 18 * mm], styles))

    story.append(_paragraph("验收记录", styles["subsection"]))
    acceptance_rows: list[list[object]] = [["日期", "付款节点", "结果", "问题数", "记录与说明"]]
    milestone_names = {item.id: item.name for item in milestones}
    acceptance_rows.extend([[item.accepted_on.isoformat(), milestone_names.get(item.milestone_id, "-"), _label(item.result), item.open_issues, f"{item.recorded_by}\n{item.notes}"] for item in acceptances])
    if len(acceptance_rows) == 1:
        acceptance_rows.append(["-", "所选范围内没有验收记录", "-", "0", "-"])
    story.append(_table(acceptance_rows, [24 * mm, 35 * mm, 28 * mm, 20 * mm, 68 * mm], styles))

    story.append(_paragraph("付款与冲正记录", styles["subsection"]))
    payment_rows: list[list[object]] = [["日期", "付款节点", "类型", "收款方 / 方式", "金额", "凭证与说明"]]
    for item in payments:
        amount = -item.amount_cents if item.record_type == "reversal" else item.amount_cents
        payment_rows.append([item.paid_on.isoformat(), milestone_names.get(item.milestone_id, "-"), _label(item.record_type), f"{item.payee}\n{item.method}", _money(amount), f"{item.reference}\n{item.override_reason or ''}"])
    if len(payment_rows) == 1:
        payment_rows.append(["-", "所选范围内没有付款记录", "-", "-", "-", "-"])
    story.append(_table(payment_rows, [23 * mm, 29 * mm, 20 * mm, 39 * mm, 27 * mm, 38 * mm], styles))

    story.extend([PageBreak(), _paragraph("附件与原始报价目录", styles["section"])])
    story.append(_paragraph(f"本次档案{'包含' if include_attachments else '不包含'}原始附件文件。下列目录仍用于说明项目依据范围。", styles["body"]))
    for item in evidence:
        source = _safe_source(settings.upload_dir, project.id, item.object_key)
        thumbnail = _thumbnail(source) if include_attachments and source and item.mime_type.startswith("image/") else None
        metadata = _paragraph(f"{item.original_name}\n{item.evidence_type} · {item.created_at.date().isoformat()} · {item.size_bytes / 1024:,.1f} KB\n{item.description or '无补充说明'}", styles["table"])
        if thumbnail:
            card = Table([[thumbnail, metadata]], colWidths=[52 * mm, 117 * mm])
            card.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "MIDDLE"), ("BOX", (0, 0), (-1, -1), .5, colors.HexColor("#cdd3cc")), ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#f7f5ef")), ("LEFTPADDING", (0, 0), (-1, -1), 7), ("RIGHTPADDING", (0, 0), (-1, -1), 7), ("TOPPADDING", (0, 0), (-1, -1), 7), ("BOTTOMPADDING", (0, 0), (-1, -1), 7)]))
            story.extend([Spacer(1, 3 * mm), KeepTogether([card])])
        else:
            story.extend([Spacer(1, 2 * mm), _table([[item.original_name, item.evidence_type, item.created_at.date().isoformat(), f"{item.size_bytes / 1024:,.1f} KB", item.description or "-"]], [55 * mm, 29 * mm, 28 * mm, 22 * mm, 42 * mm], styles, header=False)])
    if not evidence:
        story.append(_paragraph("所选范围内没有证据附件。", styles["body"]))
    story.append(_paragraph("原始报价", styles["subsection"]))
    quote_rows: list[list[object]] = [["报价名称", "原文件", "解析状态", "条目总额", "创建日期"]]
    quote_rows.extend([[item.name, item.original_name, item.status, _money(item.total_cents), item.created_at.date().isoformat()] for item in quotes])
    if len(quote_rows) == 1:
        quote_rows.append(["所选范围内没有原始报价", "-", "-", "-", "-"])
    story.append(_table(quote_rows, [43 * mm, 50 * mm, 28 * mm, 29 * mm, 27 * mm], styles))

    story.extend([PageBreak(), _paragraph("项目时间线", styles["section"])])
    timeline_rows: list[list[object]] = [["时间", "事件", "详情", "操作人"]]
    timeline_rows.extend([[item.created_at.strftime("%Y-%m-%d\n%H:%M"), item.title, item.detail, item.actor] for item in events])
    if len(timeline_rows) == 1:
        timeline_rows.append(["-", "所选范围内没有时间线事件", "-", "-"])
    story.append(_table(timeline_rows, [30 * mm, 46 * mm, 69 * mm, 30 * mm], styles))
    story.extend([
        Spacer(1, 9 * mm),
        _paragraph("报告使用边界", styles["subsection"]),
        _paragraph("金额汇总与项目页面使用同一服务端计算口径。日期范围影响过程记录和附件目录，首页预算摘要反映生成时的项目当前状态。导出包中的原始文件仍应结合合同原件和实际现场核对。", styles["body"]),
    ])
    doc.build(story, onFirstPage=_page_chrome, onLaterPages=_page_chrome)
    return buffer.getvalue()


def create_project_archive(
    db: Session,
    project: Project,
    target_dir: Path,
    *,
    include_attachments: bool = True,
    date_from: date | None = None,
    date_to: date | None = None,
    object_name: str | None = None,
) -> ArchiveBuild:
    target_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    zip_path = target_dir / (object_name or f"project-{project.id[:8]}-{stamp}.zip")
    budget = calculate_budget(db, project)
    baselines = db.scalars(select(BaselineVersion).where(BaselineVersion.project_id == project.id).order_by(BaselineVersion.version)).all()
    milestones = db.scalars(select(PaymentMilestone).where(PaymentMilestone.project_id == project.id).order_by(PaymentMilestone.sort_order)).all()
    changes = [item for item in db.scalars(select(ChangeOrder).where(ChangeOrder.project_id == project.id).order_by(ChangeOrder.proposed_on)).all() if _within(item.proposed_on, date_from, date_to)]
    acceptances = [item for item in db.scalars(select(AcceptanceRecord).where(AcceptanceRecord.project_id == project.id).order_by(AcceptanceRecord.accepted_on)).all() if _within(item.accepted_on, date_from, date_to)]
    payments = [item for item in db.scalars(select(PaymentRecord).where(PaymentRecord.project_id == project.id).order_by(PaymentRecord.paid_on)).all() if _within(item.paid_on, date_from, date_to)]
    events = [item for item in db.scalars(select(AuditEvent).where(AuditEvent.project_id == project.id).order_by(AuditEvent.created_at)).all() if _within(item.created_at, date_from, date_to)]
    evidence = [item for item in db.scalars(select(Evidence).where(Evidence.project_id == project.id).order_by(Evidence.created_at)).all() if _within(item.created_at, date_from, date_to)]
    quotes = [item for item in db.scalars(select(Quote).where(Quote.project_id == project.id).order_by(Quote.created_at)).all() if _within(item.created_at, date_from, date_to)]

    report = _build_report(
        project, budget, list(baselines), changes, list(milestones), acceptances, payments, events, evidence, quotes,
        include_attachments=include_attachments, date_from=date_from, date_to=date_to,
    )
    page_count = len(PdfReader(io.BytesIO(report)).pages)

    budget_buffer = io.StringIO()
    budget_writer = csv.writer(budget_buffer)
    budget_writer.writerow(["类型", "标题/收款方", "状态/方式", "金额（元）", "日期"])
    for item in changes:
        amount = item.amount_cents if item.change_type == "increase" else -item.amount_cents
        budget_writer.writerow(["增减项", item.title, item.status, amount / 100, item.proposed_on.isoformat()])
    for item in payments:
        amount = -item.amount_cents if item.record_type == "reversal" else item.amount_cents
        budget_writer.writerow(["付款", item.payee, item.method, amount / 100, item.paid_on.isoformat()])

    acceptance_buffer = io.StringIO()
    acceptance_writer = csv.writer(acceptance_buffer)
    acceptance_writer.writerow(["付款节点", "计划日期", "计划金额（元）", "验收日期", "验收结果", "未关闭问题", "验收说明"])
    milestone_names = {item.id: item for item in milestones}
    for item in acceptances:
        milestone = milestone_names.get(item.milestone_id)
        acceptance_writer.writerow([milestone.name if milestone else "-", milestone.planned_date.isoformat() if milestone else "-", (milestone.planned_amount_cents / 100) if milestone else "-", item.accepted_on.isoformat(), item.result, item.open_issues, item.notes])

    timeline_buffer = io.StringIO()
    timeline_writer = csv.writer(timeline_buffer)
    timeline_writer.writerow(["时间", "事件", "详情", "操作人"])
    for event in events:
        timeline_writer.writerow([event.created_at.isoformat(), event.title, event.detail, event.actor])

    attachment_buffer = io.StringIO()
    attachment_writer = csv.writer(attachment_buffer)
    attachment_writer.writerow(["类型", "文件名", "业务类别", "大小（字节）", "创建时间", "说明"])
    for item in evidence:
        attachment_writer.writerow(["证据", item.original_name, item.evidence_type, item.size_bytes, item.created_at.isoformat(), item.description])
    for item in quotes:
        attachment_writer.writerow(["原始报价", item.original_name, item.status, "-", item.created_at.isoformat(), item.name])

    used = {"项目正式报告.pdf", "预算与记录.csv", "验收与付款.csv", "项目时间线.csv", "附件目录.csv", "导出清单.txt"}
    attachments: list[SourceAttachment] = []
    if include_attachments:
        for item in evidence:
            source = _safe_source(settings.upload_dir, project.id, item.object_key)
            if source:
                name = _safe_archive_name(item.original_name, f"证据-{item.id}")
                attachments.append(SourceAttachment(source, _unique_member("证据附件", name, used)))
        for quote in quotes:
            source = _safe_source(settings.upload_dir, project.id, "quotes", quote.object_key)
            if source:
                name = _safe_archive_name(quote.original_name, f"报价-{quote.id}")
                attachments.append(SourceAttachment(source, _unique_member("原始报价", name, used)))

    base_payload_size = sum(len(value.encode("utf-8")) for value in (
        budget_buffer.getvalue(), acceptance_buffer.getvalue(), timeline_buffer.getvalue(), attachment_buffer.getvalue(),
    )) + len(report)
    max_part_size = max(1, settings.export_part_size_mb * 1024 * 1024)
    should_split = bool(attachments and base_payload_size + sum(item.path.stat().st_size for item in attachments) > max_part_size)
    attachment_groups: list[list[SourceAttachment]] = []
    if should_split:
        current_group: list[SourceAttachment] = []
        current_size = 0
        for item in attachments:
            item_size = item.path.stat().st_size
            if current_group and current_size + item_size > max_part_size:
                attachment_groups.append(current_group)
                current_group = []
                current_size = 0
            current_group.append(item)
            current_size += item_size
        if current_group:
            attachment_groups.append(current_group)

    generated_at = datetime.now(timezone.utc).isoformat()
    range_text = f"{date_from.isoformat() if date_from else '项目开始'} 至 {date_to.isoformat() if date_to else '生成时'}"
    total_parts = 1 + len(attachment_groups)
    manifest = "\n".join([
        "装修项目正式档案导出清单",
        f"项目 ID：{project.id}",
        f"报告版本：{REPORT_VERSION}",
        f"PDF 页数：{page_count}",
        f"生成时间：{generated_at}",
        f"数据范围：{range_text}",
        f"包含附件：{'是' if include_attachments else '否'}",
        f"合同基线版本：{len(baselines)}",
        f"增减项记录：{len(changes)}",
        f"验收记录：{len(acceptances)}",
        f"付款记录：{len(payments)}",
        f"时间线事件：{len(events)}",
        f"证据附件：{len(evidence) if include_attachments else 0}",
        f"原始报价：{len(quotes) if include_attachments else 0}",
        f"档案分卷：{total_parts} 卷",
        f"附件拆分：{'是，附件位于独立分卷' if should_split else '否'}",
        "",
        "说明：汇总指标反映项目当前完整状态；明细、时间线和附件遵循所选日期范围。",
    ])

    parts: list[ArchivePart] = []
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("项目正式报告.pdf", report)
        archive.writestr("预算与记录.csv", "\ufeff" + budget_buffer.getvalue())
        archive.writestr("验收与付款.csv", "\ufeff" + acceptance_buffer.getvalue())
        archive.writestr("项目时间线.csv", "\ufeff" + timeline_buffer.getvalue())
        archive.writestr("附件目录.csv", "\ufeff" + attachment_buffer.getvalue())
        archive.writestr("导出清单.txt", manifest)
        if include_attachments and not should_split:
            for item in attachments:
                archive.write(item.path, item.member_name)
    parts.append(ArchivePart(zip_path, "项目档案-主卷.zip", "primary", 1))

    for group_index, group in enumerate(attachment_groups, start=2):
        part_path = zip_path.with_name(f"{zip_path.stem}-part-{group_index:02d}.zip")
        with zipfile.ZipFile(part_path, "w", zipfile.ZIP_DEFLATED) as archive:
            archive.writestr(
                "分卷说明.txt",
                f"装修项目档案附件分卷 {group_index}/{total_parts}\n项目 ID：{project.id}\n正式报告与结构化记录位于第 1 卷。",
            )
            for item in group:
                archive.write(item.path, item.member_name)
        parts.append(ArchivePart(part_path, f"项目档案-附件卷-{group_index:02d}.zip", "attachments", group_index))
    return ArchiveBuild(parts=tuple(parts), report_page_count=page_count)
