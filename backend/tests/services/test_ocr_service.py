"""
backend/tests/services/test_ocr_service.py
发票 OCR 服务工业级核心准则全面回归测试：
1. 正常单税率发票 (13% 增值税发票)
2. 17% 老版增值税发票 (真实发票 94578.64 + 16078.36 = 110657.00)
3. 6% 发票 (住宿/现代服务发票 801.89 + 48.11 = 850.00)
4. 9% 发票 (交通运输发票 596.33 + 53.67 = 650.00)
5. 多税率发票 (表体含 13% 和 9% 复合税率)
6. OCR 缺少税额 (通过 Total - Untaxed 安全推导 DERIVED)
7. OCR 缺少不含税金额 (通过 Total - Tax 安全推导 DERIVED)
8. OCR 缺少总金额 (严禁默认 1000.00，标记 MISSING 与 NEED_REVIEW)
9. OCR 金额识别冲突 (严禁盲目猜测，标记 CONFLICT/NEED_REVIEW)
10. OCR 完全失败 (返回 FAILED，所有要素均为 None，严禁伪造模拟数据)
11. PDF 原生文本发票 (真实文本流提取，严禁写死 6% 反算)
12. sample_type 显式仿真模式 (仅显式传入 sample_type 时才启用模拟数据)
"""
import os
import io
import pytest
from decimal import Decimal
from unittest.mock import patch
from app.services.ocr_service import (
    InvoiceOcrService,
    OcrToken,
    TokenSource,
    FieldStatus,
    ChineseCurrencyParser
)


def _make_token(text: str, bbox=None, conf=0.98, source=TokenSource.RAPIDOCR) -> OcrToken:
    return OcrToken(text=text, bbox=bbox or [100, 100, 120, 200], confidence=conf, source=source)


# -----------------------------------------------------------------------------
# 1. 正常单税率发票 (13%)
# -----------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_normal_single_rate_invoice_13pct():
    tokens = [
        _make_token("增值税电子专用发票", [50, 400, 80, 600]),
        _make_token("发票号码: 23112000000888888888", [100, 600, 120, 900]),
        _make_token("发票代码: 011002000111", [80, 600, 100, 900]),
        _make_token("开票日期: 2026年03月15日", [140, 600, 160, 900]),
        _make_token("购买方 名称: 北京科技发展有限公司", [200, 100, 220, 400]),
        _make_token("纳税人识别号: 91110108MA01XXXXXX", [230, 100, 250, 400]),
        _make_token("销售方 名称: 上海制造工业有限公司", [650, 100, 670, 400]),
        _make_token("纳税人识别号: 91310101746182937X", [680, 100, 700, 400]),
        # 表体明细行 (ymin: 350)
        _make_token("10000.00", [350, 700, 370, 780]),
        _make_token("13%", [350, 800, 370, 840]),
        _make_token("1300.00", [350, 880, 370, 940]),
        # 合计行 (ymin: 600)
        _make_token("合计 ¥10000.00", [600, 650, 620, 750]),
        _make_token("¥1300.00", [600, 850, 620, 950]),
        # 价税合计 (ymin: 750)
        _make_token("价税合计（小写）¥11300.00", [750, 650, 780, 950]),
    ]

    with patch.object(InvoiceOcrService, "_extract_tokens_from_image", return_value=tokens):
        res = await InvoiceOcrService.parse_uploaded_file("invoice_13pct.jpg", b"fake_content")

    inv = res["invoice_data"]
    assert res["parse_status"] == "SUCCESS"
    assert inv["total_amount"] == 11300.00
    assert inv["untaxed_amount"] == 10000.00
    assert inv["tax_amount"] == 1300.00
    assert inv["tax_rate"] == 0.13
    assert res["validation"]["amount_equation_valid"] is True
    assert inv["field_details"]["tax_amount"]["status"] == "CONFIRMED"


# -----------------------------------------------------------------------------
# 2. 17% 老版增值税发票 (当前验收发票: 94578.64 + 16078.36 = 110657.00)
# -----------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_legacy_17pct_vat_invoice():
    tokens = [
        _make_token("增值税专用发票", [50, 400, 80, 600]),
        _make_token("No 05073978", [90, 700, 110, 850]),
        _make_token("3400174130", [70, 700, 90, 850]),
        _make_token("开票日期：2017年12月01日", [120, 700, 140, 900]),
        _make_token("购买方 名称：六安江淮电机有限公司", [200, 100, 220, 450]),
        _make_token("纳税人识别号：9134150072554518XQ", [230, 100, 250, 450]),
        _make_token("销售方 名称：合肥市日普贸易有限公司", [650, 100, 670, 450]),
        _make_token("纳税人识别号：91340100748916334H", [680, 100, 700, 450]),
        # 明细行 1
        _make_token("18741.61", [441, 723, 460, 778]),
        _make_token("17%", [440, 800, 460, 840]),
        _make_token("3186.07", [438, 893, 457, 946]),
        # 明细行 2
        _make_token("26410.79", [463, 724, 483, 780]),
        _make_token("17%", [460, 800, 480, 840]),
        _make_token("4489.83", [459, 896, 481, 946]),
        # 明细行 3
        _make_token("49426.24", [486, 725, 504, 779]),
        _make_token("17%", [480, 800, 500, 840]),
        _make_token("8402.46", [484, 896, 502, 947]),
        # 合计行
        _make_token("合 计 ¥94578.64", [620, 600, 640, 750]),
        _make_token("¥16078.36", [620, 850, 640, 950]),
        # 价税合计
        _make_token("价税合计（大写）壹拾壹万零陆佰伍拾柒圆整", [720, 100, 740, 500]),
        _make_token("（小写）¥110657.00", [720, 650, 740, 950])
    ]

    with patch.object(InvoiceOcrService, "_extract_tokens_from_image", return_value=tokens):
        res = await InvoiceOcrService.parse_uploaded_file("inv_d70ddfc1e7a2.jpg", b"fake_content")

    inv = res["invoice_data"]
    # 核心指标验证：
    assert inv["total_amount"] == 110657.00
    assert inv["untaxed_amount"] == 94578.64, "不含税金额必须为 94578.64，严禁为 0 或 6% 反算假值"
    assert inv["tax_amount"] == 16078.36, "税额必须为 16078.36，严禁被反算为 6263.60"
    assert inv["tax_rate"] == 0.17, "税率必须为真实 17%，严禁写死 6%"
    assert inv["invoice_number"] == "05073978"
    assert inv["seller_name"] == "合肥市日普贸易有限公司"
    assert inv["seller_tax_id"] == "91340100748916334H"
    # 数学勾稽层级全通：
    assert res["validation"]["amount_equation_valid"] is True
    assert res["validation"]["detail_amount_sum_valid"] is True
    assert res["validation"]["detail_tax_sum_valid"] is True
    assert res["validation"]["tax_rate_consistent"] is True
    assert res["quality_issues"] == []


# -----------------------------------------------------------------------------
# 3. 6% 发票 (住宿服务)
# -----------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_6pct_service_invoice():
    tokens = [
        _make_token("增值税普通发票", [50, 400, 80, 600]),
        _make_token("发票号码: 88123456", [100, 600, 120, 800]),
        _make_token("金额 ¥801.89", [600, 650, 620, 750]),
        _make_token("税额 ¥48.11", [600, 850, 620, 950]),
        _make_token("税率: 6%", [500, 800, 520, 850]),
        _make_token("价税合计（小写）¥850.00", [750, 650, 780, 900]),
    ]

    with patch.object(InvoiceOcrService, "_extract_tokens_from_image", return_value=tokens):
        res = await InvoiceOcrService.parse_uploaded_file("hotel_6pct.jpg", b"fake_content")

    inv = res["invoice_data"]
    assert inv["total_amount"] == 850.00
    assert inv["untaxed_amount"] == 801.89
    assert inv["tax_amount"] == 48.11
    assert inv["tax_rate"] == 0.06
    assert res["validation"]["amount_equation_valid"] is True


# -----------------------------------------------------------------------------
# 4. 9% 发票 (交通运输)
# -----------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_9pct_transportation_invoice():
    tokens = [
        _make_token("铁路电子客票", [50, 400, 80, 600]),
        _make_token("发票号码: G2026091512", [100, 600, 120, 800]),
        _make_token("金额 ¥596.33", [600, 650, 620, 750]),
        _make_token("税额 ¥53.67", [600, 850, 620, 950]),
        _make_token("税率 9%", [500, 800, 520, 850]),
        _make_token("价税合计 ¥650.00", [750, 650, 780, 900]),
    ]

    with patch.object(InvoiceOcrService, "_extract_tokens_from_image", return_value=tokens):
        res = await InvoiceOcrService.parse_uploaded_file("train_9pct.jpg", b"fake_content")

    inv = res["invoice_data"]
    assert inv["total_amount"] == 650.00
    assert inv["untaxed_amount"] == 596.33
    assert inv["tax_amount"] == 53.67
    assert inv["tax_rate"] == 0.09
    assert res["validation"]["amount_equation_valid"] is True


# -----------------------------------------------------------------------------
# 5. 多税率发票 (13% 与 9% 复合明细)
# -----------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_multi_rate_invoice():
    tokens = [
        _make_token("发票号码: 11223344", [100, 600, 120, 800]),
        # 明细 1: 1000.00, 13% -> 130.00
        _make_token("1000.00", [350, 700, 370, 780]),
        _make_token("13%", [350, 800, 370, 840]),
        _make_token("130.00", [350, 880, 370, 940]),
        # 明细 2: 2000.00, 9% -> 180.00
        _make_token("2000.00", [450, 700, 470, 780]),
        _make_token("9%", [450, 800, 470, 840]),
        _make_token("180.00", [450, 880, 470, 940]),
        # 合计行 (untaxed=3000.00, tax=310.00)
        _make_token("合计 ¥3000.00", [600, 650, 620, 750]),
        _make_token("¥310.00", [600, 850, 620, 950]),
        # 价税合计 (total=3310.00)
        _make_token("价税合计（小写）¥3310.00", [750, 650, 780, 900]),
    ]

    with patch.object(InvoiceOcrService, "_extract_tokens_from_image", return_value=tokens):
        res = await InvoiceOcrService.parse_uploaded_file("multi_rate.jpg", b"fake_content")

    inv = res["invoice_data"]
    assert inv["total_amount"] == 3310.00
    assert inv["untaxed_amount"] == 3000.00
    assert inv["tax_amount"] == 310.00
    assert res["validation"]["amount_equation_valid"] is True
    assert res["validation"]["detail_amount_sum_valid"] is True
    assert res["validation"]["detail_tax_sum_valid"] is True


# -----------------------------------------------------------------------------
# 6. OCR 缺少税额 (通过 Total - Untaxed 安全推导 DERIVED)
# -----------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_ocr_missing_tax_amount_derived():
    tokens = [
        _make_token("发票号码: 11223344", [100, 600, 120, 800]),
        _make_token("金额 ¥900.00", [600, 650, 620, 750]),
        _make_token("价税合计（小写）¥1000.00", [750, 650, 780, 900]),
        # 注意：此处完全不提供 tax_amount 的 token
    ]

    with patch.object(InvoiceOcrService, "_extract_tokens_from_image", return_value=tokens):
        res = await InvoiceOcrService.parse_uploaded_file("missing_tax.jpg", b"fake_content")

    inv = res["invoice_data"]
    assert inv["total_amount"] == 1000.00
    assert inv["untaxed_amount"] == 900.00
    assert inv["tax_amount"] == 100.00
    # 严格验证：来源必须标记为 derived，绝不冒充 OCR 原始值！
    assert inv["field_details"]["tax_amount"]["status"] == "DERIVED"
    assert inv["field_details"]["tax_amount"]["source"] == "derived"
    assert res["validation"]["amount_equation_valid"] is True


# -----------------------------------------------------------------------------
# 7. OCR 缺少不含税金额 (通过 Total - Tax 安全推导 DERIVED)
# -----------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_ocr_missing_untaxed_amount_derived():
    tokens = [
        _make_token("发票号码: 11223344", [100, 600, 120, 800]),
        _make_token("税额 ¥60.00", [600, 850, 620, 950]),
        _make_token("价税合计（小写）¥1060.00", [750, 650, 780, 900]),
        # 注意：此处完全不提供 untaxed_amount 的 token
    ]

    with patch.object(InvoiceOcrService, "_extract_tokens_from_image", return_value=tokens):
        res = await InvoiceOcrService.parse_uploaded_file("missing_untaxed.jpg", b"fake_content")

    inv = res["invoice_data"]
    assert inv["total_amount"] == 1060.00
    assert inv["tax_amount"] == 60.00
    assert inv["untaxed_amount"] == 1000.00
    # 严格验证：来源必须标记为 derived
    assert inv["field_details"]["untaxed_amount"]["status"] == "DERIVED"
    assert inv["field_details"]["untaxed_amount"]["source"] == "derived"


# -----------------------------------------------------------------------------
# 8. OCR 缺少总金额 (严禁伪造 1000.00，返回 MISSING 与 NEED_REVIEW)
# -----------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_ocr_missing_total_amount():
    tokens = [
        _make_token("发票号码: 11223344", [100, 600, 120, 800]),
        _make_token("未见任何金额数字", [200, 200, 250, 400])
    ]

    with patch.object(InvoiceOcrService, "_extract_tokens_from_image", return_value=tokens):
        res = await InvoiceOcrService.parse_uploaded_file("no_money.jpg", b"fake_content")

    inv = res["invoice_data"]
    # 核心验证：严禁造假 1000.00！必须为 None！
    assert inv["total_amount"] is None
    assert inv["untaxed_amount"] is None
    assert inv["tax_amount"] is None
    assert inv["field_details"]["total_amount"]["status"] == "MISSING"
    assert "INVOICE_TOTAL_AMOUNT_MISSING" in res["quality_issues"]
    assert res["parse_status"] == "NEED_REVIEW"


# -----------------------------------------------------------------------------
# 9. OCR 金额识别冲突 (严禁盲目猜测，标记 CONFLICT/NEED_REVIEW)
# -----------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_ocr_amount_conflict():
    tokens = [
        _make_token("发票号码: 11223344", [100, 600, 120, 800]),
        # 提供三个互不满足等式的金额
        _make_token("金额 ¥700.00", [600, 650, 620, 750]),
        _make_token("税额 ¥100.00", [600, 850, 620, 950]),
        _make_token("价税合计（小写）¥1000.00", [750, 650, 780, 900]),
    ]

    with patch.object(InvoiceOcrService, "_extract_tokens_from_image", return_value=tokens):
        res = await InvoiceOcrService.parse_uploaded_file("conflict.jpg", b"fake_content")

    # 700 + 100 != 1000，无法构成勾稽三元组，缺失要素被准确标注
    assert res["validation"]["amount_equation_valid"] is False


# -----------------------------------------------------------------------------
# 10. OCR 完全失败 (严禁伪造模拟数据，返回 FAILED)
# -----------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_ocr_complete_failure():
    # 模拟未识别出任何 Token
    with patch.object(InvoiceOcrService, "_extract_tokens_from_image", return_value=[]):
        res = await InvoiceOcrService.parse_uploaded_file("blank.png", b"empty_or_blur")

    assert res["file_info"]["ocr_status"] == "FAILED"
    assert res["parse_status"] == "FAILED"
    assert "OCR_PARSE_FAILED" in res["quality_issues"]
    inv = res["invoice_data"]
    # 必须全部为 None，绝不回退至启发式模拟发票！
    assert inv["invoice_number"] is None
    assert inv["total_amount"] is None
    assert inv["untaxed_amount"] is None
    assert inv["tax_amount"] is None
    assert inv["seller_name"] is None
    assert inv["bbox_positions"] == {}


# -----------------------------------------------------------------------------
# 11. PDF 原生文本发票 (真实解析，不产生 fake 6%)
# -----------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_pdf_native_text_invoice():
    tokens = [
        _make_token("电子发票(普通发票)", source=TokenSource.PYMUPDF),
        _make_token("发票号码: 24612000000101644605", source=TokenSource.PYMUPDF),
        _make_token("开票日期: 2024年10月18日", source=TokenSource.PYMUPDF),
        _make_token("购买方 名称: 某某大学", source=TokenSource.PYMUPDF),
        _make_token("销售方 名称: 某某商务酒店有限公司", source=TokenSource.PYMUPDF),
        _make_token("纳税人识别号: 916100006641460294", source=TokenSource.PYMUPDF),
        _make_token("合计 ¥2000.00", source=TokenSource.PYMUPDF),
        _make_token("¥60.00", source=TokenSource.PYMUPDF),
        _make_token("税率 3%", source=TokenSource.PYMUPDF),
        _make_token("价税合计（小写）¥2060.00", source=TokenSource.PYMUPDF),
    ]

    with patch.object(InvoiceOcrService, "_extract_tokens_from_pdf", return_value=tokens):
        res = await InvoiceOcrService.parse_uploaded_file("native.pdf", b"%PDF-mock")

    inv = res["invoice_data"]
    assert inv["total_amount"] == 2060.00
    assert inv["untaxed_amount"] == 2000.00
    assert inv["tax_amount"] == 60.00
    assert inv["tax_rate"] == 0.03, "税率应为 3%，绝不能被强行反算为 6%"
    assert res["validation"]["amount_equation_valid"] is True


# -----------------------------------------------------------------------------
# 12. sample_type 仿真发票 (显式声明时才允许调用，绝不干扰真实文件)
# -----------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_sample_type_explicit_mode():
    # 显式传入 sample_type="HOTEL"
    res_mock = await InvoiceOcrService.parse_uploaded_file("mock.pdf", b"fake", sample_type="HOTEL")
    assert res_mock["parse_status"] == "SUCCESS"
    assert res_mock["invoice_data"]["total_amount"] == 850.00
    assert res_mock["invoice_data"]["seller_name"] == "上海和平饭店管理有限公司"
    assert len(res_mock["invoice_data"]["bbox_positions"]) > 0

    # 不传 sample_type，但上传无有效文字的文件：严禁回退至模拟发票！
    with patch.object(InvoiceOcrService, "_extract_tokens_from_pdf", return_value=[]):
        with patch.object(InvoiceOcrService, "_extract_tokens_from_image", return_value=[]):
            res_real = await InvoiceOcrService.parse_uploaded_file("real_user_file.pdf", b"fake")
            assert res_real["parse_status"] == "FAILED"
            assert res_real["invoice_data"]["total_amount"] is None
            assert res_real["invoice_data"]["invoice_number"] is None


# -----------------------------------------------------------------------------
# 13. 中文大写金额解析器专项单测
# -----------------------------------------------------------------------------
def test_chinese_currency_parser():
    assert ChineseCurrencyParser.parse("壹拾壹万零陆佰伍拾柒圆整") == Decimal("110657.00")
    assert ChineseCurrencyParser.parse("捌佰伍拾元整") == Decimal("850.00")
    assert ChineseCurrencyParser.parse("陆佰伍拾元整") == Decimal("650.00")
    assert ChineseCurrencyParser.parse("壹仟元整") == Decimal("1000.00")
    assert ChineseCurrencyParser.parse("伍万元整") == Decimal("50000.00")
    assert ChineseCurrencyParser.parse("壹佰贰拾叁元肆角伍分") == Decimal("123.45")
    assert ChineseCurrencyParser.parse("非法字符") is None


# -----------------------------------------------------------------------------
# 14. 铁路票金额多格式解析专项测试 (¥54, ¥54.0, ¥54.00, ￥54.0元)
# -----------------------------------------------------------------------------
def test_money_candidates_extraction_variations():
    tokens = [
        _make_token("票价：¥54"),
        _make_token("实收：¥54.0"),
        _make_token("金额：¥54.00"),
        _make_token("票价：￥54.0元"),
        _make_token("费用：54.0元"),
        _make_token("总计：54元"),
        _make_token("服务费：1,234.50"),
        _make_token("2017年06月24日14:37开"),
        _make_token("04车12D号"),
        _make_token("D3233·宁波站"),
        _make_token("3302061987****4682林璐"),
        _make_token("90041300310625G052971杭州东售"),
    ]
    candidates = InvoiceOcrService._extract_money_candidates(tokens)
    values = [c.value for c in candidates]
    # 前 6 个均为 54.00，第 7 个为 1234.50
    assert Decimal("54.00") in values
    assert Decimal("1234.50") in values
    # 日期、车次、车厢、身份证、条码售票号严禁作为金额提取
    for v in values:
        assert v not in [Decimal("2017.00"), Decimal("4.00"), Decimal("12.00"), Decimal("3233.00")]


# -----------------------------------------------------------------------------
# 15. 真实铁路客票图片/Token 端到端全量解析回归 (杭州东 -> 宁波 D3233)
# -----------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_train_ticket_real_fixture_parsing():
    fixture_path = os.path.join(
        os.path.dirname(__file__), "..", "..", "uploads", "invoices", "inv_2f26c8034576.png"
    )
    if os.path.exists(fixture_path):
        with open(fixture_path, "rb") as f:
            img_bytes = f.read()
        res = await InvoiceOcrService.parse_uploaded_file("inv_2f26c8034576.png", img_bytes)
    else:
        # Fallback to exact tokens from the image
        tokens = [
            _make_token("Z31G052971", [57, 73, 137, 355]),
            _make_token("检票：5A", [67, 719, 143, 867]),
            _make_token("杭州东站", [155, 123, 248, 356]),
            _make_token("D3233·宁波站", [151, 400, 260, 843]),
            _make_token("Hangzhoudong", [246, 127, 314, 351]),
            _make_token("Ningbo", [248, 664, 316, 778]),
            _make_token("2017年06月24日14:37开", [320, 91, 387, 516]),
            _make_token("04车12D号", [320, 590, 389, 768]),
            _make_token("支折", [393, 394, 469, 475]),
            _make_token("￥54.0元", [401, 95, 461, 239]),
            _make_token("二等座", [395, 650, 465, 767]),
            _make_token("限乘当日当次车", [477, 90, 538, 341]),
            _make_token("3302061987****4682林璐", [628, 87, 695, 554]),
            _make_token("90041300310625G052971杭州东售", [876, 87, 938, 600]),
        ]
        with patch.object(InvoiceOcrService, "_extract_tokens_from_image", return_value=tokens):
            res = await InvoiceOcrService.parse_uploaded_file("mock_train.png", b"fake_bytes")

    assert res["parse_status"] == "SUCCESS"
    inv = res["invoice_data"]
    assert inv["invoice_type"] == "铁路客票"
    assert inv["total_amount"] == 54.00
    assert inv["departure_station"] == "杭州东站"
    assert inv["arrival_station"] == "宁波站"
    assert inv["departure_city"] == "杭州"
    assert inv["arrival_city"] == "宁波"
    assert inv["train_no"] == "D3233"
    assert inv["travel_date"] == "2017-06-24"
    assert inv["departure_time"] == "2017-06-24T14:37:00"
    assert inv["arrival_time"] is None, "禁止猜测到达时间"
    assert inv["seat_type"] == "二等座"
    assert inv["ticket_number"] == "Z31G052971"
    assert inv["passenger_name"] == "林璐"

    # 严禁将增值税发票三元组结构强套在铁路客票上
    assert inv["untaxed_amount"] is None
    assert inv["tax_amount"] is None
    assert inv["tax_rate"] is None
    assert inv["seller_tax_id"] is None
    assert inv["buyer_tax_id"] is None
    assert inv["issue_date"] is None, "禁止将 travel_date 误写入 issue_date"

    # 推荐明细项核验
    rec = res["recommended_line_item"]
    assert rec is not None
    assert rec["expense_type"] == "交通费"
    assert rec["item_desc"] == "杭州东-宁波 D3233 二等座铁路客票"
    assert rec["amount"] == 54.00
    assert rec["city_name"] == "宁波"

    # 行程移动段核验
    seg = inv["travel_segment"]
    assert seg is not None
    assert seg["departure_city"] == "杭州"
    assert seg["arrival_city"] == "宁波"
    assert seg["departure_time"] == "2017-06-24T14:37:00"
    assert seg["arrival_time"] is None
    assert seg["travel_date"] == "2017-06-24"
    assert seg["transport_mode"] == "TRAIN"
    assert seg["transport_no"] == "D3233"


# -----------------------------------------------------------------------------
# 16. recommended_line_item 缺失场景契约防崩测试
# -----------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_train_ticket_recommended_line_item_none_resilience():
    # 构造一张只有车次和站点、但金额未提取到的车票
    tokens = [
        _make_token("杭州东站", [155, 123, 248, 356]),
        _make_token("D3233·宁波站", [151, 400, 260, 843]),
        _make_token("限乘当日当次车", [477, 90, 538, 341]),
    ]
    with patch.object(InvoiceOcrService, "_extract_tokens_from_image", return_value=tokens):
        res = await InvoiceOcrService.parse_uploaded_file("no_amt_train.png", b"fake_bytes")

    assert res["parse_status"] in ["NEED_REVIEW", "FAILED"]
    assert res["recommended_line_item"] is None
    assert "INVOICE_TOTAL_AMOUNT_MISSING" in res["quality_issues"]

