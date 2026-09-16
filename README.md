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

### 3. 运行自动化测试回归

```bash
cd backend
pytest -v
```

---

## 🛡️ 安全合规与脱敏说明

- 本仓库已配置严密的 `.gitignore` 规则，不包含任何真实 API 密钥、数据库实体文件（`*.db`）或用户上传的私有凭证。
- 如需启用真实 LLM 语义审核能力，请在本地 `backend/.env` 中配置合法的 `OPENAI_API_KEY` 与 `OPENAI_BASE_URL`。

---

## 📄 License
[MIT License](LICENSE)
