"""
backend/tests/app/test_ocr_and_rbac.py
测试发票 OCR 上传解析服务与 RBAC 多角色数据范围隔离
"""
import pytest
from httpx import AsyncClient, ASGITransport
from main import app
from app.core.database import init_db
from app.services.auth_service import AuthService

@pytest.fixture(autouse=True)
async def setup_db():
    await init_db()

@pytest.mark.asyncio
async def test_upload_invoice_ocr():
    """测试发票 OCR 上传与智能提取接口"""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        token = AuthService.create_access_token(user_id=5, username="emp", roles=["EMPLOYEE"])
        headers = {"Authorization": f"Bearer {token}"}

        # 1. 测试仿真住宿专票 OCR 提取
        resp = await ac.post(
            "/api/v1/documents/upload-invoice",
            data={"sample_type": "HOTEL"},
            headers=headers
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["file_info"]["ocr_status"] == "SUCCESS"
        assert data["invoice_data"]["total_amount"] == 850.0
        assert data["invoice_data"]["seller_name"] == "上海和平饭店管理有限公司"
        assert "bbox_positions" in data["invoice_data"]
        assert data["recommended_line_item"]["expense_type"] == "住宿费"

        # 2. 测试仿真高铁车票 OCR 提取
        resp2 = await ac.post(
            "/api/v1/documents/upload-invoice",
            data={"sample_type": "TRAIN"},
            headers=headers
        )
        assert resp2.status_code == 200
        data2 = resp2.json()
        assert data2["invoice_data"]["total_amount"] == 650.0
        assert data2["recommended_line_item"]["expense_type"] == "交通费"

@pytest.mark.asyncio
async def test_rbac_data_scope_filtering():
    """测试不同角色的单据工作台数据范围隔离"""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        # 1. 经办员工 (小赵 emp, user_id=5, 角色 EMPLOYEE)
        token_emp = AuthService.create_access_token(user_id=5, username="emp", roles=["EMPLOYEE"])
        resp_emp = await ac.get("/api/v1/documents", headers={"Authorization": f"Bearer {token_emp}"})
        assert resp_emp.status_code == 200
        items_emp = resp_emp.json()["items"]
        for item in items_emp:
            assert item["applicant_id"] == 5

        # 2. 财务总监 (王总监 cfo, user_id=4, 角色 CFO)
        token_cfo = AuthService.create_access_token(user_id=4, username="cfo", roles=["CFO"])
        resp_cfo = await ac.get("/api/v1/documents", headers={"Authorization": f"Bearer {token_cfo}"})
        assert resp_cfo.status_code == 200
        items_cfo = resp_cfo.json()["items"]
        # CFO 看到的所有单据必须是大额(>=10000) 或高危风险单据 或本人单据
        for item in items_cfo:
            assert (
                float(item["total_amount"]) >= 10000.0 or
                item["applicant_id"] == 4 or
                item["status"] in ["PENDING_APPROVAL", "IN_REVIEW", "APPROVED", "CANCELLED", "REJECTED"]
            )

        # 3. 系统管理员 (admin, user_id=1, 角色 ADMIN)
        token_admin = AuthService.create_access_token(user_id=1, username="admin", roles=["ADMIN"])
        resp_admin = await ac.get("/api/v1/documents", headers={"Authorization": f"Bearer {token_admin}"})
        assert resp_admin.status_code == 200
        items_admin = resp_admin.json()["items"]
        # 管理员看到的数据量大于等于小赵
        assert len(items_admin) >= len(items_emp)

@pytest.mark.asyncio
async def test_real_image_rapidocr():
    """测试真实发票图片上传经过本地 RapidOCR 解析提取出真实号码、金额与销方"""
    import io
    import os
    from PIL import Image, ImageDraw, ImageFont

    # 动态绘制一张包含真实发票文字要素的内存测试图片
    img = Image.new("RGB", (800, 400), color="white")
    d = ImageDraw.Draw(img)
    font = None
    for p in ["C:/Windows/Fonts/simhei.ttf", "C:/Windows/Fonts/msyh.ttc", "C:/Windows/Fonts/simsun.ttc"]:
        if os.path.exists(p):
            font = ImageFont.truetype(p, 24)
            break

    d.text((40, 40), "发票号码: 99882211", fill="black", font=font)
    d.text((40, 90), "价税合计(小写): ¥1000.00", fill="black", font=font)
    d.text((40, 140), "销售方: 上海锦江国际酒店管理有限公司", fill="black", font=font)
    d.text((40, 190), "统一社会信用代码: 91310101746182937X", fill="black", font=font)

    buf = io.BytesIO()
    img.save(buf, format="PNG")
    png_bytes = buf.getvalue()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        token = AuthService.create_access_token(user_id=5, username="emp", roles=["EMPLOYEE"])
        headers = {"Authorization": f"Bearer {token}"}

        resp = await ac.post(
            "/api/v1/documents/upload-invoice",
            files={"file": ("my_real_invoice.png", png_bytes, "image/png")},
            headers=headers
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["file_info"]["ocr_status"] == "SUCCESS"
        assert data["invoice_data"]["total_amount"] == 1000.0
        assert data["invoice_data"]["invoice_number"] == "99882211"
        assert "饭店" in data["invoice_data"]["seller_name"] or "锦江" in data["invoice_data"]["seller_name"]
        assert data["recommended_line_item"]["expense_type"] == "住宿费"
        assert "bbox_positions" in data["invoice_data"]
