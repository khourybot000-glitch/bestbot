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
def home(): return "Fibo Smart Bot: ACTIVE", 200

# --- 2. Configuration ---
TOKEN = '8511172742:AAFxZIj8N07FB-tFnJ_l3rv13loyRMmsRYU'
CHAT_ID = '-1003731752986'
BEIRUT_TZ = pytz.timezone('Asia/Beirut')
WS_URL = "wss://blue.derivws.com/websockets/v3?app_id=16929"
SYMBOL_ID = "frxEURJPY"
SYMBOL_NAME = "EUR/JPY"

active_trade = None
last_check_min = ""

def send_telegram_msg(text):
    url = f"https://api.telegram.org/bot{TOKEN}/sendMessage"
    payload = {"chat_id": CHAT_ID, "text": text, "parse_mode": "Markdown"}
    try: requests.post(url, json=payload, timeout=10)
    except: pass

# --- 3. Result Verification Logic ---
def verify_trade_result(entry_time, direction):
    """يجلب تيكات دقيقة الصفقة ويقارن سعر الافتتاح بسعر الإغلاق"""
    try:
        start_ts = int(entry_time.timestamp())
        end_ts = start_ts + 60 # مدة الصفقة دقيقة واحدة
        
        ws = websocket.create_connection(WS_URL, timeout=15)
        ws.send(json.dumps({
            "ticks_history": SYMBOL_ID,
            "start": start_ts,
            "end": end_ts,
            "style": "ticks"
        }))
        res = json.loads(ws.recv()); ws.close()
        
        prices = res['history']['prices']
        if len(prices) < 2: return "Unknown"
        
        p_in = float(prices[0])   # سعر أول تيك في دقيقة الدخول
        p_out = float(prices[-1]) # سعر آخر تيك في دقيقة الدخول
        
        win = (p_out > p_in) if direction == "CALL" else (p_out < p_in)
        status = "✅ WIN" if win else "❌ LOSS"
        return f"{status} (In: {p_in} | Out: {p_out})"
    except: return "Error verifying"

# --- 4. Core Strategy Logic ---
def check_for_opportunity():
    try:
        now_ts = int(time.time())
        start_ts = now_ts - (105 * 60)
        ws = websocket.create_connection(WS_URL, timeout=15)
        ws.send(json.dumps({"ticks_history": SYMBOL_ID, "start": start_ts, "end": "latest", "style": "ticks", "count": 4500}))
        res = json.loads(ws.recv()); ws.close()
        
        df = pd.DataFrame({'price': res['history']['prices'], 'time': pd.to_datetime(res['history']['times'], unit='s')})
        df.set_index('time', inplace=True)
        candles = df['price'].resample('1Min').ohlc().dropna()
        
        recent_100 = candles.tail(100)
        h, l = recent_100['high'].max(), recent_100['low'].min()
        diff = h - l
        levels = [h - (diff * 0.618), h - (diff * 0.500), h - (diff * 0.382)]

        last_candle = candles.iloc[-1]
        c_open, c_close = last_candle['open'], last_candle['close']
        
        for val in levels:
            if c_open < val and c_close > val: return "CALL"
            if c_open > val and c_close < val: return "PUT"
        return None
    except: return None

# --- 5. Main Loop ---
def start_engine():
    global last_check_min, active_trade
    send_telegram_msg("⚡ **Bot System Online**\nSilent Mode | Auto Verification Enabled.")

    while True:
        now = datetime.now(BEIRUT_TZ)
        
        # 1. البحث عن إشارة (عند الثانية 01)
        if now.second == 1 and active_trade is None:
            current_min = now.strftime('%H:%M')
            if last_check_min != current_min:
                last_check_min = current_min
                signal = check_for_opportunity()
                if signal:
                    # الدخول بعد دقيقتين (مثال: إشارة 12:05 -> دخول 12:07)
                    entry_t = now + timedelta(minutes=2)
                    active_trade = {"dir": signal, "entry_time": entry_t, "notified": False}
                    msg = f"🚀 **SIGNAL FOUND**\nDir: *{signal}*\nEntry at: {entry_t.strftime('%H:%M:00')}"
                    send_telegram_msg(msg)

        # 2. التحقق من النتيجة (بعد انتهاء دقيقة الدخول)
        if active_trade:
            # وقت نهاية الصفقة (وقت الدخول + 1 دقيقة)
            finish_time = active_trade['entry_time'] + timedelta(minutes=1)
            if now >= finish_time + timedelta(seconds=5): # انتظار 5 ثوانٍ لضمان توفر البيانات
                result = verify_trade_result(active_trade['entry_time'], active_trade['dir'])
                send_telegram_msg(f"📊 **Trade Result**\n{result}")
                active_trade = None
        
        time.sleep(0.5)

if __name__ == "__main__":
    threading.Thread(target=lambda: app.run(host='0.0.0.0', port=8080), daemon=True).start()
    start_engine()
