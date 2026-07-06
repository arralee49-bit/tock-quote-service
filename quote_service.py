"""
Shioaji 只讀行情中介服務
用途：讓 Google Apps Script 能透過這個網址，抓到永豐金 Shioaji 的即時報價
安全：只做「查詢報價」，完全沒有下單相關程式碼，也不會用到下單權限
"""

import os
import time
from datetime import datetime, timezone, timedelta

from flask import Flask, request, jsonify
import shioaji as sj

app = Flask(__name__)

# 這三組值不寫死在程式碼裡，改用「環境變數」設定（等一下在 Render 後台填）
API_KEY = os.environ.get("SHIOAJI_API_KEY", "")
SECRET_KEY = os.environ.get("SHIOAJI_SECRET_KEY", "")
SERVICE_TOKEN = os.environ.get("SERVICE_TOKEN", "")  # 自訂密鑰，保護這個服務不被陌生人呼叫

TW_TZ = timezone(timedelta(hours=8))
_api = None  # 全域變數，登入一次後重複使用，避免每次查詢都重新登入


def get_api():
    """取得已登入的 Shioaji API 物件，若尚未登入就先登入"""
    global _api
    if _api is None:
        if not API_KEY or not SECRET_KEY:
            raise RuntimeError("尚未設定 SHIOAJI_API_KEY / SHIOAJI_SECRET_KEY 環境變數")
        _api = sj.Shioaji()
        _api.login(api_key=API_KEY, secret_key=SECRET_KEY)
    return _api


def find_contract(api, stock_id, retries=15, delay=1):
    """
    嘗試取得股票的商品資料(Contract)。
    Shioaji 登入後，商品資料是背景慢慢下載的，剛登入時可能還沒下載完成，
    這裡用重試機制等待，最多等 retries * delay 秒（預設最多等15秒）。
    """
    for _ in range(retries):
        try:
            contract = api.Contracts.Stocks[stock_id]
            if contract:
                return contract
        except Exception:
            pass
        time.sleep(delay)
    return None


def check_token():
    """檢查呼叫端有沒有帶正確的密鑰"""
    token = request.args.get("token", "")
    if not token:
        auth_header = request.headers.get("Authorization", "")
        if auth_header.startswith("Bearer "):
            token = auth_header[len("Bearer "):]
    return (not SERVICE_TOKEN) or (token == SERVICE_TOKEN)


@app.route("/health")
def health():
    """健康檢查用，確認服務有沒有活著"""
    return jsonify({
        "status": "ok",
        "time": datetime.now(TW_TZ).strftime("%Y-%m-%d %H:%M:%S")
    })


@app.route("/quotes")
def quotes():
    """
    主要端點：查詢多檔股票即時報價
    呼叫方式：/quotes?ids=2330,2317&token=你的密鑰
    """
    if not check_token():
        return jsonify({"error": "unauthorized，密鑰錯誤或缺少 token"}), 401

    ids_param = request.args.get("ids", "")
    stock_ids = [s.strip() for s in ids_param.split(",") if s.strip()]
    if not stock_ids:
        return jsonify({"error": "缺少 ids 參數，例如 ?ids=2330,2317"}), 400

    try:
        api = get_api()
    except Exception as err:
        return jsonify({"error": "Shioaji 登入失敗：" + str(err)}), 500

    contracts = []
    missing_ids = []
    for sid in stock_ids:
        contract = find_contract(api, sid)
        if contract:
            contracts.append(contract)
        else:
            missing_ids.append(sid)

    if not contracts:
        return jsonify({
            "quotes": [],
            "note": "找不到商品資料，可能是股票代碼錯誤，或 Shioaji 商品資料尚未下載完成，稍後再試一次",
            "missing_ids": missing_ids
        })

    try:
        snapshots = api.snapshots(contracts)
    except Exception as err:
        return jsonify({"error": "抓取報價失敗：" + str(err)}), 500

    now_str = datetime.now(TW_TZ).strftime("%Y-%m-%d %H:%M:%S")
    result = []
    for snap in snapshots:
        result.append({
            "stock_id": getattr(snap, "code", ""),
            "price": getattr(snap, "close", ""),
            "open": getattr(snap, "open", ""),
            "high": getattr(snap, "high", ""),
            "low": getattr(snap, "low", ""),
            "volume": getattr(snap, "total_volume", ""),
            "time": now_str,
            "source": "SHIOAJI"
        })

    return jsonify({"quotes": result})


if __name__ == "__main__":
    # Render 會用環境變數 PORT 告訴我們要監聽哪個 port
    port = int(os.environ.get("PORT", 8787))
    app.run(host="0.0.0.0", port=port)
