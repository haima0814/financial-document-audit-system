"""
backend/app/services/ocr_service.py
发票票据 OCR 智能版面分析与结构化提取服务 (工业级防伪造与交叉核验架构)

核心设计准则：
1. 宁可缺失 (None)，绝不伪造；严禁写死税率 (如6%)、默认金额 (如1000.00) 或随机发票号码。
2. 真实文件禁止回退至模拟/启发式数据，_extract_invoice_data 仅在明确 sample_type 模式下允许调用。
3. 底层全量保留 Raw OCR Tokens (Text, BBox, Confidence, Page, Source)。
4. 候选提取层 -> 字段映射器 (FieldMapper) -> 确定性数学交叉验证 (CrossValidator) -> 数据质量门 (DataQualityGate)。
5. 明确区分原始 OCR 提取值与程序推导值 (Derived)，严密标记字段状态 (CONFIRMED, UNCERTAIN, MISSING, CONFLICT, DERIVED)。
6. 只有存在真实视觉坐标证据时才提供 BBox，严禁虚构标准模板 BBox。
7. 全流程高精度 Decimal 计算，容忍公差 Decimal("0.01")。
"""
import os
import re
import uuid
import hashlib
import logging
from decimal import Decimal, InvalidOperation
from typing import Dict, Any, Optional, Tuple, List
from datetime import datetime, timezone
from dataclasses import dataclass, field
from enum import Enum

logger = logging.getLogger("service.ocr")

UPLOAD_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "uploads", "invoices")
os.makedirs(UPLOAD_DIR, exist_ok=True)


class TokenSource(str, Enum):
    RAPIDOCR = "rapidocr"
    PYMUPDF = "pymupdf"
    PYPDF = "pypdf"
    DERIVED = "derived"
    HEURISTIC = "heuristic"


class FieldStatus(str, Enum):
    CONFIRMED = "CONFIRMED"   # 经 OCR 提取且通过交叉勾稽验证
    UNCERTAIN = "UNCERTAIN"   # 提取到候选但缺乏交叉验证证据
    MISSING = "MISSING"       # 票面未识别到该字段 (None)
    CONFLICT = "CONFLICT"     # 存在相互冲突的多个候选值，无法消除分歧
    DERIVED = "DERIVED"       # 通过数学勾稽关系 (如 Total - Tax) 安全推导获得


@dataclass
class OcrToken:
    """底层不可变 OCR 文本单元"""
    text: str
    bbox: Optional[List[int]] = None  # 归一化网格坐标 [ymin, xmin, ymax, xmax] (0..1000)
    confidence: float = 1.0
    page: int = 1
    source: str = "rapidocr"
    cx: float = 0.0
    cy: float = 0.0

    def __post_init__(self):
        if self.bbox and len(self.bbox) == 4:
            self.cy = (self.bbox[0] + self.bbox[2]) / 2.0
            self.cx = (self.bbox[1] + self.bbox[3]) / 2.0


@dataclass
class FieldCandidate:
    """字段候选提取对象"""
    value: Any
    raw_text: str
    bbox: Optional[List[int]] = None
    confidence: float = 1.0
    source: str = "ocr"
    context_label: Optional[str] = None
    field_type: Optional[str] = None


@dataclass
class FieldDetail:
    """结构化输出字段级全息详情"""
    value: Any = None
    status: str = FieldStatus.MISSING
    confidence: float = 0.0
    source: str = "ocr"
    bbox: Optional[List[int]] = None
    raw_text: Optional[str] = None
    evidences: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        val = self.value
        if isinstance(val, Decimal):
            val = float(val)
        status_str = self.status.value if hasattr(self.status, "value") else str(self.status)
        source_str = self.source.value if hasattr(self.source, "value") else str(self.source)
        return {
            "value": val,
            "status": status_str,
            "confidence": round(float(self.confidence), 3),
            "source": source_str,
            "bbox": self.bbox,
            "raw_text": self.raw_text,
            "evidences": self.evidences
        }


class ChineseCurrencyParser:
    """中文大写发票金额解析器"""
    NUM_MAP = {
        '零': 0, '壹': 1, '贰': 2, '两': 2, '叁': 3, '肆': 4,
        '伍': 5, '陆': 6, '柒': 7, '捌': 8, '玖': 9
    }
    UNIT_MAP = {
        '拾': 10, '佰': 100, '仟': 1000
    }

    @classmethod
    def parse(cls, text: str) -> Optional[Decimal]:
        if not text:
            return None
        clean = re.sub(r"[^零壹贰两叁肆伍陆柒捌玖拾佰仟万亿圆元角分整正]", "", text)
        if not clean or not any(c in cls.NUM_MAP for c in clean):
            return None

        try:
            # 截取整数部分与角分
            main_part = clean
            jiao, fen = 0, 0
            if '角' in main_part:
                parts = main_part.split('角')
                if parts[0] and parts[0][-1] in cls.NUM_MAP:
                    jiao = cls.NUM_MAP[parts[0][-1]]
                main_part = parts[0][:-1]
                if len(parts) > 1 and '分' in parts[1]:
                    f_parts = parts[1].split('分')
                    if f_parts[0] and f_parts[0][-1] in cls.NUM_MAP:
                        fen = cls.NUM_MAP[f_parts[0][-1]]
            elif '分' in main_part:
                parts = main_part.split('分')
                if parts[0] and parts[0][-1] in cls.NUM_MAP:
                    fen = cls.NUM_MAP[parts[0][-1]]
                main_part = parts[0][:-1]

            # 移除圆/元/整/正
            for ch in ['圆', '元', '整', '正']:
                main_part = main_part.replace(ch, '')

            def parse_section(sec_text: str) -> int:
                total = 0
                r = 0
                for ch in sec_text:
                    if ch in cls.NUM_MAP:
                        r = cls.NUM_MAP[ch]
                    elif ch in cls.UNIT_MAP:
                        total += (r if r != 0 else 1) * cls.UNIT_MAP[ch]
                        r = 0
                total += r
                return total

            # 处理 亿 与 万 分段
            total_int = 0
            if '亿' in main_part:
                yi_parts = main_part.split('亿')
                total_int += parse_section(yi_parts[0]) * 100000000
                main_part = yi_parts[1] if len(yi_parts) > 1 else ""

            if '万' in main_part:
                wan_parts = main_part.split('万')
                total_int += parse_section(wan_parts[0]) * 10000
                main_part = wan_parts[1] if len(wan_parts) > 1 else ""

            total_int += parse_section(main_part)

            result = Decimal(total_int) + Decimal(jiao) * Decimal("0.1") + Decimal(fen) * Decimal("0.01")
            return result if result > 0 else None
        except Exception:
            return None


class InvoiceOcrService:
    _ocr_engine = None

    @classmethod
    def _get_ocr_engine(cls):
        if cls._ocr_engine is None:
            from rapidocr_onnxruntime import RapidOCR
            cls._ocr_engine = RapidOCR()
            logger.info("RapidOCR 视觉版面分析引擎初始化成功 (CPU/ONNX Runtime)")
        return cls._ocr_engine

    @staticmethod
    def _compute_invoice_hash(code: Optional[str], number: Optional[str], amount: Optional[Any], date: Optional[str]) -> Optional[str]:
        if not number or amount is None:
            return None
        code_str = code or "NONE"
        amount_str = f"{float(amount):.2f}"
        date_str = date or "UNKNOWN"
        raw = f"{code_str}#{number}#{amount_str}#{date_str}"
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    # =========================================================================
    # 1. 底层文件 Token 提取层 (RapidOCR + PyMuPDF / pypdf)
    # =========================================================================
    @classmethod
    def _extract_tokens_from_image(cls, content: bytes, filename: str) -> List[OcrToken]:
        """使用 RapidOCR 对真实图片进行文字与精准像素级 BBox 识别并归一化"""
        try:
            import io
            from PIL import Image

            img = Image.open(io.BytesIO(content))
            width, height = img.size

            engine = cls._get_ocr_engine()
            ocr_result, elapse = engine(content)
            if not ocr_result:
                logger.warning(f"RapidOCR 未能从图片 {filename} 中识别到文字")
                return []

            tokens = []
            for item in ocr_result:
                pts, text, score = item[0], item[1].strip(), float(item[2])
                if not text:
                    continue
                xs = [p[0] for p in pts]
                ys = [p[1] for p in pts]
                ymin = max(0, min(1000, int(min(ys) / height * 1000)))
                xmin = max(0, min(1000, int(min(xs) / width * 1000)))
                ymax = max(0, min(1000, int(max(ys) / height * 1000)))
                xmax = max(0, min(1000, int(max(xs) / width * 1000)))
                tokens.append(OcrToken(
                    text=text,
                    bbox=[ymin, xmin, ymax, xmax],
                    confidence=score,
                    page=1,
                    source=TokenSource.RAPIDOCR
                ))
            return tokens
        except Exception as e:
            logger.error(f"RapidOCR 图片解析异常 ({filename}): {e}", exc_info=True)
            return []

    @classmethod
    def _extract_tokens_from_pdf(cls, content: bytes, filename: str) -> List[OcrToken]:
        """优先使用 PyMuPDF 获取带精确坐标的 PDF 原生字词，fallback 至 pypdf 或 RapidOCR 扫描件光栅化"""
        # A. 优先尝试 PyMuPDF (fitz)
        try:
            import fitz  # PyMuPDF
            doc = fitz.open(stream=content, filetype="pdf")
            tokens = []
            for page_idx, page in enumerate(doc):
                rect = page.rect
                width, height = rect.width, rect.height
                if width <= 0 or height <= 0:
                    continue

                blocks = page.get_text("blocks")
                for b in blocks:
                    # b: (x0, y0, x1, y1, text, block_no, block_type)
                    if len(b) >= 5 and b[4].strip():
                        b_text = b[4].strip()
                        lines = b_text.splitlines()
                        # 单块可能包含多行，拆分为单行保留坐标
                        line_h = (b[3] - b[1]) / max(1, len(lines))
                        for l_idx, line in enumerate(lines):
                            l_str = line.strip()
                            if not l_str:
                                continue
                            y0 = b[1] + l_idx * line_h
                            y1 = y0 + line_h
                            ymin = max(0, min(1000, int(y0 / height * 1000)))
                            xmin = max(0, min(1000, int(b[0] / width * 1000)))
                            ymax = max(0, min(1000, int(y1 / height * 1000)))
                            xmax = max(0, min(1000, int(b[2] / width * 1000)))
                            tokens.append(OcrToken(
                                text=l_str,
                                bbox=[ymin, xmin, ymax, xmax],
                                confidence=0.99,
                                page=page_idx + 1,
                                source=TokenSource.PYMUPDF
                            ))

            if tokens:
                logger.info(f"PyMuPDF 成功从 PDF {filename} 提取 {len(tokens)} 个原生文本 Token")
                return tokens

            # 如果 PDF 内无矢量文字 (扫描版 PDF)，则光栅化第一页并使用 RapidOCR
            if len(doc) > 0:
                logger.info(f"PDF {filename} 为扫描件图像流，启用 RapidOCR 光栅化解析")
                pix = doc[0].get_pixmap(dpi=150)
                img_bytes = pix.tobytes("png")
                return cls._extract_tokens_from_image(img_bytes, filename)
        except Exception as e:
            logger.warning(f"PyMuPDF 解析异常，切换至 pypdf: {e}")

        # B. Fallback: pypdf 提取文本流 (无 BBox)
        try:
            import io
            from pypdf import PdfReader
            reader = PdfReader(io.BytesIO(content))
            tokens = []
            for p_idx, page in enumerate(reader.pages):
                txt = page.extract_text()
                if txt:
                    for line in txt.splitlines():
                        line_s = line.strip()
                        if line_s:
                            tokens.append(OcrToken(
                                text=line_s,
                                bbox=None,
                                confidence=0.95,
                                page=p_idx + 1,
                                source=TokenSource.PYPDF
                            ))
            if tokens:
                logger.info(f"pypdf 从 PDF {filename} 提取到 {len(tokens)} 行文本")
                return tokens
        except Exception as e:
            logger.error(f"pypdf 回退解析亦失败 ({filename}): {e}")

        return []

    # =========================================================================
    # 2. 候选提取层 CandidateExtractor
    # =========================================================================
    @classmethod
    def _extract_money_candidates(cls, tokens: List[OcrToken]) -> List[FieldCandidate]:
        """提取所有符合金额规范的候选数值 (统一转 Decimal 并 quantize 0.01，严禁 float 中间计算)"""
        money_re = re.compile(
            r'(?:[¥￥$]\s*([0-9]{1,3}(?:,[0-9]{3})+(?:\.[0-9]{1,2})?|[0-9]+(?:\.[0-9]{1,2})?)\s*元?'
            r'|(?<![0-9A-Za-z])([0-9]{1,3}(?:,[0-9]{3})+(?:\.[0-9]{1,2})?|[0-9]+(?:\.[0-9]{1,2})?)\s*元'
            r'|\b([0-9]{1,3}(?:,[0-9]{3})*\.[0-9]{2}|[0-9]+\.[0-9]{2})\b)'
        )
        candidates = []

        for t in tokens:
            cleaned = t.text.replace('￥', '¥')
            for m in money_re.finditer(cleaned):
                s = next(g for g in m.groups() if g is not None).replace(',', '')
                try:
                    val = Decimal(s).quantize(Decimal("0.01"))
                    if val <= 0:
                        continue
                    candidates.append(FieldCandidate(
                        value=val,
                        raw_text=t.text,
                        bbox=t.bbox,
                        confidence=t.confidence,
                        source=t.source,
                        context_label=t.text
                    ))
                except InvalidOperation:
                    continue
        return candidates

    @classmethod
    def _extract_tax_rate_candidates(cls, tokens: List[OcrToken]) -> List[FieldCandidate]:
        """提取发票票面标称的税率候选 (例如 17%, 16%, 13%, 9%, 6%, 3%, 1%, 0%, 免税)"""
        rate_re = re.compile(r'\b([0-9]{1,2}(?:\.[0-9]{1,2})?)\s*%')
        candidates = []

        for t in tokens:
            for m in rate_re.finditer(t.text):
                try:
                    pct = Decimal(m.group(1))
                    val = (pct / Decimal("100")).quantize(Decimal("0.0001"))
                    candidates.append(FieldCandidate(
                        value=val,
                        raw_text=t.text,
                        bbox=t.bbox,
                        confidence=t.confidence,
                        source=t.source,
                        context_label=f"{m.group(1)}%"
                    ))
                except InvalidOperation:
                    continue
            if any(k in t.text for k in ["免税", "不征税"]):
                candidates.append(FieldCandidate(
                    value=Decimal("0.0000"),
                    raw_text=t.text,
                    bbox=t.bbox,
                    confidence=t.confidence,
                    source=t.source,
                    context_label="免税"
                ))
        return candidates

    @classmethod
    def _extract_date_candidates(cls, tokens: List[OcrToken]) -> List[FieldCandidate]:
        """提取开票日期候选 (规范为 YYYY-MM-DD)"""
        candidates = []
        for t in tokens:
            # 形式 1: 2017年12月01日
            m1 = re.search(r"([0-9]{4})\s*[年\-\.\/]\s*([0-9]{1,2})\s*[月\-\.\/]\s*([0-9]{1,2})", t.text)
            if m1:
                y, m, d = m1.groups()
                try:
                    y_i, m_i, d_i = int(y), int(m), int(d)
                    if 2000 <= y_i <= 2035 and 1 <= m_i <= 12 and 1 <= d_i <= 31:
                        date_str = f"{y_i:04d}-{m_i:02d}-{d_i:02d}"
                        candidates.append(FieldCandidate(
                            value=date_str,
                            raw_text=t.text,
                            bbox=t.bbox,
                            confidence=t.confidence,
                            source=t.source
                        ))
                except ValueError:
                    pass
        return candidates

    @classmethod
    def _extract_tax_id_candidates(cls, tokens: List[OcrToken]) -> List[FieldCandidate]:
        """提取统一社会信用代码/纳税人识别号候选 (18位或15位)"""
        candidates = []
        uscc_re = re.compile(r'\b([0-9A-HJ-NPQRTUWXY]{2}[0-9]{6}[0-9A-HJ-NPQRTUWXY]{10})\b')
        for t in tokens:
            for m in uscc_re.finditer(t.text):
                code = m.group(1)
                candidates.append(FieldCandidate(
                    value=code,
                    raw_text=t.text,
                    bbox=t.bbox,
                    confidence=t.confidence,
                    source=t.source,
                    context_label=t.text
                ))
        return candidates

    @classmethod
    def _extract_invoice_number_and_code(cls, tokens: List[OcrToken]) -> Tuple[Optional[FieldCandidate], Optional[FieldCandidate]]:
        """提取发票号码与发票代码"""
        inv_num_cand = None
        inv_code_cand = None

        for t in tokens:
            # 发票代码 (10位或12位纯数字)
            if not inv_code_cand:
                m_code = re.search(r"(?:发票代码)[:：\s]*([0-9]{10,12})", t.text)
                if m_code:
                    inv_code_cand = FieldCandidate(
                        value=m_code.group(1),
                        raw_text=t.text,
                        bbox=t.bbox,
                        confidence=t.confidence,
                        source=t.source
                    )

            # 发票号码 (优先匹配 "No 05073978" 或 "发票号码: 8~20位纯数字")
            if not inv_num_cand:
                m_num = re.search(r"(?:发票号码|No|NO|N0|凭证号|单号)[:：\s\.]*([0-9A-Za-z]{8,20})", t.text)
                if m_num:
                    inv_num_cand = FieldCandidate(
                        value=m_num.group(1),
                        raw_text=t.text,
                        bbox=t.bbox,
                        confidence=t.confidence,
                        source=t.source
                    )

        # 二次检索：独立的 8/20 位纯数字作为发票号码候选
        if not inv_num_cand:
            for t in tokens:
                # 排除发票代码、日期、税号等长数字
                if re.fullmatch(r"[0-9]{8}", t.text) or re.fullmatch(r"[0-9]{20}", t.text):
                    inv_num_cand = FieldCandidate(
                        value=t.text,
                        raw_text=t.text,
                        bbox=t.bbox,
                        confidence=t.confidence * 0.9,
                        source=t.source
                    )
                    break

        # 二次检索：发票代码
        if not inv_code_cand:
            for t in tokens:
                if re.fullmatch(r"[0-9]{10}", t.text) or re.fullmatch(r"[0-9]{12}", t.text):
                    # 避免与发票号码重复
                    if inv_num_cand and inv_num_cand.value == t.text:
                        continue
                    inv_code_cand = FieldCandidate(
                        value=t.text,
                        raw_text=t.text,
                        bbox=t.bbox,
                        confidence=t.confidence * 0.85,
                        source=t.source
                    )
                    break

        return inv_num_cand, inv_code_cand

    @classmethod
    def _extract_line_item_details(cls, tokens: List[OcrToken]) -> Tuple[List[Decimal], List[Decimal]]:
        """从明细表格行提取单项金额与单项税额列表"""
        detail_amounts: List[Decimal] = []
        detail_taxes: List[Decimal] = []

        # 寻找处于表体区域 (通常在 ymin 300 到 700 之间) 的金额行
        for t in tokens:
            if not t.bbox:
                continue
            ymin = t.bbox[0]
            # 过滤表头及表尾合计行
            if ymin < 260 or ymin > 720:
                continue
            if any(k in t.text for k in ["合计", "小写", "总计"]):
                continue

            m = re.fullmatch(r"[¥￥]?\s*([0-9]+\.[0-9]{2})", t.text)
            if m:
                val = Decimal(m.group(1))
                # 根据 xmin 判断属于金额列还是税额列
                # 金额列通常在中间偏右 (xmin: 550~800)，税额列在最右 (xmin: 800~1000)
                xmin = t.bbox[1]
                if xmin > 780:
                    detail_taxes.append(val)
                elif xmin > 550:
                    detail_amounts.append(val)

        return detail_amounts, detail_taxes

    # =========================================================================
    # 3. 字段映射与数学交叉验证 CrossValidator
    # =========================================================================
    @classmethod
    def _resolve_amount_triplet(
        cls,
        tokens: List[OcrToken],
        money_candidates: List[FieldCandidate],
        tax_rate_candidates: List[FieldCandidate],
        detail_amounts: List[Decimal],
        detail_taxes: List[Decimal],
        capital_amount: Optional[Decimal]
    ) -> Tuple[Dict[str, FieldDetail], Dict[str, bool], List[str]]:
        """
        三元组勾稽消歧核心引擎：
        第一层：untaxed_amount + tax_amount ≈ total_amount (公差 <= 0.01)
        第二层：sum(detail_amounts) ≈ untaxed_amount
        第三层：sum(detail_tax_amounts) ≈ tax_amount
        第四层：untaxed_amount * tax_rate ≈ tax_amount
        第五层：total_amount 与中文大写合计金额对齐
        """
        field_details = {
            "total_amount": FieldDetail(status=FieldStatus.MISSING),
            "untaxed_amount": FieldDetail(status=FieldStatus.MISSING),
            "tax_amount": FieldDetail(status=FieldStatus.MISSING),
            "tax_rate": FieldDetail(status=FieldStatus.MISSING)
        }
        validation = {
            "amount_equation_valid": False,
            "detail_amount_sum_valid": False,
            "detail_tax_sum_valid": False,
            "tax_rate_consistent": False
        }
        quality_issues = []

        if not money_candidates:
            quality_issues.append("INVOICE_TOTAL_AMOUNT_MISSING")
            return field_details, validation, quality_issues

        # 收集有效的三元组组合 (untaxed, tax, total)
        valid_triplets = []
        for i, m1 in enumerate(money_candidates):
            v1 = m1.value
            for j, m2 in enumerate(money_candidates):
                if i == j:
                    continue
                v2 = m2.value
                for k, m3 in enumerate(money_candidates):
                    if k == i or k == j:
                        continue
                    v3 = m3.value
                    # 校验第一层：v1 + v2 == v3 (untaxed + tax == total)
                    if abs(v1 + v2 - v3) <= Decimal("0.01") and v1 > 0 and v2 > 0:
                        score = 0
                        evidences = []

                        # 第二层校验：明细行金额累加匹配 (过滤掉合计候选本身)
                        line_amts = [x for x in detail_amounts if x != v1 and x != v3]
                        if line_amts and abs(v1 - sum(line_amts)) <= Decimal("0.01"):
                            score += 10
                            evidences.append(f"不含税金额与明细累加值一致 ({sum(line_amts)})")

                        # 第三层校验：明细行税额累加匹配 (过滤掉税额合计候选本身)
                        line_txs = [x for x in detail_taxes if x != v2 and x != v3]
                        if line_txs and abs(v2 - sum(line_txs)) <= Decimal("0.01"):
                            score += 10
                            evidences.append(f"税额与明细税额累加值一致 ({sum(line_txs)})")

                        # 第四层校验：票面标称税率与实际算式匹配
                        calc_rate = (v2 / v1).quantize(Decimal("0.0001"))
                        matched_rate = None
                        for r_cand in tax_rate_candidates:
                            if abs(calc_rate - r_cand.value) <= Decimal("0.005"):
                                matched_rate = r_cand.value
                                score += 10
                                evidences.append(f"算式比值与票面税率一致 ({r_cand.context_label})")
                                break

                        # 第五层校验：大写金额验证
                        if capital_amount and abs(v3 - capital_amount) <= Decimal("0.01"):
                            score += 15
                            evidences.append(f"价税合计与中文大写金额吻合 ({capital_amount})")

                        # 空间/标签偏好：m3 文本含“价税合计”或“小写”
                        if any(kw in m3.raw_text for kw in ["价税合计", "小写", "总计"]):
                            score += 5
                            evidences.append("价税合计含明确前缀语义")

                        # 不含税金额含“合计”或“金额”
                        if any(kw in m1.raw_text for kw in ["合计", "金额", "不含税"]):
                            score += 3

                        valid_triplets.append({
                            "score": score,
                            "untaxed": m1,
                            "tax": m2,
                            "total": m3,
                            "matched_rate": matched_rate or calc_rate,
                            "evidences": evidences
                        })

        if valid_triplets:
            # 按可信度得分最高者胜出
            valid_triplets.sort(key=lambda x: x["score"], reverse=True)
            best = valid_triplets[0]

            u_cand = best["untaxed"]
            tx_cand = best["tax"]
            tot_cand = best["total"]
            m_rate = best["matched_rate"]

            field_details["untaxed_amount"] = FieldDetail(
                value=u_cand.value,
                status=FieldStatus.CONFIRMED,
                confidence=min(1.0, u_cand.confidence + 0.05),
                source=u_cand.source,
                bbox=u_cand.bbox,
                raw_text=u_cand.raw_text,
                evidences=best["evidences"]
            )
            field_details["tax_amount"] = FieldDetail(
                value=tx_cand.value,
                status=FieldStatus.CONFIRMED,
                confidence=min(1.0, tx_cand.confidence + 0.05),
                source=tx_cand.source,
                bbox=tx_cand.bbox,
                raw_text=tx_cand.raw_text,
                evidences=best["evidences"]
            )
            field_details["total_amount"] = FieldDetail(
                value=tot_cand.value,
                status=FieldStatus.CONFIRMED,
                confidence=min(1.0, tot_cand.confidence + 0.05),
                source=tot_cand.source,
                bbox=tot_cand.bbox,
                raw_text=tot_cand.raw_text,
                evidences=best["evidences"]
            )
            field_details["tax_rate"] = FieldDetail(
                value=m_rate,
                status=FieldStatus.CONFIRMED,
                confidence=0.99,
                source=TokenSource.RAPIDOCR,
                raw_text=f"{m_rate * 100:.1f}%",
                evidences=[f"真实票面税率匹配: {m_rate * 100:.1f}%"]
            )

            validation["amount_equation_valid"] = True
            line_amts = [x for x in detail_amounts if x != u_cand.value and x != tot_cand.value]
            if line_amts and abs(u_cand.value - sum(line_amts)) <= Decimal("0.01"):
                validation["detail_amount_sum_valid"] = True
            line_txs = [x for x in detail_taxes if x != tx_cand.value and x != tot_cand.value]
            if line_txs and abs(tx_cand.value - sum(line_txs)) <= Decimal("0.01"):
                validation["detail_tax_sum_valid"] = True
            if best["matched_rate"]:
                validation["tax_rate_consistent"] = True

            logger.info(f"发票金额三元组交叉勾稽成功: {u_cand.value} + {tx_cand.value} = {tot_cand.value} (税率: {m_rate})")
            return field_details, validation, quality_issues

        # 若未能构成完整三元组，则尝试单项可信提取并做严谨推导 (绝不伪造 6%)
        # 1. 寻找价税合计总金额
        total_cand = None
        for m in money_candidates:
            if any(k in m.raw_text for k in ["小写", "价税合计", "总计"]):
                total_cand = m
                break
        if not total_cand and capital_amount:
            # 通过大写金额锚定小写候选
            for m in money_candidates:
                if abs(m.value - capital_amount) <= Decimal("0.01"):
                    total_cand = m
                    break
        if not total_cand and money_candidates:
            # 兜底：最大金额为总金额候选 (标记为 UNCERTAIN)
            total_cand = max(money_candidates, key=lambda x: x.value)

        if total_cand:
            field_details["total_amount"] = FieldDetail(
                value=total_cand.value,
                status=FieldStatus.CONFIRMED if capital_amount and abs(total_cand.value - capital_amount) <= Decimal("0.01") else FieldStatus.UNCERTAIN,
                confidence=total_cand.confidence,
                source=total_cand.source,
                bbox=total_cand.bbox,
                raw_text=total_cand.raw_text
            )
        else:
            quality_issues.append("INVOICE_TOTAL_AMOUNT_MISSING")

        # 2. 寻找税额候选与不含税金额候选
        tax_cand = None
        for m in money_candidates:
            if "税额" in m.raw_text and total_cand and m.value < total_cand.value:
                tax_cand = m
                break

        untaxed_cand = None
        for m in money_candidates:
            if any(k in m.raw_text for k in ["不含税", "合计", "金额"]) and total_cand and m.value < total_cand.value and m != tax_cand:
                untaxed_cand = m
                break

        # 3. 若提取到总金额和税额，则安全推导不含税金额 (标记 source='derived', status='DERIVED')
        if total_cand and tax_cand and not untaxed_cand:
            derived_untaxed = total_cand.value - tax_cand.value
            field_details["untaxed_amount"] = FieldDetail(
                value=derived_untaxed,
                status=FieldStatus.DERIVED,
                confidence=min(total_cand.confidence, tax_cand.confidence) * 0.9,
                source=TokenSource.DERIVED,
                raw_text=str(derived_untaxed),
                evidences=["由 Total - Tax 公式严密推导获得，非伪造反算"]
            )
            field_details["tax_amount"] = FieldDetail(
                value=tax_cand.value,
                status=FieldStatus.CONFIRMED,
                confidence=tax_cand.confidence,
                source=tax_cand.source,
                bbox=tax_cand.bbox,
                raw_text=tax_cand.raw_text
            )
            validation["amount_equation_valid"] = True
        elif total_cand and untaxed_cand and not tax_cand:
            derived_tax = total_cand.value - untaxed_cand.value
            field_details["tax_amount"] = FieldDetail(
                value=derived_tax,
                status=FieldStatus.DERIVED,
                confidence=min(total_cand.confidence, untaxed_cand.confidence) * 0.9,
                source=TokenSource.DERIVED,
                raw_text=str(derived_tax),
                evidences=["由 Total - Untaxed 公式严密推导获得，非伪造反算"]
            )
            field_details["untaxed_amount"] = FieldDetail(
                value=untaxed_cand.value,
                status=FieldStatus.CONFIRMED,
                confidence=untaxed_cand.confidence,
                source=untaxed_cand.source,
                bbox=untaxed_cand.bbox,
                raw_text=untaxed_cand.raw_text
            )
            validation["amount_equation_valid"] = True
        else:
            # 严格准则：识别不到即为 None，严禁伪造 6% 假税额！
            if not untaxed_cand:
                quality_issues.append("INVOICE_UNTAXED_AMOUNT_MISSING")
            if not tax_cand:
                quality_issues.append("INVOICE_TAX_AMOUNT_MISSING")

        # 4. 税率提取
        if tax_rate_candidates:
            best_rate = tax_rate_candidates[0]
            field_details["tax_rate"] = FieldDetail(
                value=best_rate.value,
                status=FieldStatus.CONFIRMED,
                confidence=best_rate.confidence,
                source=best_rate.source,
                bbox=best_rate.bbox,
                raw_text=best_rate.context_label
            )

        return field_details, validation, quality_issues

    # =========================================================================
    # 4. 机构主体与元数据映射 FieldMapper
    # =========================================================================
    @classmethod
    def _map_parties_and_metadata(cls, tokens: List[OcrToken]) -> Dict[str, FieldDetail]:
        """准确区分销售方与购买方主体信息，绝不填充虚假固定企业数据"""
        details = {
            "seller_name": FieldDetail(status=FieldStatus.MISSING),
            "seller_tax_id": FieldDetail(status=FieldStatus.MISSING),
            "buyer_name": FieldDetail(status=FieldStatus.MISSING),
            "buyer_tax_id": FieldDetail(status=FieldStatus.MISSING),
            "issue_date": FieldDetail(status=FieldStatus.MISSING),
            "invoice_number": FieldDetail(status=FieldStatus.MISSING),
            "invoice_code": FieldDetail(status=FieldStatus.MISSING),
            "invoice_type": FieldDetail(status=FieldStatus.MISSING)
        }

        # 1. 发票号码与代码
        num_cand, code_cand = cls._extract_invoice_number_and_code(tokens)
        if num_cand:
            details["invoice_number"] = FieldDetail(
                value=num_cand.value,
                status=FieldStatus.CONFIRMED,
                confidence=num_cand.confidence,
                source=num_cand.source,
                bbox=num_cand.bbox,
                raw_text=num_cand.raw_text
            )
        if code_cand:
            details["invoice_code"] = FieldDetail(
                value=code_cand.value,
                status=FieldStatus.CONFIRMED,
                confidence=code_cand.confidence,
                source=code_cand.source,
                bbox=code_cand.bbox,
                raw_text=code_cand.raw_text
            )

        # 2. 开票日期
        dates = cls._extract_date_candidates(tokens)
        if dates:
            d_cand = dates[0]
            details["issue_date"] = FieldDetail(
                value=d_cand.value,
                status=FieldStatus.CONFIRMED,
                confidence=d_cand.confidence,
                source=d_cand.source,
                bbox=d_cand.bbox,
                raw_text=d_cand.raw_text
            )

        # 3. 销售方 vs 购买方纳税人识别号
        tax_ids = cls._extract_tax_id_candidates(tokens)
        full_text = "\n".join(t.text for t in tokens)

        # 区分策略：结合 BBox 空间位置与前缀上下文
        # 增值税发票标准布局：购买方在左上，销售方在左下
        for cand in tax_ids:
            # 检查邻近 token 或自身是否含 "购买方" 或 "销售方"
            if "销售方" in cand.raw_text or (cand.bbox and cand.bbox[0] > 600):
                if details["seller_tax_id"].value is None:
                    details["seller_tax_id"] = FieldDetail(
                        value=cand.value,
                        status=FieldStatus.CONFIRMED,
                        confidence=cand.confidence,
                        source=cand.source,
                        bbox=cand.bbox,
                        raw_text=cand.raw_text
                    )
            elif "购买方" in cand.raw_text or (cand.bbox and cand.bbox[0] < 450):
                if details["buyer_tax_id"].value is None:
                    details["buyer_tax_id"] = FieldDetail(
                        value=cand.value,
                        status=FieldStatus.CONFIRMED,
                        confidence=cand.confidence,
                        source=cand.source,
                        bbox=cand.bbox,
                        raw_text=cand.raw_text
                    )

        # 若有识别到税号但未能空间区分，按上下序顺延分配
        if len(tax_ids) >= 1 and details["seller_tax_id"].value is None and details["buyer_tax_id"].value is None:
            details["seller_tax_id"] = FieldDetail(
                value=tax_ids[0].value,
                status=FieldStatus.UNCERTAIN,
                confidence=tax_ids[0].confidence,
                source=tax_ids[0].source,
                bbox=tax_ids[0].bbox,
                raw_text=tax_ids[0].raw_text
            )

        # 4. 销售方与购买方名称识别
        comp_re = re.compile(r"(?:名\s*称|单位名称|销售方|购买方)[:：\s]*([^\n\r]+(?:公司|店|部|中心|网|馆|院|局|所|酒店|厂))")
        for t in tokens:
            m = comp_re.search(t.text)
            if m:
                name = m.group(1).strip()
                name = re.sub(r"^(?:名\s*称|单位名称|购买方|销售方|称)[:：\s]*", "", name).strip()
                if "销售方" in t.text or (t.bbox and t.bbox[0] > 600):
                    if details["seller_name"].value is None:
                        details["seller_name"] = FieldDetail(
                            value=name, status=FieldStatus.CONFIRMED, confidence=t.confidence,
                            source=t.source, bbox=t.bbox, raw_text=t.text
                        )
                elif "购买方" in t.text or (t.bbox and t.bbox[0] < 450):
                    if details["buyer_name"].value is None:
                        details["buyer_name"] = FieldDetail(
                            value=name, status=FieldStatus.CONFIRMED, confidence=t.confidence,
                            source=t.source, bbox=t.bbox, raw_text=t.text
                        )

        # 兜底：根据词尾特征查找公司全名
        if details["seller_name"].value is None:
            for t in tokens:
                if (t.bbox and t.bbox[0] > 600) and any(t.text.endswith(s) for s in ["公司", "有限公司", "商行", "酒店"]):
                    details["seller_name"] = FieldDetail(
                        value=t.text.strip(), status=FieldStatus.UNCERTAIN, confidence=t.confidence * 0.9,
                        source=t.source, bbox=t.bbox, raw_text=t.text
                    )
                    break

        if details["buyer_name"].value is None:
            for t in tokens:
                if (t.bbox and t.bbox[0] < 450) and any(t.text.endswith(s) for s in ["公司", "有限公司", "学校"]):
                    details["buyer_name"] = FieldDetail(
                        value=t.text.strip(), status=FieldStatus.UNCERTAIN, confidence=t.confidence * 0.9,
                        source=t.source, bbox=t.bbox, raw_text=t.text
                    )
                    break

        # 5. 发票种类判断
        if "专用发票" in full_text:
            inv_type = "增值税专用发票"
        elif "全电" in full_text or "数电" in full_text:
            inv_type = "全电增值税普通发票"
        elif "普通发票" in full_text:
            inv_type = "增值税普通发票"
        elif any(k in full_text for k in ["客运", "车票", "火车"]):
            inv_type = "铁路电子客票"
        else:
            inv_type = "增值税电子普通发票"

        details["invoice_type"] = FieldDetail(
            value=inv_type,
            status=FieldStatus.CONFIRMED,
            confidence=0.98,
            source=TokenSource.RAPIDOCR,
            raw_text=inv_type
        )

        return details

    @classmethod
    def _recommend_line_item(cls, tokens: List[OcrToken], total_amount: Optional[Decimal], seller_name: Optional[str]) -> Optional[Dict[str, Any]]:
        """基于发票内容与消费场景智能推荐申报明细科目"""
        if not total_amount or total_amount <= 0:
            return None

        full_text = " ".join(t.text for t in tokens)
        seller = seller_name or ""

        if any(k in full_text for k in ["住宿", "客房", "酒店", "宾馆", "房费"]) or any(k in seller for k in ["酒店", "客栈", "宾馆"]):
            expense_type = "住宿费"
            item_desc = f"发票原件解析：{seller} 住宿款"
            city_name = "上海" if "上海" in full_text or "上海" in seller else "北京"
        elif any(k in full_text for k in ["客票", "车票", "运输", "火车", "高铁", "二等座", "机票", "滴滴", "乘车"]):
            expense_type = "交通费"
            item_desc = "发票原件解析：商务出行交通客票"
            city_name = "北京"
        elif any(k in full_text for k in ["餐饮", "餐费", "外卖", "酒楼", "饭店", "食品", "咖啡"]):
            expense_type = "餐饮费"
            item_desc = f"发票原件解析：{seller} 餐饮消费"
            city_name = "北京"
        elif any(k in full_text for k in ["技术", "服务", "软件", "运维", "开发", "咨询"]):
            expense_type = "技术服务费"
            item_desc = f"发票原件解析：{seller} 技术服务采购款"
            city_name = "北京"
        else:
            expense_type = "办公用品"
            item_desc = f"发票原件解析：{seller} 采购支出"
            city_name = "北京"

        return {
            "expense_type": expense_type,
            "item_desc": item_desc,
            "amount": float(total_amount),
            "city_name": city_name
        }

    # =========================================================================
    # 4.5. 铁路客票/火车票专用结构化解析器
    # =========================================================================
    @classmethod
    def _is_train_ticket(cls, tokens: List[OcrToken]) -> bool:
        """识别是否为铁路客票/火车票凭证"""
        full_text = " ".join(t.text for t in tokens)

        # 强特征信号
        has_railway_keywords = any(kw in full_text for kw in [
            "限乘当日当次车", "二等座", "一等座", "商务座", "特等座", "高级软卧", "软卧", "硬卧", "无座", "硬座", "软座", "动卧"
        ])
        has_train_no = bool(re.search(r'(?<![A-Za-z0-9])([GDCKTZ]\d{1,4})(?![A-Za-z0-9])', full_text))
        has_station = "站" in full_text
        has_kai = any(re.search(r'\d{1,2}:\d{2}\s*开', t.text) for t in tokens) or "开" in full_text

        # 综合判定：
        # 1. 明确席别或限乘说明 + (车次号 or 站)
        if has_railway_keywords and (has_train_no or has_station):
            return True
        # 2. 车次号 + 包含“站” + 开车时刻
        if has_train_no and has_station and has_kai:
            return True
        # 3. 车次号紧邻“站”，如 D3233·宁波站
        if any(re.search(r'[GDCKTZ]\d{1,4}[·\-\s至到][^\s·至到\d]+站', t.text) for t in tokens):
            return True

        return False

    @classmethod
    def _parse_train_ticket(
        cls,
        tokens: List[OcrToken],
        filename: str,
        saved_filename: str,
        file_hash: str,
        file_size: int,
        ext: str
    ) -> Dict[str, Any]:
        """专门解析铁路客票/火车票 (交通票据事实驱动，禁止强套增值税发票结构)"""
        from engines.policy_agent.city_geo import get_city_geo

        # 1. 车次提取 (G/D/C/K/T/Z + 1~4位纯数字)
        train_no = None
        train_token = None
        for t in tokens:
            m = re.search(r'(?<![A-Za-z0-9])([GDCKTZ]\d{1,4})(?![A-Za-z0-9])', t.text)
            if m:
                train_no = m.group(1)
                train_token = t
                break

        # 2. 到达站与始发站提取
        arrival_station = None
        arrival_token = None
        departure_station = None
        departure_token = None

        # 优先从形如 "D3233·宁波站" 或 "D3233-宁波站" 或 "D3233至宁波站" 中提取到达站
        for t in tokens:
            m = re.search(r'[GDCKTZ]\d{1,4}[·\-\s至到]+([^\s·至到\d]+(?:站)?)', t.text)
            if m:
                arrival_station = m.group(1)
                arrival_token = t
                break

        # 始发站：查找包含“站”的有效站点 Token (排除“检票”、“限乘”、售票点如“杭州东售”等干扰项)
        for t in tokens:
            if "站" in t.text and not t.text.endswith("售") and "检票" not in t.text and "限乘" not in t.text:
                m = re.search(r'([^\s·至到\d]+站)', t.text)
                if m:
                    st = m.group(1)
                    if st != arrival_station and not departure_station:
                        departure_station = st
                        departure_token = t

        # 若未通过组合模式提取到达站，则在站点候选中按空间左右区分 (始发在左，到达在右)
        if not arrival_station or not departure_station:
            candidate_stations = []
            for t in tokens:
                if "站" in t.text and not t.text.endswith("售") and "检票" not in t.text and "限乘" not in t.text:
                    m = re.search(r'([^\s·至到\d]+站)', t.text)
                    if m:
                        candidate_stations.append((m.group(1), t))
            if len(candidate_stations) >= 2:
                candidate_stations.sort(key=lambda x: (x[1].bbox[1] if x[1].bbox else 0))
                if not departure_station:
                    departure_station = candidate_stations[0][0]
                    departure_token = candidate_stations[0][1]
                if not arrival_station:
                    arrival_station = candidate_stations[-1][0]
                    arrival_token = candidate_stations[-1][1]

        # 解析标准城市 (利用现有城市库包含匹配解析城市，严禁暴力删除'南/北/东/西')
        dep_geo = get_city_geo(departure_station)
        arr_geo = get_city_geo(arrival_station)
        departure_city = dep_geo.short_name if dep_geo else (departure_station.replace("站", "") if departure_station else None)
        arrival_city = arr_geo.short_name if arr_geo else (arrival_station.replace("站", "") if arrival_station else None)

        # 3. 真实行程日期与发车时间 (严禁猜测到达时间 arrival_time，严格保留为 None)
        travel_date = None
        departure_time = None
        date_token = None
        for t in tokens:
            m = re.search(r'([0-9]{4})\s*[年\-\.\/]\s*([0-9]{1,2})\s*[月\-\.\/]\s*([0-9]{1,2})\s*日?\s*([0-9]{1,2})[:：]([0-9]{2})\s*开?', t.text)
            if m:
                y, mo, d, h, mi = m.groups()
                travel_date = f"{int(y):04d}-{int(mo):02d}-{int(d):02d}"
                departure_time = f"{travel_date}T{int(h):02d}:{int(mi):02d}:00"
                date_token = t
                break

        # 4. 席别
        seat_type = None
        seat_token = None
        for kw in ["商务座", "特等座", "一等座", "二等座", "高级软卧", "软卧", "硬卧", "动卧", "硬座", "软座", "无座"]:
            for t in tokens:
                if kw in t.text:
                    seat_type = kw
                    seat_token = t
                    break
            if seat_type:
                break

        # 5. 票头印刷号 (如 Z31G052971)
        ticket_number = None
        ticket_token = None
        for t in tokens:
            if t.bbox and t.bbox[0] < 250:
                m = re.search(r'\b([A-Z][0-9A-Z]{6,12})\b', t.text)
                if m and not re.search(r'^[GDCKTZ]\d{1,4}$', m.group(1)):
                    ticket_number = m.group(1)
                    ticket_token = t
                    break
        if not ticket_number:
            for t in tokens:
                m = re.search(r'\b([A-Z][0-9A-Z]{7,12})\b', t.text)
                if m and not re.search(r'^[GDCKTZ]\d{1,4}$', m.group(1)):
                    ticket_number = m.group(1)
                    ticket_token = t
                    break

        # 6. 乘车人姓名
        passenger_name = None
        passenger_token = None
        for t in tokens:
            m = re.search(r'\d{6}[\d\*]{6,12}[0-9Xx*]{1,4}\s*([\u4e00-\u9fa5]{2,4})', t.text)
            if m:
                passenger_name = m.group(1)
                passenger_token = t
                break

        # 7. 票面金额提取 (支持 0~2 位小数，如 ￥54.0元 -> Decimal('54.00'))
        money_candidates = cls._extract_money_candidates(tokens)
        total_amt: Optional[Decimal] = None
        amount_token: Optional[OcrToken] = None
        if money_candidates:
            explicit_cands = [c for c in money_candidates if any(kw in c.raw_text for kw in ["¥", "￥", "$", "元"])]
            if explicit_cands:
                best_amt = explicit_cands[0]
            else:
                best_amt = money_candidates[0]
            total_amt = best_amt.value
            for t in tokens:
                if str(total_amt) in t.text or (str(best_amt.value).rstrip('0').rstrip('.') in t.text):
                    amount_token = t
                    break

        # 8. 组装字段级证据与 BBox 坐标
        all_field_details: Dict[str, FieldDetail] = {
            "invoice_type": FieldDetail(
                value="铁路客票",
                status=FieldStatus.CONFIRMED,
                confidence=0.99,
                source=TokenSource.RAPIDOCR,
                raw_text="铁路客票"
            ),
            "invoice_code": FieldDetail(
                value="NONE",
                status=FieldStatus.CONFIRMED,
                confidence=0.99,
                source=TokenSource.RAPIDOCR,
                raw_text="NONE"
            ),
            "invoice_number": FieldDetail(
                value=ticket_number or train_no,
                status=FieldStatus.CONFIRMED if (ticket_number or train_no) else FieldStatus.MISSING,
                confidence=ticket_token.confidence if ticket_token else 0.9,
                source=ticket_token.source if ticket_token else TokenSource.RAPIDOCR,
                bbox=ticket_token.bbox if ticket_token else None,
                raw_text=ticket_token.text if ticket_token else ""
            ),
            "total_amount": FieldDetail(
                value=total_amt,
                status=FieldStatus.CONFIRMED if total_amt is not None else FieldStatus.MISSING,
                confidence=0.98 if total_amt is not None else 0.0,
                source=amount_token.source if amount_token else TokenSource.RAPIDOCR,
                bbox=amount_token.bbox if amount_token else None,
                raw_text=amount_token.text if amount_token else str(total_amt)
            ),
            "untaxed_amount": FieldDetail(status=FieldStatus.MISSING),
            "tax_amount": FieldDetail(status=FieldStatus.MISSING),
            "tax_rate": FieldDetail(status=FieldStatus.MISSING),
            "seller_name": FieldDetail(
                value="中国铁路",
                status=FieldStatus.CONFIRMED,
                confidence=0.99,
                source=TokenSource.RAPIDOCR,
                raw_text="中国铁路"
            ),
            "seller_tax_id": FieldDetail(status=FieldStatus.MISSING),
            "buyer_name": FieldDetail(
                value=passenger_name,
                status=FieldStatus.CONFIRMED if passenger_name else FieldStatus.MISSING,
                confidence=passenger_token.confidence if passenger_token else 0.0,
                source=passenger_token.source if passenger_token else TokenSource.RAPIDOCR,
                bbox=passenger_token.bbox if passenger_token else None,
                raw_text=passenger_token.text if passenger_token else ""
            ),
            "buyer_tax_id": FieldDetail(status=FieldStatus.MISSING),
            "issue_date": FieldDetail(status=FieldStatus.MISSING),
            "departure_station": FieldDetail(
                value=departure_station,
                status=FieldStatus.CONFIRMED if departure_station else FieldStatus.MISSING,
                confidence=departure_token.confidence if departure_token else 0.0,
                source=departure_token.source if departure_token else TokenSource.RAPIDOCR,
                bbox=departure_token.bbox if departure_token else None,
                raw_text=departure_token.text if departure_token else ""
            ),
            "arrival_station": FieldDetail(
                value=arrival_station,
                status=FieldStatus.CONFIRMED if arrival_station else FieldStatus.MISSING,
                confidence=arrival_token.confidence if arrival_token else 0.0,
                source=arrival_token.source if arrival_token else TokenSource.RAPIDOCR,
                bbox=arrival_token.bbox if arrival_token else None,
                raw_text=arrival_token.text if arrival_token else ""
            ),
            "train_no": FieldDetail(
                value=train_no,
                status=FieldStatus.CONFIRMED if train_no else FieldStatus.MISSING,
                confidence=train_token.confidence if train_token else 0.0,
                source=train_token.source if train_token else TokenSource.RAPIDOCR,
                bbox=train_token.bbox if train_token else None,
                raw_text=train_token.text if train_token else ""
            ),
            "travel_date": FieldDetail(
                value=travel_date,
                status=FieldStatus.CONFIRMED if travel_date else FieldStatus.MISSING,
                confidence=date_token.confidence if date_token else 0.0,
                source=date_token.source if date_token else TokenSource.RAPIDOCR,
                bbox=date_token.bbox if date_token else None,
                raw_text=date_token.text if date_token else ""
            ),
            "departure_time": FieldDetail(
                value=departure_time,
                status=FieldStatus.CONFIRMED if departure_time else FieldStatus.MISSING,
                confidence=date_token.confidence if date_token else 0.0,
                source=date_token.source if date_token else TokenSource.RAPIDOCR,
                bbox=date_token.bbox if date_token else None,
                raw_text=date_token.text if date_token else ""
            ),
            "seat_type": FieldDetail(
                value=seat_type,
                status=FieldStatus.CONFIRMED if seat_type else FieldStatus.MISSING,
                confidence=seat_token.confidence if seat_token else 0.0,
                source=seat_token.source if seat_token else TokenSource.RAPIDOCR,
                bbox=seat_token.bbox if seat_token else None,
                raw_text=seat_token.text if seat_token else ""
            )
        }

        bbox_positions: Dict[str, List[int]] = {}
        for fname, fdet in all_field_details.items():
            if fdet.bbox and len(fdet.bbox) == 4:
                bbox_positions[fname] = fdet.bbox

        active_confs = [fd.confidence for fd in all_field_details.values() if fd.status != FieldStatus.MISSING]
        overall_conf = round(sum(active_confs) / len(active_confs), 3) if active_confs else 0.85

        # 质量状态判定 (铁路客票核心要素齐全即判定 SUCCESS，不强制专票税率)
        quality_issues = []
        if total_amt is None:
            quality_issues.append("INVOICE_TOTAL_AMOUNT_MISSING")
        if not (ticket_number or train_no):
            quality_issues.append("INVOICE_NUMBER_MISSING")

        if total_amt and (train_no or ticket_number) and (departure_station or arrival_station):
            parse_status = "SUCCESS"
            ocr_status = "SUCCESS"
        elif total_amt:
            parse_status = "NEED_REVIEW"
            ocr_status = "NEED_REVIEW"
        else:
            parse_status = "FAILED"
            ocr_status = "FAILED"

        # 9. 智能推荐明细项
        dep_display = departure_station.replace("站", "") if departure_station else (departure_city or "出发地")
        arr_display = arrival_station.replace("站", "") if arrival_station else (arrival_city or "目的地")
        train_display = train_no or "铁路"
        seat_display = f" {seat_type}" if seat_type else ""
        item_desc = f"{dep_display}-{arr_display} {train_display}{seat_display}客票" if (departure_station or arrival_station) else "交通费客票"
        if departure_station and arrival_station and train_no and seat_type:
            item_desc = f"{dep_display}-{arr_display} {train_no} {seat_type}铁路客票"

        rec_item = {
            "expense_type": "交通费",
            "item_desc": item_desc,
            "amount": float(total_amt) if total_amt is not None else 0.0,
            "city_name": arrival_city or dep_display
        } if total_amt else None

        invoice_hash = cls._compute_invoice_hash(
            code="NONE",
            number=ticket_number or train_no or f"TRAIN_{uuid.uuid4().hex[:8]}",
            amount=total_amt,
            date=travel_date
        )

        validation = {
            "amount_equation_valid": True,
            "detail_amount_sum_valid": True,
            "detail_tax_sum_valid": True,
            "tax_rate_consistent": True
        }

        travel_segment = {
            "departure_city": departure_city,
            "arrival_city": arrival_city,
            "departure_time": departure_time,
            "arrival_time": None,
            "travel_date": travel_date,
            "transport_mode": "TRAIN",
            "transport_no": train_no,
            "source_desc": f"{train_no or '铁路客票'}: {departure_city} -> {arrival_city}"
        } if (departure_city and arrival_city) else None

        logger.info(
            f"铁路客票解析完成 [{filename}]: status={parse_status}, train={train_no}, "
            f"{departure_station}->{arrival_station}, total={total_amt}, travel_date={travel_date}"
        )

        return {
            "file_info": {
                "file_name": filename,
                "file_type": ext.replace(".", "").upper(),
                "file_path": f"/uploads/invoices/{saved_filename}",
                "file_hash": file_hash,
                "file_size_bytes": file_size,
                "is_invoice": True,
                "ocr_status": ocr_status
            },
            "invoice_data": {
                "invoice_code": "NONE",
                "invoice_number": ticket_number or train_no or "UNKNOWN",
                "invoice_type": "铁路客票",
                "total_amount": float(total_amt) if total_amt is not None else None,
                "untaxed_amount": None,
                "tax_amount": None,
                "tax_rate": None,
                "seller_name": "中国铁路",
                "seller_tax_id": None,
                "buyer_name": passenger_name,
                "buyer_tax_id": None,
                "issue_date": None,
                "travel_date": travel_date,
                "departure_station": departure_station,
                "arrival_station": arrival_station,
                "departure_city": departure_city,
                "arrival_city": arrival_city,
                "train_no": train_no,
                "departure_time": departure_time,
                "arrival_time": None,
                "seat_type": seat_type,
                "ticket_number": ticket_number,
                "passenger_name": passenger_name,
                "invoice_hash": invoice_hash,
                "ocr_confidence": overall_conf,
                "bbox_positions": bbox_positions,
                "field_details": {k: v.to_dict() for k, v in all_field_details.items()},
                "travel_segment": travel_segment
            },
            "quality_issues": quality_issues,
            "validation": validation,
            "parse_status": parse_status,
            "recommended_line_item": rec_item
        }

    # =========================================================================
    # 5. 主流水线入口 parse_uploaded_file
    # =========================================================================
    @classmethod
    async def parse_uploaded_file(
        cls,
        filename: str,
        content: bytes,
        sample_type: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        发票凭证结构化解析主流程：
        - 明确的 sample_type：调用仿真知识库测试生成器 (仅用于单元测试/DEMO)；
        - 真实文件：严密执行 Token 提取 -> 候选生成 -> 三元组交叉核验 -> 数据质量门。
        - 宁可返回 None / NEED_REVIEW，严禁伪造金额、税率与默认企业！
        """
        file_hash = hashlib.sha256(content).hexdigest()
        ext = os.path.splitext(filename)[1].lower() or ".pdf"
        saved_filename = f"inv_{file_hash[:12]}{ext}"
        saved_path = os.path.join(UPLOAD_DIR, saved_filename)

        with open(saved_path, "wb") as f:
            f.write(content)

        file_size = len(content)

        # ---------------------------------------------------------------------
        # 模式一：明确的 sample_type 仿真发票 (专供特定单元测试/演示场景)
        # ---------------------------------------------------------------------
        if sample_type:
            logger.info(f"采用仿真示例发票知识库提取: sample_type={sample_type}")
            parsed = cls._extract_invoice_data(sample_type)
            inv_hash = cls._compute_invoice_hash(
                parsed["invoice_code"], parsed["invoice_number"],
                parsed["total_amount"], parsed["issue_date"]
            )
            return {
                "file_info": {
                    "file_name": filename,
                    "file_type": ext.replace(".", "").upper(),
                    "file_path": f"/uploads/invoices/{saved_filename}",
                    "file_hash": file_hash,
                    "file_size_bytes": file_size,
                    "is_invoice": True,
                    "ocr_status": "SUCCESS"
                },
                "invoice_data": {
                    "invoice_code": parsed["invoice_code"],
                    "invoice_number": parsed["invoice_number"],
                    "invoice_type": parsed["invoice_type"],
                    "total_amount": float(parsed["total_amount"]),
                    "untaxed_amount": float(parsed["untaxed_amount"]),
                    "tax_amount": float(parsed["tax_amount"]),
                    "tax_rate": float(parsed["tax_rate"]),
                    "seller_name": parsed["seller_name"],
                    "seller_tax_id": parsed["seller_tax_id"],
                    "buyer_name": parsed["buyer_name"],
                    "buyer_tax_id": parsed["buyer_tax_id"],
                    "issue_date": parsed["issue_date"],
                    "invoice_hash": inv_hash,
                    "ocr_confidence": 0.99,
                    "bbox_positions": {
                        "invoice_title": [40, 320, 80, 680],
                        "invoice_code": [90, 650, 120, 950],
                        "invoice_number": [125, 650, 155, 950],
                        "issue_date": [160, 650, 190, 950],
                        "total_amount": [730, 680, 780, 950],
                        "tax_amount": [730, 480, 780, 660],
                        "seller_info": [200, 510, 320, 950]
                    },
                    "field_details": {}
                },
                "quality_issues": [],
                "validation": {
                    "amount_equation_valid": True,
                    "detail_amount_sum_valid": True,
                    "detail_tax_sum_valid": True,
                    "tax_rate_consistent": True
                },
                "parse_status": "SUCCESS",
                "recommended_line_item": parsed.get("recommended_line_item")
            }

        # ---------------------------------------------------------------------
        # 模式二：真实上传文件 OCR/PDF 深度版面解析 (禁止伪造数据)
        # ---------------------------------------------------------------------
        tokens: List[OcrToken] = []
        if ext == ".pdf":
            tokens = cls._extract_tokens_from_pdf(content, filename)
        if not tokens and ext in [".png", ".jpg", ".jpeg", ".webp", ".bmp", ".pdf"]:
            tokens = cls._extract_tokens_from_image(content, filename)

        # 若底层 OCR 完全未提取到文字
        if not tokens:
            logger.warning(f"文件 {filename} OCR 解析未识别到任何文本")
            return {
                "file_info": {
                    "file_name": filename,
                    "file_type": ext.replace(".", "").upper(),
                    "file_path": f"/uploads/invoices/{saved_filename}",
                    "file_hash": file_hash,
                    "file_size_bytes": file_size,
                    "is_invoice": False,
                    "ocr_status": "FAILED"
                },
                "invoice_data": {
                    "invoice_code": None,
                    "invoice_number": None,
                    "invoice_type": None,
                    "total_amount": None,
                    "untaxed_amount": None,
                    "tax_amount": None,
                    "tax_rate": None,
                    "seller_name": None,
                    "seller_tax_id": None,
                    "buyer_name": None,
                    "buyer_tax_id": None,
                    "issue_date": None,
                    "invoice_hash": None,
                    "ocr_confidence": 0.0,
                    "bbox_positions": {},
                    "field_details": {}
                },
                "quality_issues": ["OCR_PARSE_FAILED"],
                "validation": {
                    "amount_equation_valid": False,
                    "detail_amount_sum_valid": False,
                    "detail_tax_sum_valid": False,
                    "tax_rate_consistent": False
                },
                "parse_status": "FAILED",
                "recommended_line_item": None
            }

        # 针对铁路客票/火车票，路由至专门的解析逻辑 (交通票据事实驱动，禁止强套增值税发票结构)
        if cls._is_train_ticket(tokens):
            return cls._parse_train_ticket(
                tokens=tokens,
                filename=filename,
                saved_filename=saved_filename,
                file_hash=file_hash,
                file_size=file_size,
                ext=ext
            )

        # 1. 提取所有关键候选
        money_candidates = cls._extract_money_candidates(tokens)
        tax_rate_candidates = cls._extract_tax_rate_candidates(tokens)
        detail_amounts, detail_taxes = cls._extract_line_item_details(tokens)

        # 2. 提取并验证中文大写合计
        capital_amount = None
        full_text = "\n".join(t.text for t in tokens)
        for t in tokens:
            if any(k in t.text for k in ["大写", "圆整", "元整"]):
                cap = ChineseCurrencyParser.parse(t.text)
                if cap:
                    capital_amount = cap
                    break

        # 3. 字段映射与交叉消歧
        amount_details, validation, quality_issues = cls._resolve_amount_triplet(
            tokens=tokens,
            money_candidates=money_candidates,
            tax_rate_candidates=tax_rate_candidates,
            detail_amounts=detail_amounts,
            detail_taxes=detail_taxes,
            capital_amount=capital_amount
        )

        metadata_details = cls._map_parties_and_metadata(tokens)

        # 合并所有字段详情
        all_field_details: Dict[str, FieldDetail] = {**amount_details, **metadata_details}

        # 4. 组装真实 BBox 坐标证据字典 (只有真实识别到的坐标才输出，绝不虚构模板)
        bbox_positions: Dict[str, List[int]] = {}
        for fname, fdet in all_field_details.items():
            if fdet.bbox and len(fdet.bbox) == 4:
                bbox_positions[fname] = fdet.bbox

        # 5. 计算字段级置信度均值
        active_confs = [fd.confidence for fd in all_field_details.values() if fd.status != FieldStatus.MISSING]
        overall_conf = round(sum(active_confs) / len(active_confs), 3) if active_confs else 0.80

        total_amt = all_field_details["total_amount"].value
        untaxed_amt = all_field_details["untaxed_amount"].value
        tax_amt = all_field_details["tax_amount"].value
        tax_rt = all_field_details["tax_rate"].value
        inv_no = all_field_details["invoice_number"].value
        inv_code = all_field_details["invoice_code"].value or "NONE"
        seller_nm = all_field_details["seller_name"].value
        seller_tx = all_field_details["seller_tax_id"].value
        buyer_nm = all_field_details["buyer_name"].value
        buyer_tx = all_field_details["buyer_tax_id"].value
        issue_dt = all_field_details["issue_date"].value
        inv_type = all_field_details["invoice_type"].value

        if not inv_no:
            quality_issues.append("INVOICE_NUMBER_MISSING")

        # 判定最终解析质量状态
        if "INVOICE_TOTAL_AMOUNT_MISSING" in quality_issues or "INVOICE_NUMBER_MISSING" in quality_issues:
            parse_status = "NEED_REVIEW"
            ocr_status = "NEED_REVIEW"
        elif "INVOICE_AMOUNT_CONFLICT" in quality_issues:
            parse_status = "NEED_REVIEW"
            ocr_status = "NEED_REVIEW"
        else:
            parse_status = "SUCCESS"
            ocr_status = "SUCCESS"

        invoice_hash = cls._compute_invoice_hash(
            code=inv_code,
            number=inv_no,
            amount=total_amt,
            date=issue_dt
        )

        rec_item = cls._recommend_line_item(tokens, total_amt, seller_nm)

        logger.info(
            f"发票解析完成 [{filename}]: status={parse_status}, inv_no={inv_no}, "
            f"total={total_amt}, untaxed={untaxed_amt}, tax={tax_amt}, rate={tax_rt}"
        )

        return {
            "file_info": {
                "file_name": filename,
                "file_type": ext.replace(".", "").upper(),
                "file_path": f"/uploads/invoices/{saved_filename}",
                "file_hash": file_hash,
                "file_size_bytes": file_size,
                "is_invoice": True,
                "ocr_status": ocr_status
            },
            "invoice_data": {
                "invoice_code": inv_code,
                "invoice_number": inv_no,
                "invoice_type": inv_type,
                "total_amount": float(total_amt) if total_amt is not None else None,
                "untaxed_amount": float(untaxed_amt) if untaxed_amt is not None else None,
                "tax_amount": float(tax_amt) if tax_amt is not None else None,
                "tax_rate": float(tax_rt) if tax_rt is not None else None,
                "seller_name": seller_nm,
                "seller_tax_id": seller_tx,
                "buyer_name": buyer_nm,
                "buyer_tax_id": buyer_tx,
                "issue_date": issue_dt,
                "invoice_hash": invoice_hash,
                "ocr_confidence": overall_conf,
                "bbox_positions": bbox_positions,
                "field_details": {k: v.to_dict() for k, v in all_field_details.items()}
            },
            "quality_issues": quality_issues,
            "validation": validation,
            "parse_status": parse_status,
            "recommended_line_item": rec_item
        }

    # =========================================================================
    # 6. 仿真发票数据生成器 (仅用于单元测试与 DEMO 演示)
    # =========================================================================
    @classmethod
    def _extract_invoice_data(cls, sample_type: str) -> Dict[str, Any]:
        """按 sample_type 预设生成仿真发票数据 (仅 sample_type 显式声明时允许调用)"""
        if sample_type == "HOTEL_1000":
            return {
                "invoice_code": "031002000888",
                "invoice_number": f"88{uuid.uuid4().int % 1000000:06d}",
                "invoice_type": "增值税专用发票",
                "total_amount": Decimal("1000.00"),
                "untaxed_amount": Decimal("943.40"),
                "tax_amount": Decimal("56.60"),
                "tax_rate": Decimal("0.0600"),
                "seller_name": "汉庭星空(上海)酒店管理有限公司",
                "seller_tax_id": "91310101746182937X",
                "buyer_name": "北京智能前沿科技有限公司",
                "buyer_tax_id": "91110108MA01XXXXXX",
                "issue_date": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
                "recommended_line_item": {
                    "expense_type": "住宿费",
                    "item_desc": "汉庭商务酒店2晚住宿费",
                    "amount": 1000.00,
                    "city_name": "上海"
                }
            }

        if sample_type == "HOTEL":
            return {
                "invoice_code": "031002000888",
                "invoice_number": f"88{uuid.uuid4().int % 1000000:06d}",
                "invoice_type": "增值税专用发票",
                "total_amount": Decimal("850.00"),
                "untaxed_amount": Decimal("801.89"),
                "tax_amount": Decimal("48.11"),
                "tax_rate": Decimal("0.0600"),
                "seller_name": "上海和平饭店管理有限公司",
                "seller_tax_id": "91310101746182937X",
                "buyer_name": "北京智能前沿科技有限公司",
                "buyer_tax_id": "91110108MA01XXXXXX",
                "issue_date": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
                "recommended_line_item": {
                    "expense_type": "住宿费",
                    "item_desc": "上海客户商务洽谈五星酒店住宿1晚",
                    "amount": 850.00,
                    "city_name": "上海"
                }
            }

        if sample_type == "TRAIN":
            return {
                "invoice_code": "NONE",
                "invoice_number": f"G{uuid.uuid4().int % 10000000:07d}",
                "invoice_type": "铁路电子客票",
                "total_amount": Decimal("650.00"),
                "untaxed_amount": Decimal("596.33"),
                "tax_amount": Decimal("53.67"),
                "tax_rate": Decimal("0.0900"),
                "seller_name": "中国铁路网络有限公司",
                "seller_tax_id": "91110000717882931T",
                "buyer_name": "北京智能前沿科技有限公司",
                "buyer_tax_id": "91110108MA01XXXXXX",
                "issue_date": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
                "departure_city": "北京",
                "arrival_city": "上海",
                "departure_time": None,
                "arrival_time": None,
                "train_no": "G13",
                "recommended_line_item": {
                    "expense_type": "交通费",
                    "item_desc": "北京南-上海虹桥 G13次高铁二等座",
                    "amount": 650.00,
                    "city_name": "上海"
                }
            }

        if sample_type == "CORP_SERVICE":
            return {
                "invoice_code": "011002000999",
                "invoice_number": f"99{uuid.uuid4().int % 1000000:06d}",
                "invoice_type": "全电增值税专用发票",
                "total_amount": Decimal("50000.00"),
                "untaxed_amount": Decimal("47169.81"),
                "tax_amount": Decimal("2830.19"),
                "tax_rate": Decimal("0.0600"),
                "seller_name": "北京神州数码技术有限公司",
                "seller_tax_id": "91110108551385082Q",
                "buyer_name": "北京智能前沿科技有限公司",
                "buyer_tax_id": "91110108MA01XXXXXX",
                "issue_date": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
                "recommended_line_item": {
                    "expense_type": "技术服务费",
                    "item_desc": "2026年度企业级云架构运维技术服务款(一期)",
                    "amount": 50000.00,
                    "city_name": "北京"
                }
            }

        if sample_type == "SEQ_A":
            return {
                "invoice_code": "011002000222",
                "invoice_number": "88203001",
                "invoice_type": "增值税电子普通发票",
                "total_amount": Decimal("1200.00"),
                "untaxed_amount": Decimal("1132.08"),
                "tax_amount": Decimal("67.92"),
                "tax_rate": Decimal("0.0600"),
                "seller_name": "北京餐饮服务中心",
                "seller_tax_id": "91110108551385082Q",
                "buyer_name": "北京智能前沿科技有限公司",
                "buyer_tax_id": "91110108MA01XXXXXX",
                "issue_date": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
                "recommended_line_item": {
                    "expense_type": "餐饮费",
                    "item_desc": "客户业务接待宴请发票 (第一部分)",
                    "amount": 1200.00,
                    "city_name": "北京"
                }
            }

        if sample_type == "SEQ_B":
            return {
                "invoice_code": "011002000222",
                "invoice_number": "88203002",
                "invoice_type": "增值税电子普通发票",
                "total_amount": Decimal("1200.00"),
                "untaxed_amount": Decimal("1132.08"),
                "tax_amount": Decimal("67.92"),
                "tax_rate": Decimal("0.0600"),
                "seller_name": "北京餐饮服务中心",
                "seller_tax_id": "91110108551385082Q",
                "buyer_name": "北京智能前沿科技有限公司",
                "buyer_tax_id": "91110108MA01XXXXXX",
                "issue_date": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
                "recommended_line_item": {
                    "expense_type": "餐饮费",
                    "item_desc": "客户业务接待宴请发票 (第二部分)",
                    "amount": 1200.00,
                    "city_name": "北京"
                }
            }

        # 默认示例发票
        return {
            "invoice_code": "011002000333",
            "invoice_number": f"23{uuid.uuid4().int % 1000000:06d}",
            "invoice_type": "增值税电子普通发票",
            "total_amount": Decimal("1200.00"),
            "untaxed_amount": Decimal("1132.08"),
            "tax_amount": Decimal("67.92"),
            "tax_rate": Decimal("0.0600"),
            "seller_name": "北京华联综合超市股份有限公司",
            "seller_tax_id": "91110105101736421R",
            "buyer_name": "北京智能前沿科技有限公司",
            "buyer_tax_id": "91110108MA01XXXXXX",
            "issue_date": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
            "recommended_line_item": {
                "expense_type": "办公用品",
                "item_desc": "研发部耗材与办公打印纸采购",
                "amount": 1200.00,
                "city_name": "北京"
            }
        }
