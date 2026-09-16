"""
backend/engines/contract/evidence.py
五维不可变证据模型契约
"""
from enum import Enum
from typing import Optional, List, Dict, Any
from decimal import Decimal
from datetime import datetime, timezone
import uuid
from pydantic import BaseModel, Field, ConfigDict

class EvidenceCategoryEnum(str, Enum):
    """证据大类枚举"""
    VISUAL = "visual"                  # 视觉空间维：发票原始图片/PDF归一化BBox框选坐标
    CALCULATION = "calculation"        # 数学精算维：纯代码确定性精算公式明细与操作数
    POLICY = "policy"                  # 制度合规维：制度切片指纹ID、版本号与条款原文
    EXTERNAL = "external"              # 外部权威维：国家税务局发票底账、企信网工商穿透结果
    BEHAVIORAL = "behavioral"          # 行为时空维：跨单据轨迹时空碰撞坐标与发票复用哈希

class BoundingBox(BaseModel):
    """
    发票/单据原图视觉坐标 (归一化到 0.0 ~ 1.0)
    标准格式：[ymin, xmin, ymax, xmax]
    """
    model_config = ConfigDict(frozen=True)
    
    ymin: float = Field(..., ge=0.0, le=1.0, description="顶部 Y 坐标 (归一化)")
    xmin: float = Field(..., ge=0.0, le=1.0, description="左侧 X 坐标 (归一化)")
    ymax: float = Field(..., ge=0.0, le=1.0, description="底部 Y 坐标 (归一化)")
    xmax: float = Field(..., ge=0.0, le=1.0, description="右侧 X 坐标 (归一化)")
    page_number: int = Field(default=1, ge=1, description="所在 PDF 页码 (1-based)")

    def to_list(self) -> List[float]:
        return [self.ymin, self.xmin, self.ymax, self.xmax]

class VisualAnchor(BaseModel):
    """视觉锚定详情 (支持前端联动平移聚焦)"""
    model_config = ConfigDict(frozen=True)
    
    attachment_id: int = Field(..., description="绑定的附件表 attachment_id")
    file_name: str = Field(..., description="原文件名")
    file_hash: str = Field(..., description="文件 SHA-256 哈希")
    bbox: BoundingBox = Field(..., description="视觉高亮矩形框")
    field_key: str = Field(..., description="锚定字段标识 (如 invoice_amount / seller_tax_id)")
    ocr_raw_text: str = Field(..., description="OCR 识别出的原文字符 (客观事实维)")
    ocr_confidence: float = Field(default=1.0, ge=0.0, le=1.0, description="OCR 置信度")

class CalculationProof(BaseModel):
    """确定性计算核算存证 (数学精算维)"""
    model_config = ConfigDict(frozen=True)
    
    formula_expr: str = Field(..., description="算式明细字符串，如 '(1250.00 - 800.00) = 450.00'")
    operand_left: Decimal = Field(..., description="左操作数")
    operand_right: Decimal = Field(..., description="右操作数")
    result: Decimal = Field(..., description="运算结果")
    tolerance: Decimal = Field(default=Decimal("0.00"), description="允许公差")
    is_balanced: bool = Field(..., description="是否平账/核对一致")

class PolicyProof(BaseModel):
    """制度条款引用存证 (制度合规维)"""
    model_config = ConfigDict(frozen=True)
    
    policy_id: int = Field(..., description="制度主表 ID")
    policy_name: str = Field(..., description="制度规范名称")
    policy_version: str = Field(..., description="制度版本号 (如 2026-V1)")
    chunk_id: str = Field(..., description="切片唯一指纹 Chunk ID")
    clause_title: str = Field(..., description="章节条款标题")
    clause_content: str = Field(..., description="条款原文正文")
    retrieval_similarity: float = Field(..., description="语义检索相似度余弦分值")

class EvidenceRecord(BaseModel):
    """
    五维不可变证据记录 (完整证据链单元)
    严格声明为只读不可变 (frozen=True)，可直接序列化落库为 PG JSONB
    根据规则类型按需填充载荷字段，无需强求全部具备
    """
    model_config = ConfigDict(frozen=True)

    evidence_id: str = Field(default_factory=lambda: str(uuid.uuid4()), description="全局唯一证据指纹 ID")
    category: EvidenceCategoryEnum = Field(..., description="证据大类")
    produced_by: str = Field(..., description="生产该证据的 Agent 角色名")
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc), description="生成时间 (UTC)")
    
    # 五维具体证据载荷 (按需装配，可选填充)
    visual_anchor: Optional[VisualAnchor] = Field(default=None, description="视觉空间维证据")
    calc_proof: Optional[CalculationProof] = Field(default=None, description="数学精算维证据")
    policy_proof: Optional[PolicyProof] = Field(default=None, description="制度合规维证据")
    extra_data: Optional[Dict[str, Any]] = Field(default=None, description="外部权威或时空冲突快照")
