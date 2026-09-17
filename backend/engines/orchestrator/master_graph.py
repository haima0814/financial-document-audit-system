"""
backend/engines/orchestrator/master_graph.py
多 Agent 编排流水线中枢
"""
import asyncio
import logging
from decimal import Decimal, InvalidOperation
from typing import Dict, Any, List, Optional
from datetime import datetime, timezone
from sqlalchemy import select

from app.core.database import AsyncSessionLocal
from app.repositories import document_repo, invoice_repo

from engines.contract.events import EventTypeEnum
from engines.contract.finding import RiskFindingContract, RiskLevelEnum
from engines.contract.agent_role import AgentRoleEnum

from engines.amount_agent import AmountAgent
from engines.policy_agent import PolicyAgent
from engines.supplier_agent import SupplierAgent
from engines.anomaly_agent import AnomalyAgent, InvoiceFact, SpatioPoint, TravelSegment

from .state import MasterAuditState
from .stream_producer import StreamProducer

from engines.contract.context import DocumentContext
from engines.contract.result import AuditResultDTO, AgentExecutionResult, AgentExecutionStatus
from engines.policy_agent.city_geo import get_city_geo
from .planner import AuditPlanner

logger = logging.getLogger("orchestrator.master_graph")

class MasterOrchestrator:
    @staticmethod
    def _safe_decimal(val: Any) -> Optional[Decimal]:
        """安全转换 Decimal，非法或缺失值返回 None，严禁崩溃"""
        if val is None:
            return None
        if isinstance(val, Decimal):
            return val
        s = str(val).strip()
        if not s or s.lower() in ("none", "null", "nan", "undefined"):
            return None
        try:
            return Decimal(s)
        except (InvalidOperation, TypeError, ValueError):
            return None

    @classmethod
    def _resolve_station_city(cls, raw_input: Optional[str]) -> Optional[str]:
        """优先利用现有城市库的包含匹配解析标准城市，解析失败保留原始文本，禁止暴力裁剪字符"""
        if not raw_input:
            return None
        cleaned = raw_input.strip()
        geo = get_city_geo(cleaned)
        if geo:
            return geo.short_name
        return cleaned

    @classmethod
    def _extract_travel_segments(
        cls,
        invoices: List[Dict[str, Any]],
        line_items: List[Dict[str, Any]],
        context_segments: Optional[List[Dict[str, Any]]] = None
    ) -> List[Dict[str, Any]]:
        """从票据事实与明细中提取标准化合法行程段 (严禁伪造发到时刻与乘车日期)"""
        segments: List[Dict[str, Any]] = []
        if context_segments:
            for s in context_segments:
                seg_dict = dict(s)
                seg_dict["departure_city"] = cls._resolve_station_city(seg_dict.get("departure_city"))
                seg_dict["arrival_city"] = cls._resolve_station_city(seg_dict.get("arrival_city"))
                segments.append(seg_dict)
            return segments

        for inv in invoices:
            raw_p = inv.get("raw_payload") or inv.get("raw_ocr_data") or {}
            dep_city = cls._resolve_station_city(inv.get("departure_city") or raw_p.get("departure_city"))
            arr_city = cls._resolve_station_city(inv.get("arrival_city") or raw_p.get("arrival_city"))
            dep_time = inv.get("departure_time") or raw_p.get("departure_time")
            arr_time = inv.get("arrival_time") or raw_p.get("arrival_time")
            train_no = inv.get("train_no") or inv.get("flight_no") or raw_p.get("train_no") or raw_p.get("flight_no")

            inv_type = str(inv.get("invoice_type") or "")
            if (not dep_city or not arr_city) and any(kw in inv_type for kw in ["铁路", "火车", "航空", "机票", "客票", "行程单"]):
                desc = raw_p.get("item_desc", "")
                if not desc:
                    for item in line_items:
                        item_desc = str(item.get("item_desc") or "")
                        if "-" in item_desc or "—" in item_desc or "至" in item_desc:
                            desc = item_desc
                            break
                if desc:
                    import re
                    m = re.search(r"([^\s\-—至]+)[—\-至到]([^\s\-—次]+)", desc)
                    if m:
                        dep_raw = m.group(1).strip()
                        arr_raw = m.group(2).strip()
                        dep_city = dep_city or cls._resolve_station_city(dep_raw)
                        arr_city = arr_city or cls._resolve_station_city(arr_raw)
                    m_no = re.search(r"([A-Z]\d{1,4})次?", desc)
                    if m_no:
                        train_no = train_no or m_no.group(1)

            # 严格限制：只有 OCR/raw_payload 明确存在真实出行日期字段时才写入，严禁将开票日期 issue_date 当做 travel_date
            real_travel_date = (
                inv.get("travel_date")
                or raw_p.get("travel_date")
                or inv.get("departure_date")
                or raw_p.get("departure_date")
                or inv.get("journey_date")
                or raw_p.get("journey_date")
            )
            real_travel_date_str = str(real_travel_date).strip() if real_travel_date else None

            is_travel_inv = any(kw in inv_type for kw in ["铁路", "火车", "航空", "机票", "客票", "行程单", "出租车", "打车", "网约车", "交通"])
            if dep_city or arr_city or is_travel_inv:
                segments.append({
                    "departure_city": dep_city or "",
                    "arrival_city": arr_city or "",
                    "departure_time": dep_time, # 严格保持原样，缺时间为 None，严禁猜测
                    "arrival_time": arr_time,   # 严格保持原样，缺时间为 None，严禁猜测
                    "travel_date": real_travel_date_str, # 真实乘车日期；缺失时为 None，禁止使用 issue_date
                    "transport_mode": "TRAIN" if ("铁" in inv_type or "车" in inv_type) else "FLIGHT",
                    "transport_no": train_no,
                    "attachment_id": inv.get("attachment_id", 1),
                    "invoice_number": inv.get("invoice_number", ""),
                    "source_desc": f"{train_no or '交通凭证'}: {dep_city or '未知'} -> {arr_city or '未知'}"
                })
        return segments

    @classmethod
    async def run(
        cls,
        task_id: str,
        document_id: Optional[int] = None,
        applicant_id: int = 1,
        tenant_id: int = 1,
        context: Optional[DocumentContext] = None
    ) -> AuditResultDTO:
        """
        端到端驱动四阶段审查流水线 (纯内存认知推理，严禁直接写数据库)
        """
        doc_id = context.document_id if context else (document_id or 0)
        app_id = context.applicant_id if context else applicant_id
        ten_id = context.tenant_id if context else tenant_id

        state = MasterAuditState(
            task_id=task_id,
            document_id=doc_id,
            applicant_id=app_id,
            tenant_id=ten_id
        )

        # 阶段 1: 票据事实摄入与解析 (Stage 1 Intake)
        await cls._stage1_intake(state, context=context)

        # 阶段 2: 动态扇出并行分析 (Stage 2 Dynamic Parallel Fan-out)
        await cls._stage2_parallel(state)

        # 阶段 3: 终审门禁与事实消歧 (Stage 3 Reviewer Gatekeeper)
        await cls._stage3_reviewer(state)

        # 阶段 4: 综合风控体检报告生成 (Stage 4 Pure In-Memory Report)
        result_dto = await cls._stage4_report(state)

        # 广播可靠领域事件 AuditCompletedEvent (驱动跨进程 Celery 或本地 AuditCompletionHandler 消费落库)
        from engines.contract.events import AuditCompletedEvent
        from engines.contract.event_bus import event_bus
        aud_ver = context.audit_version if (context and hasattr(context, "audit_version") and context.audit_version) else 1
        await event_bus.publish_domain_event(
            AuditCompletedEvent(
                task_id=state.task_id,
                document_id=state.document_id,
                audit_version=aud_ver,
                result=result_dto
            )
        )

        return result_dto

    @classmethod
    async def _stage1_intake(cls, state: MasterAuditState, context: Optional[DocumentContext] = None):
        """阶段 1：加载单据主子表与发票事实数据，使用标准地理库解析坐标，并生成可解释执行计划"""
        if context is not None:
            state.document_type = context.document_type
            line_items_data = [
                {
                    "line_no": item.get("line_no", idx + 1),
                    "expense_type": item.get("expense_type", ""),
                    "item_desc": item.get("item_desc", ""),
                    "amount": item.get("amount", 0),
                    "city_name": item.get("city_name"),
                    "start_date": item.get("start_date")
                }
                for idx, item in enumerate(context.line_items)
            ]

            # 空间轨迹点处理 (优先使用传入点，若为空且为差旅单则基于标准城市地理库智能解析)
            spatio_points_data = []
            if context.spatio_points:
                spatio_points_data = [
                    p if isinstance(p, dict) else {
                        "event_time": getattr(p, "event_time", None) or p.get("event_time"),
                        "city_name": getattr(p, "city_name", None) or p.get("city_name"),
                        "latitude": getattr(p, "latitude", None) or p.get("latitude"),
                        "longitude": getattr(p, "longitude", None) or p.get("longitude"),
                        "source_desc": getattr(p, "source_desc", "") or p.get("source_desc", "")
                    }
                    for p in context.spatio_points
                ]
            elif state.document_type == "TRAVEL_REIMBURSEMENT":
                for item in line_items_data:
                    c_name = item.get("city_name")
                    s_date = item.get("start_date")
                    if c_name and s_date:
                        geo = get_city_geo(c_name)
                        if geo:
                            spatio_points_data.append({
                                "event_time": str(s_date),
                                "city_name": geo.name,
                                "latitude": geo.latitude,
                                "longitude": geo.longitude,
                                "source_desc": item.get("item_desc") or ""
                            })

            travel_segments_data = cls._extract_travel_segments(
                invoices=context.invoices,
                line_items=line_items_data,
                context_segments=getattr(context, "travel_segments", None)
            )

            state.document_facts = {
                "document_no": context.document_no,
                "total_amount": context.total_amount,
                "document_type": context.document_type,
                "title": context.title,
                "department_name": context.department_name,
                "line_items": line_items_data,
                "invoices": [
                    {
                        "invoice_code": inv.get("invoice_code", ""),
                        "invoice_number": inv.get("invoice_number", ""),
                        "total_amount": inv.get("total_amount"),
                        "untaxed_amount": inv.get("untaxed_amount"),
                        "tax_amount": inv.get("tax_amount"),
                        "tax_rate": inv.get("tax_rate"),
                        "seller_tax_id": inv.get("seller_tax_id", ""),
                        "seller_name": inv.get("seller_name", ""),
                        "issue_date": inv.get("issue_date", ""),
                        "is_manual_modified": inv.get("is_manual_modified", False),
                        "original_extracted_amount": inv.get("original_extracted_amount"),
                        "invoice_type": inv.get("invoice_type", ""),
                        "departure_city": inv.get("departure_city") or (inv.get("raw_payload") or {}).get("departure_city"),
                        "arrival_city": inv.get("arrival_city") or (inv.get("raw_payload") or {}).get("arrival_city"),
                        "departure_time": inv.get("departure_time") or (inv.get("raw_payload") or {}).get("departure_time"),
                        "arrival_time": inv.get("arrival_time") or (inv.get("raw_payload") or {}).get("arrival_time"),
                        "train_no": inv.get("train_no") or (inv.get("raw_payload") or {}).get("train_no"),
                        "flight_no": inv.get("flight_no") or (inv.get("raw_payload") or {}).get("flight_no"),
                        "raw_payload": inv.get("raw_payload") or inv.get("raw_ocr_data") or {}
                    }
                    for inv in context.invoices
                ],
                "spatio_points": spatio_points_data,
                "travel_segments": travel_segments_data,
                "historical_fingerprints": (
                    context.extra_context.get("historical_fingerprints")
                    if getattr(context, "extra_context", None)
                    else None
                ),
                "supplier_profiles": (
                    context.extra_context.get("supplier_profiles")
                    if getattr(context, "extra_context", None)
                    else None
                ),
                "extra_context": getattr(context, "extra_context", {}) or {},
                "rules": getattr(context, "rules", []) or [],
                "allowance_policy_verified": (
                    getattr(context, "extra_context", {}).get("allowance_policy_verified", False)
                    if getattr(context, "extra_context", None)
                    else False
                )
            }
            items_count = len(context.line_items)
            invoices_count = len(context.invoices)
        else:
            async with AsyncSessionLocal() as db:
                doc = await document_repo.get_with_details(db, state.document_id)
                if not doc:
                    raise ValueError(f"单据[ID:{state.document_id}]不存在！")

                state.document_type = doc.document_type
                doc_invoices = await invoice_repo.list_by_document(db, state.document_id)

                line_items_data = [
                    {
                        "line_no": item.line_no,
                        "expense_type": item.expense_type,
                        "item_desc": item.item_desc,
                        "amount": item.amount,
                        "city_name": item.city_name,
                        "start_date": item.start_date
                    }
                    for item in doc.line_items
                ]

                spatio_points_data = []
                if state.document_type == "TRAVEL_REIMBURSEMENT":
                    for item in line_items_data:
                        c_name = item.get("city_name")
                        s_date = item.get("start_date")
                        if c_name and s_date:
                            geo = get_city_geo(c_name)
                            if geo:
                                spatio_points_data.append({
                                    "event_time": str(s_date),
                                    "city_name": geo.name,
                                    "latitude": geo.latitude,
                                    "longitude": geo.longitude,
                                    "source_desc": item.get("item_desc") or ""
                                })

                invoices_dicts = [
                    {
                        "invoice_code": inv.invoice_code,
                        "invoice_number": inv.invoice_number,
                        "total_amount": inv.total_amount,
                        "untaxed_amount": inv.untaxed_amount,
                        "tax_amount": inv.tax_amount,
                        "tax_rate": inv.tax_rate,
                        "seller_tax_id": inv.seller_tax_id,
                        "seller_name": inv.seller_name,
                        "issue_date": inv.issue_date,
                        "is_manual_modified": inv.is_manual_modified,
                        "original_extracted_amount": inv.original_extracted_amount,
                        "invoice_type": inv.invoice_type,
                        "departure_city": (inv.raw_payload or {}).get("departure_city"),
                        "arrival_city": (inv.raw_payload or {}).get("arrival_city"),
                        "departure_time": (inv.raw_payload or {}).get("departure_time"),
                        "arrival_time": (inv.raw_payload or {}).get("arrival_time"),
                        "train_no": (inv.raw_payload or {}).get("train_no"),
                        "flight_no": (inv.raw_payload or {}).get("flight_no"),
                        "raw_payload": inv.raw_payload or {}
                    }
                    for inv in doc_invoices
                ]
                travel_segments_data = cls._extract_travel_segments(
                    invoices=invoices_dicts,
                    line_items=line_items_data
                )

                extra_attrs = dict(doc.extra_attributes or {})
                state.document_facts = {
                    "document_no": doc.document_no,
                    "total_amount": doc.total_amount,
                    "document_type": doc.document_type,
                    "title": doc.title,
                    "department_name": doc.department_name,
                    "line_items": line_items_data,
                    "invoices": invoices_dicts,
                    "spatio_points": spatio_points_data,
                    "travel_segments": travel_segments_data,
                    "historical_fingerprints": await cls._load_historical_fps_from_db(db, state.document_id),
                    "supplier_profiles": await cls._load_supplier_profiles_from_db(db, doc_invoices),
                    "extra_context": extra_attrs,
                    "rules": [],
                    "allowance_policy_verified": bool(extra_attrs.get("allowance_policy_verified", False))
                }
                items_count = len(doc.line_items)
                invoices_count = len(doc_invoices)

        # 依据单据类型与要素探测，构建不可变执行计划 (AuditExecutionPlan)
        state.execution_plan = AuditPlanner.build_plan(
            document_id=state.document_id,
            document_type=state.document_type,
            facts=state.document_facts
        )

        await StreamProducer.publish_event(
            task_id=state.task_id,
            document_id=state.document_id,
            event_type=EventTypeEnum.TASK_STARTED,
            payload={
                "stage": "STAGE_1_PARSED",
                "items_count": items_count,
                "invoices_count": invoices_count,
                "execution_plan": state.execution_plan.model_dump(mode="json")
            }
        )

        await StreamProducer.publish_event(
            task_id=state.task_id,
            document_id=state.document_id,
            event_type=EventTypeEnum.TASK_PROGRESS,
            payload={
                "stage": "STAGE_1_PLAN_GENERATED",
                "explanation": state.execution_plan.explanation_summary,
                "planned_tasks": [t.role.value for t in state.execution_plan.tasks if t.enabled],
                "total_capabilities": state.execution_plan.total_capabilities_count,
                "percent": 25
            }
        )

    @classmethod
    async def _load_historical_fps_from_db(cls, db, document_id: int) -> Dict[str, Dict[str, Any]]:
        """从数据库装配历史发票指纹，优先基于 identity_key"""
        try:
            from app.services.audit_context_builder import AuditContextBuilder
            return await AuditContextBuilder.build_historical_fingerprints(
                db=db,
                current_document_id=document_id
            )
        except Exception as e:
            logger.warning(f"[MasterOrchestrator] 装配历史发票指纹失败，降级为空字典: {e}")
            return {}

    @classmethod
    async def _stage2_parallel(cls, state: MasterAuditState):
        """阶段 2：依据 AuditExecutionPlan 智能扇出并行执行目标 Agent 与细粒度能力"""
        facts = state.document_facts
        doc_total = cls._safe_decimal(facts.get("total_amount"))
        line_amounts = [cls._safe_decimal(item.get("amount")) for item in facts.get("line_items", [])]
        invoices_raw = facts.get("invoices", [])

        # 构建 Anomaly 需要的事实对象 (安全 Decimal 防护，非法值安全回退)
        invoice_facts = [
            InvoiceFact(
                invoice_code=str(inv.get("invoice_code") or ""),
                invoice_number=str(inv.get("invoice_number") or ""),
                total_amount=cls._safe_decimal(inv.get("total_amount")) or Decimal("0.00"),
                issue_date=str(inv.get("issue_date") or ""),
                seller_tax_id=str(inv.get("seller_tax_id") or ""),
                seller_name=str(inv.get("seller_name") or "")
            )
            for inv in invoices_raw
        ]

        # 构建时空轨迹点
        spatio_points: List[SpatioPoint] = [
            SpatioPoint(
                event_time=p["event_time"],
                city_name=p["city_name"],
                latitude=p["latitude"],
                longitude=p["longitude"],
                source_desc=p.get("source_desc", "")
            )
            for p in facts.get("spatio_points", [])
        ]

        from engines.harness.agent_harness import AgentHarness

        # 容错保障：确保计划存在
        if state.execution_plan is None:
            state.execution_plan = AuditPlanner.build_plan(
                document_id=state.document_id,
                document_type=state.document_type,
                facts=state.document_facts
            )

        coros = []
        immediate_results: List[AgentExecutionResult] = []

        for planned_task in state.execution_plan.tasks:
            role = planned_task.role
            if not planned_task.enabled:
                immediate_results.append(AgentExecutionResult(
                    role=role,
                    status=planned_task.status,
                    reason=planned_task.reason,
                    findings=[],
                    duration_ms=0,
                    capabilities_run=[]
                ))
                continue

            if role == AgentRoleEnum.AMOUNT:
                if doc_total is None:
                    # 核心金额缺失：使 Amount mandatory 任务进入 FAILED/DATA_MISSING，进而裁定 INCOMPLETE
                    immediate_results.append(AgentExecutionResult(
                        role=role,
                        status=AgentExecutionStatus.FAILED,
                        reason="DATA_MISSING: CORE_TOTAL_AMOUNT_MISSING",
                        findings=[],
                        duration_ms=0,
                        capabilities_run=[]
                    ))
                else:
                    coro = AmountAgent.run(doc_total, line_amounts, invoices_raw)
                    coros.append(AgentHarness.execute_safely(role, coro, capabilities=planned_task.capabilities))
            elif role == AgentRoleEnum.POLICY:
                coro = PolicyAgent.run(
                    document_type=state.document_type,
                    line_items=facts["line_items"],
                    document_title=facts.get("title", ""),
                    department_name=facts.get("department_name"),
                    capabilities=planned_task.capabilities
                )
                coros.append(AgentHarness.execute_safely(role, coro, capabilities=planned_task.capabilities))
            elif role == AgentRoleEnum.ANOMALY:
                historical_fps = facts.get("historical_fingerprints")
                travel_segs: List[TravelSegment] = []
                for s in facts.get("travel_segments", []):
                    dep_t = s.get("departure_time")
                    arr_t = s.get("arrival_time")
                    if isinstance(dep_t, str) and dep_t:
                        try:
                            dep_t = datetime.fromisoformat(dep_t)
                        except Exception:
                            pass
                    if isinstance(arr_t, str) and arr_t:
                        try:
                            arr_t = datetime.fromisoformat(arr_t)
                        except Exception:
                            pass
                    travel_segs.append(TravelSegment(
                        departure_city=s["departure_city"],
                        arrival_city=s["arrival_city"],
                        departure_time=dep_t if isinstance(dep_t, datetime) else None,
                        arrival_time=arr_t if isinstance(arr_t, datetime) else None,
                        travel_date=s.get("travel_date"),
                        transport_mode=s.get("transport_mode", "TRAIN"),
                        transport_no=s.get("transport_no"),
                        attachment_id=s.get("attachment_id", 1),
                        invoice_number=s.get("invoice_number", ""),
                        source_desc=s.get("source_desc", "")
                    ))

                coro = AnomalyAgent.run(
                    db=None,
                    document_id=state.document_id,
                    invoices=invoice_facts,
                    spatio_points=spatio_points,
                    travel_segments=travel_segs,
                    line_items=facts.get("line_items", []),
                    historical_fingerprints=historical_fps,
                    capabilities=planned_task.capabilities
                )
                coros.append(AgentHarness.execute_safely(role, coro, capabilities=planned_task.capabilities))
            elif role == AgentRoleEnum.SUPPLIER:
                # 从规划中提取去重供应商清单，支持多供应商并发尽调与 Semaphore 保护
                supplier_meta = planned_task.meta.get("suppliers", [])
                if not supplier_meta:
                    # 没有真实供应商主体，显式标记数据缺失跳过 (严禁伪造示例供应商！)
                    immediate_results.append(AgentExecutionResult(
                        role=role,
                        status=AgentExecutionStatus.SKIPPED,
                        reason="DATA_MISSING: SUPPLIER_IDENTITY_MISSING",
                        findings=[],
                        duration_ms=0,
                        capabilities_run=[]
                    ))
                    continue

                sem = asyncio.Semaphore(5)
                async def _execute_multi_suppliers_guarded(
                    suppliers: List[Dict[str, str]],
                    title: str,
                    capabilities: List[str],
                    supplier_profiles: Optional[Dict[str, Dict[str, Any]]] = None
                ) -> AgentExecutionResult:
                    import time
                    start_t = time.perf_counter()
                    profiles_map = supplier_profiles or {}

                    async def _single_supplier(uscc: str, name: str):
                        norm_uscc = uscc.strip().upper()
                        profile = profiles_map.get(norm_uscc)
                        async with sem:
                            try:
                                if profile:
                                    f_list = await asyncio.wait_for(
                                        SupplierAgent.run(
                                            supplier_name=profile.get("supplier_name") or name,
                                            uscc=norm_uscc,
                                            is_dishonest=profile.get("is_dishonest", False),
                                            operating_status=profile.get("operating_status", "存续"),
                                            registered_capital=profile.get("registered_capital"),
                                            purchase_desc=title,
                                            business_scope=profile.get("business_scope"),
                                            is_shell_company=profile.get("is_shell_company", False),
                                            capabilities=capabilities,
                                            profile_found=True
                                        ),
                                        timeout=AgentHarness.AGENT_POLICIES.get(AgentRoleEnum.SUPPLIER, 8.0)
                                    )
                                else:
                                    # 供应商画像缺失：不得假定为正常存续/非失信，标记为降级
                                    f_list = await asyncio.wait_for(
                                        SupplierAgent.run(
                                            supplier_name=name,
                                            uscc=norm_uscc,
                                            purchase_desc=title,
                                            capabilities=capabilities,
                                            profile_found=False
                                        ),
                                        timeout=AgentHarness.AGENT_POLICIES.get(AgentRoleEnum.SUPPLIER, 8.0)
                                    )
                                return True, f_list, None
                            except asyncio.TimeoutError:
                                logger.warning(f"[SupplierAgent] 供应商 [{name} ({uscc})] 查询超时")
                                return False, [], "TIMEOUT"
                            except Exception as e:
                                logger.warning(f"[SupplierAgent] 供应商 [{name} ({uscc})] 查询失败: {e}")
                                return False, [], str(e)

                    sub_results = await asyncio.gather(*[
                        _single_supplier(s["uscc"], s["name"]) for s in suppliers
                    ])

                    duration_ms = int((time.perf_counter() - start_t) * 1000)
                    all_findings: List[RiskFindingContract] = []
                    success_cnt = 0
                    failed_details = []
                    degraded_details = []

                    for (ok, findings, err), s in zip(sub_results, suppliers):
                        if ok:
                            success_cnt += 1
                            all_findings.extend(findings)
                            if getattr(findings, "is_degraded", False):
                                d_reason = getattr(findings, "degraded_reason", None) or "部分供应商降级执行"
                                degraded_details.append(f"{s['name']}({d_reason})")
                        else:
                            failed_details.append(f"{s['name']}({err})")

                    total_cnt = len(suppliers)
                    if total_cnt == 0:
                        status = AgentExecutionStatus.SKIPPED
                        reason = "DATA_MISSING: SUPPLIER_IDENTITY_MISSING"
                    elif success_cnt == total_cnt:
                        if degraded_details:
                            status = AgentExecutionStatus.DEGRADED
                            reason = f"全部供应商核查完成，但存在降级 ({len(degraded_details)}/{total_cnt} 降级: {', '.join(degraded_details)})"
                        else:
                            status = AgentExecutionStatus.SUCCESS
                            reason = f"全部供应商核查完成 ({success_cnt}/{total_cnt} 成功)"
                    elif success_cnt > 0:
                        status = AgentExecutionStatus.DEGRADED
                        reason = f"部分供应商核查完成 ({success_cnt}/{total_cnt} 成功，部分异常: {', '.join(failed_details)})"
                    else:
                        status = AgentExecutionStatus.FAILED
                        reason = f"所有供应商核查均失败 (0/{total_cnt}，详情: {', '.join(failed_details)})"

                    return AgentExecutionResult(
                        role=AgentRoleEnum.SUPPLIER,
                        status=status,
                        findings=all_findings,
                        reason=reason,
                        duration_ms=duration_ms,
                        capabilities_run=capabilities,
                        source="HEURISTIC_RULE" if degraded_details else "LLM_INFERENCE",
                        is_degraded=bool(degraded_details or status == AgentExecutionStatus.DEGRADED)
                    )

                coros.append(_execute_multi_suppliers_guarded(
                    suppliers=supplier_meta,
                    title=facts.get("title", ""),
                    capabilities=planned_task.capabilities,
                    supplier_profiles=facts.get("supplier_profiles")
                ))

        # 执行各 Agent 并由 AgentHarness 进行分级超时与强类型保护
        gathered_results: List[AgentExecutionResult] = []
        if coros:
            gathered_results = await asyncio.gather(*coros)

        all_results = immediate_results + list(gathered_results)
        state.agent_results = all_results

        collected_findings: List[RiskFindingContract] = []
        for r in all_results:
            if r.findings:
                collected_findings.extend(r.findings)

        state.findings = collected_findings

        # 广播各专业智能体节点的独立执行结果与耗时 (供 SSE 时间轴精确渲染)
        for r in all_results:
            source_val = r.source.value if hasattr(r.source, "value") else (str(r.source) if r.source else "UNKNOWN")
            await StreamProducer.publish_event(
                task_id=state.task_id,
                document_id=state.document_id,
                event_type=EventTypeEnum.NODE_STATUS,
                payload={
                    "role": r.role,
                    "status": r.status.value,
                    "message": f"{r.role.value} 审查完成 ({r.status.value})",
                    "duration_ms": r.duration_ms,
                    "elapsed_ms": r.duration_ms,
                    "findings_count": len(r.findings),
                    "reason": r.reason,
                    "capabilities_run": [c.value if hasattr(c, "value") else str(c) for c in (r.capabilities_run or [])],
                    "source": source_val
                }
            )

        await StreamProducer.publish_event(
            task_id=state.task_id,
            document_id=state.document_id,
            event_type=EventTypeEnum.TASK_PROGRESS,
            payload={
                "stage": "STAGE_2_PARALLEL_DONE",
                "findings_count": len(collected_findings),
                "agent_results": [
                    {"role": r.role.value, "status": r.status.value, "duration_ms": r.duration_ms}
                    for r in all_results
                ],
                "percent": 70
            }
        )

    @classmethod
    async def _stage3_reviewer(cls, state: MasterAuditState):
        """阶段 3：终审质检门禁与二阶反思消歧回路 (Stage 3 Reviewer Gatekeeper & Reflection)"""
        from .reviewer_reflector import ReviewerReflector

        # 触发多智能体二阶交叉反思与合规消歧 (Fail-Safe 兜底保护)
        try:
            verified, reflection_logs = ReviewerReflector.reflect_and_disambiguate(
                findings=state.findings,
                document_facts=state.document_facts
            )
        except Exception as e:
            logger.exception(f"[Stage 3 Reviewer] 反思回路抛出异常，触发 Fail-Safe 保留原风险项: {e}")
            verified = list(state.findings)
            reflection_logs = []

        state.verified_findings = verified
        state.disambiguation_logs = reflection_logs

        # 若触发反思消歧，广播专有 REVIEW_REFLECT 流式事件
        if reflection_logs:
            await StreamProducer.publish_event(
                task_id=state.task_id,
                document_id=state.document_id,
                event_type=EventTypeEnum.REVIEW_REFLECT,
                payload={
                    "stage": "STAGE_3_REVIEW_REFLECT",
                    "disambiguated_count": len(reflection_logs),
                    "reflection_logs": reflection_logs
                }
            )

        await StreamProducer.publish_event(
            task_id=state.task_id,
            document_id=state.document_id,
            event_type=EventTypeEnum.TASK_PROGRESS,
            payload={
                "stage": "STAGE_3_REVIEW_DONE",
                "verified_count": len(verified),
                "reflection_applied": len(reflection_logs) > 0,
                "disambiguation_logs": reflection_logs,
                "percent": 85
            }
        )

    @classmethod
    async def _stage4_report(cls, state: MasterAuditState) -> AuditResultDTO:
        """阶段 4：生成加权审计报告与高管摘要 (含审核完整度裁定与安全评分防线)"""
        high_cnt = sum(1 for f in state.verified_findings if f.risk_level.value == "high")
        med_cnt = sum(1 for f in state.verified_findings if f.risk_level.value == "medium")
        low_cnt = sum(1 for f in state.verified_findings if f.risk_level.value == "low")

        overall_lvl = "high" if high_cnt > 0 else ("medium" if med_cnt > 0 else "low")
        score = max(0, 100 - (high_cnt * 25 + med_cnt * 10 + low_cnt * 3))

        # -------------------------------------------------------------
        # 完整度裁定逻辑 (Audit Completeness Gate - 计划 vs 实际执行校验):
        # 1. 检查所有必检项 (Mandatory Tasks) 的实际执行状态：
        #    - 若 mandatory 任务未在 agent_results 中出现 (调度缺失) -> INCOMPLETE
        #    - 若 mandatory 任务存在 TIMEOUT 或 FAILED -> INCOMPLETE
        #    - 若 mandatory 任务为 SKIPPED (包括 DATA_MISSING) -> INCOMPLETE
        # 2. 检查非必检项执行状态：
        #    - 若为 NOT_APPLICABLE 正常不适用跳过，保持 COMPLETE
        #    - 若因 DATA_MISSING 缺失必要字段跳过，或状态为 DEGRADED/FAILED/TIMEOUT -> DEGRADED
        # 3. 唯有所有必检项 SUCCESS 且无数据缺失与降级时，完整度才为 COMPLETE
        # -------------------------------------------------------------
        failed_mandatory: List[str] = []
        degraded_reasons: List[str] = []

        mandatory_roles = set()
        if state.execution_plan:
            for task in state.execution_plan.tasks:
                if task.mandatory:
                    mandatory_roles.add(task.role)

        # 校验 1：计划中的 mandatory 任务必须有对应的 AgentExecutionResult
        actual_results_by_role = {res.role: res for res in state.agent_results}
        for m_role in mandatory_roles:
            if m_role not in actual_results_by_role:
                failed_mandatory.append(f"{m_role.value}(NO_EXECUTION_RESULT)")

        # 校验 2：遍历实际执行结果判定完整度
        for res in state.agent_results:
            is_mandatory = res.role in mandatory_roles
            if res.status == AgentExecutionStatus.SKIPPED:
                if is_mandatory:
                    # mandatory 任务被跳过（包括因 DATA_MISSING 跳过）均属于关键审核盲区
                    failed_mandatory.append(f"{res.role.value}({res.reason or 'SKIPPED'})")
                else:
                    # 非 mandatory 正常不适用跳过不影响完整度
                    if res.reason and "NOT_APPLICABLE" in res.reason:
                        continue
                    # 非 mandatory 缺失数据跳过 -> DEGRADED
                    degraded_reasons.append(f"{res.role.value}({res.reason or 'DATA_MISSING'})")
            elif res.status in [AgentExecutionStatus.TIMEOUT, AgentExecutionStatus.FAILED]:
                if is_mandatory:
                    failed_mandatory.append(f"{res.role.value}({res.status.value})")
                else:
                    degraded_reasons.append(f"{res.role.value}({res.status.value})")
            elif res.status == AgentExecutionStatus.DEGRADED:
                degraded_reasons.append(f"{res.role.value}({res.reason or 'DEGRADED'})")

        if failed_mandatory:
            audit_completeness = "INCOMPLETE"
        elif degraded_reasons:
            audit_completeness = "DEGRADED"
        else:
            audit_completeness = "COMPLETE"

        # 解耦风险分与完整度：risk_score 只反映已发现的业务风险，不因超时/失败强压至 60 分
        state.audit_completeness = audit_completeness
        state.overall_risk_level = overall_lvl
        state.final_score = score
        state.risk_score = score
        state.completed_at = datetime.now(timezone.utc)

        # 构造高管摘要 (确定性安全模板)
        if audit_completeness == "INCOMPLETE":
            fallback_summary = (
                f"⚠️ 智能风控审查未完全完成（存在关键审核盲区）：智能体 [{', '.join(failed_mandatory)}] "
                f"执行超时或发生异常，必要核验未闭环。当前已核验项目业务风险评分为 {score} 分，因审核完整度为 INCOMPLETE，"
                f"系统要求转入人工重点复审，禁止自动放行。"
            )
        elif audit_completeness == "DEGRADED":
            if state.verified_findings:
                reasons_summary = "；".join([
                    f"【{f.title or f.rule_name}】{f.description[:50] + '...' if len(f.description) > 50 else f.description}" +
                    (f" (偏差金额: ¥{float(f.discrepancy_amount):.2f})" if f.discrepancy_amount else "")
                    for f in state.verified_findings[:3]
                ])
                fallback_summary = (
                    f"智能风控审查部分降级完成：当前业务风险评分为 {score} 分（检出高危 {high_cnt} 项，中危 {med_cnt} 项，低危 {low_cnt} 项）。"
                    f"存在部分核验降级或要素缺失 ({', '.join(degraded_reasons)})。检出违规项：{reasons_summary}。"
                )
            else:
                fallback_summary = (
                    f"智能风控审查部分降级完成：当前业务风险评分为 {score} 分。核验中存在部分核验降级或要素缺失 ({', '.join(degraded_reasons)})，"
                    f"在已核验范围内未命中违规。因审核完整度为 DEGRADED，系统保留提示，建议人工确认。"
                )
        elif state.verified_findings:
            reasons_summary = "；".join([
                f"【{f.title or f.rule_name}】{f.description[:50] + '...' if len(f.description) > 50 else f.description}" +
                (f" (偏差金额: ¥{float(f.discrepancy_amount):.2f})" if f.discrepancy_amount else "")
                for f in state.verified_findings[:3]
            ])
            fallback_summary = f"智能风控审查完毕：审核完整度为 COMPLETE，综合风险评分为 {score} 分（检出高危 {high_cnt} 项，中危 {med_cnt} 项，低危 {low_cnt} 项）。核心违规原因：{reasons_summary}。"
        else:
            fallback_summary = "智能风控审查完毕：审核完整度为 COMPLETE，综合风险评分为 100 分。经多智能体联合核查，各项数据与发票匹配一致，全规则合规放行，未命中任何违规项。"

        # 保护最终安全文案防线：
        # 1. audit_completeness != COMPLETE (即 INCOMPLETE 或 DEGRADED) 时禁止 LLM 覆盖 fallback_summary
        # 2. INCOMPLETE / DEGRADED 必须使用确定性模板，防止大模型幻觉稀释风险提示
        # 3. LLM 只能润色 COMPLETE 场景，且绝不能改变完整度门禁
        if audit_completeness != "COMPLETE":
            state.summary = fallback_summary
        else:
            if state.verified_findings:
                from app.core.llm_client import LLMClient
                if LLMClient.is_configured():
                    doc_no = state.document_facts.get("document_no") or f"DOC-{state.document_id}"
                    total_amt = float(state.document_facts.get("total_amount", 0.0))
                    findings_summary = [
                        {"rule_code": f.rule_code, "title": f.title, "description": f.description}
                        for f in state.verified_findings
                    ]
                    exec_summary = await LLMClient.generate_executive_summary(
                        document_no=doc_no,
                        total_amount=total_amt,
                        findings_summary=findings_summary,
                        fallback_summary=fallback_summary
                    )
                    state.summary = exec_summary
                else:
                    state.summary = fallback_summary
            else:
                state.summary = fallback_summary

        await StreamProducer.publish_event(
            task_id=state.task_id,
            document_id=state.document_id,
            event_type=EventTypeEnum.TASK_PROGRESS,
            payload={
                "stage": "STAGE_4_EVALUATED",
                "overall_risk_level": overall_lvl,
                "risk_score": score,
                "audit_completeness": audit_completeness,
                "high_count": high_cnt,
                "medium_count": med_cnt,
                "low_count": low_cnt,
                "percent": 95
            }
        )

        return state.to_audit_result()

    @classmethod
    async def _load_historical_fps_from_db(cls, db: Any, doc_id: int) -> Dict[str, Any]:
        from app.services.audit_context_builder import AuditContextBuilder
        return await AuditContextBuilder.build_historical_fingerprints(db, doc_id)

    @classmethod
    async def _load_supplier_profiles_from_db(cls, db: Any, doc_invoices: List[Any]) -> Dict[str, Dict[str, Any]]:
        from app.services.audit_context_builder import AuditContextBuilder
        inv_data = [{"seller_tax_id": getattr(inv, "seller_tax_id", "")} for inv in doc_invoices]
        return await AuditContextBuilder.build_supplier_profiles(db, inv_data)
