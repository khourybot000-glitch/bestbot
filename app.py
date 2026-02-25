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
    return "All-Level Fibonacci Engine: ACTIVE", 200

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

# --- 3. محرك المستويات المتعددة ---
def calculate_all_fibs_logic(tick_prices, tick_times):
    df_ticks = pd.DataFrame({'price': tick_prices, 'time': pd.to_datetime(tick_times, unit='s')})
    df_ticks.set_index('time', inplace=True)
    
    # تحويل لشموع دقيقة
    df_candles = df_ticks['price'].resample('1Min').ohlc()
    
    if len(df_candles) < 3: return "NONE", 0, 0

    # حساب القمة والقاع لآخر 100 دقيقة
    recent = df_candles.tail(100)
    h, l = recent['high'].max(), recent['low'].min()
    diff = h - l

    # قائمة مستويات فيبوناتشي المطلوب مراقبتها
    levels = {
        "23.6%": h - (diff * 0.236),
        "38.2%": h - (diff * 0.382),
        "50.0%": h - (diff * 0.500),
        "61.8%": h - (diff * 0.618),
        "78.6%": h - (diff * 0.786)
    }

    prev_close = df_candles['close'].iloc[-2]
    current_price = df_candles['close'].iloc[-1]
    
    # فحص كل مستوى على حدة
    for name, val in levels.items():
        # حالة PUT: اختراق كاذب للأعلى (كان فوق وأصبح تحت)
        if prev_close > val and current_price < val:
            return "PUT", 92, name
        
        # حالة CALL: اختراق كاذب للأسفل (كان تحت وأصبح فوق)
        if prev_close < val and current_price > val:
            return "CALL", 92, name

    return "NONE", 0, ""

# --- 4. التحقق من النتيجة ---
def verify_result(entry_ts, exit_ts):
    try:
        ws = websocket.create_connection(WS_URL, timeout=15)
        ws.send(json.dumps({"ticks_history": SYMBOL_ID, "count": 100, "end": exit_ts + 2, "style": "ticks"}))
        res = json.loads(ws.recv()); ws.close()
        
        prices, times = res['history']['prices'], res['history']['times']
        p_open, p_close = None, None
        
        for i in range(len(times)):
            if times[i] >= entry_ts and p_open is None: p_open = prices[i]
            if times[i] >= exit_ts and p_close is None: p_close = prices[i]
            
        return p_open, p_close
    except: return None, None

# --- 5. المحرك الرئيسي ---
def start_engine():
    global active_trade, last_signal_time
    print(f"Multi-Fib Engine Running: {SYMBOL_NAME}")
    
    while True:
        now = datetime.now(BEIRUT_TZ)
        
        # التحليل عند الثانية 45
        if now.second == 45 and active_trade is None:
            current_min = now.strftime('%H:%M')
            if last_signal_time != current_min:
                last_signal_time = current_min
                try:
                    ws = websocket.create_connection(WS_URL, timeout=10)
                    ws.send(json.dumps({"ticks_history": SYMBOL_ID, "count": 6500, "style": "ticks"}))
                    res = json.loads(ws.recv()); ws.close()
                    
                    direction, accuracy, level_name = calculate_all_fibs_logic(res['history']['prices'], res['history']['times'])
                    
                    if direction != "NONE":
                        entry_t = (now + timedelta(seconds=15)).replace(second=0, microsecond=0)
                        exit_t = entry_t + timedelta(minutes=1)
                        
                        active_trade = {
                            "dir": direction, 
                            "entry": entry_t, 
                            "exit": exit_t,
                            "entry_ts": int(entry_t.timestamp()),
                            "exit_ts": int(exit_t.timestamp()),
                            "level": level_name
                        }
                        
                        send_telegram_msg(f"🔥 **MULTI-FIB SIGNAL**\n"
                                          f"Asset: {SYMBOL_NAME}\n"
                                          f"Level: `{level_name}`\n"
                                          f"Action: *{direction}*\n"
                                          f"Entry: {entry_t.strftime('%H:%M:00')}\n"
                                          f"Exp: 1 min")
                except: pass

        # التحقق من النتيجة
        if active_trade and now >= active_trade['exit'] + timedelta(seconds=2):
            p_open, p_close = verify_result(active_trade['entry_ts'], active_trade['exit_ts'])
            if p_open and p_close:
                win = (p_close > p_open) if active_trade['dir'] == "CALL" else (p_close < p_open)
                status = "✅ **WIN**" if win else "❌ **LOSS**"
                send_telegram_msg(f"{status} | {active_trade['level']}\nIn: {p_open} | Out: {p_close}")
            active_trade = None 

        time.sleep(0.5)

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    threading.Thread(target=lambda: app.run(host='0.0.0.0', port=port), daemon=True).start()
    start_engine()
