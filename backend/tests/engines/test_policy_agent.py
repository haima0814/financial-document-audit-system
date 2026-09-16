"""
backend/tests/engines/test_policy_agent.py
policy_agent 制度合规审查与双门禁、优雅降级单元测试
"""
import pytest
from decimal import Decimal
from engines.policy_agent import PolicyAgent
from engines.contract.finding import RiskLevelEnum

@pytest.mark.asyncio
async def test_policy_agent_within_standard():
    """测试标准范围内报销：北京 450 元 (上限 500 元)，顺利通过"""
    items = [
        {"expense_type": "住宿费", "item_desc": "如家酒店", "amount": "450.00", "city_name": "北京"}
    ]
    findings = await PolicyAgent.run("TRAVEL_REIMBURSEMENT", items, capabilities=["travel_hotel_limit"])
    assert len(findings) == 0

@pytest.mark.asyncio
async def test_policy_agent_exceeded_standard():
    """测试超标报销：杭州 600 元 (上限 350 元)，触发 R05 超标预警"""
    items = [
        {"expense_type": "住宿费", "item_desc": "杭州西湖喜来登", "amount": "600.00", "city_name": "杭州"}
    ]
    findings = await PolicyAgent.run("TRAVEL_REIMBURSEMENT", items, capabilities=["travel_hotel_limit"])
    assert len(findings) == 1
    assert findings[0].rule_code == "R05_POLICY_EXCEEDED"
    assert findings[0].discrepancy_amount == Decimal("250.00")
    assert findings[0].risk_level == RiskLevelEnum.HIGH # 超标 250/350 = 71% > 50%

@pytest.mark.asyncio
async def test_policy_agent_graceful_fallback():
    """测试城市缺失：触发 R14 柔性降级提示 (零崩溃)"""
    items = [
        {"expense_type": "住宿费", "item_desc": "某地客栈", "amount": "300.00", "city_name": None}
    ]
    findings = await PolicyAgent.run("TRAVEL_REIMBURSEMENT", items)
    assert len(findings) == 1
    assert findings[0].rule_code == "R14_POLICY_NOT_FOUND"
    assert findings[0].risk_level == RiskLevelEnum.LOW


@pytest.mark.asyncio
async def test_policy_agent_unrecorded_city_triggers_r14():
    """测试未收录城市触发 R14，而不是默认当成 TIER_3(260元) 误报 R05 超标"""
    # 申报 300 元：若按老逻辑默认 TIER_3(260元) 会误报超标；新逻辑必须生成 R14 并跳过超标比对
    items = [
        {"expense_type": "住宿费", "item_desc": "某未收录小镇客栈", "amount": "300.00", "city_name": "未知乌托邦小镇"}
    ]
    findings = await PolicyAgent.run("TRAVEL_REIMBURSEMENT", items)
    rule_codes = [f.rule_code for f in findings]
    assert "R14_POLICY_NOT_FOUND" in rule_codes
    assert "R05_POLICY_EXCEEDED" not in rule_codes


@pytest.mark.asyncio
async def test_policy_agent_multi_nights_calculation():
    """测试多晚住宿按单晚均摊金额与标准比对，支持 stay_nights / nights"""
    # 场景 1: 北京(上限500元)，申报 1200 元，stay_nights=3。单晚 400 <= 500，应顺利放行
    items_pass = [
        {"expense_type": "住宿费", "item_desc": "北京快捷酒店3晚", "amount": "1200.00", "city_name": "北京", "stay_nights": 3}
    ]
    findings_pass = await PolicyAgent.run("TRAVEL_REIMBURSEMENT", items_pass)
    assert not any(f.rule_code == "R05_POLICY_EXCEEDED" for f in findings_pass)

    # 场景 2: 北京(上限500元)，申报 1400 元，nights=2。单晚 700 > 500，超标 200/晚 (40% < 50%) -> MEDIUM
    items_over = [
        {"expense_type": "住宿费", "item_desc": "北京五星酒店2晚", "amount": "1400.00", "city_name": "北京", "nights": 2}
    ]
    findings_over = await PolicyAgent.run("TRAVEL_REIMBURSEMENT", items_over)
    r05_findings = [f for f in findings_over if f.rule_code == "R05_POLICY_EXCEEDED"]
    assert len(r05_findings) == 1
    assert r05_findings[0].discrepancy_amount == Decimal("400.00") # (700-500) * 2 = 400
    assert r05_findings[0].risk_level == RiskLevelEnum.MEDIUM
    assert "2 晚" in r05_findings[0].description
    assert "700.00 元/间夜" in r05_findings[0].description


def test_llm_bool_safely_parsed():
    """测试统一严格布尔解析器 parse_bool_safely，未知值返回 None，绝不默认判定为 True"""
    from app.core.llm_client import LLMClient
    assert LLMClient.parse_bool_safely(True) is True
    assert LLMClient.parse_bool_safely(False) is False
    assert LLMClient.parse_bool_safely("true") is True
    assert LLMClient.parse_bool_safely("True") is True
    assert LLMClient.parse_bool_safely("false") is False
    assert LLMClient.parse_bool_safely("False") is False
    assert LLMClient.parse_bool_safely("0") is False
    assert LLMClient.parse_bool_safely("1") is True
    # 未知值必须返回 None，不能擅自假设为 True
    assert LLMClient.parse_bool_safely("unknown") is None
    assert LLMClient.parse_bool_safely("maybe") is None
    assert LLMClient.parse_bool_safely("invalid") is None
    assert LLMClient.parse_bool_safely(None) is None
    assert LLMClient.parse_bool_safely(None, default=True) is True
    assert LLMClient.parse_bool_safely(None, default=False) is False


@pytest.mark.asyncio
async def test_llm_string_false_triggers_findings(monkeypatch):
    """测试 LLM 响应 JSON 中 is_rational / is_match 为字符串 'false' 时能正确识别为违规"""
    from app.core.llm_client import LLMClient
    from engines.supplier_agent import SupplierAgent

    # 1. 模拟 Policy 审查 LLM 返回 {"is_rational": "false"}
    async def mock_policy_rat(*args, **kwargs):
        return {
            "is_rational": "false",
            "risk_analysis": "明细消费与因公事由严重脱节，涉嫌公款私用",
            "source": "LLM_INFERENCE"
        }
    monkeypatch.setattr(LLMClient, "audit_policy_rationality", mock_policy_rat)

    items = [{"expense_type": "办公用品", "item_desc": "高档饰品", "amount": "800.00"}]
    findings = await PolicyAgent.run("GENERAL_EXPENSE", items, document_title="日常办公报销")
    assert any(f.rule_code == "R16_BUSINESS_PURPOSE_MISMATCH" for f in findings)

    # 2. 模拟 Supplier 审查 LLM 返回 {"is_match": "false"}
    async def mock_supplier_scope(*args, **kwargs):
        return {
            "is_match": "false",
            "analysis": "供应商主营范围不包含该类采购",
            "source": "LLM_INFERENCE"
        }
    monkeypatch.setattr(LLMClient, "audit_supplier_business_scope", mock_supplier_scope)

    supplier_findings = await SupplierAgent.run(
        supplier_name="虚假科技公司",
        uscc="91110000000000001X",
        purchase_desc="购买重型工程机械",
        business_scope="日常办公用品销售"
    )
    assert any(f.rule_code == "R17_SUPPLIER_SCOPE_DEVIATION" for f in supplier_findings)


@pytest.mark.asyncio
async def test_policy_agent_none_amount_safe(monkeypatch):
    """测试明细 amount 为 None、空或非法字符串时不崩溃，安全跳过"""
    from app.core.llm_client import LLMClient
    monkeypatch.setattr(LLMClient, "is_configured", lambda: False)

    items = [
        {"expense_type": "住宿费", "item_desc": "无金额住宿", "amount": None, "city_name": "北京"},
        {"expense_type": "住宿费", "item_desc": "非法金额住宿", "amount": "invalid-num", "city_name": "上海"}
    ]
    findings = await PolicyAgent.run("TRAVEL_REIMBURSEMENT", items)
    # 金额缺失无法判定限额超标，安全跳过，不崩溃
    assert len(findings) == 0


@pytest.mark.asyncio
async def test_policy_agent_capabilities_filtering():
    """测试 capabilities 参数能精准启停住宿限额规则与大模型事由审查"""
    items = [
        {"expense_type": "住宿费", "item_desc": "豪华酒店", "amount": "1000.00", "city_name": "北京"} # 超标 500
    ]

    # 1. 仅启用 business_purpose_check：住宿规则不运行，无 R05
    findings_llm_only = await PolicyAgent.run(
        "TRAVEL_REIMBURSEMENT",
        items,
        capabilities=["business_purpose_check"]
    )
    assert not any(f.rule_code == "R05_POLICY_EXCEEDED" for f in findings_llm_only)

    # 2. 仅启用 travel_hotel_limit：住宿规则运行，触发 R05
    findings_hotel_only = await PolicyAgent.run(
        "TRAVEL_REIMBURSEMENT",
        items,
        capabilities=["travel_hotel_limit"]
    )
    assert any(f.rule_code == "R05_POLICY_EXCEEDED" for f in findings_hotel_only)

    # 3. 传入空 capabilities：两者均不运行
    findings_none = await PolicyAgent.run(
        "TRAVEL_REIMBURSEMENT",
        items,
        capabilities=[]
    )
    assert len(findings_none) == 0


@pytest.mark.asyncio
async def test_quantity_fallback_only_with_night_unit():
    """测试 quantity 仅在明确标识为间夜/晚时作为晚数 fallback，避免误判房间数或人数"""
    # 场景 1: 北京(上限500元)，申报 800 元，quantity=2，unit="间" (代表2间房，并非2晚)
    # 不应被当作2晚折算为400，应按单笔超标比对，触发 R05
    items_rooms = [
        {"expense_type": "住宿费", "item_desc": "北京酒店2间房", "amount": "800.00", "city_name": "北京", "quantity": 2, "unit": "间"}
    ]
    findings_rooms = await PolicyAgent.run("TRAVEL_REIMBURSEMENT", items_rooms, capabilities=["travel_hotel_limit"])
    assert any(f.rule_code == "R05_POLICY_EXCEEDED" for f in findings_rooms)

    # 场景 2: 北京(上限500元)，申报 800 元，quantity=2，unit="间夜" (明确代表2间夜)
    # 单晚 400 <= 500，应顺利放行
    items_nights = [
        {"expense_type": "住宿费", "item_desc": "北京酒店2间夜", "amount": "800.00", "city_name": "北京", "quantity": 2, "unit": "间夜"}
    ]
    findings_nights = await PolicyAgent.run("TRAVEL_REIMBURSEMENT", items_nights, capabilities=["travel_hotel_limit"])
    assert not any(f.rule_code == "R05_POLICY_EXCEEDED" for f in findings_nights)

    # 场景 3: 同时有 stay_nights=2 与 quantity=5，优先取 stay_nights
    items_prioritize = [
        {"expense_type": "住宿费", "item_desc": "快捷酒店", "amount": "800.00", "city_name": "北京", "stay_nights": 2, "quantity": 5}
    ]
    findings_prio = await PolicyAgent.run("TRAVEL_REIMBURSEMENT", items_prioritize, capabilities=["travel_hotel_limit"])
    assert not any(f.rule_code == "R05_POLICY_EXCEEDED" for f in findings_prio)


@pytest.mark.asyncio
async def test_heuristic_rule_marks_degraded(monkeypatch):
    """测试 LLM 不可用退化到 HEURISTIC_RULE 时，执行结果与 source 明确体现降级审核"""
    from engines.harness.agent_harness import AgentHarness
    from engines.contract.agent_role import AgentRoleEnum
    from app.core.llm_client import LLMClient
    monkeypatch.setattr(LLMClient, "is_configured", lambda: False)

    items = [{"expense_type": "办公用品", "item_desc": "普通笔记本", "amount": "50.00"}]
    # 未配置 LLM，PolicyAgent 执行 business_purpose_check 退化为 HEURISTIC_RULE
    findings = await PolicyAgent.run(
        "GENERAL_EXPENSE",
        items,
        document_title="日常办公",
        capabilities=["business_purpose_check"]
    )
    assert findings.is_degraded is True
    assert findings.source == "HEURISTIC_RULE"

    # 经由 AgentHarness 包装执行
    coro = PolicyAgent.run("GENERAL_EXPENSE", items, document_title="日常办公", capabilities=["business_purpose_check"])
    result = await AgentHarness.execute_safely(AgentRoleEnum.POLICY, coro)
    assert result.is_degraded is True
    assert result.source == "HEURISTIC_RULE"
    assert "降级审核" in (result.reason or "")


