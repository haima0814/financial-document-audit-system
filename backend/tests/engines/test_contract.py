"""
backend/tests/engines/test_contract.py
engines/contract 核心契约、五维存证与事件协议自动化测试
"""
import pytest
from decimal import Decimal
from datetime import datetime
import json
import operator
from pydantic import ValidationError

from engines.contract import (
    AgentRoleEnum,
    AgentRoleMeta,
    AGENT_ROLE_REGISTRY,
    EvidenceCategoryEnum,
    BoundingBox,
    VisualAnchor,
    CalculationProof,
    PolicyProof,
    EvidenceRecord,
    RiskLevelEnum,
    RiskFindingContract,
    EventTypeEnum,
    BaseEventEnvelope,
    TaskProgressPayload,
    RiskDetectedPayload,
    TaskCompletedPayload,
    ENGINE_CONFIG
)

# ==================== 1. Agent 角色与注册表测试 ====================

def test_agent_roles_registry():
    """验证 8 大 Agent 角色枚举与元数据完备性"""
    assert len(AgentRoleEnum) == 8
    for role in AgentRoleEnum:
        assert role in AGENT_ROLE_REGISTRY
        meta = AGENT_ROLE_REGISTRY[role]
        assert isinstance(meta, AgentRoleMeta)
        assert meta.role == role
        assert len(meta.name_cn) > 0
        assert meta.timeout_seconds > 0
        assert meta.max_retries >= 1


# ==================== 2. 视觉坐标与 BBox 边界测试 ====================

def test_bounding_box_valid():
    """验证合法 BBox 坐标构建与 to_list()"""
    bbox = BoundingBox(ymin=0.15, xmin=0.20, ymax=0.45, xmax=0.60, page_number=1)
    coords = bbox.to_list()
    assert coords == [0.15, 0.20, 0.45, 0.60]
    assert bbox.page_number == 1

def test_bounding_box_out_of_bounds():
    """验证非法越界 BBox 坐标必须被 Pydantic 校验拦截"""
    with pytest.raises(ValidationError):
        BoundingBox(ymin=-0.1, xmin=0.0, ymax=0.5, xmax=0.5)  # ymin < 0.0

    with pytest.raises(ValidationError):
        BoundingBox(ymin=0.0, xmin=0.0, ymax=1.2, xmax=0.5)   # ymax > 1.0


# ==================== 3. 确定性 Decimal 计算存证测试 ====================

def test_calculation_proof_decimal_precision():
    """验证精算存证 Decimal 精度保持与 JSON 序列化"""
    proof = CalculationProof(
        formula_expr="(1250.00 - 800.00) = 450.00",
        operand_left=Decimal("1250.00"),
        operand_right=Decimal("800.00"),
        result=Decimal("450.00"),
        tolerance=Decimal("0.00"),
        is_balanced=False
    )
    assert proof.result == Decimal("450.00")
    
    # 验证序列化与反序列化无浮点数漂移
    dumped_json = proof.model_dump_json()
    reloaded = CalculationProof.model_validate_json(dumped_json)
    assert reloaded.operand_left == Decimal("1250.00")
    assert reloaded.result == Decimal("450.00")
    assert isinstance(reloaded.result, Decimal)


# ==================== 4. 五维不可变证据模型与只读性测试 ====================

def test_evidence_record_immutability():
    """验证五维证据记录不可被篡改 (frozen=True)"""
    bbox = BoundingBox(ymin=0.1, xmin=0.1, ymax=0.3, xmax=0.4)
    visual = VisualAnchor(
        attachment_id=101,
        file_name="invoice_01.pdf",
        file_hash="e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
        bbox=bbox,
        field_key="invoice_amount",
        ocr_raw_text="￥1,250.00",
        ocr_confidence=0.98
    )
    
    evidence = EvidenceRecord(
        category=EvidenceCategoryEnum.VISUAL,
        produced_by="document_agent",
        visual_anchor=visual
    )
    
    # 验证尝试修改已冻结实例必须报错
    with pytest.raises(ValidationError):
        evidence.category = EvidenceCategoryEnum.CALCULATION


# ==================== 5. 风险发现契约与引用分离测试 ====================

def test_risk_finding_contract():
    """验证标准风险发现项契约构建、不可变性与序列化"""
    finding = RiskFindingContract(
        rule_code="R01_AMOUNT_MISMATCH",
        rule_name="发票明细加和与单据总金额不符",
        risk_level=RiskLevelEnum.HIGH,
        agent_role=AgentRoleEnum.AMOUNT,
        title="单据金额算不平，差额 450.00 元",
        description="申报单据报销金额为 1250.00 元，发票实际金额合计为 800.00 元，相差 450.00 元。",
        actual_value={"claimed_total": "1250.00"},
        expected_value={"invoice_sum": "800.00"},
        discrepancy_amount=Decimal("450.00"),
        evidence_ids=["ev-uuid-001", "ev-uuid-002"],
        suggestion="请核实发票附件是否漏传或申报金额填写有误。",
        is_overridable=False
    )
    
    assert finding.risk_level == RiskLevelEnum.HIGH
    assert finding.discrepancy_amount == Decimal("450.00")
    assert finding.is_overridable is False
    assert len(finding.evidence_ids) == 2

    # 验证冻结不可篡改
    with pytest.raises(ValidationError):
        finding.title = "被恶意篡改的标题"

    # 验证 JSON 导出与恢复
    json_str = finding.model_dump_json()
    recovered = RiskFindingContract.model_validate_json(json_str)
    assert recovered.rule_code == "R01_AMOUNT_MISMATCH"
    assert recovered.discrepancy_amount == Decimal("450.00")


# ==================== 6. WebSocket 实时流事件信封测试 ====================

def test_websocket_event_envelope():
    """验证实时事件外层信封与业务载荷组装"""
    payload = TaskProgressPayload(
        percent=45,
        current_stage="并行智能体分析中",
        active_roles=[AgentRoleEnum.AMOUNT, AgentRoleEnum.POLICY]
    )
    
    envelope = BaseEventEnvelope(
        event=EventTypeEnum.TASK_PROGRESS,
        task_id="task-uuid-8888",
        document_id=1001,
        data=payload.model_dump()
    )
    
    assert envelope.event == EventTypeEnum.TASK_PROGRESS
    assert envelope.task_id == "task-uuid-8888"
    assert envelope.data["percent"] == 45
    assert len(envelope.data["active_roles"]) == 2

    json_data = envelope.model_dump_json()
    assert "task-uuid-8888" in json_data


# ==================== 7. 主图状态 operator.add 合流测试 ====================

def test_master_state_operator_add_merging():
    """验证使用 operator.add 进行子图结果并发安全合流"""
    f1 = RiskFindingContract(
        rule_code="R01_AMOUNT_MISMATCH",
        rule_name="金额不平",
        risk_level=RiskLevelEnum.HIGH,
        agent_role=AgentRoleEnum.AMOUNT,
        title="金额不平",
        description="说明",
        suggestion="建议"
    )
    f2 = RiskFindingContract(
        rule_code="R05_POLICY_EXCEEDED",
        rule_name="差旅超标",
        risk_level=RiskLevelEnum.MEDIUM,
        agent_role=AgentRoleEnum.POLICY,
        title="酒店超标",
        description="说明",
        suggestion="建议"
    )
    
    # 模拟两个并行子图各自返回的列表
    amount_findings = [f1]
    policy_findings = [f2]
    
    # 执行 operator.add 合并
    combined = operator.add(amount_findings, policy_findings)
    assert len(combined) == 2
    assert combined[0].rule_code == "R01_AMOUNT_MISMATCH"
    assert combined[1].rule_code == "R05_POLICY_EXCEEDED"
