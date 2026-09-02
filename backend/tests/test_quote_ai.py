import io
import json

import httpx
from PIL import Image

from app.services.quote_ai import QwenQuoteParser
from app.services.quote_parser import _normalize_ai_items


def image_bytes() -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (80, 60), "white").save(buffer, format="JPEG")
    return buffer.getvalue()


def api_response(content: dict, prompt_tokens: int = 100, completion_tokens: int = 30) -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "choices": [{"message": {"content": json.dumps(content, ensure_ascii=False)}}],
            "usage": {"prompt_tokens": prompt_tokens, "completion_tokens": completion_tokens},
        },
    )


def test_low_confidence_primary_automatically_uses_fallback():
    called_models = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        called_models.append(body["model"])
        confidence = 60 if body["model"] == "primary" else 94
        return api_response(
            {
                "page_type": "quote_table",
                "items": [
                    {
                        "original_name": "墙面基层处理",
                        "quantity": "10",
                        "unit": "㎡",
                        "unit_price": "38",
                        "total": "380",
                        "source_excerpt": "墙面基层处理 10 ㎡ 38 380",
                        "confidence": confidence,
                    }
                ],
            }
        )

    client = httpx.Client(transport=httpx.MockTransport(handler))
    parser = QwenQuoteParser(
        api_key="test-key",
        base_url="https://example.test/v1",
        primary_model="primary",
        fallback_model="fallback",
        ocr_model="ocr",
        client=client,
    )
    result = parser.parse_document(image_bytes(), ".jpg", 1)

    assert called_models == ["primary", "fallback"]
    assert result.model == "fallback"
    assert result.parse_method == "ai_qwen_fallback"
    assert result.input_tokens == 100
    assert result.output_tokens == 30


def test_ai_item_normalization_flags_arithmetic_mismatch():
    items, warnings = _normalize_ai_items(
        [
            {
                "original_name": "新增插座",
                "quantity": "2",
                "unit": "个",
                "unit_price": "180",
                "total": "500",
                "confidence": 92,
                "page_number": 2,
                "item_number": 3,
            }
        ]
    )

    assert items[0]["unit_price_cents"] == 18_000
    assert items[0]["total_cents"] == 50_000
    assert items[0]["confidence"] == 65
    assert items[0]["source_location"] == "AI 视觉第 2 页·条目 3"
    assert "数量×单价与合价不一致" in warnings[0]
