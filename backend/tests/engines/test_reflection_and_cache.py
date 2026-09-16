"""
backend/tests/engines/test_reflection_and_cache.py
针对【优化点 1：二阶反思与交叉消歧回路】与【优化点 2：大模型语义缓存与结构化输出防线】的专项自动化测试
"""
import pytest
import time
from decimal import Decimal
from datetime import datetime
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker

from app.core.llm_client import LLMClient, SemanticCache
from engines.contract.finding import RiskFindingContract, RiskLevelEnum
from engines.contract.agent_role import AgentRoleEnum
from engines.orchestrator.reviewer_reflector import ReviewerReflector
from engines.orchestrator import MasterOrchestrator, StreamProducer
import engines.orchestrator.master_graph as mg
from app.core.database import Base
from app.models import User, FinancialDocument, DocumentLineItem, InvoiceRecord

# =====================================================================
# 优化点 2 单元测试：语义缓存与结构化输出防线
# =====================================================================

def test_semantic_cache_lru_and_stats():
    """测试 SemanticCache 的哈希命中、统计率与 LRU 淘汰机制"""
    cache = SemanticCache(capacity=2, ttl_seconds=3600)
    
    # 1. 初始状态
    assert cache.stats()["size"] == 0
    assert cache.stats()["hits"] == 0
    assert cache.stats()["misses"] == 0

    # 2. 写入与命中
    payload1 = {"supplier": "测试科技", "tax_id": "12345"}
    cache.set("test_prefix", payload1, {"status": "ok"})
    
    val = cache.get("test_prefix", payload1)
    assert val == {"status": "ok"}
    assert cache.stats()["hits"] == 1
    assert cache.stats()["hit_rate_pct"] == 100.0

    # 3. 未命中
    val_miss = cache.get("test_prefix", {"supplier": "未收录主体"})
    assert val_miss is None
    assert cache.stats()["misses"] == 1
    assert cache.stats()["hit_rate_pct"] == 50.0

    # 4. 容量上限与 LRU 淘汰测试 (capacity=2)
    payload2 = {"item": "电脑"}
    payload3 = {"item": "打印纸"}
    cache.set("item", payload2, "val2")
    # 此时队列有 payload1, payload2
    # 再次访问 payload1，使其变为最近使用
    _ = cache.get("test_prefix", payload1)
    # 插入 payload3，应该淘汰最久未使用的 payload2
    cache.set("item", payload3, "val3")

    assert cache.get("test_prefix", payload1) is not None
    assert cache.get("item", payload2) is None  # 被 LRU 淘汰
    assert cache.get("item", payload3) == "val3"

    # 5. 清理
    cache.clear()
    assert cache.stats()["size"] == 0
    assert cache.stats()["hits"] == 0

def test_semantic_cache_ttl_expiry():
    """测试 SemanticCache 的 TTL 超时自动失效"""
    cache = SemanticCache(capacity=10, ttl_seconds=1)
    payload = {"query": "差旅住宿"}
    cache.set("ttl_test", payload, "result_val")

    # 篡改时间戳模拟过期
    key = cache._make_key("ttl_test", payload)
    cache._store[key] = (time.time() - 2.0, "result_val")

    res = cache.get("ttl_test", payload)
    assert res is None  # 已过期自动驱逐

def test_structured_output_guard_safe_parsing():
    """测试 LLMClient.parse_json_safely 弹性解析各种不规范的 LLM 返回"""
    # 1. 标准规范 JSON
    raw1 = '{"is_rational": true, "risk_analysis": "合规"}'
    p1 = LLMClient.parse_json_safely(raw1)
    assert p1.get("is_rational") is True

    # 2. Markdown 围栏包裹代码块 (常见于 DeepSeek / OpenAI 输出)
    raw2 = '```json\n{\n  "is_match": false,\n  "analysis": "经营范围不符"\n}\n```'
    p2 = LLMClient.parse_json_safely(raw2)
    assert p2.get("is_match") is False
    assert "经营范围不符" in p2.get("analysis")

    # 3. 前后带自然语言客套话或开场白的 JSON
    raw3 = '尊敬的审计员，根据对企业主营资质的比对，分析结果如下：\n{"is_match": true, "suggestion": "放行"}\n请财务部门知悉。'
    p3 = LLMClient.parse_json_safely(raw3)
    assert p3.get("is_match") is True
    assert p3.get("suggestion") == "放行"

    # 4. 完全不合法的非 JSON 文本，平滑回退兜底字典，零中断崩溃
    raw4 = '这是一段纯文本回复，模型未按照指令输出 JSON。'
    default_dict = {"is_fallback": True}
    p4 = LLMClient.parse_json_safely(raw4, default_data=default_dict)
    assert p4 == default_dict

@pytest.mark.asyncio
async def test_llm_client_semantic_cache_hit_behavior():
    """测试 LLMClient 审计方法的语义缓存命中与返回标记"""
    LLMClient.clear_cache()
    
    # 构造测试明细
    items = [{"line_no": 1, "expense_type": "差旅费", "item_desc": "拜访客户", "amount": 500.0}]
    
    # 第一次调用（走规则启发式或大模型）
    res1 = await LLMClient.audit_policy_rationality(
        title="拜访上海核心客户",
        department_name="销售部",
        line_items=items
    )
    assert res1["is_rational"] is True
    assert res1["source"] in ["HEURISTIC_RULE", "LLM_INFERENCE"]

    # 主动向缓存注入一个预期值，验证下一次调用是否直接走 SEMANTIC_CACHE
    cache_payload = {
        "title": "拜访上海核心客户",
        "department_name": "销售部",
        "line_items": [
            {"expense_type": i.get("expense_type"), "item_desc": i.get("item_desc"), "amount": i.get("amount")}
            for i in items
        ]
    }
    LLMClient._cache.set("policy_rationality", cache_payload, {"is_rational": True, "risk_analysis": "缓存测试命中成功"})

    # 第二次相同入参调用，必须秒级命中语义缓存
    res2 = await LLMClient.audit_policy_rationality(
        title="拜访上海核心客户",
        department_name="销售部",
        line_items=items
    )
    assert res2["source"] == "SEMANTIC_CACHE"
    assert res2["risk_analysis"] == "缓存测试命中成功"

    # 校验缓存统计指标
    stats = LLMClient.get_cache_stats()
    assert stats["hits"] >= 1


# =====================================================================
# 优化点 1 单元测试：二阶反思与交叉消歧回路 (ReviewerReflector)
# =====================================================================

def test_reviewer_reflector_full_allowance_disambiguation():
    """
    测试反思消歧场景 1：
    发票差额正好等于免票包干津贴 (如差旅出差总额 1200，发票 1000，免票差旅津贴 200)
    验证：原高危 R02_INVOICE_SUM_MISMATCH 被成功消歧降级为低风险 R02_ALLOWANCE_AUTO_RESOLVED
    """
    raw_findings = [
        RiskFindingContract(
            rule_code="R02_INVOICE_SUM_MISMATCH",
            rule_name="发票价税合计与申报总额不符",
            risk_level=RiskLevelEnum.HIGH,
            agent_role=AgentRoleEnum.AMOUNT,
            title="发票总额与单据申报相差 200.00 元",
            description="单据申报 1200 元，实际发票合计 1000 元，相差 200 元。",
            actual_value={"sum_invoices": "1000.00"},
            expected_value={"document_total": "1200.00"},
            discrepancy_amount=Decimal("200.00"),
            suggestion="发票不平",
            is_overridable=False
        )
    ]

    document_facts = {
        "total_amount": "1200.00",
        "document_type": "TRAVEL_REIMBURSEMENT",
        "allowance_policy_verified": True,
        "line_items": [
            {"line_no": 1, "expense_type": "住宿费", "item_desc": "如家酒店", "amount": "1000.00"},
            {"line_no": 2, "expense_type": "差旅津贴", "item_desc": "出差包干补助(100元/天*2天)", "amount": "200.00"}
        ]
    }

    resolved, logs = ReviewerReflector.reflect_and_disambiguate(raw_findings, document_facts)

    # 核心改进断言：完全消歧后不再作为 LOW 风险项扣 3 分，因此不在 resolved 中，只保留在 logs 中
    assert len(resolved) == 0
    assert len(logs) == 1
    assert logs[0]["action"] == "AUTO_RESOLVED_FULL"
    assert logs[0]["original_rule"] == "R02_INVOICE_SUM_MISMATCH"
    assert logs[0]["resolved_rule"] == "R02_ALLOWANCE_AUTO_RESOLVED"

def test_reviewer_reflector_allowance_without_policy_evidence_strict_hold():
    """
    测试反思消歧安全门禁：
    单据明细写有'差旅津贴'，但缺少 Policy/Context 免票制度证据
    验证：不得仅凭文字自动放行，严格保留原 R02 高危违规项
    """
    raw_findings = [
        RiskFindingContract(
            rule_code="R02_INVOICE_SUM_MISMATCH",
            rule_name="发票价税合计与申报总额不符",
            risk_level=RiskLevelEnum.HIGH,
            agent_role=AgentRoleEnum.AMOUNT,
            title="发票总额与单据申报相差 200.00 元",
            description="单据申报 1200 元，实际发票合计 1000 元，相差 200 元。",
            actual_value={"sum_invoices": "1000.00"},
            expected_value={"document_total": "1200.00"},
            discrepancy_amount=Decimal("200.00"),
            suggestion="发票不平",
            is_overridable=False
        )
    ]

    # 无任何 allowance_policy_verified 证据
    document_facts = {
        "total_amount": "1200.00",
        "document_type": "TRAVEL_REIMBURSEMENT",
        "allowance_policy_verified": False,
        "line_items": [
            {"line_no": 1, "expense_type": "住宿费", "item_desc": "如家酒店", "amount": "1000.00"},
            {"line_no": 2, "expense_type": "差旅津贴", "item_desc": "出差包干补助(100元/天*2天)", "amount": "200.00"}
        ]
    }

    resolved, logs = ReviewerReflector.reflect_and_disambiguate(raw_findings, document_facts)

    # 核心安全断言：未获取到制度证据时，原 R02 严格保留，不发生消歧
    assert len(resolved) == 1
    assert resolved[0].rule_code == "R02_INVOICE_SUM_MISMATCH"
    assert resolved[0].risk_level == RiskLevelEnum.HIGH
    assert len(logs) == 0

def test_reviewer_reflector_partial_allowance_disambiguation():
    """
    测试反思消歧场景 2：
    发票差额大于免票津贴 (申报 1300，发票 1000，津贴 200，仍有 100 既无发票亦无津贴)
    具备免票制度验证依据时：自动抵扣 200 津贴，剩余 100 重新标定为实质未平账风险
    """
    raw_findings = [
        RiskFindingContract(
            rule_code="R02_INVOICE_SUM_MISMATCH",
            rule_name="发票价税合计与申报总额不符",
            risk_level=RiskLevelEnum.HIGH,
            agent_role=AgentRoleEnum.AMOUNT,
            title="发票总额与单据申报相差 300.00 元",
            description="单据申报 1300 元，实际发票合计 1000 元，相差 300 元。",
            actual_value={"sum_invoices": "1000.00"},
            expected_value={"document_total": "1300.00"},
            discrepancy_amount=Decimal("300.00"),
            suggestion="发票不平",
            is_overridable=False
        )
    ]

    document_facts = {
        "total_amount": "1300.00",
        "document_type": "TRAVEL_REIMBURSEMENT",
        "allowance_policy_verified": True,
        "line_items": [
            {"line_no": 1, "expense_type": "住宿费", "item_desc": "酒店住宿", "amount": "1000.00"},
            {"line_no": 2, "expense_type": "交通补贴", "item_desc": "市内交通包干补贴", "amount": "200.00"},
            {"line_no": 3, "expense_type": "杂费", "item_desc": "打印费", "amount": "100.00"}
        ]
    }

    resolved, logs = ReviewerReflector.reflect_and_disambiguate(raw_findings, document_facts)

    assert len(resolved) == 1
    rf = resolved[0]
    assert rf.rule_code == "R02_INVOICE_SUM_MISMATCH"
    assert rf.risk_level == RiskLevelEnum.HIGH
    # 差额应该被自动核减为 100 元
    assert rf.discrepancy_amount == Decimal("100.00")
    assert "核减" in rf.description and "免票津贴" in rf.description
    assert len(logs) == 1
    assert logs[0]["action"] == "PARTIAL_DEDUCTED"
    assert logs[0]["remaining_gap"] == "100.00"

def test_reviewer_reflector_no_allowance_strict_hold():
    """
    测试反思消歧场景 3：
    普通采购无任何津贴项目，发票不平照常严控拦截
    """
    raw_findings = [
        RiskFindingContract(
            rule_code="R02_INVOICE_SUM_MISMATCH",
            rule_name="发票价税合计与申报总额不符",
            risk_level=RiskLevelEnum.HIGH,
            agent_role=AgentRoleEnum.AMOUNT,
            title="相差 500 元",
            description="发票缺额 500 元",
            actual_value={"sum_invoices": "1500.00"},
            expected_value={"document_total": "2000.00"},
            discrepancy_amount=Decimal("500.00"),
            suggestion="补发票",
            is_overridable=False
        )
    ]

    document_facts = {
        "total_amount": "2000.00",
        "document_type": "EXPENSE_REIMBURSEMENT",
        "line_items": [
            {"line_no": 1, "expense_type": "办公用品", "item_desc": "办公耗材采购", "amount": "2000.00"}
        ]
    }

    resolved, logs = ReviewerReflector.reflect_and_disambiguate(raw_findings, document_facts)
    assert len(resolved) == 1
    assert resolved[0].rule_code == "R02_INVOICE_SUM_MISMATCH"
    assert resolved[0].discrepancy_amount == Decimal("500.00")
    assert len(logs) == 0  # 无任何反思修改触发

# =====================================================================
# 优化点 1 + 2 集成端到端测试：MasterOrchestrator 流水线实测
# =====================================================================

TEST_DB_URL = "sqlite+aiosqlite:///:memory:"

@pytest.mark.asyncio
async def test_master_orchestrator_reflection_e2e(monkeypatch):
    """测试 MasterOrchestrator 端到端运行，并在含有免票津贴的单据上触发反思消歧与流式事件广播"""
    test_engine = create_async_engine(TEST_DB_URL, echo=False)
    async with test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    SessionMaker = async_sessionmaker(bind=test_engine, expire_on_commit=False)
    monkeypatch.setattr(mg, "AsyncSessionLocal", SessionMaker)

    # 构造一张：申报 1200元，发票 1000元，包含 200元出差补贴的单据
    async with SessionMaker() as session:
        user = User(username="test_traveler", hashed_password="pw", real_name="出差员工")
        session.add(user)
        await session.flush()

        doc = FinancialDocument(
            document_no="TRV-REFLECT-001",
            document_type="TRAVEL_REIMBURSEMENT",
            title="杭州客户拜访出差",
            applicant_id=user.id,
            total_amount=Decimal("1200.00"),
            status="SUBMITTED",
            extra_attributes={"allowance_policy_verified": True}
        )
        session.add(doc)
        await session.flush()

        # 明细 1: 住宿 1000
        session.add(DocumentLineItem(
            document_id=doc.id,
            line_no=1,
            expense_type="住宿费",
            item_desc="杭州快捷酒店2晚",
            amount=Decimal("1000.00"),
            city_name="杭州",
            start_date=datetime(2026, 9, 10, 10, 0)
        ))
        # 明细 2: 差旅津贴 200 (免票政策)
        session.add(DocumentLineItem(
            document_id=doc.id,
            line_no=2,
            expense_type="差旅津贴",
            item_desc="定额出差包干津贴2天",
            amount=Decimal("200.00"),
            city_name="杭州",
            start_date=datetime(2026, 9, 10, 10, 0)
        ))
        # 仅有 1000 元发票 (与总额 1200 相差 200)
        session.add(InvoiceRecord(
            document_id=doc.id,
            attachment_id=1,
            invoice_code="0330023",
            invoice_number="88776655",
            total_amount=Decimal("1000.00"),
            untaxed_amount=Decimal("943.40"),
            tax_amount=Decimal("56.60"),
            seller_tax_id="91330100000000",
            seller_name="杭州酒店集团",
            issue_date="2026-09-10",
            invoice_hash="test_hz_hotel_001"
        ))
        await session.commit()
        doc_id = doc.id

    task_id = "task-reflection-test-001"
    final_state = await MasterOrchestrator.run(
        task_id=task_id,
        document_id=doc_id,
        applicant_id=1,
        tenant_id=1
    )

    # 1. 验证最终风险项：R02_INVOICE_SUM_MISMATCH 必须被成功反思消歧，不应存在 R02 未平账高危项！
    verified_rules = [f.rule_code for f in final_state.verified_findings]
    assert "R02_INVOICE_SUM_MISMATCH" not in verified_rules
    # 核心断言：完全消歧后不再作为 LOW 风险项（不在 verified_findings 中扣3分），而是在 disambiguation_logs 中记录
    assert len(final_state.disambiguation_logs) >= 1
    assert final_state.disambiguation_logs[0]["action"] == "AUTO_RESOLVED_FULL"
    # R02 已完全消歧（0 扣分），仅剩明细1住宿超标 R05 (扣 25 分)，最终得分为 75 分
    assert final_state.risk_score == 75

    # 2. 验证流式事件中 Stage 3 记录了反思决策与事件广播
    events = StreamProducer.get_events_since(task_id)
    event_types = [ev.event.value for ev in events]
    assert "review_reflect" in event_types

    stage3_events = [ev for ev in events if ev.data.get("stage") == "STAGE_3_REVIEW_DONE"]
    assert len(stage3_events) >= 1
    assert stage3_events[0].data.get("reflection_applied") is True

    async with test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await test_engine.dispose()
