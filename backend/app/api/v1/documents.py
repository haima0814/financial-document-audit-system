"""
backend/app/api/v1/documents.py
财务单据管理、草稿编辑与智能审核提交接口
"""
from typing import Optional, List, Dict, Any
from fastapi import APIRouter, Depends, HTTPException, Query, status, UploadFile, File, Form
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.schemas.document import (
    FinancialDocumentCreateReq,
    FinancialDocumentUpdateReq,
    FinancialDocumentDetailOut,
    FinancialDocumentListItemOut,
    DocumentCancelReq,
    DocumentVersionOut,
)
from app.schemas.auth import TokenPayload
from app.services.auth_service import get_current_user
from app.services.document_service import DocumentService, DocumentNotFoundError, DocumentStateConflictError
from app.services.ocr_service import InvoiceOcrService

router = APIRouter(prefix="/documents", tags=["财务单据"])

@router.post("/upload-invoice", summary="上传发票并执行 OCR 智能版面结构化解析")
async def upload_invoice(
    file: Optional[UploadFile] = File(None),
    sample_type: Optional[str] = Form(None),
    current_user: TokenPayload = Depends(get_current_user),
):
    """
    发票凭证 OCR 智能解析入口：
    - 支持真实发票图片/PDF文件上传与解析；
    - 支持 sample_type 仿真示例发票快速提取 (HOTEL, TRAIN, CORP_SERVICE, SEQ_A, SEQ_B)；
    - 结构化提取代码、号码、金额、税号并生成归一化 BBox 视框坐标与自动填表明细项。
    """
    if file and file.filename:
        content = await file.read()
        filename = file.filename
    else:
        sample_type = sample_type or "HOTEL"
        filename = f"sample_{sample_type.lower()}_invoice.pdf"
        content = b"%PDF-1.4 Mock Invoice Content For Financial Risk Audit..."

    result = await InvoiceOcrService.parse_uploaded_file(
        filename=filename,
        content=content,
        sample_type=sample_type
    )
    return result

@router.post("", response_model=FinancialDocumentDetailOut, summary="创建财务单据草稿")
async def create_document(
    req: FinancialDocumentCreateReq,
    current_user: TokenPayload = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    service = DocumentService(db)
    try:
        doc = await service.create_document(applicant_id=current_user.user_id, req=req)
        # 重新带子表载入详情
        detail = await service.get_document_detail(doc.id)
        return detail
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))

@router.get("", summary="分页获取单据列表")
async def list_documents(
    status_filter: Optional[str] = Query(None, alias="status", description="单据状态: DRAFT, SUBMITTED, PENDING_APPROVAL, APPROVED, REJECTED, CANCELLED"),
    doc_type: Optional[str] = Query(None, alias="document_type", description="单据类型"),
    only_mine: bool = Query(False, description="是否仅查询我提交的"),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    current_user: TokenPayload = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    service = DocumentService(db)
    applicant_id = current_user.user_id if only_mine else None
    items, total = await service.list_documents(
        current_user=current_user,
        applicant_id=applicant_id,
        status=status_filter,
        document_type=doc_type,
        page=page,
        page_size=page_size
    )


    return {
        "total": total,
        "page": page,
        "page_size": page_size,
        "items": [
            FinancialDocumentListItemOut.model_validate(item) for item in items
        ]
    }

@router.get("/{document_id}", response_model=FinancialDocumentDetailOut, summary="获取单据全量详情")
async def get_document_detail(
    document_id: int,
    current_user: TokenPayload = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    service = DocumentService(db)
    doc = await service.get_document_detail(document_id)
    if not doc:
        raise HTTPException(status_code=404, detail="单据不存在")
    return doc

@router.post("/{document_id}/submit", summary="提交单据并启动多 Agent 风险审查")
async def submit_document(
    document_id: int,
    current_user: TokenPayload = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    service = DocumentService(db)
    try:
        res = await service.submit_document(document_id=document_id, user_id=current_user.user_id)
        return res
    except DocumentNotFoundError as ne:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(ne))
    except DocumentStateConflictError as ce:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(ce))
    except PermissionError as pe:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(pe))
    except ValueError as ve:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(ve))

@router.post("/{document_id}/cancel", summary="经办人或管理员撤回单据")
async def cancel_document(
    document_id: int,
    req: Optional[DocumentCancelReq] = None,
    current_user: TokenPayload = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    service = DocumentService(db)
    is_admin = "ADMIN" in (current_user.roles or [])
    reason = req.reason if req else "经办人主动撤回单据"
    try:
        doc = await service.cancel_document(
            document_id=document_id,
            user_id=current_user.user_id,
            reason=reason,
            is_admin=is_admin
        )
        return {"message": "单据已成功撤回", "document_id": doc.id, "status": doc.status}
    except PermissionError as pe:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(pe))
    except ValueError as ve:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(ve))

@router.get("/{document_id}/versions", response_model=List[DocumentVersionOut], summary="查询单据全量历史不可变版本快照")
async def get_document_versions(
    document_id: int,
    current_user: TokenPayload = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    service = DocumentService(db)
    versions = await service.list_document_versions(document_id)
    return versions

@router.put("/{document_id}", response_model=FinancialDocumentDetailOut, summary="编辑草稿或驳回单据")
async def update_document(
    document_id: int,
    req: FinancialDocumentUpdateReq,
    current_user: TokenPayload = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    service = DocumentService(db)
    is_admin = "ADMIN" in (current_user.roles or [])
    try:
        await service.update_document(
            document_id=document_id,
            user_id=current_user.user_id,
            req=req,
            is_admin=is_admin
        )
        return await service.get_document_detail(document_id)
    except PermissionError as pe:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(pe))
    except ValueError as ve:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(ve))

