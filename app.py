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

# --- 1. Flask Server ---
app = Flask(__name__)
@app.route('/')
def home(): 
    return "Fibo-Contra Bot: ACTIVE | Stable Mode", 200

def run_flask():
    port = int(os.environ.get("PORT", 8080))
    app.run(host='0.0.0.0', port=port)

# --- 2. Configuration ---
TOKEN = '8511172742:AAFxZIj8N07FB-tFnJ_l3rv13loyRMmsRYU'
CHAT_ID = '-1003731752986'
BEIRUT_TZ = pytz.timezone('Asia/Beirut')
WS_URL = "wss://blue.derivws.com/websockets/v3?app_id=16929"
SYMBOL_NAME = "EUR/JPY"
SYMBOL_ID = "frxEURJPY"

active_trade = None
last_signal_time = ""

def send_telegram_msg(text):
    url = f"https://api.telegram.org/bot{TOKEN}/sendMessage"
    payload = {"chat_id": CHAT_ID, "text": text, "parse_mode": "Markdown"}
    try: requests.post(url, json=payload, timeout=10)
    except: pass

# --- 3. Fibonacci & Market Logic ---
def get_market_data_and_fibo():
    """جلب 2000 تيك فقط لضمان استقرار الاتصال وحساب المستويات"""
    try:
        ws = websocket.create_connection(WS_URL, timeout=12)
        ws.send(json.dumps({"ticks_history": SYMBOL_ID, "count": 2000, "style": "ticks"}))
        res = json.loads(ws.recv()); ws.close()
        
        prices = [float(p) for p in res['history']['prices']]
        h, l = max(prices), min(prices)
        diff = h - l
        
        # حساب مستويات فيبوناتشي الأساسية
        levels = {
            "61.8%": h - (diff * 0.618),
            "50.0%": h - (diff * 0.500),
            "38.2%": h - (diff * 0.382)
        }
        return prices, levels
    except Exception as e:
        print(f"Data Error: {e}")
        return None, None

def calculate_logic(prices, levels):
    if not prices or not levels: return "NONE", ""
    
    current_p = prices[-1]
    last_p = prices[-2]
    
    # فحص الاختراق (Breakout) لأي مستوى فيبوناتشي
    for name, val in levels.items():
        # اختراق صاعد (شمعة اخترقت المستوى للأعلى)
        if last_p < val and current_p > val:
            return "CALL", name
        # اختراق هابط (شمعة اخترقت المستوى للأسفل)
        if last_p > val and current_p < val:
            return "PUT", name
            
    return "NONE", ""

# --- 4. Result Verification ---
def verify_result(o_ts, c_ts):
    try:
        ws = websocket.create_connection(WS_URL, timeout=15)
        ws.send(json.dumps({"ticks_history": SYMBOL_ID, "count": 100, "end": "latest", "style": "ticks"}))
        res = json.loads(ws.recv()); ws.close()
        prices, times = res['history']['prices'], res['history']['times']
        p_open, p_close = None, None
        for i in range(len(times)):
            if times[i] >= o_ts and p_open is None: p_open = float(prices[i])
            if times[i] >= c_ts and p_close is None: p_close = float(prices[i])
        return p_open, p_close
    except: return None, None

# --- 5. Main Loop ---
def start_engine():
    global last_signal_time, active_trade
    print(f"Fibo Engine Active | {SYMBOL_NAME}")
    send_telegram_msg("🚀 **Fibo Bot Started**\nStable 2000-Tick Mode Enabled.")
    
    while True:
        now = datetime.now(BEIRUT_TZ)
        # جدول العمل: الاثنين-الجمعة، 9 صباحاً - 9 مساءً
        if (0 <= now.weekday() <= 4) and (9 <= now.hour < 21):
            
            # التحليل عند الثانية 00 من كل دقيقة
            if now.second == 0 and active_trade is None:
                current_min = now.strftime('%H:%M')
                if last_signal_time != current_min:
                    last_signal_time = current_min
                    
                    prices, fibo_levels = get_market_data_and_fibo()
                    direction, lvl_name = calculate_logic(prices, fibo_levels)
                    
                    if direction != "NONE":
                        # الدخول بعد دقيقتين كما طلبت سابقاً
                        entry_t = now + timedelta(minutes=2)
                        active_trade = {"dir": direction, "time": entry_t, "level": lvl_name}
                        
                        send_telegram_msg(f"🚨 **FIBO SIGNAL**\nLevel: `{lvl_name}`\nDir: *{direction}*\nEntry: {entry_t.strftime('%H:%M:00')}")
                    else:
                        print(f"[{current_min}] No Fibo Breakout.")

            # التحقق من النتيجة (نفس نظام كودك الناجح)
            if active_trade:
                target_close = active_trade['time'] + timedelta(minutes=1)
                if now >= target_close + timedelta(seconds=2):
                    o_ts, c_ts = int(active_trade['time'].timestamp()), int(target_close.timestamp())
                    p_in, p_out = verify_result(o_ts, c_ts)
                    if p_in and p_out:
                        win = (p_out > p_in) if active_trade['dir'] == "CALL" else (p_out < p_in)
                        status = "✅ **WIN**" if win else "❌ **LOSS**"
                        send_telegram_msg(f"{status} | {active_trade['level']}\nIn: {p_in} | Out: {p_out}")
                    active_trade = None
        
        time.sleep(0.5)

if __name__ == "__main__":
    threading.Thread(target=run_flask, daemon=True).start()
    start_engine()
