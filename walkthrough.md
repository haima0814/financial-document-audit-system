# 单据与审核任务双层提交幂等与实时流式精度加固完成报告

## 一、 优化与修复全景

针对演示和高并发场景下出现的“单据重复创建”、“同一单据点击提交生成多个 task_id”、“SSE 进度估算不准”、“抽屉误触关闭”、“节点来源伪造”等稳定性问题，本次实施了**双层提交幂等机制**与**流式协议精度校准**：

1. **单据创建层 (Layer 1 - Document Creation)**：
   - 前端表单添加 `@submit.prevent` 阻止原生刷新提交。
   - “保存并立即提交审查”按钮绑定 `:loading="submitting" :disabled="submitting"`。
   - 新增前端函数级防重互斥 `if (submitting.value) return`。
   - 在 `FinancialDocument` 模型与 Schema 中新增 `idempotency_key` 唯一索引字段；后端遇到重复 `idempotency_key` 时自动复用已创建单据，DB 唯一约束兜底。

2. **审核任务提交层 (Layer 2 - AnalysisTask Submission)**：
   - 移除单一依赖 SQLite 伪行锁的做法，在 `AnalysisTask` 模型中新增并持久化 `audit_version`，并添加数据库级联合唯一索引：`UNIQUE(document_id, audit_version)`。
   - `submit_document` 与 `start_audit` 在并发 INSERT 冲突时捕获 `IntegrityError`，自动查询并返回已有 `task_id`，返回体明确携带 `reused: true/false` 与 `audit_version`。
   - 驳回/需补正后重新提交时，计算目标版本 `target_version = doc.current_version + 1`，生成全新 `task_id`，并在库中完整保留各历史版本对应的 AnalysisTask 与 ReviewReport。

3. **SSE 实时流式与前端组件加固**：
   - `AuditStreamDrawer.vue` 移除 `node_status` 中的自增进度伪估算，严格由后端 `task_progress` / `task_completed` 的 `percent` 驱动（25% → 70% → 85% → 95% → 100%）。
   - 时间线按 `payload.stage` 精准映射各阶段语义：`STAGE_1_PLAN_GENERATED`、`STAGE_2_PARALLEL_DONE`、`STAGE_3_REVIEW_DONE`、`STAGE_4_EVALUATED`。
   - `MasterOrchestrator` 在 Stage 2 真实透传各 Agent 的 `source`，前端在来源缺失时默认展示 `UNKNOWN`，严禁伪造 `DETERMINISTIC`。
   - 抽屉新增 `:close-on-click-modal="false"` 与 `:close-on-press-escape="isCompleted || isFailed"`，在审查中关闭时弹窗确认，提示用户收起抽屉不终止后台审查。
   - `review_reflect` 移除对不存在字段 `reflection_applied` / `verified_count` 的读取，改由真实字段 `disambiguated_count > 0` 判定是否发生消歧，展示“自动核减/修正 N 项”；最终有效风险项由 `STAGE_3_REVIEW_DONE.verified_count` 展示。
   - `STAGE_1_PLAN_GENERATED` 移除对不存在字段 `tasks_count` 的读取，改用 `payload.planned_tasks.length` 并展示 `payload.total_capabilities`。
   - 抽屉跳转及终审报告页面强绑定 `task_id` query 参数，精准调用 `/api/v1/audits/reports/by-task/{task_id}` 获取当前次审核报告。

---

## 二、 核心修改文件明细

### 后端核心
- **`backend/app/models/audit.py`**:
  - `AnalysisTask` 增加 `audit_version: Mapped[int] = mapped_column(Integer, default=1, nullable=False, index=True)`。
  - 增加 `__table_args__ = (UniqueConstraint("document_id", "audit_version", name="uq_task_document_audit_version"),)`。
- **`backend/app/models/document.py`**:
  - `FinancialDocument` 增加 `idempotency_key: Mapped[Optional[str]] = mapped_column(String(64), unique=True, nullable=True, index=True)`。
- **`backend/app/schemas/document.py`**:
  - `FinancialDocumentCreateReq` 与 `FinancialDocumentListItemOut` 增加 `idempotency_key: Optional[str]`。
- **`backend/app/services/document_service.py`**:
  - `create_document`: 增加 `idempotency_key` 检查与 `IntegrityError` 兜底。
  - `submit_document`: 依据单据当前状态精确计算 `target_version`（驳回重提递增），基于 `(document_id, target_version)` 进行防重校验与并发锁控制，返回 `reused`、`audit_version`、`task_id`。
- **`backend/app/services/audit_service.py`**:
  - `start_audit`: 增加 `(document_id, audit_version)` 查询前置拦截与 `IntegrityError` 并发冲突回退复用机制。
  - `handle_audit_completed`: `TASK_COMPLETED` 事件中显式附带 `task_id` 与 `percent: 100`。
- **`backend/engines/contract/events.py`**:
  - `NodeStatusPayload` 增加 `source: Optional[str] = "UNKNOWN"`。
  - `TaskCompletedPayload` 增加 `task_id` 与 `percent`。
  - `TaskProgressPayload` 增加 `stage` 与 `current_stage` 双向兼容性 validator。
  - 保持 `BaseEventEnvelope` 原生字段与时间戳强类型不变。
- **`backend/engines/orchestrator/master_graph.py`**:
  - `_stage2_parallel` 广播 `NODE_STATUS` 时透传真实 `source`。
  - 多供应商合并结果显式赋予 `source` 与 `is_degraded`。

### 前端页面与组件
- **`frontend/src/views/DocumentCreate.vue`**:
  - 表单增加 `@submit.prevent`，按钮增加 `:loading="submitting" :disabled="submitting"`。
  - 新增 `submitting` 互斥保护与 `idempotencyKey`。
  - 提交成功后 console 打印 `document_id + audit_version + task_id + reused`。
  - 跳转终审报告附带 `task_id`。
- **`frontend/src/views/DocumentDetail.vue`**:
  - 增加 `submitting` 状态与按钮 `:loading/:disabled`。
  - `handleSubmit` 增加互斥防重与完整错误提示。
- **`frontend/src/views/DocumentList.vue`**:
  - 增加 `submittingId` 响应式状态，提交按钮动态绑定 `:loading="submittingId === row.id" :disabled="submittingId !== null"`。
- **`frontend/src/components/AuditStreamDrawer.vue`**:
  - 属性增加 `:close-on-click-modal="false"` 与 `:close-on-press-escape="isCompleted || isFailed"`。
  - `handleClose` 针对运行中任务提示“收起抽屉不终止后台审查”。
  - 进度条完全由 `payload.percent` 驱动，移除前端自增猜测。
  - 时间线支持 4 大阶段精确语义与 `source || 'UNKNOWN'` 真实呈现。
  - `review_reflect` 移除对不存在字段 `reflection_applied` / `verified_count` 的读取，改由真实字段 `disambiguated_count > 0` 判定是否发生消歧，展示“自动核减/修正 N 项”；最终有效风险项由 `STAGE_3_REVIEW_DONE.verified_count` 展示。
  - `STAGE_1_PLAN_GENERATED` 移除对不存在字段 `tasks_count` 的读取，改用 `payload.planned_tasks.length` 并展示 `payload.total_capabilities`。
  - `goToReport` 携带 `?task_id=xxx` 路由参数。
- **`frontend/src/views/AuditReportView.vue`**:
  - `fetchReport` 优先读取 `route.query.task_id` 并请求 `/audits/reports/by-task/${task_id}`。
  - 协同明细缺失来源时降级为 `UNKNOWN`，杜绝伪造 `DETERMINISTIC`。

---

## 三、 自动化测试与验证

### 1. 提交幂等与版本升级专项测试 (`tests/app/test_submission_idempotency.py`)
- ✅ `test_document_creation_idempotency_key`: 同一 `idempotency_key` 重复调用仅创建 1 张单据。
- ✅ `test_document_submit_sequential_idempotency`: 同一单据同一版本顺序调用，返回相同 `task_id` 且 `reused=True`。
- ✅ `test_document_submit_concurrent_gather`: `asyncio.gather` 5 路高并发提交，保证仅生成 1 个 `AnalysisTask`，所有请求返回相同 `task_id`。
- ✅ `test_resubmit_after_rejection_increments_version`: 单据驳回后重新提交，版本升级至 2，生成新 `task_id`，库中保留 V1 与 V2 两个任务。

### 2. SSE 协议与沙箱门禁专项测试 (`tests/engines/test_harness_and_sse.py`)
- ✅ `test_sse_review_reflect_and_plan_payloads`: 验证 `ReviewReflectPayload`（`disambiguated_count` 与 `reflection_logs`）与 `TaskProgressPayload`（`planned_tasks`、`total_capabilities` 与 `percent`）契约对齐。
- ✅ 8 项沙箱门禁与 SSE 流式测试全部通过 (`8 passed in 1.41s`)。

### 3. 全量测试与构建
- **后端全量测试套件**：`144 passed (100% 通过)`
- **数据库种子数据预置**：`python scripts/seed_data.py` (退出码 0)
- **前端生产构建**：`npm run build` (退出码 0，Vite 打包成功)
