from __future__ import annotations

import re
from difflib import SequenceMatcher

from app.models import Quote, QuoteItem, QuoteMatchGroup


def _name(value: str) -> str:
    return re.sub(r"[^0-9a-zA-Z\u4e00-\u9fff]", "", value).lower()


def item_similarity(left: QuoteItem, right: QuoteItem) -> float:
    name_score = SequenceMatcher(None, _name(left.standard_name), _name(right.standard_name)).ratio()
    category_score = 1.0 if left.category == right.category else 0.0
    area_score = 1.0 if left.area and right.area and left.area == right.area else 0.0
    unit_score = 1.0 if left.unit and right.unit and left.unit.lower() == right.unit.lower() else 0.0
    return round(name_score * 0.7 + category_score * 0.15 + area_score * 0.1 + unit_score * 0.05, 4)


def _item_payload(item: QuoteItem) -> dict:
    return {
        "id": item.id,
        "quote_id": item.quote_id,
        "name": item.standard_name,
        "original_name": item.original_name,
        "area": item.area,
        "category": item.category,
        "quantity": item.quantity_text,
        "unit": item.unit,
        "unit_price_cents": item.unit_price_cents,
        "total_cents": item.total_cents,
        "material_info": item.material_info,
        "craft_notes": item.craft_notes,
        "source_location": item.source_location,
    }


def build_quote_comparison(quotes: list[Quote], manual_groups: list[QuoteMatchGroup] | None = None) -> dict:
    if not 2 <= len(quotes) <= 3:
        raise ValueError("报价对比需要选择 2～3 份候选报价")
    groups: list[dict] = []
    quote_ids = [quote.id for quote in quotes]
    manually_grouped_item_ids: set[str] = set()
    for manual in manual_groups or []:
        present = {
            member.quote_id: member.quote_item
            for member in manual.members
            if member.quote_id in quote_ids and member.quote_item is not None
        }
        if len(present) < 2:
            continue
        manually_grouped_item_ids.update(item.id for item in present.values())
        representative = next(iter(present.values()))
        groups.append(
            {
                "id": manual.id,
                "key": manual.canonical_name,
                "category": representative.category,
                "area": representative.area,
                "items": present,
                "scores": [],
                "match_type": "manual",
                "locked": True,
            }
        )

    first = quotes[0]
    for item in first.items:
        if item.id not in manually_grouped_item_ids:
            groups.append({"id": None, "key": item.standard_name, "category": item.category, "area": item.area, "items": {first.id: item}, "scores": [], "match_type": "suggested", "locked": False})

    for quote in quotes[1:]:
        for item in quote.items:
            if item.id in manually_grouped_item_ids:
                continue
            candidates = []
            for group in groups:
                if group["locked"] or quote.id in group["items"]:
                    continue
                representative = next(iter(group["items"].values()))
                candidates.append((item_similarity(representative, item), group))
            best_score, best_group = max(candidates, key=lambda pair: pair[0], default=(0.0, None))
            if best_group is not None and best_score >= 0.55:
                best_group["items"][quote.id] = item
                best_group["scores"].append(best_score)
            else:
                groups.append({"id": None, "key": item.standard_name, "category": item.category, "area": item.area, "items": {quote.id: item}, "scores": [], "match_type": "suggested", "locked": False})

    serialized_groups = []
    for index, group in enumerate(groups, 1):
        present = group["items"]
        totals = [item.total_cents for item in present.values()]
        scores = group["scores"]
        serialized_groups.append(
            {
                "id": group["id"] or f"suggested-{index}",
                "standard_name": group["key"],
                "category": group["category"],
                "area": group["area"],
                "match_type": group["match_type"],
                "match_confidence": 100 if group["match_type"] == "manual" else (round(sum(scores) / len(scores) * 100) if scores else (100 if len(present) == 1 else 0)),
                "missing_quote_ids": [quote_id for quote_id in quote_ids if quote_id not in present],
                "price_spread_cents": max(totals) - min(totals) if len(totals) > 1 else 0,
                "items": {quote_id: _item_payload(item) for quote_id, item in present.items()},
            }
        )
    return {
        "quotes": [
            {
                "id": quote.id,
                "name": quote.name,
                "status": quote.status,
                "total_cents": quote.total_cents,
                "item_count": len(quote.items),
            }
            for quote in quotes
        ],
        "summary": {
            "lowest_total_cents": min(quote.total_cents for quote in quotes),
            "highest_total_cents": max(quote.total_cents for quote in quotes),
            "total_spread_cents": max(quote.total_cents for quote in quotes) - min(quote.total_cents for quote in quotes),
            "matched_group_count": sum(1 for group in serialized_groups if not group["missing_quote_ids"]),
            "incomplete_group_count": sum(1 for group in serialized_groups if group["missing_quote_ids"]),
        },
        "groups": serialized_groups,
        "notice": "匹配结果是待确认建议；差异金额由系统确定性计算，不构成签约建议。",
    }
