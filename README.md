# 财务单据智能风险审核系统 (Intelligent Financial Document Risk Audit System)

> 企业级多智能体协同（Multi-Agent）与确定性规则引擎双重驱动的财务合规与风控审计平台。  
> 遵循 **“算归算，想归想 (Deterministic Computation + LLM Reasoning)”** 与 **“Fail-Safe 兜底降级门禁”** 原则。

---

## 🌟 项目亮点与核心特性

- **双模架构 (Dual-Engine)**：
  - 本地演示极速模式：`SQLite 3 (WAL)` + 原生 `asyncio` 协程工作池，零外部中间件，克隆即跑。
  - 生产高可用模式：`PostgreSQL 15+` + `Redis 7` + `Celery` 分布式解耦。
- **多智能体分层图拓扑 (LangGraph 0.2+)**：
  - 主图全局契约 + 各领域子图（金额校验 AmountAgent、制度合规 PolicyAgent、供应商风控 SupplierAgent、异常反欺诈 AnomalyAgent）。
  - 二阶反思消歧器（ReviewerReflector）与四阶审批决策门禁（Decision Gate），具备 Fail-Safe 兜底能力。
- **高精度与合规审计**：
  - 金额计算严格基于 Python `Decimal` 强精度与银行家舍入，拒绝浮点精度误差与大模型幻觉计算。
  - 支持多发票与对公付款凭证的四单对齐、交叉查验、黑名单穿透与免票津贴制度合规判定。
- **现代化全栈体验**：
  - 后端：FastAPI + SQLAlchemy 2.0 (Async) + Pydantic v2 + DeepSeek/Qwen 兼容协议。
  - 前端：Vue 3 + Vite + Pinia + Element Plus，支持发票原件双模画布（SVG BBox 视觉坐标高亮聚焦）。

---

## 📂 项目目录结构

```text
.
├── backend/                  # 后端项目 (FastAPI + LangGraph + SQLAlchemy)
│   ├── app/                  # 业务逻辑、API 路由、数据模型
│   │   ├── api/              # RESTful API 路由 (v1)
│   │   ├── core/             # 系统配置、安全、任务调度器
│   │   ├── models/           # 数据库 ORM 模型与实体定义
│   │   └── schemas/          # Pydantic 契约模型与序列化
│   ├── engines/              # 核心审计引擎与多智能体子图
│   │   ├── orchestrator/     # MasterOrchestrator、ReviewerReflector、Stage4 决策门禁
│   │   ├── agents/           # 各专业子图 Agent (Amount, Policy, Supplier, Anomaly)
│   │   └── rules/            # 确定性规则与风险编码 (R01~R18)
│   ├── tests/                # 自动化测试用例 (130+ 单元/集成测试)
│   ├── main.py               # 后端应用启动入口
│   ├── requirements.txt      # 后端依赖清单
│   └── .env.example          # 环境变量示例模版
├── frontend/                 # 前端项目 (Vue 3 + Vite + Element Plus)
│   ├── src/                  # 前端源码 (Views, Components, Stores, API)
│   ├── package.json          # 前端依赖配置
│   └── vite.config.js        # Vite 构建配置
├── specs/                    # 规格设计与工程标准说明
├── PRD-v0.1.md               # 业务需求说明书
├── 概要设计.md                # 系统概要架构设计与 ADR
├── 数据实体设计.md            # 数据库表结构与实体关系图
└── 整体流程图.png             # 全流程架构图
```

---

## 🚀 快速启动指南

### 1. 后端启动 (Backend)

```bash
# 1. 进入后端目录
cd backend

# 2. 创建并激活虚拟环境 (Windows PowerShell)
python -m venv .venv
.venv\Scripts\Activate.ps1

# 3. 安装依赖
pip install -r requirements.txt

# 4. 配置环境变量 (复制模板文件并填入 API Key，若使用 mock-key 将自动启用离线规则降级)
cp .env.example .env

# 5. 启动开发服务器
python main.py
# 或使用 uvicorn
uvicorn main:app --reload --port 8000
```
- API 交互文档地址：`http://localhost:8000/docs`

### 2. 前端启动 (Frontend)

```bash
# 1. 进入前端目录
cd frontend

# 2. 安装 Node.js 依赖
npm install

# 3. 启动 Vite 开发服务器
npm run dev
```
- 前端访问地址：`http://localhost:5173`

### 3. 一键预置 Demo 场景数据与全链路回放

系统内置了 3 大核心业务闭环场景及 1 项终审反思消歧案例，可通过脚本直接一键重放与断言校验：

```bash
cd backend
# 1. 预置全量数据库与场景单据
python scripts/seed_data.py

# 2. 自动化执行端到端回放校验
python scripts/replay_demos.py
```

### 4. 运行全量自动化测试套件

```bash
cd backend
pytest -v
```
> 目前全量通过 **139/139** 项单元测试与数据库状态机集成测试。

---

## 🎯 3 大核心演示场景说明 (Minimum Viable Closed Loop)

登录系统（默认密码统一为 `123456`）：
* 经办员工：`emp` (小赵)
* 部门主管：`manager` (张经理)
* 财务专员：`finance` (李财务)
* 财务总监：`cfo` (王总监)
* 管理员：`admin` (系统管理员)

| 场景编号 | 场景名称与单据 | 风险特征与完整度 | 审批状态机最终裁决 | 业务后果与界面效果 |
| :--- | :--- | :--- | :---: | :--- |
| **场景 A** | **正常小额市内打车费**<br>`EXP-20260312-PASS01` (¥320.00) | `COMPLETE`<br>`LOW` (评分 100) | **`AUTO_APPROVE`**<br>(系统自动放行) | 单据直接置为 `APPROVED`，生成系统免审任务，无需人工介入。 |
| **场景 B** | **跨单重复报销发票**<br>`EXP-20260313-VETO02` (¥850.00) | `COMPLETE`<br>`HIGH` (R08不可覆盖) | **`REJECT`**<br>(一票否决驳回) | 优先于完整度直接一票否决，单据置为 `REJECTED`，工作流终止，**不创建任何普通待办**。 |
| **场景 C** | **会务技术运维服务款**<br>`EXP-20260314-DEGR003` (¥2400.00) | `DEGRADED`<br>`LOW` (评分 95) | **`MANUAL_REVIEW`**<br>(转人工重点复核) | 外部接口超时导致核验降级，严禁自动放行，单据进入 `PENDING_APPROVAL`，转主管与财务人工复核。 |
| **场景 D**<br>*(附加)* | **差旅交通与包干津贴**<br>`TRV-20260315-DISAM04` (¥480.00) | `COMPLETE`<br>门禁反思消歧 | **`AUTO_APPROVE`**<br>(消歧后自动放行) | 存在 100 元发票差额，ReviewerReflector 匹配差旅包干津贴制度证据后自动消歧核减，最终放行。 |

> 💡 **现场动态演示建议**：切换至 `emp` 账号，进入「单据管理」，找到草稿单 `EXP-20260316-DFT005`，点击「提交审查」，可直观感受 **SSE 实时流式时间线抽屉** 与 **智能风控体检报告工作台** 的端到端交互。

---

## 🛡️ 安全合规与脱敏说明

- 本仓库已配置严密的 `.gitignore` 规则，不包含任何真实 API 密钥、数据库实体文件（`*.db`）或用户上传的私有凭证。
- 如需启用真实 LLM 语义审核能力，请在本地 `backend/.env` 中配置合法的 `OPENAI_API_KEY` 与 `OPENAI_BASE_URL`。

---

## 📄 License
[MIT License](LICENSE)

