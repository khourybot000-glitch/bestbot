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

# --- 1. Flask Server لفتح الـ Port ---
app = Flask(__name__)

@app.route('/')
def home():
    return "EUR/JPY Engine: ACTIVE | Port 8080 Open", 200

def run_flask():
    # سيقوم بجلب الـ Port من إعدادات السيرفر أو استخدام 8080 كافتراضي
    port = int(os.environ.get("PORT", 8080))
    app.run(host='0.0.0.0', port=port)

# --- 2. الإعدادات ---
TOKEN = '8511172742:AAFxZIj8N07FB-tFnJ_l3rv13loyRMmsRYU'
CHAT_ID = '-1003731752986'
BEIRUT_TZ = pytz.timezone('Asia/Beirut')
WS_URL = "wss://blue.derivws.com/websockets/v3?app_id=16929"
SYMBOL_ID = "frxEURJPY"
SYMBOL_NAME = "EUR/JPY"

active_trade = None
last_signal_time = ""

def send_telegram_msg(text):
    url = f"https://api.telegram.org/bot{TOKEN}/sendMessage"
    payload = {"chat_id": CHAT_ID, "text": text, "parse_mode": "Markdown"}
    try: requests.post(url, json=payload, timeout=10)
    except: pass

# --- 3. محرك الـ 20 مؤشر ---
def calculate_logic(prices):
    if len(prices) < 50: return "NONE", 0
    df = pd.DataFrame(prices, columns=['close'])
    up_votes = 0
    c = df['close']

    # تحليل الاتجاه والزخم (20 نقطة)
    if c.iloc[-1] > c.rolling(10).mean().iloc[-1]: up_votes += 1
    if c.iloc[-1] > c.rolling(20).mean().iloc[-1]: up_votes += 1
    if c.rolling(5).mean().iloc[-1] > c.rolling(15).mean().iloc[-1]: up_votes += 1
    if c.iloc[-1] > c.rolling(50).mean().iloc[-1]: up_votes += 1
    
    # RSI محاكي
    diff = c.diff()
    gain = diff.where(diff > 0, 0).rolling(14).mean()
    loss = diff.where(diff < 0, 0).abs().rolling(14).mean()
    rsi = 100 - (100 / (1 + (gain / loss)))
    if rsi.iloc[-1] > 50: up_votes += 1
    
    if (c.iloc[-1] - c.iloc[-10]) > 0: up_votes += 1
    
    for p in range(5, 18): # تكملة الـ 20 تصويت
        if c.iloc[-1] > c.rolling(p).mean().iloc[-1]: up_votes += 1

    accuracy = (max(up_votes, 20 - up_votes) / 20) * 100
    direction = "CALL" if up_votes >= 10 else "PUT"
    return direction, accuracy

# --- 4. التحقق من النتيجة (12:05 -> 12:10) ---
def verify_result(entry_time, exit_time):
    try:
        ws = websocket.create_connection(WS_URL, timeout=15)
        ws.send(json.dumps({"ticks_history": SYMBOL_ID, "count": 320, "end": int(exit_time.timestamp()) + 2, "style": "ticks"}))
        res = json.loads(ws.recv()); ws.close()
        
        prices, times = res['history']['prices'], res['history']['times']
        p_open, p_close = None, None
        
        for i in range(len(times)):
            if times[i] >= int(entry_time.timestamp()) and p_open is None: p_open = prices[i]
            if times[i] >= int(exit_time.timestamp()) and p_close is None: p_close = prices[i]
        
        return p_open, p_close
    except: return None, None

# --- 5. المحرك الرئيسي ---
def start_engine():
    global active_trade, last_signal_time
    print(f"Engine LIVE | Port Open: 8080 | {SYMBOL_NAME}")
    
    while True:
        now = datetime.now(BEIRUT_TZ)
        
        # التحليل عند الثانية 30
        if now.second == 30 and active_trade is None:
            current_min = now.strftime('%H:%M')
            if last_signal_time != current_min:
                last_signal_time = current_min
                try:
                    ws = websocket.create_connection(WS_URL, timeout=10)
                    ws.send(json.dumps({"ticks_history": SYMBOL_ID, "count": 100, "style": "ticks"}))
                    res = json.loads(ws.recv()); ws.close()
                    
                    direction, accuracy = calculate_logic(res['history']['prices'])
                    
                    if accuracy >= 70:
                        entry_t = (now + timedelta(seconds=30)).replace(second=0, microsecond=0)
                        exit_t = entry_t + timedelta(minutes=5)
                        active_trade = {"dir": direction, "entry": entry_t, "exit": exit_t}
                        
                        send_telegram_msg(f"🚀 **SIGNAL**: {SYMBOL_NAME}\nDir: {direction}\nAcc: {accuracy}%\nEntry: {entry_t.strftime('%H:%M:00')}\nExit: {exit_t.strftime('%H:%M:00')}")
                except: pass

        # التحقق من النتيجة بعد 5 ثوانٍ من وقت الإغلاق
        if active_trade and now >= active_trade['exit'] + timedelta(seconds=5):
            p_open, p_close = verify_result(active_trade['entry'], active_trade['exit'])
            if p_open and p_close:
                win = (p_close > p_open) if active_trade['dir'] == "CALL" else (p_close < p_open)
                status = "✅ **WIN**" if win else "❌ **LOSS**"
                send_telegram_msg(f"{status} | {SYMBOL_NAME}\nEntry: {p_open} | Exit: {p_close}")
            active_trade = None 

        time.sleep(0.5)

if __name__ == "__main__":
    # تشغيل Flask في Thread منفصل لفتح الـ Port
    threading.Thread(target=run_flask, daemon=True).start()
    # تشغيل محرك التداول
    start_engine()
