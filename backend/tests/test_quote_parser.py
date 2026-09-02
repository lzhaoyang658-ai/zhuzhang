from app.services.quote_parser import parse_quote, parse_text_rows, standardize_item_name


def test_csv_quote_parser_recalculates_rows():
    content = "项目名称,区域,类别,数量,单位,单价,合价\n墙面找平,厨房,泥瓦,18,㎡,120,2160\n新增插座,主卧,水电,2,个,180,360\n".encode("utf-8")
    result = parse_quote(content, ".csv")
    assert len(result) == 2
    assert result[0]["total_cents"] == 2160_00
    assert result[0]["source_location"] == "CSV!第 2 行"
    assert sum(item["total_cents"] for item in result) == 2520_00


def test_text_pdf_rows_keep_source_and_confidence():
    text = "1 墙顶面乳胶漆 86 ㎡ 52 4472\n2 强电点位 52 位 145 7540"
    result = parse_text_rows(text, page_number=3, confidence=81)
    assert len(result) == 2
    assert result[0]["standard_name"] == "乳胶漆涂刷"
    assert result[0]["source_location"] == "PDF 文本第 3 页·行 1"
    assert result[0]["confidence"] == 81
    assert result[1]["total_cents"] == 7540_00


def test_standard_name_synonyms_are_deterministic():
    assert standardize_item_name("装修垃圾清运费") == "垃圾清运"
    assert standardize_item_name("轻钢龙骨石膏板吊顶") == "石膏板吊顶"
