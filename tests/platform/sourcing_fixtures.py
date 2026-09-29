"""Recorded-shape responses for the sourcing platforms (no network)."""

from __future__ import annotations

import json

import httpx

OFFERS_1688 = [
    {"offerId": 1001, "subject": "义齿重衬材料 软衬 假牙修复套装 牙科", "subjectTrans": "Denture reline kit soft liner denture repair set dental",
     "detailUrl": "https://detail.1688.com/offer/1001.html", "imageUrl": "https://cbu01.alicdn.com/1001.jpg",
     "priceInfo": {"price": "12.50"}, "priceRangeList": [{"startQuantity": 2, "price": 12.5}, {"startQuantity": 500, "price": 10.8}],
     "minOrderQuantity": 2, "monthSold": 3200, "repurchaseRate": "38%", "companyName": "Shenzhen Denta Medical Technology Co., Ltd.",
     "sellerOpenId": "s-denta", "province": "广东", "tpYear": 9, "isFactory": True, "tradeAssurance": True, "sellerScore": 4.9,
     "certificateList": [{"name": "ISO 13485"}, {"name": "CE"}]},
    {"offerId": 1002, "subject": "假牙修复套装 义齿修复 临时牙", "subjectTrans": "Denture repair kit temporary tooth",
     "priceInfo": {"price": "6.80"}, "minOrderQuantity": 1000, "monthSold": 150, "companyName": "Yiwu Smile Trading Co., Ltd.",
     "sellerOpenId": "s-yiwu", "province": "浙江", "tpYear": 2, "isFactory": False, "sellerScore": 4.3},
    {"offerId": 1003, "subject": "牙齿模型玩具 钥匙扣 假牙", "subjectTrans": "Tooth toy keychain denture",
     "priceInfo": {"price": "1.20"}, "minOrderQuantity": 10, "companyName": "Toy Factory", "tpYear": 5},
    {"offerId": 1004, "subject": "义齿软衬材料 重衬 假牙", "subjectTrans": "Denture soft reline material",
     "priceInfo": {"price": "25.00"}, "minOrderQuantity": 50, "monthSold": 80, "companyName": "Hangzhou Premium Dental Co., Ltd.",
     "tpYear": 12, "isFactory": True, "tradeAssurance": True, "sellerScore": 5.0,
     "certificateList": [{"name": "ISO 13485"}, {"name": "FDA"}]},
]

ALIBABA_ITEMS = [
    {"num_iid": "A77", "title": "Denture Reline Kit Soft Liner Professional Dental", "price": "1.95", "min_num": 300,
     "company_name": "Shenzhen Denta Medical Technology Co Ltd", "seller_id": "ali-denta", "area": "Guangdong",
     "years": 9, "verified_supplier": "true", "rating": "96", "certifications": "ISO 13485, CE"},
    {"num_iid": "A78", "title": "Pet Denture Toy", "price": "0.50", "min_num": 100, "company_name": "Pet Goods Ltd"},
]

ALIEXPRESS_PRODUCTS = [
    {"product_id": 90001, "product_title": "Denture Reline Kit Soft Denture Repair", "target_sale_price": "4.99",
     "lastest_volume": 812, "shop_id": 777, "evaluate_rate": "95.1%", "product_detail_url": "https://aliexpress.com/item/90001.html"},
]


def transport(calls: list | None = None) -> httpx.MockTransport:
    def handler(req: httpx.Request) -> httpx.Response:
        if calls is not None:
            calls.append(req)
        host = req.url.host
        if host == "gw.open.1688.com":
            assert "_aop_signature" in req.url.params
            if "imageQuery" in req.url.path:
                return httpx.Response(200, json={"result": {"success": True, "result": {"data": OFFERS_1688[:1]}}})
            page = int(json.loads(req.url.params["offerQueryParam"])["beginPage"])
            return httpx.Response(200, json={"result": {"success": True, "result": {"data": OFFERS_1688 if page == 1 else []}}})
        if host == "api-sg.aliexpress.com":
            assert "sign" in req.url.params and req.url.params["sign_method"] == "sha256"
            products = ALIEXPRESS_PRODUCTS if req.url.params["page_no"] == "1" else []
            return httpx.Response(200, json={"aliexpress_affiliate_product_query_response": {"resp_result": {
                "result": {"products": {"product": products}}}}})
        if host == "api.alibaba.test":
            items = ALIBABA_ITEMS if req.url.params["page"] == "1" else []
            return httpx.Response(200, json={"items": {"item": items}})
        return httpx.Response(404, json={})
    return httpx.MockTransport(handler)


ENV = {"DIP_1688_APP_KEY": "k1688", "DIP_1688_APP_SECRET": "s1688",
       "DIP_ALIEXPRESS_APP_KEY": "kae", "DIP_ALIEXPRESS_APP_SECRET": "sae", "DIP_ALIEXPRESS_TRACKING_ID": "t",
       "DIP_ALIBABA_API_KEY": "kali", "DIP_ALIBABA_API_URL": "https://api.alibaba.test/search"}
