"""
backend/scripts/replay_demos.py
财务单据智能风险审核系统 - 3大核心场景 + 1项终审消歧端到端可重放验证脚本

运行方式:
  cd backend
  .venv/Scripts/python scripts/replay_demos.py
"""
import sys
import os
import asyncio
from decimal import Decimal

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

# 确保 backend 在 Python 路径中
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app.core.database import AsyncSessionLocal
from app.models.document import FinancialDocument
from app.models.audit import ReviewReport, RiskFinding
from app.services.approval_engine import ApprovalEngine, ApprovalDecisionAction

async def replay():
    print("=" * 70)
    print("🚀 开始回放与验收 3 大核心业务场景 + 1 项终审消歧门禁决策")
    print("=" * 70)

    async with AsyncSessionLocal() as db:
        # 1. 场景 A: 正常小额单据 -> COMPLETE + LOW -> AUTO_APPROVE
        doc1 = await db.get(FinancialDocument, 1)
        report1 = (await db.execute(
            ReviewReport.__table__.select().where(ReviewReport.document_id == 1)
        )).first()
        rep1_obj = await db.get(ReviewReport, 1) if report1 else None

        decision1 = await ApprovalEngine.evaluate_transition(doc1, rep1_obj)
        print(f"\n[场景 A] 正常小额打车费 (<=500元)")
        print(f"  - 单据编号: {doc1.document_no}, 金额: ¥{doc1.total_amount}")
        print(f"  - 审核完整度: {rep1_obj.full_report_payload.get('audit_completeness')}")
        print(f"  - 风险等级: {rep1_obj.overall_risk_level.upper()}, 评分: {rep1_obj.final_score}")
        print(f"  - 审批引擎决策: {decision1.action.value} -> 目标状态: {decision1.target_state}")
        print(f"  - 决策说明: {decision1.reason}")
        assert decision1.action == ApprovalDecisionAction.AUTO_APPROVE, f"场景 A 期望 AUTO_APPROVE, 实际: {decision1.action}"
        print("  ✅ 场景 A 校验通过: COMPLETE + LOW -> AUTO_APPROVE 成功闭环！")

        # 2. 场景 B: 跨单重复发票 R08 -> HIGH + is_overridable=False -> REJECT
        doc2 = await db.get(FinancialDocument, 2)
        rep2_obj = await db.get(ReviewReport, 2)
        decision2 = await ApprovalEngine.evaluate_transition(doc2, rep2_obj)
        print(f"\n[场景 B] 跨单重复发票一票否决")
        print(f"  - 单据编号: {doc2.document_no}, 金额: ¥{doc2.total_amount}")
        print(f"  - 审核完整度: {rep2_obj.full_report_payload.get('audit_completeness')}")
        print(f"  - 风险等级: {rep2_obj.overall_risk_level.upper()}, 评分: {rep2_obj.final_score}")
        print(f"  - 审批引擎决策: {decision2.action.value} -> 目标状态: {decision2.target_state}")
        print(f"  - 决策说明: {decision2.reason}")
        assert decision2.action == ApprovalDecisionAction.REJECT, f"场景 B 期望 REJECT, 实际: {decision2.action}"
        print("  ✅ 场景 B 校验通过: 不可覆盖 HIGH -> REJECT 成功一票否决！")

        # 3. 场景 C: 供应商资质核验降级 -> DEGRADED -> MANUAL_REVIEW
        doc3 = await db.get(FinancialDocument, 3)
        rep3_obj = await db.get(ReviewReport, 3)
        decision3 = await ApprovalEngine.evaluate_transition(doc3, rep3_obj)
        print(f"\n[场景 C] 外部资质核验服务超时降级")
        print(f"  - 单据编号: {doc3.document_no}, 金额: ¥{doc3.total_amount}")
        print(f"  - 审核完整度: {rep3_obj.full_report_payload.get('audit_completeness')}")
        print(f"  - 风险等级: {rep3_obj.overall_risk_level.upper()}, 评分: {rep3_obj.final_score}")
        print(f"  - 审批引擎决策: {decision3.action.value} -> 目标状态: {decision3.target_state}")
        print(f"  - 决策说明: {decision3.reason}")
        assert decision3.action == ApprovalDecisionAction.MANUAL_REVIEW, f"场景 C 期望 MANUAL_REVIEW, 实际: {decision3.action}"
        print("  ✅ 场景 C 校验通过: DEGRADED -> MANUAL_REVIEW 成功阻断自动放行！")

        # 4. 场景 D (附加演示): ReviewerReflector 差旅津贴免票消歧 -> AUTO_APPROVE
        doc4 = await db.get(FinancialDocument, 4)
        rep4_obj = await db.get(ReviewReport, 4)
        decision4 = await ApprovalEngine.evaluate_transition(doc4, rep4_obj)
        print(f"\n[场景 D (附加)] 终审门禁差旅津贴免票反思消歧")
        print(f"  - 单据编号: {doc4.document_no}, 金额: ¥{doc4.total_amount}")
        print(f"  - 审核完整度: {rep4_obj.full_report_payload.get('audit_completeness')}")
        print(f"  - 消歧日志: {rep4_obj.full_report_payload.get('disambiguation_logs')}")
        print(f"  - 审批引擎决策: {decision4.action.value} -> 目标状态: {decision4.target_state}")
        print(f"  - 决策说明: {decision4.reason}")
        assert decision4.action == ApprovalDecisionAction.AUTO_APPROVE, f"场景 D 期望 AUTO_APPROVE, 实际: {decision4.action}"
        print("  ✅ 场景 D 校验通过: ReviewerReflector 终审消歧成功！")

    print("\n" + "=" * 70)
    print("🏆 全部 3 套核心场景 + 1 套反思消歧场景回放与断言 100% 通过！")
    print("=" * 70)

if __name__ == "__main__":
    asyncio.run(replay())
