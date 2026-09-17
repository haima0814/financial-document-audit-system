"""
backend/tests/engines/test_anomaly_agent.py
anomaly_agent 反欺诈、发票查重、时空碰撞与连号拆单测试
"""
import pytest
from decimal import Decimal
from datetime import datetime
from engines.anomaly_agent import (
    AnomalyAgent,
    InvoiceFact,
    SpatioPoint,
    SpatioTemporalVerifier,
    SequentialDetector,
    TravelSegment,
    TravelSegmentVerifier
)
from engines.contract.finding import RiskLevelEnum

@pytest.mark.asyncio
async def test_anomaly_single_doc_duplicate_invoice():
    """测试单据内重复上传发票：触发 R08"""
    inv1 = InvoiceFact(
        invoice_code="0110023",
        invoice_number="88889999",
        total_amount=Decimal("500.00"),
        issue_date="2026-09-01",
        seller_tax_id="91110000MA0001"
    )
    inv2 = InvoiceFact(
        invoice_code="0110023",
        invoice_number="88889999", # 完全相同
        total_amount=Decimal("500.00"),
        issue_date="2026-09-01",
        seller_tax_id="91110000MA0001"
    )

    findings = await AnomalyAgent.run(db=None, document_id=1, invoices=[inv1, inv2])
    assert len(findings) == 1
    assert findings[0].rule_code == "R08_INVOICE_DUPLICATE"
    assert findings[0].risk_level == RiskLevelEnum.HIGH

def test_spatio_temporal_collision():
    """测试时空超光速碰撞：09:00 北京，10:00 上海 (相距约 1068 km，间隔 1h，时速超千公里)"""
    p1 = SpatioPoint(
        event_time=datetime(2026, 9, 10, 9, 0),
        city_name="北京",
        latitude=39.9042,
        longitude=116.4074,
        source_desc="北京全聚德餐饮",
        invoice_number="INV_BJ_01"
    )
    p2 = SpatioPoint(
        event_time=datetime(2026, 9, 10, 10, 0),
        city_name="上海",
        latitude=31.2304,
        longitude=121.4737,
        source_desc="上海出租车",
        invoice_number="INV_SH_01"
    )

    findings = SpatioTemporalVerifier.detect_spatio_temporal_collisions([p1, p2])
    assert len(findings) == 1
    assert findings[0].rule_code == "R09_SPATIO_TEMPORAL_COLLISION"
    assert findings[0].risk_level == RiskLevelEnum.HIGH

def test_spatio_temporal_normal_movement():
    """测试正常同城位移：09:00 朝阳，11:00 海淀 (20km，2h，时速 10 km/h，正常)"""
    p1 = SpatioPoint(
        event_time=datetime(2026, 9, 10, 9, 0),
        city_name="北京朝阳",
        latitude=39.9219,
        longitude=116.4430,
        source_desc="朝阳咖啡",
        invoice_number="INV_01"
    )
    p2 = SpatioPoint(
        event_time=datetime(2026, 9, 10, 11, 0),
        city_name="北京海淀",
        latitude=39.9599,
        longitude=116.2981,
        source_desc="海淀午餐",
        invoice_number="INV_02"
    )

    findings = SpatioTemporalVerifier.detect_spatio_temporal_collisions([p1, p2])
    assert len(findings) == 0

def test_sequential_invoices_detection():
    """测试同商户连号发票拆单套现：连续3张发票，合计 9000 元 > 3000 元门槛"""
    invoices = [
        InvoiceFact(invoice_code="011", invoice_number="No.10001", total_amount=Decimal("3000.00"), issue_date="2026-09-01", seller_tax_id="TAX999", seller_name="宏达酒楼"),
        InvoiceFact(invoice_code="011", invoice_number="No.10002", total_amount=Decimal("3000.00"), issue_date="2026-09-01", seller_tax_id="TAX999", seller_name="宏达酒楼"),
        InvoiceFact(invoice_code="011", invoice_number="No.10003", total_amount=Decimal("3000.00"), issue_date="2026-09-01", seller_tax_id="TAX999", seller_name="宏达酒楼"),
    ]

    findings = SequentialDetector.detect_sequential_invoices(invoices)
    assert len(findings) == 1
    assert findings[0].rule_code == "R10_SEQUENTIAL_INVOICES"
    assert findings[0].discrepancy_amount == Decimal("9000.00")


@pytest.mark.asyncio
async def test_historical_invoice_duplicate_triggers_r08():
    """测试历史发票重复报销跨单触发 R08"""
    inv = InvoiceFact(
        invoice_code="0110024",
        invoice_number="99990001",
        total_amount=Decimal("1200.00"),
        issue_date="2026-09-01",
        seller_tax_id="TAX_HIST_01"
    )
    historical_mock = {
        "0110024#99990001": {
            "document_no": "EXP-2026-PAID-088",
            "status": "APPROVED",
            "total_amount": 1200.00
        }
    }

    findings = await AnomalyAgent.run(
        invoices=[inv],
        historical_fingerprints=historical_mock
    )
    assert len(findings) == 1
    assert findings[0].rule_code == "R08_INVOICE_DUPLICATE"
    assert findings[0].risk_level == RiskLevelEnum.HIGH
    assert "EXP-2026-PAID-088" in findings[0].description
    assert findings[0].is_overridable is False


@pytest.mark.asyncio
async def test_duplicate_invoice_detected_even_if_amount_modified():
    """测试同票号但金额被篡改修改，仍能准确发现重复 (单内 & 跨单)"""
    # 1. 单内重复提交，但第2张金额被篡改
    inv1 = InvoiceFact(
        invoice_code="0110088",
        invoice_number="66668888",
        total_amount=Decimal("500.00"),
        issue_date="2026-09-01",
        seller_tax_id="TAX_A"
    )
    inv2 = InvoiceFact(
        invoice_code="0110088",
        invoice_number="66668888",  # 相同票号
        total_amount=Decimal("600.00"),  # 金额被篡改修改
        issue_date="2026-09-01",
        seller_tax_id="TAX_A"
    )
    findings_single = await AnomalyAgent.run(invoices=[inv1, inv2])
    assert len(findings_single) == 1
    assert findings_single[0].rule_code == "R08_INVOICE_DUPLICATE"
    assert findings_single[0].actual_value.get("is_amount_modified") is True
    assert "修改" in findings_single[0].description

    # 2. 跨单历史比对，当前申报金额与历史金额不一致
    inv_cross = InvoiceFact(
        invoice_code="0110099",
        invoice_number="77779999",
        total_amount=Decimal("800.00"),
        issue_date="2026-09-01",
        seller_tax_id="TAX_B"
    )
    historical_mock = {
        "0110099#77779999": {
            "document_no": "EXP-HIST-002",
            "status": "PAID",
            "total_amount": 500.00
        }
    }
    findings_cross = await AnomalyAgent.run(
        invoices=[inv_cross],
        historical_fingerprints=historical_mock
    )
    assert len(findings_cross) == 1
    assert findings_cross[0].rule_code == "R08_INVOICE_DUPLICATE"
    assert findings_cross[0].actual_value.get("is_amount_modified") is True

    # 3. 缺少发票代码或发票号码时，跳过查重，不生成不可靠指纹
    inv_missing = InvoiceFact(
        invoice_code="",
        invoice_number="",
        total_amount=Decimal("200.00"),
        issue_date="2026-09-01"
    )
    findings_missing = await AnomalyAgent.run(invoices=[inv_missing, inv_missing])
    assert len(findings_missing) == 0


def test_sequential_invoices_two_or_low_amount_no_trigger():
    """测试防误报：2 张连号不触发，3 张但金额未达到 split_amount_threshold 不触发"""
    # 场景 A: 仅 2 张连号，即使金额大也不触发
    invoices_2 = [
        InvoiceFact(invoice_code="011", invoice_number="No.1001", total_amount=Decimal("5000.00"), seller_tax_id="TAX_SEQ"),
        InvoiceFact(invoice_code="011", invoice_number="No.1002", total_amount=Decimal("5000.00"), seller_tax_id="TAX_SEQ")
    ]
    f_2 = SequentialDetector.detect_sequential_invoices(invoices_2)
    assert len(f_2) == 0

    # 场景 B: 3 张连号，但累计金额 2400 < 3000 默认阈值，不触发
    invoices_low = [
        InvoiceFact(invoice_code="011", invoice_number="No.2001", total_amount=Decimal("800.00"), seller_tax_id="TAX_SEQ"),
        InvoiceFact(invoice_code="011", invoice_number="No.2002", total_amount=Decimal("800.00"), seller_tax_id="TAX_SEQ"),
        InvoiceFact(invoice_code="011", invoice_number="No.2003", total_amount=Decimal("800.00"), seller_tax_id="TAX_SEQ")
    ]
    f_low = SequentialDetector.detect_sequential_invoices(invoices_low, split_amount_threshold=Decimal("3000.00"))
    assert len(f_low) == 0


def test_sequential_invoices_three_over_threshold_triggers_r10():
    """测试 3 张连号且金额达到阈值触发 R10，客观文案且区分 MEDIUM/HIGH"""
    # 场景 A: 3 张连号，累计 3500 >= 3000 阈值，触发 MEDIUM
    invoices_3 = [
        InvoiceFact(invoice_code="011", invoice_number="No.3001", total_amount=Decimal("1000.00"), seller_tax_id="TAX_M", seller_name="品尚酒楼"),
        InvoiceFact(invoice_code="011", invoice_number="No.3002", total_amount=Decimal("1200.00"), seller_tax_id="TAX_M", seller_name="品尚酒楼"),
        InvoiceFact(invoice_code="011", invoice_number="No.3003", total_amount=Decimal("1300.00"), seller_tax_id="TAX_M", seller_name="品尚酒楼")
    ]
    f_3 = SequentialDetector.detect_sequential_invoices(invoices_3, split_amount_threshold=Decimal("3000.00"))
    assert len(f_3) == 1
    assert f_3[0].risk_level == RiskLevelEnum.MEDIUM
    assert "存在拆单风险特征" in f_3[0].title
    assert "套现" not in f_3[0].description
    assert f_3[0].discrepancy_amount == Decimal("3500.00")

    # 场景 B: 4 张连号，累计金额 >= 10000 触发 HIGH
    invoices_4 = [
        InvoiceFact(invoice_code="011", invoice_number="No.4001", total_amount=Decimal("3000.00"), seller_tax_id="TAX_H"),
        InvoiceFact(invoice_code="011", invoice_number="No.4002", total_amount=Decimal("3000.00"), seller_tax_id="TAX_H"),
        InvoiceFact(invoice_code="011", invoice_number="No.4003", total_amount=Decimal("3000.00"), seller_tax_id="TAX_H"),
        InvoiceFact(invoice_code="011", invoice_number="No.4004", total_amount=Decimal("3000.00"), seller_tax_id="TAX_H")
    ]
    f_4 = SequentialDetector.detect_sequential_invoices(invoices_4)
    assert len(f_4) == 1
    assert f_4[0].risk_level == RiskLevelEnum.HIGH


@pytest.mark.asyncio
async def test_capabilities_control_rules_individually():
    """测试 capabilities 能分别独立控制开启与关闭 R08/R09/R10"""
    # 构造可同时满足 R08, R09, R10 的测试数据
    invoices = [
        InvoiceFact(invoice_code="011", invoice_number="No.8001", total_amount=Decimal("2000.00"), seller_tax_id="TAX_ALL"),
        InvoiceFact(invoice_code="011", invoice_number="No.8002", total_amount=Decimal("2000.00"), seller_tax_id="TAX_ALL"),
        InvoiceFact(invoice_code="011", invoice_number="No.8003", total_amount=Decimal("2000.00"), seller_tax_id="TAX_ALL"),
        InvoiceFact(invoice_code="011", invoice_number="No.8001", total_amount=Decimal("2000.00"), seller_tax_id="TAX_ALL"), # 重复
    ]
    points = [
        SpatioPoint(event_time=datetime(2026, 9, 10, 9, 0), city_name="北京", latitude=39.9, longitude=116.4, source_desc="点1"),
        SpatioPoint(event_time=datetime(2026, 9, 10, 9, 30), city_name="上海", latitude=31.2, longitude=121.4, source_desc="点2")
    ]

    # 1. 仅开启 R08
    res_r08 = await AnomalyAgent.run(invoices=invoices, spatio_points=points, capabilities=["duplicate_invoice_hash_check"])
    codes_r08 = {f.rule_code for f in res_r08}
    assert codes_r08 == {"R08_INVOICE_DUPLICATE"}

    # 2. 仅开启 R09
    res_r09 = await AnomalyAgent.run(invoices=invoices, spatio_points=points, capabilities=["spatio_temporal_trajectory_conflict"])
    codes_r09 = {f.rule_code for f in res_r09}
    assert codes_r09 == {"R09_SPATIO_TEMPORAL_COLLISION"}

    # 3. 仅开启 R10
    res_r10 = await AnomalyAgent.run(invoices=invoices, spatio_points=points, capabilities=["sequential_invoice_number_check"])
    codes_r10 = {f.rule_code for f in res_r10}
    assert codes_r10 == {"R10_SEQUENTIAL_INVOICES"}

    # 4. 全部关闭
    res_none = await AnomalyAgent.run(invoices=invoices, spatio_points=points, capabilities=[])
    assert len(res_none) == 0

    # 5. capabilities=None 保持全量向后兼容
    res_all = await AnomalyAgent.run(invoices=invoices, spatio_points=points, capabilities=None)
    codes_all = {f.rule_code for f in res_all}
    assert "R08_INVOICE_DUPLICATE" in codes_all
    assert "R09_SPATIO_TEMPORAL_COLLISION" in codes_all
    assert "R10_SEQUENTIAL_INVOICES" in codes_all


def test_simultaneous_remote_events_without_fake_speed():
    """测试同一时间异地事件不通过 0.001h 伪造速度，并优化文案"""
    p1 = SpatioPoint(
        event_time=datetime(2026, 9, 10, 10, 0),
        city_name="北京",
        latitude=39.9042,
        longitude=116.4074,
        source_desc="北京午餐",
        invoice_number="INV_A"
    )
    p2 = SpatioPoint(
        event_time=datetime(2026, 9, 10, 10, 0),  # 完全相同时间
        city_name="上海",
        latitude=31.2304,
        longitude=121.4737,
        source_desc="上海打车",
        invoice_number="INV_B"
    )

    findings = SpatioTemporalVerifier.detect_spatio_temporal_collisions([p1, p2])
    assert len(findings) == 1
    f = findings[0]
    assert f.rule_code == "R09_SPATIO_TEMPORAL_COLLISION"
    assert "同一时间" in f.title
    assert f.actual_value.get("is_simultaneous") is True
    # 严禁出现数百万公里的伪造时速
    assert f.actual_value.get("speed_kmh") is None
    # 验证删除了夸张和无据表述
    assert "严重违背物理规律" not in f.description
    assert "缺乏民航机票佐证" not in f.description
    assert "短时间跨城市高速位移异常，需要核实交通凭证和真实行程" in f.description


@pytest.mark.asyncio
async def test_cross_check_degraded_when_history_unavailable():
    """测试规划了跨单查重但历史数据不可用时，标记为 DEGRADED"""
    inv = InvoiceFact(
        invoice_code="011",
        invoice_number="12345",
        total_amount=Decimal("100.00"),
        issue_date="2026-09-01"
    )
    findings = await AnomalyAgent.run(
        invoices=[inv],
        capabilities=["cross_document_duplicate_check"],
        historical_fingerprints=None
    )
    assert findings.is_degraded is True
    assert "历史发票数据源未就绪" in (findings.degraded_reason or "")


@pytest.mark.asyncio
async def test_historical_fingerprints_production_chain_with_tampered_amount():
    """
    生产链路集成测试：
    1. 从历史原始发票记录通过 AuditContextBuilder.generate_fingerprints_from_records 生成指纹字典；
    2. 验证字典 Key 严格为 normalize(invoice_code) + '#' + normalize(invoice_number)，而非旧 SHA256；
    3. 验证字典 Value 完整保留 total_amount 和 issue_date；
    4. 当前单据提交同票号但金额被篡改的发票；
    5. 通过 AnomalyAgent.run() 核验，必须触发 R08 发票跨单重复。
    """
    from app.services.audit_context_builder import AuditContextBuilder

    # 1. 模拟历史已审批/已报销单据下的原始发票记录
    historical_raw_records = [
        {
            "invoice_code": "011002200888",
            "invoice_number": "88990011",
            "total_amount": Decimal("1500.00"),
            "issue_date": "2026-08-15",
            "seller_tax_id": "91110108MA999999",
            "seller_name": "北京国贸大厦酒店",
            "document_no": "EXP-2026-PAID-019",
            "status": "APPROVED"
        }
    ]

    # 2. 从历史原始数据生成生产标准指纹字典
    hist_fps = AuditContextBuilder.generate_fingerprints_from_records(historical_raw_records)

    # 验证 Key 必须与 HashVerifier 保持一致的 identity_key (code#number)，绝非旧 SHA256
    expected_id_key = "011002200888#88990011"
    assert expected_id_key in hist_fps
    assert len(expected_id_key) < 64
    assert "#" in expected_id_key

    # 验证 Value 中保留了 amount / issue_date / document_no
    assert hist_fps[expected_id_key]["total_amount"] == 1500.00
    assert hist_fps[expected_id_key]["issue_date"] == "2026-08-15"
    assert hist_fps[expected_id_key]["document_no"] == "EXP-2026-PAID-019"

    # 3. 当前单据中提交相同发票代码与号码，但金额被篡改为 2000.00
    current_inv = InvoiceFact(
        invoice_code=" 011002200888 ",  # 带前后空格测试 normalize
        invoice_number=" 88990011 ",
        total_amount=Decimal("2000.00"),  # 金额不同 (1500 vs 2000)
        issue_date="2026-08-15",
        seller_tax_id="91110108MA999999",
        seller_name="北京国贸大厦酒店"
    )

    # 4. 执行 AnomalyAgent.run()
    findings = await AnomalyAgent.run(
        invoices=[current_inv],
        historical_fingerprints=hist_fps
    )

    # 5. 断言必须触发 R08 跨单发票重复报销
    assert len(findings) == 1
    f = findings[0]
    assert f.rule_code == "R08_INVOICE_DUPLICATE"
    assert f.risk_level == RiskLevelEnum.HIGH
    assert f.is_overridable is False
    assert "EXP-2026-PAID-019" in f.description
    # 验证金额篡改被准确识别
    assert f.actual_value.get("is_amount_modified") is True
    assert f.actual_value.get("historical_amount") == 1500.00
    assert f.actual_value.get("current_amount") == 2000.00


def test_scenario_a_single_train_ticket_missing_time_partial():
    """
    场景 A：单张火车票 (北京-上海，G13，无精确发到时刻)
    - 严禁伪造时间 (departure_time=None, arrival_time=None)
    - 允许执行行程路线与申报明细比对
    - 不会触发误报时空碰撞
    """
    seg = TravelSegment(
        departure_city="北京",
        arrival_city="上海",
        departure_time=None,
        arrival_time=None,
        transport_mode="TRAIN",
        transport_no="G13",
        source_desc="G13: 北京 -> 上海"
    )
    line_items = [
        {"city_name": "上海", "item_desc": "上海客户拜访", "amount": 500.0}
    ]

    findings = TravelSegmentVerifier.verify_travel_consistency(
        segments=[seg],
        line_items=line_items,
        spatio_points=[]
    )
    # 目的地与申报城市匹配，无碰撞，无任何违规检出
    assert len(findings) == 0


def test_scenario_b_high_speed_rail_normal_transit_no_false_collision():
    """
    场景 B：真实发到时刻的高铁行程 (08:00 北京 - 12:30 上海)
    - 物理位移合法移动路径，绝不误判为超光速碰撞
    """
    seg = TravelSegment(
        departure_city="北京",
        arrival_city="上海",
        departure_time=datetime(2026, 9, 10, 8, 0),
        arrival_time=datetime(2026, 9, 10, 12, 30),
        transport_mode="TRAIN",
        transport_no="G13",
        source_desc="G13: 北京 -> 上海"
    )
    line_items = [
        {"city_name": "上海", "item_desc": "上海差旅住宿", "amount": 400.0, "start_date": "2026-09-10"}
    ]

    findings = TravelSegmentVerifier.verify_travel_consistency(
        segments=[seg],
        line_items=line_items,
        spatio_points=[]
    )
    assert len(findings) == 0


def test_scenario_c_simultaneous_transit_remote_consumption():
    """
    场景 C：交通行程运行期间在异地城市发生消费记录
    - 车票：北京 -> 上海 09:00 至 13:30
    - 离散事件点：广州 11:00 餐饮消费
    - 触发 R09_SPATIO_TEMPORAL_COLLISION
    """
    seg = TravelSegment(
        departure_city="北京",
        arrival_city="上海",
        departure_time=datetime(2026, 9, 10, 9, 0),
        arrival_time=datetime(2026, 9, 10, 13, 30),
        transport_mode="TRAIN",
        transport_no="G1",
        source_desc="G1: 北京 -> 上海"
    )
    remote_point = SpatioPoint(
        event_time=datetime(2026, 9, 10, 11, 0),
        city_name="广州",
        latitude=23.1291,
        longitude=113.2644,
        source_desc="广州酒家午餐",
        invoice_number="INV_GZ_888"
    )

    findings = TravelSegmentVerifier.verify_travel_consistency(
        segments=[seg],
        line_items=[{"city_name": "上海", "amount": 500.0}],
        spatio_points=[remote_point]
    )
    assert len(findings) == 1
    assert findings[0].rule_code == "R09_SPATIO_TEMPORAL_COLLISION"
    assert "广州" in findings[0].title
    assert "INV_GZ_888" in findings[0].description


def test_scenario_destination_mismatch_detection():
    """
    交通行程目的地与单据申报城市脱节
    - 车票：北京 -> 成都
    - 申报单据：仅有上海
    - 触发 R09_TRAVEL_DESTINATION_MISMATCH
    """
    seg = TravelSegment(
        departure_city="北京",
        arrival_city="成都",
        transport_mode="FLIGHT",
        transport_no="CA4102",
        source_desc="CA4102: 北京 -> 成都"
    )
    line_items = [
        {"city_name": "上海", "item_desc": "上海项目实施", "amount": 2000.0}
    ]

    findings = TravelSegmentVerifier.verify_travel_consistency(
        segments=[seg],
        line_items=line_items,
        spatio_points=[]
    )
    assert len(findings) == 1
    assert findings[0].rule_code == "R09_TRAVEL_DESTINATION_MISMATCH"
    assert "成都" in findings[0].title



