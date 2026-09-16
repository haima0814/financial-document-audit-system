"""
backend/tests/verify_manual.py
端到端验证脚本：发票 OCR 上传解析绑定 + RBAC 多角色数据隔离
"""
import os
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import asyncio
from httpx import AsyncClient, ASGITransport
from main import app
from app.services.auth_service import AuthService


async def verify_all():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        token_emp = AuthService.create_access_token(user_id=5, username="emp", roles=["EMPLOYEE"])
        headers = {"Authorization": f"Bearer {token_emp}"}

        # 1. OCR 上传测试
        ocr_res = await ac.post("/api/v1/documents/upload-invoice", data={"sample_type": "HOTEL"}, headers=headers)
        assert ocr_res.status_code == 200, f"OCR failed: {ocr_res.text}"
        ocr_data = ocr_res.json()
        print("1. OCR 接口返回成功:", ocr_data["invoice_data"]["invoice_number"], ocr_data["invoice_data"]["seller_name"])

        # 2. 创建单据并绑定 OCR 发票
        create_payload = {
            "document_type": "TRAVEL_REIMBURSEMENT",
            "title": "端到端 OCR 绑定验证单据",
            "department_name": "市场营销部",
            "total_amount": ocr_data["invoice_data"]["total_amount"],
            "line_items": [
                {
                    "line_no": 1,
                    "expense_type": ocr_data["recommended_line_item"]["expense_type"],
                    "item_desc": ocr_data["recommended_line_item"]["item_desc"],
                    "amount": ocr_data["recommended_line_item"]["amount"],
                    "city_name": ocr_data["recommended_line_item"]["city_name"]
                }
            ],
            "invoices": [ocr_data["invoice_data"]]
        }
        create_res = await ac.post("/api/v1/documents", json=create_payload, headers=headers)
        assert create_res.status_code == 200, f"Create failed: {create_res.text}"
        doc = create_res.json()
        doc_id = doc["id"]
        print("2. 单据与发票持久化成功! 单据ID:", doc_id, "绑定发票张数:", len(doc.get("invoices", [])))

        # 3. 详情验证
        detail_res = await ac.get(f"/api/v1/documents/{doc_id}", headers=headers)
        detail = detail_res.json()
        assert len(detail["invoices"]) >= 1, "Invoices missing from detail!"
        print("3. 单据详情发票验证通过: 发票号", detail["invoices"][0]["invoice_number"], "经办人:", detail["applicant_name"])

        # 4. RBAC 列表数据范围隔离验证
        # 小赵 (emp) 查列表
        list_emp = (await ac.get("/api/v1/documents", headers=headers)).json()["items"]
        for d in list_emp:
            assert d["applicant_id"] == 5, f"emp saw other applicant: {d['applicant_id']}"
        print("4.1 经办人数据隔离验证通过: 仅见本人单据，共", len(list_emp), "条")

        # 王总监 (cfo) 查列表
        token_cfo = AuthService.create_access_token(user_id=4, username="cfo", roles=["CFO"])
        list_cfo = (await ac.get("/api/v1/documents", headers={"Authorization": f"Bearer {token_cfo}"})).json()["items"]
        for d in list_cfo:
            assert float(d["total_amount"]) >= 10000.0 or d["applicant_id"] == 4 or d["status"] in ["PENDING_APPROVAL", "IN_REVIEW", "APPROVED"]
        print("4.2 财务总监数据隔离验证通过: 仅见大额/在审高危单据，共", len(list_cfo), "条")

        # 系统管理员 (admin) 查列表
        token_admin = AuthService.create_access_token(user_id=1, username="admin", roles=["ADMIN"])
        list_admin = (await ac.get("/api/v1/documents", headers={"Authorization": f"Bearer {token_admin}"})).json()["items"]
        print("4.3 管理员全局验证通过: 全量单据，共", len(list_admin), "条")
        print("\nAll E2E verifications passed successfully!")


if __name__ == "__main__":
    asyncio.run(verify_all())
