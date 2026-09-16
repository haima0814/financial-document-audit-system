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
        # 场景 1: 小额免审直通单 (≤500元低危) -> AUTO_PASS / APPROVED
        # -------------------------------------------------------------
        doc1 = FinancialDocument(
            id=1, document_no="EXP-20260312-PASS01", document_type="EXPENSE_REIMBURSEMENT",
            title="市内交通打车费 (触发 RULE_AUTO_PASS 小额免审直通)",
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
            document_id=1, file_name="滴滴出行行程电子客票.png", file_type="PNG",
            file_path="/uploads/invoices/inv_4bb34d2b5c99.png", file_hash="hash_seed_01",
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
                "bbox_positions": {
                    "total_amount": [420, 680, 480, 940],
                    "invoice_number": [70, 680, 120, 950],
                    "seller_name": [160, 200, 210, 450],
                    "issue_date": [90, 700, 130, 900]
                },
                "file_path": "/uploads/invoices/inv_4bb34d2b5c99.png"
            }
        ))
        db.add(ReviewReport(
            id=1, task_id="task_seed_01", document_id=1, overall_risk_level="low",
            final_score=100, high_risks_count=0, medium_risks_count=0, low_risks_count=0,
            summary="智能风控核查无误：单据金额 320 元 ≤ 500 元且全项合规，命中小额免审规则直通放行，未命中任何违规项。"
        ))
        inst1 = ApprovalInstance(
            id=1, workflow_id=2, document_id=1, status="COMPLETED",
            start_time=now - timedelta(days=2), end_time=now - timedelta(days=2)
        )
        db.add(inst1)
        await db.flush()
        db.add(ApprovalTask(
            instance_id=1, node_id=3, assignee_id=0, status="AUTO_PASSED",
            comment="系统自动免审直通通过"
        ))

        # -------------------------------------------------------------
        # 场景 2: 差旅住宿超标单 (上海住宿 850元/天，限额 500元) -> R05
        # -------------------------------------------------------------
        doc2 = FinancialDocument(
            id=2, document_no="TRV-20260313-HOTEL2", document_type="TRAVEL_REIMBURSEMENT",
            title="上海商务出差超标报销 (触发 R05 超标风控)",
            applicant_id=5, department_name="市场营销部",
            total_amount=Decimal("1500.00"), currency="CNY", status="PENDING_APPROVAL",
            current_version=1, submission_time=now - timedelta(days=1)
        )
        db.add(doc2)
        await db.flush()
        db.add(DocumentLineItem(
            document_id=2, line_no=1, expense_type="交通费", item_desc="京沪高铁二等座往返",
            amount=Decimal("650.00"), city_name="上海", start_date=now - timedelta(days=3)
        ))
        db.add(DocumentLineItem(
            document_id=2, line_no=2, expense_type="住宿费", item_desc="豪华商务酒店1晚",
            amount=Decimal("850.00"), city_name="上海", start_date=now - timedelta(days=2)
        ))

        att2 = DocumentAttachment(
            document_id=2, file_name="上海全季酒店住宿费专票.png", file_type="PNG",
            file_path="/uploads/invoices/inv_d87211a2b22c.png", file_hash="hash_seed_02",
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
                "bbox_positions": {
                    "total_amount": [420, 680, 480, 940],
                    "invoice_number": [70, 680, 120, 950],
                    "seller_name": [160, 200, 210, 450],
                    "seller_tax_id": [215, 200, 255, 450],
                    "issue_date": [90, 700, 130, 900]
                },
                "file_path": "/uploads/invoices/inv_d87211a2b22c.png"
            }
        ))

        report2 = ReviewReport(
            id=2, task_id="task_seed_02", document_id=2, overall_risk_level="medium",
            final_score=80, high_risks_count=0, medium_risks_count=1, low_risks_count=0,
            summary="智能风控审查完毕：综合评分 80 分。核心违规项：【差旅住宿费超出城市制度限额】依据《企业差旅管理标准》第3.2条，上海一线城市住宿费上限 500.00 元/天，申报金额 850.00 元，超标 350.00 元。"
        )
        db.add(report2)
        await db.flush()
        db.add(RiskFinding(
            report_id=2, finding_id="find_seed_02", rule_code="R05_POLICY_EXCEEDED",
            rule_name="差旅住宿费超出城市制度限额", risk_level="medium", agent_role="PolicyAgent",
            title="上海差旅住宿超标 350.00 元",
            description="依据《企业差旅管理标准》第3.2条，上海属于一线城市，住宿费报销上限为 500 元/天，本次申报金额为 850 元/天。",
            actual_value={"city": "上海", "amount_per_day": 850.00},
            expected_value={"city_tier": "TIER_1", "max_amount": 500.00},
            discrepancy_amount=Decimal("350.00"),
            primary_visual_anchor={"box_2d": [420, 680, 480, 940], "label": "发票住宿费金额 850.00元 (超标350元)"},
            suggestion="按制度上限 500 元核销，差额 350 元由员工个人自理；或提供业务 VP 书面特批由审批人在审批意见中注明。"
        ))
        inst2 = ApprovalInstance(
            id=2, workflow_id=1, document_id=2, status="RUNNING",
            current_node_id=1, start_time=now - timedelta(days=1)
        )
        db.add(inst2)
        await db.flush()
        db.add(ApprovalTask(
            instance_id=2, node_id=1, assignee_id=2, status="PENDING", # 张经理待办
            created_at=now - timedelta(days=1)
        ))

        # -------------------------------------------------------------
        # 场景 3: 餐饮发票连号异常单 (连号发票集中报销) -> R10
        # -------------------------------------------------------------
        doc3 = FinancialDocument(
            id=3, document_no="EXP-20260314-SEQ003", document_type="EXPENSE_REIMBURSEMENT",
            title="部门团建与办公物资集中采购 (触发 R10 连号发票风控)",
            applicant_id=5, department_name="市场营销部",
            total_amount=Decimal("2400.00"), currency="CNY", status="PENDING_APPROVAL",
            current_version=1, submission_time=now - timedelta(hours=8)
        )
        db.add(doc3)
        await db.flush()
        db.add(DocumentLineItem(
            document_id=3, line_no=1, expense_type="餐饮费", item_desc="部门业务招待发票1",
            amount=Decimal("1200.00"), city_name="北京"
        ))
        db.add(DocumentLineItem(
            document_id=3, line_no=2, expense_type="餐饮费", item_desc="部门业务招待发票2",
            amount=Decimal("1200.00"), city_name="北京"
        ))
        # 记录发票附件
        att3_1 = DocumentAttachment(
            document_id=3, file_name="餐饮消费电子发票_88203001.png", file_type="PNG",
            file_path="/uploads/invoices/inv_aa7910dfa882.png", file_hash="hash_88203001",
            file_size_bytes=28925, is_invoice=True, ocr_status="SUCCESS"
        )
        att3_2 = DocumentAttachment(
            document_id=3, file_name="餐饮消费电子发票_88203002.png", file_type="PNG",
            file_path="/uploads/invoices/inv_2512924c3749.png", file_hash="hash_88203002",
            file_size_bytes=5079, is_invoice=True, ocr_status="SUCCESS"
        )
        db.add_all([att3_1, att3_2])
        await db.flush()

        db.add(InvoiceRecord(
            document_id=3, attachment_id=att3_1.id, invoice_code="011002000222", invoice_number="88203001",
            invoice_type="增值税电子普通发票", total_amount=Decimal("1200.00"), untaxed_amount=Decimal("1132.08"), tax_amount=Decimal("67.92"),
            seller_tax_id="91110108551385082Q", seller_name="北京餐饮服务中心", issue_date="2026-03-10",
            invoice_hash="sha256_011002000222_88203001",
            raw_payload={
                "bbox_positions": {
                    "invoice_number": [70, 680, 120, 950],
                    "total_amount": [420, 680, 480, 940],
                    "seller_name": [160, 200, 210, 450]
                },
                "file_path": "/uploads/invoices/inv_aa7910dfa882.png"
            }
        ))
        db.add(InvoiceRecord(
            document_id=3, attachment_id=att3_2.id, invoice_code="011002000222", invoice_number="88203002", # 连号！
            invoice_type="增值税电子普通发票", total_amount=Decimal("1200.00"), untaxed_amount=Decimal("1132.08"), tax_amount=Decimal("67.92"),
            seller_tax_id="91110108551385082Q", seller_name="北京餐饮服务中心", issue_date="2026-03-10",
            invoice_hash="sha256_011002000222_88203002",
            raw_payload={
                "bbox_positions": {
                    "invoice_number": [70, 680, 120, 950],
                    "total_amount": [420, 680, 480, 940],
                    "seller_name": [160, 200, 210, 450]
                },
                "file_path": "/uploads/invoices/inv_2512924c3749.png"
            }
        ))
        report3 = ReviewReport(
            id=3, task_id="task_seed_03", document_id=3, overall_risk_level="medium",
            final_score=75, high_risks_count=0, medium_risks_count=1, low_risks_count=0,
            summary="智能风控审查完毕：综合评分 75 分。核心违规项：【检出同批次连号发票集中入账嫌疑】申请人在本单内提交了连号发票 [88203001, 88203002]，总额 2,400 元，疑似拆单避审。"
        )
        db.add(report3)
        await db.flush()
        db.add(RiskFinding(
            report_id=3, finding_id="find_seed_03", rule_code="R10_SEQUENTIAL_INVOICES",
            rule_name="检出同批次连号发票集中入账嫌疑", risk_level="medium", agent_role="AnomalyAgent",
            title="餐饮发票 [88203001-88203002] 连号开具",
            description="检测到发票号码 88203001 与 88203002 属于同一销售方且号码连续，总额 2,400 元，疑似规避 2,000 元以上审批门槛。",
            actual_value={"invoice_numbers": ["88203001", "88203002"]},
            expected_value={"rule": "正常发票散列"},
            primary_visual_anchor={"box_2d": [70, 680, 120, 950], "label": "连号发票号码 88203001"},
            suggestion="核实发票开具真实背景及消费明细水单，核实是否存在化整为零恶意拆单拆分发票行为。"
        ))
        inst3 = ApprovalInstance(
            id=3, workflow_id=2, document_id=3, status="RUNNING",
            current_node_id=3, start_time=now - timedelta(hours=8)
        )
        db.add(inst3)
        await db.flush()
        db.add(ApprovalTask(
            instance_id=3, node_id=3, assignee_id=2, status="PENDING", # 张经理待办
            created_at=now - timedelta(hours=8)
        ))

        # -------------------------------------------------------------
        # 场景 4: 对公付款失信供应商风险单 (高危红线) -> R12 + R15
        # -------------------------------------------------------------
        doc4 = FinancialDocument(
            id=4, document_no="CORP-20260315-RED004", document_type="CORP_PAYMENT",
            title="对外技术运维服务采购款 (触发 R12/R15 供应商黑名单风控)",
            applicant_id=5, department_name="市场营销部",
            total_amount=Decimal("50000.00"), currency="CNY", status="PENDING_APPROVAL",
            current_version=1, submission_time=now - timedelta(hours=4)
        )
        db.add(doc4)
        await db.flush()
        db.add(DocumentLineItem(
            document_id=4, line_no=1, expense_type="技术服务费", item_desc="云架构运维第一期付款",
            amount=Decimal("50000.00"), city_name="北京"
        ))

        att4 = DocumentAttachment(
            document_id=4, file_name="云架构运维技术服务专票.jpg", file_type="JPG",
            file_path="/uploads/invoices/inv_d70ddfc1e7a2.jpg", file_hash="hash_seed_04",
            file_size_bytes=1289752, is_invoice=True, ocr_status="SUCCESS"
        )
        db.add(att4)
        await db.flush()

        db.add(InvoiceRecord(
            document_id=4, attachment_id=att4.id, invoice_code="011002000999", invoice_number="99001234",
            invoice_type="增值税专用发票", total_amount=Decimal("50000.00"), untaxed_amount=Decimal("47169.81"),
            tax_amount=Decimal("2830.19"), tax_rate=Decimal("0.0600"),
            seller_name="北京星火虚开供应链管理有限公司", seller_tax_id="91110108MA01TEST99",
            buyer_name="北京智能前沿科技有限公司", buyer_tax_id="91110108MA01XXXXXX",
            issue_date="2026-03-08", invoice_hash="sha256_seed_04",
            raw_payload={
                "bbox_positions": {
                    "total_amount": [420, 680, 480, 940],
                    "invoice_number": [70, 680, 120, 950],
                    "seller_name": [180, 520, 230, 950],
                    "seller_tax_id": [220, 520, 270, 950]
                },
                "file_path": "/uploads/invoices/inv_d70ddfc1e7a2.jpg"
            }
        ))

        report4 = ReviewReport(
            id=4, task_id="task_seed_04", document_id=4, overall_risk_level="high",
            final_score=35, high_risks_count=2, medium_risks_count=0, low_risks_count=0,
            summary="🚨 触犯企业高危风控红线：收款供应商【北京星火虚开供应链管理有限公司】已被最高人民法院列入失信被执行人名单，且实缴注册资本仅5万元存在空壳公司嫌疑！审批人若特批放行必须填写具名 override_reason！"
        )
        db.add(report4)
        await db.flush()
        db.add(RiskFinding(
            report_id=4, finding_id="find_seed_04_a", rule_code="R12_DISHONEST_DEBTOR",
            rule_name="交易供应商为失信被执行人", risk_level="high", agent_role="SupplierAgent",
            title="供应商 [北京星火虚开供应链管理有限公司] 属于失信被执行人",
            description="统一社会信用代码 91110108MA01TEST99 在全国失信惩戒数据库命中，涉及司法执行标的金额逾千万元。",
            actual_value={"is_dishonest_debtor": True, "dishonest_record_count": 3},
            expected_value={"is_dishonest_debtor": False},
            primary_visual_anchor={"box_2d": [220, 520, 270, 950], "label": "供应商纳税人识别号 (失信被执行人)"},
            suggestion="立即中止付款，启动法务与供应商合规背调程序。"
        ))
        db.add(RiskFinding(
            report_id=4, finding_id="find_seed_04_b", rule_code="R15_SHELL_COMPANY_SUSPICION",
            rule_name="疑似空壳走账公司与资金外流风险", risk_level="high", agent_role="SupplierAgent",
            title="注册资本仅 5 万元且税务被列入非正常户",
            description="该供应商注册资本仅 50,000 元，参保人数为 0 人，纳税信用等级为 D 级且处于税务非正常户走逃状态。",
            actual_value={"registered_capital": 50000, "is_abnormal_taxpayer": True},
            expected_value={"min_capital": 1000000},
            primary_visual_anchor={"box_2d": [180, 520, 230, 950], "label": "供应商名称 (疑似空壳主体)"},
            suggestion="财务严控放款，严禁通过失信空壳主体洗钱或套取资金。"
        ))
        inst4 = ApprovalInstance(
            id=4, workflow_id=3, document_id=4, status="RUNNING",
            current_node_id=7, start_time=now - timedelta(hours=4)
        )
        db.add(inst4)
        await db.flush()
        db.add(ApprovalTask(
            instance_id=4, node_id=7, assignee_id=4, status="PENDING", # 王总监/CFO 特批待办
            created_at=now - timedelta(hours=4)
        ))

        # -------------------------------------------------------------
        # 场景 5: 日常办公用品草稿单 (待提交体验) -> DRAFT
        # -------------------------------------------------------------
        doc5 = FinancialDocument(
            id=5, document_no="EXP-20260316-DFT005", document_type="EXPENSE_REIMBURSEMENT",
            title="研发部日常办公文具与实验耗材采购 (待提交草稿示例)",
            applicant_id=5, department_name="市场营销部",
            total_amount=Decimal("680.00"), currency="CNY", status="DRAFT",
            current_version=1
        )
        db.add(doc5)
        await db.flush()
        db.add(DocumentLineItem(
            document_id=5, line_no=1, expense_type="办公用品", item_desc="打印纸与马克笔等耗材",
            amount=Decimal("680.00"), city_name="北京"
        ))
        att5 = DocumentAttachment(
            document_id=5, file_name="办公用品采购发票.png", file_type="PNG",
            file_path="/uploads/invoices/inv_4bb34d2b5c99.png", file_hash="hash_seed_05",
            file_size_bytes=815036, is_invoice=True, ocr_status="SUCCESS"
        )
        db.add(att5)
        await db.flush()

        db.add(InvoiceRecord(
            document_id=5, attachment_id=att5.id, invoice_code="011002000555", invoice_number="55667788",
            invoice_type="增值税电子普通发票", total_amount=Decimal("680.00"), untaxed_amount=Decimal("641.51"),
            tax_amount=Decimal("38.49"), tax_rate=Decimal("0.0600"),
            seller_name="北京晨光文具科技有限公司", seller_tax_id="91110108551385082Q",
            buyer_name="北京智能前沿科技有限公司", buyer_tax_id="91110108MA01XXXXXX",
            issue_date="2026-03-15", invoice_hash="sha256_seed_05",
            raw_payload={
                "bbox_positions": {
                    "total_amount": [420, 680, 480, 940],
                    "invoice_number": [70, 680, 120, 950]
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
