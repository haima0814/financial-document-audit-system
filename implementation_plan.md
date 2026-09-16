# 实施方案：后端工程基础脚手架与 engines/contract 核心契约实现

本实施计划旨在将前面制定的全套系统架构设计与 7 份详细设计 Spec 正式落地为可执行的高性能 Python 代码。首先构建 `backend/` 基础工程骨架，并率先实现整个多智能体审核系统的底层通信底座 —— **`engines/contract/` 智能体契约体系**，配备完整的自动化单元测试集。

---

## 阶段目标与业务范围

1. **工程骨架搭建**：创建规范的整洁架构分层目录（`app/` 业务层、`engines/` 推理层、`models/` 实体层、`tests/` 测试层），建立依赖清单 `requirements.txt`；
2. **核心契约落地 (`engines/contract/`)**：
   - 8 大智能体角色枚举、中文名与硬超时配置（`agent_role.py`）；
   - 五维不可变证据链模型与按需装配矩阵（`evidence.py`，含 BBox 视觉坐标、Decimal 精算存证、制度版本切片）；
   - 统一风险发现项契约与账本分离规范（`finding.py`）；
   - WebSocket / Redis Streams 实时流式事件载荷协议（`events.py`，支持 9 大事件与断点续传）；
   - 引擎全局超参与阈值规约（`settings.py`）；
3. **自动化测试套件**：编写 `tests/engines/test_contract.py`，验证数据模型序列化、不可变性（Immutability）、坐标转换与边界约束。

---

## 拟创建的文件清单

### 1. 基础配置与依赖
- [NEW] `backend/requirements.txt`: 核心依赖定义（FastAPI, Pydantic v2, SQLAlchemy 2.0, Pytest 等）
- [NEW] `backend/engines/__init__.py`

### 2. 核心契约体系 (`backend/engines/contract/`)
- [NEW] `backend/engines/contract/__init__.py`: 模块统一对外导出
- [NEW] `backend/engines/contract/agent_role.py`: 8 大角色枚举、生命周期元数据与超时配置
- [NEW] `backend/engines/contract/evidence.py`: 视觉锚点、算术凭据、制度切片与不可变五维证据模型
- [NEW] `backend/engines/contract/finding.py`: 风险等级与标准风险发现项契约
- [NEW] `backend/engines/contract/events.py`: 9 种审计流实时事件定义与序列化协议
- [NEW] `backend/engines/contract/settings.py`: 引擎超参配置（阈值、容差、Redis 连接等）

### 3. 测试套件 (`backend/tests/`)
- [NEW] `backend/tests/__init__.py`
- [NEW] `backend/tests/engines/__init__.py`
- [NEW] `backend/tests/engines/test_contract.py`: 覆盖契约只读性、序列化、证据链装配、事件流的 pytest 测试用例

---

## 验证与验收计划

### 自动化测试命令
在 `e:/面试项目实战/财务单据智能风险审核系统/backend` 目录下执行：
```bash
pytest tests/engines/test_contract.py -v
```

### 验证标准
1. **测试通过率**：100% 通过所有断言（零 warning、零 error）；
2. **只读不可变性（Immutability）**：尝试篡改已生成的 `EvidenceRecord` 或 `RiskFindingContract` 必须抛出 `ValidationError`；
3. **精确度校验**：Decimal 金额核算存证确保精确至两位小数，杜绝浮点数精度截断；
4. **序列化兼容性**：契约对象 `model_dump_json()` 与 `model_validate_json()` 可无缝与 PostgreSQL JSONB 字段互转。
