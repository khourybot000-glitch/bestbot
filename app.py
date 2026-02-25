import websocket
import json
import pandas as pd
import numpy as np
import time
import requests
from datetime import datetime, timedelta
import pytz
import threading
from flask import Flask
import os

# --- 1. Flask Server for Uptime ---
app = Flask(__name__)
@app.route('/')
def home(): return "Fibo 24/7 Bot: ACTIVE", 200

def run_flask():
    port = int(os.environ.get("PORT", 8080))
    app.run(host='0.0.0.0', port=port)

# --- 2. Configuration ---
TOKEN = '8511172742:AAFxZIj8N07FB-tFnJ_l3rv13loyRMmsRYU'
CHAT_ID = '-1003731752986'
BEIRUT_TZ = pytz.timezone('Asia/Beirut')
WS_URL = "wss://blue.derivws.com/websockets/v3?app_id=16929"
SYMBOL_ID = "frxEURJPY"
SYMBOL_NAME = "EUR/JPY"

active_trade = None
last_analysis_time = ""

def send_telegram_msg(text):
    url = f"https://api.telegram.org/bot{TOKEN}/sendMessage"
    payload = {"chat_id": CHAT_ID, "text": text, "parse_mode": "Markdown"}
    try: requests.post(url, json=payload, timeout=10)
    except: pass

# --- 3. Stable Data Engine ---
def get_market_analysis():
    """Fetches ticks for last 100 mins with a safety limit of 3000 ticks"""
    try:
        now_ts = int(time.time())
        start_ts = now_ts - (100 * 60) 
        
        ws = websocket.create_connection(WS_URL, timeout=12)
        ws.send(json.dumps({
            "ticks_history": SYMBOL_ID,
            "start": start_ts,
            "end": "latest",
            "style": "ticks",
            "count": 3000 
        }))
        res = json.loads(ws.recv()); ws.close()
        
        if 'history' not in res: return None, 0, 0, {}

        prices = [float(p) for p in res['history']['prices']]
        if len(prices) < 10: return None, 0, 0, {}

        h, l = max(prices), min(prices)
        diff = h - l
        levels = {
            "61.8%": round(h - (diff * 0.618), 3),
            "50.0%": round(h - (diff * 0.500), 3),
            "38.2%": round(h - (diff * 0.382), 3)
        }
        
        return levels, prices[-1], prices[-2], levels
    except Exception as e:
        print(f"Data Error: {e}")
        return None, 0, 0, {}

# --- 4. Main Execution Loop ---
def start_engine():
    global last_analysis_time, active_trade
    send_telegram_msg("🚀 **Bot Deployment Successful**\n24/7 Monitoring Mode Activated.\nReporting every 1 minute.")

    while True:
        now = datetime.now(BEIRUT_TZ)
        
        # العمل في أي وقت (بدون شرط الساعة 9)
        if now.second == 0 and active_trade is None:
            current_min = now.strftime('%H:%M')
            if last_analysis_time != current_min:
                last_analysis_time = current_min
                
                fibo_lvls, current_p, last_p, all_lvls = get_market_analysis()
                
                if fibo_lvls:
                    direction, lvl_name = "NONE", ""
                    for name, val in fibo_lvls.items():
                        if last_p < val and current_p > val: direction, lvl_name = "CALL", name; break
                        if last_p > val and current_p < val: direction, lvl_name = "PUT", name; break
                    
                    # --- بناء تقرير الدقيقة الشامل ---
                    report = f"🔍 **Analysis {current_min}**\n"
                    report += f"Current Price: `{current_p}`\n"
                    report += "--- Fibo Levels ---\n"
                    for n, v in all_lvls.items():
                        report += f"{n}: `{v}`\n"
                    
                    if direction != "NONE":
                        entry_t = now + timedelta(minutes=2)
                        active_trade = {"dir": direction, "time": entry_t, "level": lvl_name}
                        report += f"\n🚨 **SIGNAL FOUND: {direction}**\nLevel: `{lvl_name}`\nEntry: {entry_t.strftime('%H:%M:00')}"
                        send_telegram_msg(report)
                    else:
                        report += "\nℹ️ **Result:** `No Signal`"
                        send_telegram_msg(report)
                else:
                    # في حال فشل جلب البيانات
                    send_telegram_msg(f"⚠️ **Warning {current_min}**: Failed to fetch market data.")

        # التحقق من النتيجة (إذا كانت هناك صفقة نشطة)
        if active_trade:
            target_close = active_trade['time'] + timedelta(minutes=1)
            if now >= target_close + timedelta(seconds=2):
                # يمكنك إضافة كود التحقق من النتيجة هنا
                active_trade = None

        time.sleep(0.5)

if __name__ == "__main__":
    threading.Thread(target=run_flask, daemon=True).start()
    start_engine()
