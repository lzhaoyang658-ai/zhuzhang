from __future__ import annotations

import base64
import io
import json
import re
import time
from dataclasses import dataclass, field
from typing import Any

import httpx
from PIL import Image, ImageOps, UnidentifiedImageError
from pydantic import BaseModel, ConfigDict, Field, ValidationError


AI_PARSER_VERSION = "qwen-vision-1"
RETRYABLE_STATUS_CODES = {408, 409, 429, 500, 502, 503, 504}


class QuoteAIError(ValueError):
    """Safe model error that can be shown without exposing credentials or source data."""


class AIQuoteItem(BaseModel):
    model_config = ConfigDict(extra="ignore", coerce_numbers_to_str=True)

    original_name: str
    area: str | None = None
    category: str | None = None
    quantity: str | None = None
    unit: str | None = None
    unit_price: str | None = None
    total: str
    material_info: str | None = None
    craft_notes: str | None = None
    source_excerpt: str | None = None
    confidence: int | None = Field(default=None, ge=0, le=100)


class AIPagePayload(BaseModel):
    model_config = ConfigDict(extra="ignore", coerce_numbers_to_str=True)

    page_type: str = "unknown"
    items: list[AIQuoteItem] = Field(default_factory=list)
    source_total: str | None = None
    warnings: list[str] = Field(default_factory=list)


@dataclass
class AIParseResult:
    items: list[dict[str, Any]]
    page_count: int
    parse_method: str
    model: str
    source_total: str | None = None
    warnings: list[str] = field(default_factory=list)
    input_tokens: int = 0
    output_tokens: int = 0
    parser_version: str = AI_PARSER_VERSION


SYSTEM_PROMPT = """你是装修报价单结构化录入器。只读取文件中明确存在的信息，不猜测、不补全、不评价价格。
金额统一输出为人民币元的数字字符串，不要输出货币符号或千位逗号。合计、小计、税费汇总行不作为施工条目。
必须返回一个有效 JSON 对象，不要使用 Markdown 代码块。"""

PAGE_PROMPT = """提取这一页装修报价单中的施工明细。返回以下结构：
{
  "page_type": "quote_table|cover|terms|unknown",
  "items": [{
    "original_name": "原始项目名称",
    "area": "施工空间或 null",
    "category": "原表分类或 null",
    "quantity": "数量原文或 null",
    "unit": "单位或 null",
    "unit_price": "单价（元）或 null",
    "total": "该行合价（元）",
    "material_info": "品牌规格材料或 null",
    "craft_notes": "工艺做法或 null",
    "source_excerpt": "对应原行的简短原文",
    "confidence": 0到100的整数
  }],
  "source_total": "页面明确出现的整份报价总计（元）或 null",
  "warnings": ["无法确认的信息"]
}
要求：
1. 每个 items 元素必须有 original_name 和 total；看不清合价的行不要输出。
2. 不把序号当数量，不把单价当合价。
3. 跨行项目合并成一条；重复表头和页脚不要输出。
4. source_excerpt 尽量逐字保留项目名、数量、单位、单价、合价。
5. 如果本页没有报价明细，items 返回空数组。"""

OCR_PROMPT = """完整转录这一页装修报价单。保留表格每一行的列顺序、项目名称、数量、单位、单价和合价；不要总结、不要改写、不要计算。只输出纯文本。"""

TEXT_NORMALIZE_PROMPT = """下面是 OCR 转录的装修报价单页面。按照指定 JSON 结构提取施工明细。只使用文本中明确存在的数据，不猜测。\n\n"""


def _strip_json_fence(value: str) -> str:
    text = value.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.IGNORECASE)
        text = re.sub(r"\s*```$", "", text)
    return text.strip()


def _safe_api_message(response: httpx.Response) -> str:
    try:
        payload = response.json()
    except ValueError:
        return f"HTTP {response.status_code}"
    message = payload.get("message") or (payload.get("error") or {}).get("message")
    return str(message or f"HTTP {response.status_code}")[:240]


def _content_from_response(payload: dict[str, Any]) -> str:
    try:
        content = payload["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as exc:
        raise QuoteAIError("模型返回结构异常") from exc
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(str(part.get("text", "")) for part in content if isinstance(part, dict))
    raise QuoteAIError("模型未返回可读取内容")


def _usage(payload: dict[str, Any]) -> tuple[int, int]:
    usage = payload.get("usage") or {}
    return int(usage.get("prompt_tokens") or 0), int(usage.get("completion_tokens") or 0)


def _image_data_url(content: bytes, mime_type: str) -> str:
    return f"data:{mime_type};base64,{base64.b64encode(content).decode('ascii')}"


def _prepare_raster(content: bytes, suffix: str) -> tuple[bytes, str]:
    try:
        with Image.open(io.BytesIO(content)) as image:
            normalized = ImageOps.exif_transpose(image).convert("RGB")
            max_pixels = 8_000_000
            if normalized.width * normalized.height > max_pixels:
                ratio = (max_pixels / (normalized.width * normalized.height)) ** 0.5
                normalized = normalized.resize((max(1, round(normalized.width * ratio)), max(1, round(normalized.height * ratio))))
            output = io.BytesIO()
            normalized.save(output, format="JPEG", quality=88, optimize=True)
            return output.getvalue(), "image/jpeg"
    except (UnidentifiedImageError, OSError):
        if suffix == ".heic" and len(content) <= 20 * 1024 * 1024:
            return content, "image/heic"
        raise QuoteAIError("图片无法转换为模型支持的格式")


def render_document_pages(content: bytes, suffix: str, page_count: int, max_pages: int) -> tuple[list[tuple[int, bytes, str]], list[str]]:
    warnings: list[str] = []
    if suffix != ".pdf":
        raster, mime_type = _prepare_raster(content, suffix)
        return [(1, raster, mime_type)], warnings
    try:
        import pymupdf
    except ImportError as exc:
        raise QuoteAIError("当前环境未安装 PDF 渲染组件") from exc
    pages: list[tuple[int, bytes, str]] = []
    try:
        with pymupdf.open(stream=content, filetype="pdf") as document:
            limit = min(len(document), max_pages)
            matrix = pymupdf.Matrix(170 / 72, 170 / 72)
            for index in range(limit):
                pixmap = document[index].get_pixmap(matrix=matrix, alpha=False)
                pages.append((index + 1, pixmap.tobytes("jpeg", jpg_quality=88), "image/jpeg"))
    except (RuntimeError, ValueError) as exc:
        raise QuoteAIError("扫描 PDF 无法转换为模型图片") from exc
    if page_count > max_pages:
        warnings.append(f"扫描 PDF 共 {page_count} 页，模型本次仅识别前 {max_pages} 页")
    if not pages:
        raise QuoteAIError("扫描 PDF 没有可识别页面")
    return pages, warnings


class QwenQuoteParser:
    def __init__(
        self,
        *,
        api_key: str,
        base_url: str,
        primary_model: str,
        fallback_model: str,
        ocr_model: str,
        timeout_seconds: float = 90,
        max_retries: int = 2,
        max_pages: int = 20,
        client: httpx.Client | None = None,
    ) -> None:
        if not api_key.strip():
            raise QuoteAIError("未配置百炼 API Key")
        self.api_key = api_key.strip()
        self.base_url = base_url.rstrip("/")
        self.primary_model = primary_model
        self.fallback_model = fallback_model
        self.ocr_model = ocr_model
        self.max_retries = max(0, max_retries)
        self.max_pages = max(1, max_pages)
        self.client = client or httpx.Client(timeout=httpx.Timeout(timeout_seconds, connect=15.0))
        self._owns_client = client is None

    def close(self) -> None:
        if self._owns_client:
            self.client.close()

    def __enter__(self) -> QwenQuoteParser:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def _post(self, body: dict[str, Any]) -> dict[str, Any]:
        last_error = "模型服务暂时不可用"
        for attempt in range(self.max_retries + 1):
            try:
                response = self.client.post(
                    f"{self.base_url}/chat/completions",
                    headers={"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"},
                    json=body,
                )
            except (httpx.TimeoutException, httpx.NetworkError) as exc:
                last_error = "模型请求超时或网络不可用"
                if attempt >= self.max_retries:
                    raise QuoteAIError(last_error) from exc
            else:
                if response.is_success:
                    try:
                        return response.json()
                    except ValueError as exc:
                        raise QuoteAIError("模型返回了无效响应") from exc
                last_error = _safe_api_message(response)
                if response.status_code not in RETRYABLE_STATUS_CODES or attempt >= self.max_retries:
                    raise QuoteAIError(f"模型调用失败：{last_error}")
            time.sleep(min(2**attempt, 4))
        raise QuoteAIError(last_error)

    def _vision_page(self, model: str, image: bytes, mime_type: str) -> tuple[AIPagePayload, tuple[int, int]]:
        payload = self._post(
            {
                "model": model,
                "messages": [
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {
                        "role": "user",
                        "content": [
                            {"type": "image_url", "image_url": {"url": _image_data_url(image, mime_type)}},
                            {"type": "text", "text": PAGE_PROMPT},
                        ],
                    },
                ],
                "response_format": {"type": "json_object"},
                "temperature": 0,
                "enable_thinking": False,
                "max_tokens": 8192,
            }
        )
        try:
            parsed = AIPagePayload.model_validate_json(_strip_json_fence(_content_from_response(payload)))
        except ValidationError as exc:
            raise QuoteAIError("模型结构化结果未通过字段校验") from exc
        return parsed, _usage(payload)

    def _ocr_page(self, image: bytes, mime_type: str) -> tuple[str, tuple[int, int]]:
        payload = self._post(
            {
                "model": self.ocr_model,
                "messages": [
                    {
                        "role": "user",
                        "content": [
                            {"type": "image_url", "image_url": {"url": _image_data_url(image, mime_type)}},
                            {"type": "text", "text": OCR_PROMPT},
                        ],
                    }
                ],
                "temperature": 0,
                "max_tokens": 8192,
            }
        )
        return _content_from_response(payload).strip(), _usage(payload)

    def _normalize_ocr_text(self, text: str) -> tuple[AIPagePayload, tuple[int, int]]:
        payload = self._post(
            {
                "model": self.fallback_model,
                "messages": [
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": TEXT_NORMALIZE_PROMPT + PAGE_PROMPT + "\n\nOCR 文本：\n" + text[:50_000]},
                ],
                "response_format": {"type": "json_object"},
                "temperature": 0,
                "enable_thinking": False,
                "max_tokens": 8192,
            }
        )
        try:
            parsed = AIPagePayload.model_validate_json(_strip_json_fence(_content_from_response(payload)))
        except ValidationError as exc:
            raise QuoteAIError("OCR 二次结构化结果未通过字段校验") from exc
        return parsed, _usage(payload)

    @staticmethod
    def _quality(pages: list[tuple[int, AIPagePayload]]) -> float:
        items = [item for _, page in pages for item in page.items]
        if not items:
            return 0
        completeness = sum(bool(item.quantity and item.unit and item.unit_price) for item in items) / len(items)
        confidence_values = [item.confidence for item in items if item.confidence is not None]
        confidence = (sum(confidence_values) / len(confidence_values) / 100) if confidence_values else 0.72
        excerpts = sum(bool(item.source_excerpt) for item in items) / len(items)
        coverage = min(len(items) / 8, 1)
        quote_pages = [page for _, page in pages if page.page_type == "quote_table"]
        page_consistency = 1 if not quote_pages or all(page.items for page in quote_pages) else 0
        return completeness * 30 + confidence * 40 + excerpts * 10 + coverage * 10 + page_consistency * 10

    @staticmethod
    def _needs_fallback(pages: list[tuple[int, AIPagePayload]]) -> bool:
        items = [item for _, page in pages for item in page.items]
        if not items:
            return True
        quote_pages = [page for _, page in pages if page.page_type == "quote_table"]
        if any(not page.items for page in quote_pages):
            return True
        completeness = sum(bool(item.quantity and item.unit and item.unit_price) for item in items) / len(items)
        confidence_values = [item.confidence for item in items if item.confidence is not None]
        average_confidence = sum(confidence_values) / len(confidence_values) if confidence_values else 72
        return completeness < 0.7 or average_confidence < 75

    def _run_vision(self, model: str, pages: list[tuple[int, bytes, str]]) -> tuple[list[tuple[int, AIPagePayload]], int, int]:
        parsed_pages: list[tuple[int, AIPagePayload]] = []
        input_tokens = output_tokens = 0
        for page_number, image, mime_type in pages:
            parsed, usage = self._vision_page(model, image, mime_type)
            parsed_pages.append((page_number, parsed))
            input_tokens += usage[0]
            output_tokens += usage[1]
        return parsed_pages, input_tokens, output_tokens

    def parse_document(self, content: bytes, suffix: str, page_count: int) -> AIParseResult:
        pages, render_warnings = render_document_pages(content, suffix, page_count, self.max_pages)
        errors: list[str] = []
        candidates: list[tuple[str, list[tuple[int, AIPagePayload]], int, int]] = []
        for model in (self.primary_model, self.fallback_model):
            if any(candidate[0] == model for candidate in candidates):
                continue
            try:
                parsed_pages, input_tokens, output_tokens = self._run_vision(model, pages)
                candidates.append((model, parsed_pages, input_tokens, output_tokens))
                if not self._needs_fallback(parsed_pages):
                    break
            except QuoteAIError as exc:
                errors.append(f"{model}: {exc}")

        best = max(candidates, key=lambda candidate: self._quality(candidate[1]), default=None)
        if best is None or not any(page.items for _, page in best[1]):
            try:
                ocr_pages: list[tuple[int, AIPagePayload]] = []
                input_tokens = output_tokens = 0
                for page_number, image, mime_type in pages:
                    text, first_usage = self._ocr_page(image, mime_type)
                    if not text:
                        continue
                    parsed, second_usage = self._normalize_ocr_text(text)
                    ocr_pages.append((page_number, parsed))
                    input_tokens += first_usage[0] + second_usage[0]
                    output_tokens += first_usage[1] + second_usage[1]
                if any(page.items for _, page in ocr_pages):
                    best = (f"{self.ocr_model}+{self.fallback_model}", ocr_pages, input_tokens, output_tokens)
            except QuoteAIError as exc:
                errors.append(f"OCR pipeline: {exc}")

        if best is None or not any(page.items for _, page in best[1]):
            detail = "；".join(errors[-2:]) if errors else "模型未识别出报价明细"
            raise QuoteAIError(f"AI 解析失败：{detail}")

        model, parsed_pages, input_tokens, output_tokens = best
        raw_items: list[dict[str, Any]] = []
        warnings = list(render_warnings)
        source_total = None
        for page_number, page in parsed_pages:
            warnings.extend(str(warning)[:240] for warning in page.warnings if warning)
            if page.source_total:
                source_total = page.source_total
            for index, item in enumerate(page.items, 1):
                raw_items.append({**item.model_dump(), "page_number": page_number, "item_number": index})
        warnings.append(f"AI 模型：{model}；输入 {input_tokens} tokens，输出 {output_tokens} tokens")
        method = "ai_qwen_ocr_plus" if "+" in model else ("ai_qwen_fallback" if model == self.fallback_model else "ai_qwen_vision")
        return AIParseResult(
            items=raw_items,
            page_count=page_count,
            parse_method=method,
            model=model,
            source_total=source_total,
            warnings=warnings,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
        )
