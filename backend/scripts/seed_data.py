"""
backend/scripts/seed_data.py
财务单据智能风险审核系统 - 真实场景种子数据生成脚本
运行命令: uv run python scripts/seed_data.py
"""
import sys
import os
import asyncio

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
from decimal import Decimal
from datetime import datetime, timedelta, timezone

# 确保 backend 在 Python 路径中
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app.core.database import init_db, AsyncSessionLocal, engine, Base
from app.models.user import User, Role, UserRole
from app.models.workflow import ApprovalWorkflow, ApprovalWorkflowNode, ApprovalInstance, ApprovalTask, WorkflowStatusLog
from app.models.document import FinancialDocument, DocumentLineItem, DocumentAttachment, DocumentVersion
from app.models.invoice import InvoiceRecord
from app.models.policy import PolicyUnit, PolicyChunk
from app.models.supplier import SupplierProfile
from app.models.audit import AnalysisTask, ReviewReport, RiskFinding
from app.services.auth_service import AuthService

async def seed():
    print("🚀 正在重置并初始化数据库实体表...")
    import app.models  # noqa: F401
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)

    async with AsyncSessionLocal() as db:
        print("👤 正在预置用户与组织角色...")
        pwd_hash = AuthService.hash_password("123456")

        # 1. 角色
        roles = [
            Role(id=1, role_code="ADMIN", role_name="系统管理员", description="全系统管理与监控"),
            Role(id=2, role_code="MANAGER", role_name="直属部门主管", description="业务合理性初审"),
            Role(id=3, role_code="FINANCE", role_name="财务审核专员", description="合规与发票复核"),
            Role(id=4, role_code="CFO", role_name="财务总监/分管VP", description="高危红线与大额特批"),
            Role(id=5, role_code="EMPLOYEE", role_name="经办员工", description="单据申报与草稿编辑"),
        ]
        db.add_all(roles)
        await db.flush()

        # 2. 用户
        users = [
            User(id=1, username="admin", hashed_password=pwd_hash, real_name="系统管理员", department_name="信息技术部"),
            User(id=2, username="manager", hashed_password=pwd_hash, real_name="张经理", department_name="市场营销部"),
            User(id=3, username="finance", hashed_password=pwd_hash, real_name="李财务", department_name="财务审核中心"),
            User(id=4, username="cfo", hashed_password=pwd_hash, real_name="王总监", department_name="财务部"),
            User(id=5, username="emp", hashed_password=pwd_hash, real_name="小赵", department_name="市场营销部"),
        ]
        db.add_all(users)
        await db.flush()

        user_roles = [
            UserRole(user_id=1, role_id=1),
            UserRole(user_id=2, role_id=2),
            UserRole(user_id=3, role_id=3),
            UserRole(user_id=4, role_id=4),
            UserRole(user_id=5, role_id=5),
        ]
        db.add_all(user_roles)

        print("📋 正在预置审批流程模板与节点拓扑...")
        wf_travel = ApprovalWorkflow(
            id=1, workflow_code="WF_TRAVEL_STANDARD", workflow_name="差旅报销审批流", document_type="TRAVEL_REIMBURSEMENT"
        )
        wf_expense = ApprovalWorkflow(
            id=2, workflow_code="WF_EXPENSE_STANDARD", workflow_name="日常费用报销审批流", document_type="EXPENSE_REIMBURSEMENT"
        )
        wf_corp = ApprovalWorkflow(
            id=3, workflow_code="WF_CORP_PAYMENT", workflow_name="对公付款多级审批流", document_type="CORP_PAYMENT"
        )
        db.add_all([wf_travel, wf_expense, wf_corp])
        await db.flush()

        # 差旅审批节点
        wf_nodes = [
            ApprovalWorkflowNode(id=1, workflow_id=1, node_order=1, node_name="部门主管初审", approver_type="USER", user_id=2),
            ApprovalWorkflowNode(id=2, workflow_id=1, node_order=2, node_name="财务合规复核", approver_type="USER", user_id=3, is_final=True),
            # 日常费用节点
            ApprovalWorkflowNode(id=3, workflow_id=2, node_order=1, node_name="部门主管初审", approver_type="USER", user_id=2),
            ApprovalWorkflowNode(id=4, workflow_id=2, node_order=2, node_name="财务结算复核", approver_type="USER", user_id=3, is_final=True),
            # 对公付款节点
            ApprovalWorkflowNode(id=5, workflow_id=3, node_order=1, node_name="部门主管审核", approver_type="USER", user_id=2),
            ApprovalWorkflowNode(id=6, workflow_id=3, node_order=2, node_name="财务合规专员", approver_type="USER", user_id=3),
            ApprovalWorkflowNode(id=7, workflow_id=3, node_order=3, node_name="财务总监/分管VP特批", approver_type="USER", user_id=4, is_final=True),
        ]
        db.add_all(wf_nodes)

        print("⚖️ 正在预置企业差旅与采购风控制度库...")
        p1 = PolicyUnit(
            id=1, policy_code="POL_TRAVEL_2026", policy_name="企业差旅管理标准 (2026年修订版)",
            policy_type="差旅管理", is_active=True, effective_date=datetime(2026, 1, 1, tzinfo=timezone.utc)
        )
        db.add(p1)
        await db.flush()

        chunks = [
            PolicyChunk(
                policy_id=1, chunk_id="POL_TRAVEL_2026#CH_001",
                clause_title="第三条 一线城市住宿限额标准",
                clause_content="员工前往一线城市（北京、上海、广州、深圳）出差，住宿费限额标准为 500 元/人·天，超出部分不予报销或需由部门VP特批。",
                expense_category="住宿费",
                applicable_city_tier="TIER_1",
                max_amount_limit=Decimal("500.00")
            ),
            PolicyChunk(
                policy_id=1, chunk_id="POL_TRAVEL_2026#CH_002",
                clause_title="第四条 二线城市住宿限额标准",
                clause_content="前往二线重点城市（杭州、成都、武汉、南京等）出差，住宿费限额标准为 350 元/人·天。",
                expense_category="住宿费",
                applicable_city_tier="TIER_2",
                max_amount_limit=Decimal("350.00")
            )
        ]
        db.add_all(chunks)

        print("🏢 正在预置工商与供应商风险画像库...")
        suppliers = [
            SupplierProfile(
                uscc="91110108551385082Q",
                supplier_name="北京神州数码技术有限公司",
                legal_person="郭为",
                registered_capital="1000万元人民币",
                is_dishonest=False,
                is_shell_company=False,
                business_scope="计算机软硬件及外围设备的技术开发、技术转让、销售计算机、软件及辅助设备、技术服务。"
            ),
            SupplierProfile(
                uscc="91110108MA01TEST99",
                supplier_name="北京星火虚开供应链管理有限公司",
                legal_person="赵某某",
                registered_capital="5万元人民币",
                is_dishonest=True, # 失信被执行人
                is_shell_company=True, # 空壳嫌疑
                business_scope="供应链管理；销售新鲜蔬菜、水果、农副产品。"
            )
        ]
        db.add_all(suppliers)

        print("📑 正在构建 5 大典型风控单据与证据链...")
        now = datetime.now(timezone.utc)

        # -------------------------------------------------------------
        # 场景 1: 小额免审直通单 (≤500元低危) -> COMPLETE + LOW -> AUTO_APPROVE
        # -------------------------------------------------------------
        doc1 = FinancialDocument(
            id=1, document_no="EXP-20260312-PASS01", document_type="EXPENSE_REIMBURSEMENT",
            title="市内交通打车费 (场景A: COMPLETE + LOW -> AUTO_APPROVE 自动放行)",
            applicant_id=5, department_name="市场营销部",
            total_amount=Decimal("320.00"), currency="CNY", status="APPROVED",
            current_version=1, submission_time=now - timedelta(days=2)
        )
        db.add(doc1)
        await db.flush()
        db.add(DocumentLineItem(
            document_id=1, line_no=1, expense_type="交通费", item_desc="拜访客户往返滴滴出行",
            amount=Decimal("320.00"), city_name="北京"
        ))
        att1 = DocumentAttachment(
            document_id=1, file_name="滴滴出行行程电子客票.pdf", file_type="PDF",
            file_path=None, file_hash="hash_seed_01",
            file_size_bytes=815000, is_invoice=True, ocr_status="SUCCESS"
        )
        db.add(att1)
        await db.flush()

        db.add(InvoiceRecord(
            document_id=1, attachment_id=att1.id, invoice_code="011002000111", invoice_number="26110091",
            invoice_type="增值税电子普通发票", total_amount=Decimal("320.00"), untaxed_amount=Decimal("301.89"),
            tax_amount=Decimal("18.11"), tax_rate=Decimal("0.0600"),
            seller_tax_id="91110108551385082Q", seller_name="北京滴滴出行科技有限公司",
            buyer_name="北京智能前沿科技有限公司", buyer_tax_id="91110108MA01XXXXXX",
            issue_date="2026-03-11", invoice_hash="sha256_seed_01",
            raw_payload={
                "bbox_positions": None,
                "file_path": None
            }
        ))
        report1 = ReviewReport(
            id=1, task_id="task_seed_01", document_id=1, overall_risk_level="low",
            final_score=100, high_risks_count=0, medium_risks_count=0, low_risks_count=0,
            summary="智能风控核查无误：单据金额 320 元 ≤ 500 元且全项合规，命中小额免审规则直通放行，未命中任何违规项。",
            full_report_payload={
                "audit_completeness": "COMPLETE",
                "risk_score": 100,
                "final_score": 100,
                "overall_risk_level": "low",
                "approval_decision": {
                    "action": "AUTO_APPROVE",
                    "target_state": "APPROVED",
                    "reason": "单据金额 320.00 元 ≤ 500 元且全项合规，命中小额免审规则直通放行。"
                },
                "execution_plan": {
                    "planned_agents": ["InvoiceOcrAgent", "AmountAgent", "ComplianceAgent", "SupplierAgent"]
                },
                "agent_execution_results": {
                    "InvoiceOcrAgent": {"status": "SUCCESS", "elapsed_ms": 32, "source": "DETERMINISTIC", "reason": "发票票面与印章解析成功"},
                    "AmountAgent": {"status": "SUCCESS", "elapsed_ms": 18, "source": "DETERMINISTIC", "reason": "价税合计与申报金额精确吻合 (0容差)"},
                    "ComplianceAgent": {"status": "SUCCESS", "elapsed_ms": 25, "source": "DETERMINISTIC", "reason": "未触发任何合规红线与超标"},
                    "SupplierAgent": {"status": "SUCCESS", "elapsed_ms": 29, "source": "DETERMINISTIC", "reason": "供应商经营资质存续合规"}
                }
            }
        )
        db.add(report1)
        await db.flush()

        inst1 = ApprovalInstance(
            id=1, workflow_id=2, document_id=1, report_id=1, audit_version=1, status="COMPLETED",
            start_time=now - timedelta(days=2), end_time=now - timedelta(days=2)
        )
        db.add(inst1)
        await db.flush()
        auto_task1 = ApprovalTask(
            instance_id=1, node_id=3, assignee_id=None, status="AUTO_PASSED",
            comment="系统自动免审直通通过", created_at=now - timedelta(days=2), end_time=now - timedelta(days=2)
        )
        db.add(auto_task1)
        await db.flush()
        db.add(WorkflowStatusLog(
            instance_id=1, task_id=auto_task1.id, operator_id=None, action="AUTO_PASS",
            comment="单据金额 320.00 元 ≤ 500 元且全项合规，命中小额免审规则直通放行。"
        ))

        # -------------------------------------------------------------
        # 场景 2: 跨单重复发票一票否决 -> 不可覆盖 HIGH -> REJECT
        # -------------------------------------------------------------
        doc2 = FinancialDocument(
            id=2, document_no="EXP-20260313-VETO02", document_type="EXPENSE_REIMBURSEMENT",
            title="商务出差差旅报销 (场景B: 跨单重复发票 R08 -> HIGH不可覆盖 -> REJECT 一票否决)",
            applicant_id=5, department_name="市场营销部",
            total_amount=Decimal("850.00"), currency="CNY", status="REJECTED",
            current_version=1, submission_time=now - timedelta(days=1)
        )
        db.add(doc2)
        await db.flush()
        db.add(DocumentLineItem(
            document_id=2, line_no=1, expense_type="住宿费", item_desc="商务差旅全季酒店1晚",
            amount=Decimal("850.00"), city_name="上海", start_date=now - timedelta(days=2)
        ))

        att2 = DocumentAttachment(
            document_id=2, file_name="上海全季酒店住宿费专票.pdf", file_type="PDF",
            file_path=None, file_hash="hash_seed_02",
            file_size_bytes=28815, is_invoice=True, ocr_status="SUCCESS"
        )
        db.add(att2)
        await db.flush()

        db.add(InvoiceRecord(
            document_id=2, attachment_id=att2.id, invoice_code="031001900111", invoice_number="20227891",
            invoice_type="增值税专用发票", total_amount=Decimal("850.00"), untaxed_amount=Decimal("801.89"),
            tax_amount=Decimal("48.11"), tax_rate=Decimal("0.0600"),
            seller_name="上海全季商务酒店管理有限公司", seller_tax_id="91310115MA1H789012",
            buyer_name="北京智能前沿科技有限公司", buyer_tax_id="91110108MA01XXXXXX",
            issue_date="2026-03-10", invoice_hash="sha256_seed_02",
            raw_payload={
                "bbox_positions": None,
                "file_path": None
            }
        ))

        report2 = ReviewReport(
            id=2, task_id="task_seed_02", document_id=2, overall_risk_level="high",
            final_score=20, high_risks_count=1, medium_risks_count=0, low_risks_count=0,
            summary="🚨 触犯企业最高风控红线：检出【跨单重复发票报销】（发票号码 20227891 已在前期已办结单据中报销并归档），属于不可覆盖的一票否决违规项，系统直接终止审批流程并予以驳回。",
            full_report_payload={
                "audit_completeness": "COMPLETE",
                "risk_score": 20,
                "final_score": 20,
                "overall_risk_level": "high",
                "approval_decision": {
                    "action": "REJECT",
                    "target_state": "REJECTED",
                    "reason": "检出一票否决高危违规项（不可覆盖），优先于完整度直接驳回: 跨单重复发票报销。"
                },
                "findings": [
                    {
                        "rule_code": "R08_DUPLICATE_INVOICE",
                        "rule_name": "跨单重复发票报销",
                        "risk_level": "high",
                        "is_overridable": False,
                        "title": "发票代码 [031001900111] 号码 [20227891] 跨单重复报销"
                    }
                ],
                "execution_plan": {
                    "planned_agents": ["InvoiceOcrAgent", "AmountAgent", "ComplianceAgent", "SupplierAgent"]
                },
                "agent_execution_results": {
                    "InvoiceOcrAgent": {"status": "SUCCESS", "elapsed_ms": 36, "source": "DETERMINISTIC", "reason": "发票票面提取成功"},
                    "AmountAgent": {"status": "SUCCESS", "elapsed_ms": 19, "source": "DETERMINISTIC", "reason": "金额平衡计算无误"},
                    "ComplianceAgent": {"status": "SUCCESS", "elapsed_ms": 48, "source": "DETERMINISTIC", "reason": "在跨期报销库与历史归档单据中匹配到相同发票代码与号码，判定跨单重复报销"},
                    "SupplierAgent": {"status": "SUCCESS", "elapsed_ms": 27, "source": "DETERMINISTIC", "reason": "供应商资质核验通过"}
                }
            }
        )
        db.add(report2)
        await db.flush()
        db.add(RiskFinding(
            report_id=2, finding_id="find_seed_02", rule_code="R08_DUPLICATE_INVOICE",
            rule_name="跨单重复发票报销", risk_level="high", agent_role="ComplianceAgent",
            title="发票代码 [031001900111] 号码 [20227891] 跨单重复报销",
            description="依据《企业发票报销查重管理规范》，该发票已在已结案单据 EXP-20260210-001 中审核列支，本次再次作为附件提交申报，触发一票否决红线。",
            actual_value={"invoice_code": "031001900111", "invoice_number": "20227891", "prior_document_no": "EXP-20260210-001"},
            expected_value={"is_duplicate": False},
            discrepancy_amount=Decimal("850.00"),
            is_overridable=False, # 一票否决，严禁覆盖！
            primary_visual_anchor=None,
            suggestion="一票否决终止审批流并直接驳回，责令经办人自查。"
        ))
        inst2 = ApprovalInstance(
            id=2, workflow_id=2, document_id=2, report_id=2, audit_version=1, status="TERMINATED",
            start_time=now - timedelta(days=1), end_time=now - timedelta(days=1)
        )
        db.add(inst2)
        await db.flush()
        # 一票否决：不创建 PENDING 任务，直接落库状态转移日志
        db.add(WorkflowStatusLog(
            instance_id=2, task_id=None, operator_id=None, action="AUTO_REJECT",
            comment="检出一票否决高危违规项（不可覆盖），优先于完整度直接驳回: 跨单重复发票报销。"
        ))

        # -------------------------------------------------------------
        # 场景 3: 外部核验服务超时降级 -> DEGRADED -> MANUAL_REVIEW
        # -------------------------------------------------------------
        doc3 = FinancialDocument(
            id=3, document_no="EXP-20260314-DEGR003", document_type="EXPENSE_REIMBURSEMENT",
            title="外地会务技术运维技术服务费 (场景C: 供应商画像核验降级 DEGRADED -> MANUAL_REVIEW 人工复核)",
            applicant_id=5, department_name="市场营销部",
            total_amount=Decimal("2400.00"), currency="CNY", status="PENDING_APPROVAL",
            current_version=1, submission_time=now - timedelta(hours=8)
        )
        db.add(doc3)
        await db.flush()
        db.add(DocumentLineItem(
            document_id=3, line_no=1, expense_type="技术服务费", item_desc="云会议支持服务",
            amount=Decimal("2400.00"), city_name="北京"
        ))
        att3 = DocumentAttachment(
            document_id=3, file_name="技术服务费普通发票.pdf", file_type="PDF",
            file_path=None, file_hash="hash_88203001",
            file_size_bytes=28925, is_invoice=True, ocr_status="SUCCESS"
        )
        db.add(att3)
        await db.flush()

        db.add(InvoiceRecord(
            document_id=3, attachment_id=att3.id, invoice_code="011002000222", invoice_number="88203001",
            invoice_type="增值税电子普通发票", total_amount=Decimal("2400.00"), untaxed_amount=Decimal("2264.15"), tax_amount=Decimal("135.85"),
            seller_tax_id="91110108MA01TEST99", seller_name="北京创新网络技术服务工作室", issue_date="2026-03-10",
            invoice_hash="sha256_011002000222_88203001",
            raw_payload={
                "bbox_positions": None,
                "file_path": None
            }
        ))
        report3 = ReviewReport(
            id=3, task_id="task_seed_03", document_id=3, overall_risk_level="low",
            final_score=95, high_risks_count=0, medium_risks_count=0, low_risks_count=0,
            summary="智能风控审查完毕：基础票面核验优良。但因国家企业信用信息公示系统外部接口调用超时，供应商资质核验发生降级 (DEGRADED)，触发防盲区安全门禁，禁止自动放行，转入人工重点复核。",
            full_report_payload={
                "audit_completeness": "DEGRADED",
                "risk_score": 95,
                "final_score": 95,
                "overall_risk_level": "low",
                "approval_decision": {
                    "action": "MANUAL_REVIEW",
                    "target_state": "PENDING_APPROVAL",
                    "reason": "审核完整度为 [DEGRADED]（存在核验降级或部分要素缺失），禁止自动放行，转入人工重点复核。"
                },
                "execution_plan": {
                    "planned_agents": ["InvoiceOcrAgent", "AmountAgent", "ComplianceAgent", "SupplierAgent"]
                },
                "agent_execution_results": {
                    "InvoiceOcrAgent": {"status": "SUCCESS", "elapsed_ms": 35, "source": "DETERMINISTIC", "reason": "票面信息提取一致"},
                    "AmountAgent": {"status": "SUCCESS", "elapsed_ms": 19, "source": "DETERMINISTIC", "reason": "金额及税额平衡校验通过"},
                    "ComplianceAgent": {"status": "SUCCESS", "elapsed_ms": 26, "source": "DETERMINISTIC", "reason": "内控规则初查无违规"},
                    "SupplierAgent": {"status": "DEGRADED", "elapsed_ms": 150, "source": "DETERMINISTIC", "reason": "外部企业资质查询接口响应超时(>150ms)，供应商经营范围与存续状态未完成全量穿透核验，降级运行"}
                }
            }
        )
        db.add(report3)
        await db.flush()

        inst3 = ApprovalInstance(
            id=3, workflow_id=2, document_id=3, report_id=3, audit_version=1, status="RUNNING",
            current_node_id=3, start_time=now - timedelta(hours=8)
        )
        db.add(inst3)
        await db.flush()
        db.add(ApprovalTask(
            instance_id=3, node_id=3, assignee_id=2, status="PENDING", # 张经理待办
            comment="审核完整度降级 (DEGRADED)，系统转入人工重点复核", created_at=now - timedelta(hours=8)
        ))
        db.add(WorkflowStatusLog(
            instance_id=3, task_id=None, operator_id=None, action="SUBMIT_FOR_REVIEW",
            comment="审核完整度为 [DEGRADED]（存在核验降级或部分要素缺失），禁止自动放行，转入人工重点复核。"
        ))

        # -------------------------------------------------------------
        # 场景 4: 附加演示案例 - ReviewerReflector 差礼津贴免票消歧 -> AUTO_APPROVE
        # -------------------------------------------------------------
        doc4 = FinancialDocument(
            id=4, document_no="TRV-20260315-DISAM04", document_type="TRAVEL_REIMBURSEMENT",
            title="异地出差交通与合规津贴报销 (附加演示: ReviewerReflector 差旅津贴免票自动消歧)",
            applicant_id=5, department_name="市场营销部",
            total_amount=Decimal("480.00"), currency="CNY", status="APPROVED",
            current_version=1, submission_time=now - timedelta(hours=4)
        )
        db.add(doc4)
        await db.flush()
        db.add(DocumentLineItem(
            document_id=4, line_no=1, expense_type="交通费", item_desc="高铁二等座车票",
            amount=Decimal("380.00"), city_name="天津"
        ))
        db.add(DocumentLineItem(
            document_id=4, line_no=2, expense_type="出差津贴", item_desc="1天出差市内包干津贴 (免发票)",
            amount=Decimal("100.00"), city_name="天津"
        ))

        att4 = DocumentAttachment(
            document_id=4, file_name="高铁车票报销凭证.pdf", file_type="PDF",
            file_path=None, file_hash="hash_seed_04",
            file_size_bytes=1289752, is_invoice=True, ocr_status="SUCCESS"
        )
        db.add(att4)
        await db.flush()

        db.add(InvoiceRecord(
            document_id=4, attachment_id=att4.id, invoice_code="031001900888", invoice_number="88001234",
            invoice_type="铁路电子客票", total_amount=Decimal("380.00"), untaxed_amount=Decimal("348.62"),
            tax_amount=Decimal("31.38"), tax_rate=Decimal("0.0900"),
            seller_name="中国铁路北京局集团有限公司", seller_tax_id="911100001322000000",
            buyer_name="北京智能前沿科技有限公司", buyer_tax_id="91110108MA01XXXXXX",
            issue_date="2026-03-12", invoice_hash="sha256_seed_04",
            raw_payload={
                "bbox_positions": None,
                "file_path": None
            }
        ))

        report4 = ReviewReport(
            id=4, task_id="task_seed_04", document_id=4, overall_risk_level="low",
            final_score=100, high_risks_count=0, medium_risks_count=0, low_risks_count=0,
            summary="智能风控审查完毕：综合评分 100 分。经终审门禁反思 (ReviewerReflector)，单据申报 480 元与发票 380 元的 100 元差额确认为合规免票差旅包干津贴 (allowance_policy_verified=True)，系统已自动消除 R02 异常，全项合规放行。",
            full_report_payload={
                "audit_completeness": "COMPLETE",
                "risk_score": 100,
                "final_score": 100,
                "overall_risk_level": "low",
                "approval_decision": {
                    "action": "AUTO_APPROVE",
                    "target_state": "APPROVED",
                    "reason": "经终审反思门禁核验，差额确认为合规免票差旅津贴，消歧后全项放行。"
                },
                "execution_plan": {
                    "planned_agents": ["InvoiceOcrAgent", "AmountAgent", "ComplianceAgent", "ReviewerReflector"]
                },
                "agent_execution_results": {
                    "InvoiceOcrAgent": {"status": "SUCCESS", "elapsed_ms": 30, "source": "DETERMINISTIC", "reason": "铁路客票核验无误"},
                    "AmountAgent": {"status": "SUCCESS", "elapsed_ms": 22, "source": "DETERMINISTIC", "reason": "初步检出发票金额 380 与申报 480 存在 100 元偏差"},
                    "ReviewerReflector": {"status": "SUCCESS", "elapsed_ms": 65, "source": "DETERMINISTIC", "reason": "检索到《企业差旅管理标准》第3.4条包干津贴条款 (100元/天*1天)，确认 allowance_policy_verified=True，执行自动消歧核减"}
                },
                "disambiguation_logs": [
                    {
                        "rule_code": "R02_AMOUNT_MISMATCH",
                        "action": "自动消歧",
                        "reason": "发票金额 380.00 元与单据总额 480.00 元之差额 100.00 元，已成功匹配制度库差旅包干津贴标准（每天100元*1天，allowance_policy_verified=True），免票条款依据确凿，原金额不一致风险已自动解除并核减。"
                    }
                ]
            }
        )
        db.add(report4)
        await db.flush()

        inst4 = ApprovalInstance(
            id=4, workflow_id=1, document_id=4, report_id=4, audit_version=1, status="COMPLETED",
            start_time=now - timedelta(hours=4), end_time=now - timedelta(hours=4)
        )
        db.add(inst4)
        await db.flush()
        auto_task4 = ApprovalTask(
            instance_id=4, node_id=2, assignee_id=None, status="AUTO_PASSED",
            comment="终审反思消歧通过，系统自动放行", created_at=now - timedelta(hours=4), end_time=now - timedelta(hours=4)
        )
        db.add(auto_task4)
        await db.flush()
        db.add(WorkflowStatusLog(
            instance_id=4, task_id=auto_task4.id, operator_id=None, action="AUTO_PASS",
            comment="经终审反思门禁核验，差额确认为合规免票差旅津贴，消歧后全项放行。"
        ))

        # -------------------------------------------------------------
        # 场景 5: 真实原件真图与 OCR 锚点体验单 (待提交草稿) -> DRAFT
        # -------------------------------------------------------------
        doc5 = FinancialDocument(
            id=5, document_no="EXP-20260316-DFT005", document_type="EXPENSE_REIMBURSEMENT",
            title="商务出差住宿费报销 (真实原件真图与 OCR 体验单)",
            applicant_id=5, department_name="市场营销部",
            total_amount=Decimal("700.00"), currency="CNY", status="DRAFT",
            current_version=1
        )
        db.add(doc5)
        await db.flush()
        db.add(DocumentLineItem(
            document_id=5, line_no=1, expense_type="住宿费", item_desc="陕西世纪金源大饭店住宿",
            amount=Decimal("700.00"), city_name="西安"
        ))
        att5 = DocumentAttachment(
            document_id=5, file_name="陕西世纪金源大饭店住宿发票.png", file_type="PNG",
            file_path="/uploads/invoices/inv_4bb34d2b5c99.png", file_hash="hash_seed_05",
            file_size_bytes=815036, is_invoice=True, ocr_status="SUCCESS"
        )
        db.add(att5)
        await db.flush()

        db.add(InvoiceRecord(
            document_id=5, attachment_id=att5.id, invoice_code="246120000001", invoice_number="0101644605",
            invoice_type="增值税电子普通发票", total_amount=Decimal("700.00"), untaxed_amount=Decimal("660.38"),
            tax_amount=Decimal("39.62"), tax_rate=Decimal("0.0600"),
            seller_name="陕西世纪金源大饭店有限公司", seller_tax_id="91610132742813133A",
            buyer_name="陕西西咸新区秦汉新城中诺机械科技有限公司", buyer_tax_id="91611104MA70W95J4N",
            issue_date="2024-11-07", invoice_hash="sha256_seed_05",
            raw_payload={
                "bbox_positions": {
                    "total_amount": [742, 695, 792, 942],
                    "invoice_number": [128, 640, 160, 940],
                    "seller_info": [830, 210, 920, 520],
                    "issue_date": [165, 715, 195, 930]
                },
                "file_path": "/uploads/invoices/inv_4bb34d2b5c99.png"
            }
        ))

        await db.commit()

    print("🎉 种子数据预置完成！")
    print("=========================================================")
    print(" 登录账号清单 (密码统一为 123456):")
    print("   1. 经办员工: emp      (小赵)")
    print("   2. 主管初审: manager  (张经理)")
    print("   3. 财务复核: finance  (李财务)")
    print("   4. 财务总监: cfo      (王总监)")
    print("   5. 管理员:   admin    (管理员)")
    print("=========================================================")

if __name__ == "__main__":
    asyncio.run(seed())
