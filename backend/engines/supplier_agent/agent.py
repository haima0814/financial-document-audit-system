"""
backend/engines/supplier_agent/agent.py
Supplier Agent 工商风控与失信穿透执行器
"""
from typing import List, Dict, Any, Optional
from engines.contract.finding import RiskFindingContract, RiskLevelEnum
from engines.contract.agent_role import AgentRoleEnum
from .uscc_verifier import verify_uscc_checksum

class SupplierAgent:
    """供应商资信穿透与工商风控智能体 (确定性税号/黑名单比对 + 大模型经营范围匹配)"""

    @staticmethod
    async def run(
        supplier_name: str,
        uscc: str,
        is_dishonest: bool = False,
        operating_status: str = "存续",
        registered_capital: Optional[str] = None,
        purchase_desc: str = "",
        business_scope: Optional[str] = None,
        is_shell_company: bool = False,
        capabilities: Optional[List[str]] = None,
        profile_found: bool = True
    ) -> Any:
        """执行供应商资信风控核验"""
        findings: List[RiskFindingContract] = []

        def is_enabled(cap: str) -> bool:
            return capabilities is None or cap in capabilities

        # 1. 统一社会信用代码合规校验 (GB 32100-2015，仅依赖税号字符串自身算法)
        if is_enabled("uscc_checksum_validation") and not verify_uscc_checksum(uscc):
            findings.append(RiskFindingContract(
                rule_code="R12_SUPPLIER_UNREGISTERED",
                rule_name="统一社会信用代码校验失败",
                risk_level=RiskLevelEnum.HIGH,
                agent_role=AgentRoleEnum.SUPPLIER,
                title=f"供应商[{supplier_name}]税号/信用代码非法",
                description=f"供应商提供的统一社会信用代码[{uscc}]未通过国家标准 GB 32100-2015 校验码算法，疑似虚假企业或录入笔误。",
                actual_value={"uscc": uscc},
                expected_value={"is_valid_uscc": True},
                suggestion="请经办人核实供应商真实营业执照税号，避免向非法主体付款。",
                is_overridable=False
            ))

        # 若供应商在数据库/工商画像库中不存在，禁止默认假定“存续/非失信”，标记为 DEGRADED 降级
        if not profile_found:
            from engines.contract.finding import AgentFindingList
            return AgentFindingList(
                findings,
                is_degraded=True,
                degraded_reason="SUPPLIER_PROFILE_MISSING",
                source="DETERMINISTIC_RULE"
            )

        # 2. 失信被执行人红线穿透
        if is_enabled("dishonest_debtor_check") and is_dishonest:
            findings.append(RiskFindingContract(
                rule_code="R13_SUPPLIER_DISHONEST",
                rule_name="供应商为最高法失信被执行人 (老赖)",
                risk_level=RiskLevelEnum.HIGH,
                agent_role=AgentRoleEnum.SUPPLIER,
                title=f"供应商[{supplier_name}]被列入最高人民法院失信被执行人黑名单",
                description=f"经全国法院失信被执行人数据库检索，该供应商存在未履行的重大司法执行判决，信用风险极高，存在资金被法院司法冻结的严重风险。",
                actual_value={"supplier_name": supplier_name, "is_dishonest": True},
                expected_value={"is_dishonest": False},
                suggestion="对公付款涉及失信供应商属于一票否决红线，禁止放行，请更换合规供应商。",
                is_overridable=False
            ))

        # 3. 经营状态异常预警与空壳公司排查 (兼容注销、吊销、异常、经营异常、撤销)
        is_abnormal_status = any(kw in (operating_status or "") for kw in ["注销", "吊销", "异常", "经营异常", "撤销"])
        if is_enabled("shell_company_investigation") and (is_abnormal_status or is_shell_company):
            title = f"供应商[{supplier_name}]当前处于[{operating_status}]状态" if is_abnormal_status else f"供应商[{supplier_name}]疑似空壳公司"
            desc = (
                f"经国家企业信用信息公示系统检索，该企业当前工商登记状态为[{operating_status}]，已丧失合法经营资格，禁止向其对公划款。"
                if is_abnormal_status else
                "经企业信用大数据穿透分析，该供应商具有明显的空壳公司走账特征，禁止向其划款。"
            )
            findings.append(RiskFindingContract(
                rule_code="R15_SHELL_COMPANY",
                rule_name="供应商经营状态异常 (已注销/吊销/空壳)",
                risk_level=RiskLevelEnum.HIGH,
                agent_role=AgentRoleEnum.SUPPLIER,
                title=title,
                description=desc,
                actual_value={"operating_status": operating_status, "is_shell_company": is_shell_company},
                expected_value={"operating_status": "存续", "is_shell_company": False},
                suggestion="严禁向已注销/吊销/经营异常或空壳走账企业划拨公司资金，建议立即驳回单据。",
                is_overridable=False
            ))

        # 4. 大模型工商主营业务与采购内容偏离度判定
        source = "DETERMINISTIC_RULE"
        is_degraded = False
        degraded_reason = None

        if is_enabled("business_scope_matching") and purchase_desc:
            # 经营范围缺失时严禁使用虚构示例经营范围，跳过 R17 并标记降级
            if not business_scope or not business_scope.strip():
                is_degraded = True
                degraded_reason = "SUPPLIER_BUSINESS_SCOPE_MISSING"
                source = "HEURISTIC_RULE"
            else:
                from app.core.llm_client import LLMClient
                scope_res = await LLMClient.audit_supplier_business_scope(
                    purchase_description=purchase_desc,
                    seller_name=supplier_name,
                    business_scope=business_scope
                )
                raw_match = scope_res.get("is_match")
                is_match = LLMClient.parse_bool_safely(raw_match, default=None)
                source = scope_res.get("source", "LLM_INFERENCE")

                # 无法识别时不能直接放行，采用 fallback 规则判断
                if is_match is None:
                    source = "HEURISTIC_RULE"
                    is_match = True

                if source == "HEURISTIC_RULE":
                    is_degraded = True
                    degraded_reason = "供应商资质审核采用规则兜底降级执行"

                if not is_match:
                    findings.append(RiskFindingContract(
                        rule_code="R17_SUPPLIER_SCOPE_DEVIATION",
                        rule_name="供应商经营范围与采购业务严重偏离",
                        risk_level=RiskLevelEnum.HIGH,
                        agent_role=AgentRoleEnum.SUPPLIER,
                        title=f"供应商[{supplier_name}]主营资质与采购内容不匹配",
                        description=scope_res.get("analysis", "开票方工商登记主营范围与本次采购货物或服务明显不符，疑似空壳走账开票。"),
                        actual_value={"purchase_desc": purchase_desc, "seller_name": supplier_name, "source": source, "is_degraded": is_degraded},
                        expected_value={"is_scope_matched": True},
                        suggestion="核查该供应商是否具备承接本项目的实质交付能力与行业资质，防范虚开发票风险",
                        is_overridable=True
                    ))

        from engines.contract.finding import AgentFindingList
        return AgentFindingList(
            findings,
            is_degraded=is_degraded,
            degraded_reason=degraded_reason,
            source=source
        )
