"""
backend/tests/engines/test_amount_agent.py
amount_agent 确定性金额精算与五方对账单元测试
"""
import pytest
from decimal import Decimal
from engines.amount_agent import AmountAgent, AmountCalculator
from engines.contract.finding import RiskLevelEnum

@pytest.mark.asyncio
async def test_amount_agent_perfect_balance():
    """测试完全平账场景：无任何风险项"""
    doc_total = Decimal("1000.00")
    lines = [Decimal("600.00"), Decimal("400.00")]
    invoices = [
        {"invoice_number": "INV001", "total_amount": "600.00", "untaxed_amount": "566.04", "tax_amount": "33.96"},
        {"invoice_number": "INV002", "total_amount": "400.00", "untaxed_amount": "377.36", "tax_amount": "22.64"}
    ]

    findings = await AmountAgent.run(doc_total, lines, invoices)
    assert len(findings) == 0

@pytest.mark.asyncio
async def test_amount_agent_header_line_mismatch():
    """测试单据总额与明细不平：触发 R01"""
    doc_total = Decimal("1000.00")
    lines = [Decimal("600.00"), Decimal("350.00")] # 合计 950 != 1000
    invoices = []

    findings = await AmountAgent.run(doc_total, lines, invoices)
    assert len(findings) == 1
    assert findings[0].rule_code == "R01_HEADER_LINE_MISMATCH"
    assert findings[0].risk_level == RiskLevelEnum.HIGH
    assert findings[0].discrepancy_amount == Decimal("50.00")

@pytest.mark.asyncio
async def test_amount_agent_invoice_mismatch_and_override():
    """测试发票总额不平及人工手动修改标打：触发 R02 与 R07"""
    doc_total = Decimal("1000.00")
    lines = [Decimal("1000.00")]
    invoices = [
        {
            "invoice_number": "INV001",
            "total_amount": "900.00", # 发票只有 900 != 1000
            "untaxed_amount": "900.00",
            "tax_amount": "0.00",
            "is_manual_modified": True,
            "original_extracted_amount": "800.00"
        }
    ]

    findings = await AmountAgent.run(doc_total, lines, invoices)
    assert len(findings) == 2
    rule_codes = {f.rule_code for f in findings}
    assert "R02_INVOICE_SUM_MISMATCH" in rule_codes
    assert "R07_MANUAL_OVERRIDE_FLAG" in rule_codes

def test_fixed_tax_tolerance():
    """测试发票价税固定公差为 0.01 元，不再按发票张数放大"""
    assert AmountCalculator.compute_tax_tolerance(1) == Decimal("0.01")
    assert AmountCalculator.compute_tax_tolerance(3) == Decimal("0.01")
    assert AmountCalculator.compute_tax_tolerance(10) == Decimal("0.01")


@pytest.mark.asyncio
async def test_amount_agent_manual_override_with_missing_tax_fields():
    """测试税额要素缺失但存在人工修改：必须产生 R07 且不因 None 中断"""
    doc_total = Decimal("1000.00")
    lines = [Decimal("1000.00")]
    invoices = [
        {
            "invoice_number": "INV_NO_TAX",
            "total_amount": "1000.00",
            "untaxed_amount": None,
            "tax_amount": None,
            "is_manual_modified": True,
            "original_extracted_amount": "950.00"
        }
    ]

    findings = await AmountAgent.run(doc_total, lines, invoices)
    rule_codes = [f.rule_code for f in findings]
    assert "R07_MANUAL_OVERRIDE_FLAG" in rule_codes
    # 税额缺失，不应产生假 R05
    assert "R05_TAX_AMOUNT_MISMATCH" not in rule_codes


@pytest.mark.asyncio
async def test_amount_agent_none_total_amount_safe():
    """测试 total_amount 为 None 或非法值时安全跳过数学核查，不抛异常也不伪造为 0"""
    # 场景 1: 发票 total_amount 为 None，不应抛出 Decimal("None") 异常
    doc_total = Decimal("1000.00")
    lines = [Decimal("1000.00")]
    invoices = [
        {
            "invoice_number": "INV_NONE",
            "total_amount": None,
            "untaxed_amount": "900.00",
            "tax_amount": "100.00"
        }
    ]
    findings = await AmountAgent.run(doc_total, lines, invoices)
    # 因为缺少有效 total_amount，跳过 R02 和 R05，不伪造 0 导致误报
    assert len(findings) == 0

    # 场景 2: doc_total 为 None 或非法字符串，不崩溃
    findings_none_doc = await AmountAgent.run(None, lines, invoices)
    assert len(findings_none_doc) == 0


@pytest.mark.asyncio
async def test_amount_agent_tax_diff_above_point_zero_one():
    """测试单票价税误差 >0.01 触发 R05，即使发票张数多也不放宽公差"""
    doc_total = Decimal("500.00")
    lines = [Decimal("500.00")]
    # 构造 5 张发票，其中一张价税相差 0.02 元
    invoices = [
        {
            "invoice_number": "INV_ERR",
            "total_amount": "100.00",
            "untaxed_amount": "90.00",
            "tax_amount": "9.98"  # 90.00 + 9.98 = 99.98，相差 0.02 > 0.01
        }
    ] + [
        {
            "invoice_number": f"INV_OK_{i}",
            "total_amount": "100.00",
            "untaxed_amount": "90.00",
            "tax_amount": "10.00"
        }
        for i in range(4)
    ]

    findings = await AmountAgent.run(doc_total, lines, invoices)
    r05_findings = [f for f in findings if f.rule_code == "R05_TAX_AMOUNT_MISMATCH"]
    assert len(r05_findings) == 1
    assert r05_findings[0].discrepancy_amount == Decimal("0.02")

    # 对比：相差恰好 0.01 元时不应触发 R05
    inv_ok_edge = [
        {
            "invoice_number": "INV_EDGE",
            "total_amount": "100.00",
            "untaxed_amount": "90.00",
            "tax_amount": "9.99"  # 相差 0.01 <= 0.01
        }
    ]
    findings_edge = await AmountAgent.run(Decimal("100.00"), [Decimal("100.00")], inv_ok_edge)
    assert not any(f.rule_code == "R05_TAX_AMOUNT_MISMATCH" for f in findings_edge)


@pytest.mark.asyncio
async def test_amount_agent_manual_override_missing_original_amount():
    """测试 R07 中 original_extracted_amount 缺失时保持 UNKNOWN，不伪造 0.00"""
    doc_total = Decimal("500.00")
    lines = [Decimal("500.00")]
    invoices = [
        {
            "invoice_number": "INV_MOD_NO_ORIG",
            "total_amount": "500.00",
            "untaxed_amount": "470.00",
            "tax_amount": "30.00",
            "is_manual_modified": True,
            "original_extracted_amount": None  # 缺失 OCR 原值
        }
    ]

    findings = await AmountAgent.run(doc_total, lines, invoices)
    r07_findings = [f for f in findings if f.rule_code == "R07_MANUAL_OVERRIDE_FLAG"]
    assert len(r07_findings) == 1
    # 期望值中 OCR 原值应为 UNKNOWN，严禁伪造成 0.00
    assert r07_findings[0].expected_value["ocr_extracted_amount"] == "UNKNOWN"
    assert r07_findings[0].expected_value["ocr_extracted_amount"] != "0.00"

