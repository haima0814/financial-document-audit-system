# 财务单据智能风险审核系统

> **Intelligent Financial Document Risk Audit System**
>
> 一套"**确定性规则引擎 + 多智能体大模型推理**"双引擎驱动的企业财务合规与风控审计平台。
> 核心设计哲学：**算归算，想归想**（Deterministic Computation + LLM Reasoning），
> 以及 **Fail-Safe 兜底降级门禁**（宁可转人工，绝不误放行）。

<p>
  <img alt="Python" src="https://img.shields.io/badge/Python-3.11%2B-3776AB?logo=python&logoColor=white">
  <img alt="FastAPI" src="https://img.shields.io/badge/FastAPI-0.115%2B-009688?logo=fastapi&logoColor=white">
  <img alt="Vue" src="https://img.shields.io/badge/Vue-3.5-4FC08D?logo=vuedotjs&logoColor=white">
  <img alt="Vite" src="https://img.shields.io/badge/Vite-6.0-646CFF?logo=vite&logoColor=white">
  <img alt="Tests" src="https://img.shields.io/badge/pytest-21%20files%20%7C%20174%20cases-0A9EDC?logo=pytest&logoColor=white">
</p>

---

## 一、项目简介

### 1.1 它解决什么问题

企业财务报销与对公付款场景中，传统人工审核面临三个痛点：

| 痛点 | 具体表现 |
| :--- | :--- |
| **审得慢** | 财务人员需逐张核对发票、明细、制度标准，小额单据占用大量人力 |
| **审不准** | 金额勾稽靠肉眼加总，尾差、跨单重复报销、连号发票拆单难以发现 |
| **审不透** | 供应商失信状态、空壳公司、行程时空冲突等外部风险无法在单据层面穿透 |

本项目把审核链路拆成"**机器能算的清、模型能想的清**"两部分：

- **能算的**（金额勾稽、税额核验、税号校验、发票查重、行程冲突）→ 交给 **纯代码确定性规则**，毫厘不差、结果可复现；
- **该想的**（报销事由是否合理、供应商经营范围与采购是否匹配）→ 交给 **大模型语义推理**；
- **拿不准的** → 交给 **审批决策门禁**，一律转人工，绝不自动放行。

### 1.2 三条不可妥协的工程铁律

1. **算归算**：所有金额使用 Python `Decimal` 高精度运算，严禁 float，严禁让大模型做算术。
2. **想归想**：大模型只负责语义判断（合理性、匹配度），且**必须**在解析失败时退化为本地启发式规则，不允许"解析不出来就视为合规"。
3. **Fail-Safe**：任何环节异常、超时、要素缺失，都只能让审核结论**变保守**（转人工/驳回），绝不允许变宽松。

### 1.3 核心能力一览

- ✅ **多智能体并行审查**：金额精算、制度合规、供应商工商风控、时空反欺诈四类专家智能体按单据要素**动态扇出**
- ✅ **五方交叉核算**：单据抬头额 ↔ 明细累加 ↔ 发票合计 ↔ 单票价税勾稽 ↔ 手工修改标记
- ✅ **发票跨单防重**：基于发票代码/号码/金额/日期的指纹哈希，穿透历史全量单据查重
- ✅ **差旅时空反欺诈**：行程起终点一致性、行程日期与住宿日期一致性、在途区间碰撞、离散轨迹点时空冲突
- ✅ **工商穿透**：统一社会信用代码 GB 32100-2015 校验、失信被执行人红线、空壳/注销/吊销排查
- ✅ **二阶反思消歧**：终审阶段交叉比对差旅包干免票津贴，消除"免票津贴导致的不平账"假阳性误报
- ✅ **审核完整度门禁**：`COMPLETE / DEGRADED / INCOMPLETE` 三态裁定，非 COMPLETE 一律禁止自动放行
- ✅ **全链路证据链**：五维不可变证据（视觉/精算/制度/外部/行为）+ 原图 BBox 归一化坐标高亮
- ✅ **SSE 实时流式看板**：审查过程 9 类事件实时推流，前端时间轴逐节点呈现
- ✅ **审核 Copilot**：基于审查报告与证据链的流式智能问答（SSE）
- ✅ **审批工作流状态机**：同意/驳回/转交/前置加签/后置加签/撤回 六种动作，含行锁并发防护
- ✅ **双模数据中心**：本地 SQLite 零依赖秒起 / 生产 PostgreSQL + Redis + Celery 分布式解耦

---

## 二、技术栈

### 2.1 后端

| 分类 | 技术选型 | 说明 |
| :--- | :--- | :--- |
| Web 框架 | **FastAPI** ≥ 0.115 | 原生 async、自动 OpenAPI 文档 |
| ASGI 服务器 | **Uvicorn** ≥ 0.30 (standard) | 含 websockets / httptools |
| ORM | **SQLAlchemy 2.0** (asyncio) | 全异步 Session，`AsyncSessionLocal` |
| 数据校验 | **Pydantic v2** + pydantic-settings | 强类型领域契约 |
| 数据库 | **SQLite 3 (WAL)** / **PostgreSQL 15+** | 通过 `DATABASE_URL` 切换 |
| 驱动 | `aiosqlite` / `asyncpg` | 异步驱动 |
| 迁移 | **Alembic** | 依赖已声明 |
| 缓存/消息 | **Redis 7** / **Celery 5.4** | `production` 模式下启用 |
| 大模型接入 | **OpenAI 兼容协议**（DeepSeek / Qwen / Kimi / OpenAI） | 经 `httpx` 调用，含语义缓存 |
| 认证 | `pyjwt` + `passlib[bcrypt]` + `python-jose` | JWT Bearer Token |
| OCR | **RapidOCR (ONNX Runtime)** | 纯 CPU 可跑，无需 GPU |
| PDF 解析 | **PyMuPDF (fitz)** 优先 → `pypdf` 兜底 | 矢量文字直取 BBox，扫描件光栅化后走 OCR |
| 测试 | **pytest** + `pytest-asyncio`（`asyncio_mode = auto`） | 21 个测试文件 / 174 个用例 |
| 实时通道 | **SSE**（`text/event-stream`）+ **WebSocket** | 过程推流双通道 |

### 2.2 前端

| 分类 | 技术选型 |
| :--- | :--- |
| 框架 | **Vue 3.5**（Composition API / `<script setup>`） |
| 构建 | **Vite 6** |
| 状态管理 | **Pinia 2** |
| UI 组件库 | **Element Plus 2.9** + `@element-plus/icons-vue` |
| 路由 | **vue-router 4**（含登录守卫） |
| HTTP | **axios**（请求注入 JWT / 401 自动登出） |
| 富文本渲染 | **marked** + **DOMPurify**（防 XSS） |
| 发票原件查看 | 自研双模画布（位图 + SVG 覆盖层，BBox 坐标高亮） |

### 2.3 关于"多智能体编排"的准确说明

本项目的多智能体编排中枢（`engines/orchestrator/master_graph.py`）是一套**自研的 asyncio 协程编排引擎**，
并非直接依赖 LangGraph 运行时。其设计**借鉴了 LangGraph 的状态图范式**：

- `engines/contract/master_state.py` 定义了 `TypedDict` 主图状态契约，全局账本使用
  `Annotated[List[...], operator.add]` 声明式归约器，保证并行子图合流安全；
- `engines/orchestrator/planner.py` 承担"路由"职责，按单据类型与要素探测决定激活哪些智能体；
- `engines/orchestrator/reviewer_reflector.py` 承担"反思回路"职责（对应 Reflection 模式）。

因此它是**LangGraph 思想的落地实现**，可平滑迁移到 LangGraph 运行时，但当前版本不引入该依赖。

---

## 三、系统架构

### 3.1 四阶段审查流水线

```
                     ┌──────────────────────────────────────────────┐
                     │         Stage 1 · 事实摄入与规划              │
   单据提交 ────────► │  AuditContextBuilder 装配不可变内存快照       │
                     │  AuditPlanner 依据单据类型 + 要素探测生成计划  │
                     └──────────────────┬───────────────────────────┘
                                        │  Execution Plan (可审计)
                                        ▼
                     ┌──────────────────────────────────────────────┐
                     │         Stage 2 · 动态扇出并行审查            │
                     │  asyncio.gather 并发驱动，AgentHarness 管控   │
                     │  ┌────────┬────────┬─────────┬────────────┐ │
                     │  │Amount  │Policy  │Supplier │ Anomaly    │ │
                     │  │金额精算 │制度合规 │工商风控  │ 时空反欺诈 │ │
                     │  └────────┴────────┴─────────┴────────────┘ │
                     └──────────────────┬───────────────────────────┘
                                        │  Findings + Evidence
                                        ▼
                     ┌──────────────────────────────────────────────┐
                     │         Stage 3 · 终审门禁与反思消歧          │
                     │  ReviewerReflector 二阶交叉反思，消解假阳性   │
                     │  （免票包干津贴 ↔ 发票差额 平账核减）          │
                     └──────────────────┬───────────────────────────┘
                                        │  Verified Findings
                                        ▼
                     ┌──────────────────────────────────────────────┐
                     │         Stage 4 · 综合体检报告与完整度裁定     │
                     │  加权评分 + COMPLETE/DEGRADED/INCOMPLETE 门禁 │
                     └──────────────────┬───────────────────────────┘
                                        │  AuditResultDTO
                                        ▼
                     ┌──────────────────────────────────────────────┐
                     │   领域事件 AuditCompletedEvent（可靠投递）     │
                     │   AuditCompletionHandler → 落库 + 启动审批流  │
                     └──────────────────────────────────────────────┘
```

### 3.2 审批决策门禁（四阶裁决，优先级从高到低）

| 优先级 | 判定条件 | 裁决动作 | 单据目标状态 |
| :---: | :--- | :--- | :--- |
| **0** | 存在**不可覆盖**（`is_overridable=False`）的 HIGH 风险 | **`REJECT`** 一票否决 | `REJECTED` |
| **1** | 命中 `R14_MISSING_INVOICE`（关键发票凭证缺失） | **`NEED_SUPPLEMENT`** 打回补件 | `NEED_SUPPLEMENT` |
| **2** | 审核完整度 ≠ `COMPLETE`（即 `INCOMPLETE` 或 `DEGRADED`） | **`MANUAL_REVIEW`** 强制人工 | `PENDING_APPROVAL` |
| **3** | 综合评级 `HIGH`（属可人工覆盖范畴） | **`MANUAL_REVIEW`** 三级终审 | `PENDING_APPROVAL` |
| **4** | 审核完整度 `COMPLETE` **且** `LOW` **且** 金额 ≤ ¥500 | **`AUTO_APPROVE`** 免审放行 | `APPROVED` |
| **5** | 其他中风险 / 大额（≥ ¥10,000 追加 CFO 节点） | **`MANUAL_REVIEW`** 多级人工 | `PENDING_APPROVAL` |

> ⚠️ **注意**：`COMPLETE` 只代表"审核没有盲区"，**不等于放行依据**。只有同时满足"小额 + 低危"才触发免审。

### 3.3 单据状态机

```
DRAFT ──提交──► PENDING_APPROVAL ──全部节点通过──► APPROVED
  │                    │
  │                    ├──任一节点驳回──────────► REJECTED ──修改后重提──► PENDING_APPROVAL
  │                    ├──经办人撤回────────────► CANCELLED
  │                    │
  └──提交（命中一票否决）──► REJECTED
      提交（缺少发票原件）──► NEED_SUPPLEMENT ──补充材料──► PENDING_APPROVAL
```

### 3.4 审核完整度三态裁定

| 状态 | 触发条件 | 后果 |
| :--- | :--- | :--- |
| **`COMPLETE`** | 所有 mandatory 任务 `SUCCESS`，无非适用性以外的降级 | 允许按风险等级正常流转 |
| **`DEGRADED`** | 非必检项 `DEGRADED`/`TIMEOUT`/`FAILED`，或关键要素缺失导致核验降级 | **禁止自动放行**，转人工重点复核 |
| **`INCOMPLETE`** | mandatory 任务被 `SKIPPED`（含 `DATA_MISSING`）、`TIMEOUT`、`FAILED`，或未产生执行结果 | **禁止自动放行**，转人工重点复审 |

---

## 四、项目结构

```text
财务单据智能风险审核系统/
├── backend/                              # 后端服务 (FastAPI + SQLAlchemy 2.0 Async)
│   ├── app/
│   │   ├── api/v1/                       # RESTful + 实时通道路由
│   │   │   ├── auth.py                   #   POST /auth/login、GET /auth/me
│   │   │   ├── documents.py              #   单据 CRUD、发票上传 OCR、提交审查、撤回
│   │   │   ├── audits.py                 #   体检报告、任务状态、审核 Copilot 问答
│   │   │   ├── approvals.py              #   待办列表、审批动作、流转轨迹
│   │   │   ├── dashboard.py              #   风控全景大盘指标
│   │   │   ├── sse.py                    #   SSE 实时事件流（断线重连）
│   │   │   ├── ws.py                     #   WebSocket 实时推流
│   │   │   └── router.py                 #   v1 路由聚合
│   │   ├── core/
│   │   │   ├── config.py                 #   Pydantic Settings 全局配置
│   │   │   ├── database.py               #   异步引擎、CompatibleJSONB、SQLite WAL PRAGMA
│   │   │   └── llm_client.py             #   LLM 客户端 + 语义缓存 + 安全 JSON/布尔解析
│   │   ├── models/                       # SQLAlchemy ORM 实体（24 张表）
│   │   ├── repositories/                 # 数据访问层（仓储模式）
│   │   ├── schemas/                      # Pydantic 请求/响应契约
│   │   └── services/                     # 应用服务层
│   │       ├── audit_service.py          #   审查任务生命周期 + 报告落库 + Copilot
│   │       ├── audit_context_builder.py  #   不可变上下文快照装配（引擎与 DB 物理隔离）
│   │       ├── audit_handler.py          #   AuditCompletedEvent 领域事件消费者
│   │       ├── approval_engine.py        #   审批决策门禁 + 工作流状态机驱动
│   │       ├── document_service.py       #   单据生命周期与版本快照
│   │       ├── dashboard_service.py      #   大盘聚合指标
│   │       ├── auth_service.py           #   密码哈希 / JWT 签发与校验
│   │       └── ocr_service.py            #   发票 OCR 版面解析（1782 行，最重模块）
│   ├── engines/                          # ★ 认知推理引擎（与 DB 完全隔离）
│   │   ├── contract/                     #   领域契约：状态、证据、发现项、事件协议
│   │   │   ├── master_state.py           #     主图状态契约（声明式归约器）
│   │   │   ├── evidence.py               #     五维不可变证据模型 + 归一化 BBox
│   │   │   ├── finding.py                #     RiskFindingContract + AgentFindingList
│   │   │   ├── result.py                 #     AuditResultDTO / CapabilityExecutionResult
│   │   │   ├── events.py                 #     9 类推流事件 + 领域事件
│   │   │   ├── event_bus.py              #     六边形架构事件总线端口与适配器
│   │   │   ├── context.py                #     AuditExecutionContext 不可变入参
│   │   │   ├── agent_role.py             #     8 类智能体角色元数据注册表
│   │   │   └── settings.py               #     超时、阈值、熔断配置
│   │   ├── amount_agent/                 #   金额确定性精算（Decimal 五方交叉核算）
│   │   ├── policy_agent/                 #   制度合规（城市限额比对 + LLM 事由合理性）
│   │   ├── supplier_agent/               #   工商风控（USCC 校验 + 失信 + 空壳 + 经营范围）
│   │   ├── anomaly_agent/                #   时空反欺诈（查重/连号/行程/轨迹碰撞）
│   │   ├── harness/agent_harness.py      #   智能体安全执行外壳（分级超时 + 异常兜底）
│   │   └── orchestrator/                 #   编排中枢
│   │       ├── master_graph.py           #     MasterOrchestrator 四阶段流水线
│   │       ├── planner.py                #     AuditPlanner 动态规划与能力裁剪
│   │       ├── reviewer_reflector.py     #     ReviewerReflector 二阶反思消歧
│   │       ├── state.py                  #     MasterAuditState 运行时状态
│   │       ├── stream_producer.py        #     流式事件生产者
│   │       └── dispatcher/               #     LocalTaskManager / Celery 双模派发
│   ├── scripts/
│   │   ├── seed_data.py                  #   预置 5 张演示单据 + 全量用户/工作流
│   │   └── replay_demos.py               #   4 大场景端到端回放与断言校验
│   ├── tests/                            # 21 个测试文件 / 174 个用例
│   │   ├── app/                          #   服务层与 API 集成测试
│   │   ├── engines/                      #   引擎与契约单元测试
│   │   └── services/                     #   OCR 服务专项测试（16 例）
│   ├── uploads/invoices/                 # 发票原件存储目录（内容已被 gitignore）
│   ├── main.py                           # 应用入口（lifespan 初始化 + 路由挂载）
│   ├── requirements.txt                  # Python 依赖清单
│   ├── pytest.ini                        # pytest 配置（asyncio_mode=auto）
│   └── .env.example                      # 环境变量模板
├── frontend/                             # 前端工程 (Vue 3 + Vite + Element Plus)
│   ├── src/
│   │   ├── views/
│   │   │   ├── Login.vue                 #   登录页
│   │   │   ├── Layout.vue                #   主框架（侧边栏 + 角色化菜单）
│   │   │   ├── Dashboard.vue             #   风控全景大盘
│   │   │   ├── DocumentList.vue          #   单据列表（按状态筛选）
│   │   │   ├── DocumentCreate.vue        #   单据创建 + 发票上传 + OCR 结果确认
│   │   │   ├── DocumentDetail.vue        #   单据详情 + 历史版本快照溯源
│   │   │   ├── AuditReportView.vue       #   ★ 智能风控体检报告工作台
│   │   │   └── ApprovalCenter.vue        #   审批中心（同意/驳回/转交/加签/撤回）
│   │   ├── components/
│   │   │   ├── AuditStreamDrawer.vue     #   SSE 实时时间线抽屉
│   │   │   ├── InvoiceCanvasViewer.vue   #   发票原件双模画布（BBox 高亮聚焦）
│   │   │   └── AuditChatCopilot.vue      #   AI 审核助手对话面板
│   │   ├── api/index.js                  #   axios 实例（注入 JWT / 401 登出）
│   │   ├── stores/auth.js                #   Pinia 认证状态
│   │   ├── router/index.js               #   路由与登录守卫
│   │   └── utils/auditFormatters.js      #   报告字段中文映射与格式化
│   ├── package.json
│   └── vite.config.js                    #   dev 端口 5174 + API/SSE/uploads 代理
├── specs/                                # 7 份模块级工程规格说明书
├── .github/workflows/ci.yml              # CI：后端 pytest + 回放校验 + 前端构建
├── PRD-v0.1.md                           # 产品需求说明书
├── 概要设计.md                            # 概要架构设计与 ADR
├── 数据实体设计.md                        # 数据表结构与实体关系
├── implementation_plan.md                # 实施计划
├── walkthrough.md                        # 验收走查记录
├── 整体流程图.png                         # 全流程架构图
└── 一键上传到GitHub.bat                   # 打包上传辅助脚本（需自行确认密钥已脱敏）
```

### 4.1 数据模型（24 张表，按域划分）

| 领域 | 表名 |
| :--- | :--- |
| **用户权限** | `users`、`roles`、`user_roles` |
| **单据** | `financial_documents`、`document_line_items`、`document_attachments`、`document_versions`、`document_status_logs` |
| **票据** | `attachment_parse_results`、`invoice_records` |
| **制度** | `policy_units`、`policy_chunks` |
| **供应商** | `supplier_profiles`、`market_price_references` |
| **审批流** | `approval_workflows`、`approval_workflow_nodes`、`approval_instances`、`approval_tasks`、`workflow_status_logs` |
| **审核** | `analysis_tasks`、`review_reports`、`risk_findings`、`audit_chat_sessions`、`audit_chat_messages`、`processed_events` |

---

## 五、风险规则清单

### 5.1 金额确定性规则（AmountAgent · `DETERMINISTIC_RULE`）

| 规则码 | 名称 | 等级 | 可否人工覆盖 |
| :--- | :--- | :---: | :---: |
| `R01_HEADER_LINE_MISMATCH` | 单据总额与明细行累加不符 | HIGH | ❌ 不可覆盖 |
| `R02_INVOICE_SUM_MISMATCH` | 发票价税合计与申报总额不符 | HIGH | ❌ 不可覆盖 |
| `R05_TAX_AMOUNT_MISMATCH` | 单张发票"不含税 + 税额 ≠ 总额"（公差 0.01 元） | MEDIUM | ✅ 可覆盖 |
| `R07_MANUAL_OVERRIDE_FLAG` | 经办人手工修正 OCR 识别值 | MEDIUM | ✅ 可覆盖 |

### 5.2 制度合规规则（PolicyAgent）

| 规则码 | 名称 | 等级 | 可否人工覆盖 |
| :--- | :--- | :---: | :---: |
| `R05_POLICY_EXCEEDED` | 差旅住宿费超标（按城市分级限额 × 住宿间夜数折算单晚均价）；超标率 ≥ 50% 时**升级为 HIGH** | MEDIUM / HIGH | ✅ 可覆盖 |
| `R16_BUSINESS_PURPOSE_MISMATCH` | 报销事由与消费明细偏离（公款私用嫌疑） | HIGH | ✅ 可覆盖 |
| `R14_POLICY_NOT_FOUND` | 城市缺失或未收录制度标准 → 柔性降级转人工 | LOW | ✅ 可覆盖 |

> 📐 **差旅住宿双门禁**：优先按"总金额 ÷ 住宿间夜数 = 单晚均价"与城市标准比对（更公平）；
> 未提供晚数时退化为单笔金额比对。城市未收录时**绝不假设为最低档城市**制造假阳性，而是降级为 `R14_POLICY_NOT_FOUND` 转人工。

### 5.3 供应商工商风控规则（SupplierAgent）

| 规则码 | 名称 | 等级 | 可否人工覆盖 |
| :--- | :--- | :---: | :---: |
| `R12_SUPPLIER_UNREGISTERED` | 统一社会信用代码未通过 GB 32100-2015 校验 | HIGH | ❌ 不可覆盖 |
| `R13_SUPPLIER_DISHONEST` | 供应商为最高法失信被执行人 | HIGH | ❌ 不可覆盖 |
| `R15_SHELL_COMPANY` | 供应商注销 / 吊销 / 经营异常 / 空壳走账 | HIGH | ❌ 不可覆盖 |
| `R17_SUPPLIER_SCOPE_DEVIATION` | 经营范围与采购业务严重偏离（虚开发票嫌疑） | HIGH | ✅ 可覆盖 |

### 5.4 时空与行为反欺诈规则（AnomalyAgent）

| 规则码 | 名称 | 等级 | 可否人工覆盖 |
| :--- | :--- | :---: | :---: |
| `R08_INVOICE_DUPLICATE` | 发票重复报销（**单内重复 + 跨单历史全局红线**） | HIGH | ❌ 不可覆盖 |
| `R09_SPATIO_TEMPORAL_COLLISION` | 离散轨迹点时空物理冲突（同一时刻出现在两地） | HIGH | ✅ 可覆盖 |
| `R09_TRAVEL_DESTINATION_MISMATCH` | 交通票据起终点与申报城市不符 | HIGH | ✅ 可覆盖 |
| `R09_TRAVEL_HOTEL_DATE_CONFLICT` | 行程日期与住宿日期冲突 | HIGH | ✅ 可覆盖 |
| `R09_TRAVEL_SEGMENT_OVERLAP` | 多段行程时间区间重叠 | HIGH | ✅ 可覆盖 |
| `R10_SEQUENTIAL_INVOICES` | 同商户连号发票拆单风险（≥3 张连号**且**累计金额达阈值才触发；累计 ≥ ¥10,000 或 ≥ 4 张升为 HIGH） | MEDIUM / HIGH | ✅ 可覆盖 |

### 5.5 流程类规则

| 规则码 | 名称 | 等级 | 实现状态 |
| :--- | :--- | :---: | :--- |
| `R14_MISSING_INVOICE` | 关键发票凭证缺失或无法辨识 → 打回补件 | HIGH | 审批门禁已实现**消费判定**（优先级 1），等待上游发票校验能力产出该编码 |
| `R16_AGENT_DEGRADED` | 智能体降级运行（审计留痕） | — | 仅在设计规格（`specs/07`）中定义；当前版本的降级事实由 `AgentExecutionResult.status = DEGRADED` + `is_degraded` 承载，并由审核完整度裁定消费 |

### 5.6 评分与等级

```python
综合评分 = max(0, 100 - (HIGH × 25 + MEDIUM × 10 + LOW × 3))

综合等级 = "high"   if HIGH > 0
         = "medium" if MEDIUM > 0
         = "low"    otherwise
```

> `risk_score` 只反映**已发现的业务风险**，不会因为某智能体超时就把分数强压到 60 分——
> 因超时导致的"审核盲区"由 **审核完整度**（`DEGRADED`/`INCOMPLETE`）单独承载，
> 两者解耦，避免风险信号被稀释。

---

## 六、环境配置与运行步骤

### 6.1 前置要求

| 组件 | 版本要求 | 说明 |
| :--- | :--- | :--- |
| **Python** | 3.11+（推荐 3.11 / 3.12） | 使用到 `Decimal`、`asyncio`、Pydantic v2 |
| **Node.js** | 18+（推荐 20 LTS） | Vite 6 要求 |
| **npm** | 9+ | 随 Node.js 安装 |
| Redis / PostgreSQL | 可选 | 仅 `RUN_MODE=production` 时需要 |

> ✅ **零依赖起步**：默认 `RUN_MODE=local` + SQLite，**无需安装任何数据库或中间件**，克隆即可运行。

### 6.2 第一步 · 后端启动

```powershell
# 1. 进入后端目录
cd backend

# 2. 创建虚拟环境（如已存在 backend/.venv 可跳过）
python -m venv .venv

# 3. 激活虚拟环境 (Windows PowerShell)
.venv\Scripts\Activate.ps1
#   macOS / Linux 请使用： source .venv/bin/activate

# 4. 安装依赖
pip install -r requirements.txt

# 5. 生成环境变量文件
Copy-Item .env.example .env
#   macOS / Linux： cp .env.example .env

# 6. 启动开发服务器（默认端口 8001，reload 已内置）
python main.py
```

启动成功后可见：

- 健康检查：<http://127.0.0.1:8001/health>
- Swagger 交互文档：<http://127.0.0.1:8001/docs>
- ReDoc 文档：<http://127.0.0.1:8001/redoc>
- 发票原件静态服务：`http://127.0.0.1:8001/uploads/...`

也可用 Uvicorn 显式启动（端口需与前端代理一致）：

```powershell
uvicorn main:app --reload --host 0.0.0.0 --port 8001
```

### 6.3 第二步 · 前端启动

```powershell
# 新开一个终端
cd frontend

# 1. 安装依赖
npm install

# 2. 启动 Vite 开发服务器（端口 5174）
npm run dev
```

- 前端访问地址：<http://localhost:5174>
- Vite 已配置代理，前端 `/api`、`/ws`、`/uploads` 请求会自动转发到 `http://127.0.0.1:8001`，
  **因此后端必须运行在 8001 端口**（如需改端口，请同步修改 `frontend/vite.config.js` 的 proxy target）。

生产构建：

```powershell
npm run build      # 产物输出到 frontend/dist
npm run preview    # 本地预览构建产物
```

### 6.4 第三步 · 关键环境变量说明

`backend/.env` 中需要关注的配置项：

| 变量 | 默认值 | 说明 |
| :--- | :--- | :--- |
| `RUN_MODE` | `local` | `local` = SQLite + asyncio 任务管理器；`production` = PostgreSQL + Redis + Celery |
| `DATABASE_URL` | `sqlite+aiosqlite:///./financial_audit.db` | 切 PostgreSQL 示例：`postgresql+asyncpg://user:pwd@host:5432/financial_audit` |
| `REDIS_URL` | `redis://localhost:6379/0` | 仅生产模式使用 |
| `SECRET_KEY` | 开发默认值 | **生产环境必须替换** |
| `ACCESS_TOKEN_EXPIRE_MINUTES` | `1440` | JWT 有效期（分钟） |
| `CORS_ORIGINS` | `["*"]` | 生产环境应收窄为具体域名 |
| `OPENAI_API_KEY` | `mock-key` | 保持 `mock-key` → **自动启用离线降级模式**，全流程 100% 可用 |
| `OPENAI_BASE_URL` | `https://api.deepseek.com/v1` | 任意 OpenAI 兼容端点 |
| `DEFAULT_LLM_MODEL` | `deepseek-chat` | 模型名 |

> 🔌 **离线可用性说明**：当 `OPENAI_API_KEY` 为 `mock-key`（或网络不可达）时，
> 系统会自动把"事由合理性判断""经营范围匹配""高管摘要生成"降级为**本地启发式规则 + 确定性模板**，
> 并把这些智能体标记为 `DEGRADED` → 触发人工复核门禁。**降级不报错、不崩溃、不放行。**

### 6.5 第四步 · 预置演示数据与全链路回放

```powershell
cd backend

# 1. 预置演示用户、工作流与 5 张场景单据（会重建 financial_audit.db）
python scripts/seed_data.py

# 2. 执行 4 大场景端到端回放与断言校验
python scripts/replay_demos.py
```

预期输出结尾：

```
🏆 全部 3 套核心场景 + 1 套反思消歧场景回放与断言 100% 通过！
```

### 6.6 第五步 · 运行自动化测试

```powershell
cd backend
pytest -v
```

当前测试规模：**21 个测试文件 / 174 个用例**，覆盖领域契约、四大智能体、
编排器、反思消歧、审批状态机、仓储层、OCR、RBAC、幂等与恢复等。

```powershell
pytest -v tests/engines/          # 只跑引擎层单元测试
pytest -v tests/app/              # 只跑服务层与 API 集成测试
pytest -v tests/services/         # 只跑 OCR 服务测试
```

---

## 七、演示账号与场景

### 7.1 内置账号（统一密码 `123456`）

| 用户名 | 姓名 | 角色 | 典型视角 |
| :--- | :--- | :--- | :--- |
| `emp` | 小赵 | `EMPLOYEE` | 经办人：创建/提交单据、查看自己的报告 |
| `manager` | 张经理 | `MANAGER` | 部门主管：一级审批 |
| `finance` | 李财务 | `FINANCE` | 财务专员：复核票据与报告 |
| `cfo` | 王总监 | `CFO` | 财务总监：大额/高危终审、特批 |
| `admin` | 系统管理员 | `ADMIN` | 全局运维监控视图 |

### 7.2 四大演示场景

| 场景 | 单据编号 | 金额 | 完整度 / 风险 | 门禁裁决 | 观察点 |
| :--- | :--- | ---: | :--- | :---: | :--- |
| **A · 正常小额** | `EXP-20260312-PASS01` | ¥320.00 | `COMPLETE` / `LOW` | **`AUTO_APPROVE`** | 系统生成 `AUTO_PASSED` 免审任务，单据直置 `APPROVED` |
| **B · 跨单重复发票** | `EXP-20260313-VETO02` | ¥850.00 | `COMPLETE` / `HIGH`（R08 不可覆盖） | **`REJECT`** | 一票否决优先于完整度，**不创建任何人工待办** |
| **C · 核验降级** | `EXP-20260314-DEGR003` | ¥2400.00 | `DEGRADED` / `LOW` | **`MANUAL_REVIEW`** | 供应商画像缺失/超时 → 即便低危也禁止放行 |
| **D · 免票津贴消歧** | `TRV-20260315-DISAM04` | ¥480.00 | `COMPLETE` + 反思消歧 | **`AUTO_APPROVE`** | ¥100 发票差额被制度免票津贴证据消解，`REVIEW_REFLECT` 事件可见 |

### 7.3 现场动态演示动线（推荐）

1. 用 `emp` 登录 → 进入「**单据管理**」→ 找到草稿单 `EXP-20260316-DFT005`
2. 点击「**提交审查**」→ 右侧弹出 **SSE 实时时间线抽屉**，可看到：
   `task_started` → `task_progress`（阶段1 规划 / 阶段2 并行 / 阶段3 反思 / 阶段4 评分）→ 各 `node_status` → `task_completed`
3. 自动跳转「**智能风控体检报告工作台**」：
   - 左侧：发票原件画布，风险项对应 **BBox 红框高亮**
   - 右上：`CONSENSUS/LEVEL` 风险等级、评分、完整度徽章
   - 中部：智能体执行状态表（角色 / 状态 / 耗时 / 决策来源 / 降级说明）
   - 右下：**AI 审核助手**，可基于报告与证据链流式追问
4. 切到 `manager` → 进入「**审批中心**」→ 体验同意 / 驳回 / 转交 / 前置加签 / 后置加签
5. 切到 `admin` → 进入「**风控全景大盘**」→ 查看整体单据量、金额分布、合规达标率

---

## 八、API 速览

| 方法 | 路径 | 说明 |
| :--- | :--- | :--- |
| `POST` | `/api/v1/auth/login` | 账号密码登录，返回 JWT |
| `GET` | `/api/v1/auth/me` | 获取当前用户与角色 |
| `POST` | `/api/v1/documents/upload-invoice` | 上传发票，执行 OCR 版面结构化解析 |
| `POST` | `/api/v1/documents` | 创建单据草稿 |
| `GET` | `/api/v1/documents` | 分页查询单据列表 |
| `GET` | `/api/v1/documents/{id}` | 单据全量详情 |
| `PUT` | `/api/v1/documents/{id}` | 编辑草稿 / 修改被驳回单据 |
| `POST` | `/api/v1/documents/{id}/submit` | **提交并启动多 Agent 风险审查** |
| `POST` | `/api/v1/documents/{id}/cancel` | 经办人撤回 |
| `GET` | `/api/v1/documents/{id}/versions` | 历史不可变版本快照 |
| `GET` | `/api/v1/audits/reports/{document_id}` | 最新风控体检报告 |
| `GET` | `/api/v1/audits/reports/by-task/{task_id}` | 按 task_id 精确取报告 |
| `GET` | `/api/v1/audits/tasks/{task_id}` | 审查任务状态与进度 |
| `GET` | `/api/v1/audits/tasks/by-document/{id}/latest` | 按单据取最新任务 |
| `POST` | `/api/v1/audits/chat` | 基于报告与证据链的智能问答 |
| `POST` | `/api/v1/audits/chat/stream` | 流式智能问答（SSE） |
| `GET` | `/api/v1/approvals/tasks/pending` | 我的待办审批任务 |
| `POST` | `/api/v1/approvals/tasks/{id}/action` | 执行审批动作（同意/驳回/转交/加签/撤回） |
| `GET` | `/api/v1/approvals/instances/{document_id}` | 审批实例与流转轨迹 |
| `GET` | `/api/v1/dashboard/metrics` | 风控全景大盘指标 |
| `GET` | `/api/v1/audits/events/{task_id}` | **SSE 实时事件流**（支持 `Last-Event-ID` 断线重连、`: ping` 心跳保活） |
| `WS` | `/ws/audit/{task_id}`（亦可 `/api/v1/ws/audit/{task_id}`） | WebSocket 实时推流 |
| `GET` | `/health` | 系统探活 |

### 8.1 流式事件类型（9 类）

| 事件 | 含义 |
| :--- | :--- |
| `task_started` | 分析任务已受理，附执行计划快照 |
| `task_progress` | 全局进度（25% → 70% → 85% → 95% → 100%） |
| `node_status` | 单个智能体节点进入/完成（含状态、耗时、决策来源） |
| `evidence_found` | 实时捕获新的高维证据（前端原图红框跳出） |
| `risk_detected` | 检出新的风险发现项（前端告警角标） |
| `role_error` | 单个智能体异常（触发软降级） |
| `review_reflect` | 终审质检进行反思仲裁消歧 |
| `task_completed` | 全流程完毕，报告已生成落库 |
| `task_failed` | 任务遭遇不可恢复崩溃 |

---

## 九、CI 与质量保障

`.github/workflows/ci.yml` 在 push / PR 到 `main`/`master` 时执行两个 Job：

1. **Backend Pytest & Replay**（Python 3.11）
   `pip install -r requirements.txt` → `pytest -v` → `python scripts/seed_data.py` → `python scripts/replay_demos.py`
2. **Frontend Build Check**（Node 20）
   `npm install` → `npm run build`

---

## 十、安全与脱敏说明

- 🔐 `.env` 已被 `.gitignore` 严格排除，仓库只保留 `.env.example` 模板。
- 🗄️ `*.db` / `*.db-shm` / `*.db-wal` / `*.sqlite*` 均被排除，数据库由 `scripts/seed_data.py` 本地重建。
- 🖼️ `backend/uploads/**` 的发票原件被排除（保留 `.gitkeep` 目录结构）。
- 🔑 **分享代码前请自行确认**：`backend/.env`（含真实 `OPENAI_API_KEY`）与 `financial_audit.db`
  虽已被 gitignore 覆盖，但若曾以 `git add -f` 强制提交或打包成压缩包外发，密钥仍可能泄露，
  建议上传前**轮换 API Key** 并核对暂存区文件清单。
- 🚨 生产部署前必须替换：`SECRET_KEY`、收窄 `CORS_ORIGINS`、为数据库与 Redis 配置独立凭据。

---

## 十一、已知限制与后续演进

| 项 | 现状 | 演进方向 |
| :--- | :--- | :--- |
| 编排运行时 | 自研 asyncio 编排，LangGraph 范式契约已就位 | 可平滑迁移至 LangGraph 运行时 |
| 外部工商数据 | `supplier_profiles` 为本地预置画像表 | 对接企信网 / 天眼查等真实 API |
| 发票查验 | 未接入国家税务总局底账库 | 接入官方发票查验接口做真伪校验 |
| 知识库检索 | `policy_chunks` 表结构已就位，当前以确定性限额规则为主 | 引入向量检索做制度条款 RAG |
| 分布式模式 | `RedisStreamsEventBus` / `Celery` 适配器已实现降级桩 | 补齐真实 Redis/Broker 接入与压测 |

---

## 十二、License

本项目以 **MIT License** 授权发布。

> 📌 说明：当前仓库根目录尚未包含 `LICENSE` 文件，如需正式对外开源，
> 请补充标准 MIT 协议文本，或按贵司内部规范替换为合适的许可条款。

---

## 十三、参考文档

| 文档 | 内容 |
| :--- | :--- |
| `PRD-v0.1.md` | 业务需求说明书 |
| `概要设计.md` | 概要架构设计与关键技术决策（ADR） |
| `数据实体设计.md` | 表结构与实体关系设计 |
| `specs/01_engines_contract_spec.md` | 领域契约规格 |
| `specs/02_engines_amount_agent_spec.md` | 金额精算智能体规格 |
| `specs/03_app_repositories_spec.md` | 仓储层规格 |
| `specs/04_engines_policy_agent_spec.md` | 制度合规智能体规格 |
| `specs/05_app_approval_engine_spec.md` | 审批引擎规格 |
| `specs/06_engines_anomaly_agent_spec.md` | 反欺诈智能体规格 |
| `specs/07_engines_orchestrator_spec.md` | 编排中枢规格 |
| `walkthrough.md` | 验收走查记录 |
| `整体流程图.png` | 全流程架构图 |
