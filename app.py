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

# --- Flask Server ---
app = Flask(__name__)
@app.route('/')
def health_check(): return "RSI Reversal Bot Active", 200

# --- Configuration ---
TOKEN = '8511172742:AAFxZIj8N07FB-tFnJ_l3rv13loyRMmsRYU'
CHAT_ID = '-1003731752986'
BEIRUT_TZ = pytz.timezone('Asia/Beirut')
WS_URL = "wss://blue.derivws.com/websockets/v3?app_id=16929"

SYMBOL = "frxEURGBP"
RSI_PERIOD = 14
RSI_OVERBOUGHT = 70
RSI_OVERSOLD = 30

is_waiting_for_result = False
trade_entry_time = None
target_result_time = None
pending_direction = None

def send_telegram_msg(text):
    url = f"https://api.telegram.org/bot{TOKEN}/sendMessage"
    try: requests.post(url, json={"chat_id": CHAT_ID, "text": text, "parse_mode": "Markdown"}, timeout=10)
    except: pass

def calculate_rsi(prices, period=14):
    if len(prices) < period + 1: return 50
    deltas = np.diff(prices)
    seed = deltas[:period+1]
    up = seed[seed >= 0].sum()/period
    down = -seed[seed < 0].sum()/period
    if down == 0: return 100
    rs = up/down
    rsi = np.zeros_like(prices)
    rsi[:period] = 100. - 100./(1. + rs)

    for i in range(period, len(prices)):
        delta = deltas[i-1]
        upval = delta if delta > 0 else 0.
        downval = -delta if delta < 0 else 0.
        up = (up * (period - 1) + upval) / period
        down = (down * (period - 1) + downval) / period
        if down == 0: rsi[i] = 100
        else:
            rs = up / down
            rsi[i] = 100. - 100. / (1. + rs)
    return rsi[-1]

def analyze_strategy(history):
    global is_waiting_for_result, trade_entry_time, target_result_time, pending_direction
    
    df = pd.DataFrame({
        'price': history['prices'],
        'time': [datetime.fromtimestamp(t, tz=pytz.utc).astimezone(BEIRUT_TZ) for t in history['times']]
    })
    
    now = datetime.now(BEIRUT_TZ)
    t_anchor = now.replace(second=0, microsecond=0)
    
    # 1. حساب RSI للحظة الحالية (قبل إغلاق الشمعة الأخيرة)
    current_rsi = calculate_rsi(df['price'].values, RSI_PERIOD)
    
    # 2. تحليل اتجاه الشمعة التي أغلقت لتوها (الدقيقة الماضية)
    # من (now - 1 min) إلى (now)
    last_min_start = t_anchor - timedelta(minutes=1)
    last_min_data = df[(df['time'] >= last_min_start) & (df['time'] < t_anchor)]
    
    if last_min_data.empty: return
    
    open_p = last_min_data['price'].iloc[0]
    close_p = last_min_data['price'].iloc[-1]
    is_green = close_p > open_p
    is_red = close_p < open_p

    trade_type = None

    # شرط الصعود: تشبع بيعي + شمعة تأكيد خضراء
    if current_rsi <= RSI_OVERSOLD and is_green:
        trade_type = "CALL (BUY)"
    # شرط الهبوط: تشبع شرائي + شمعة تأكيد حمراء
    elif current_rsi >= RSI_OVERBOUGHT and is_red:
        trade_type = "PUT (SELL)"

    if trade_type:
        # الدخول مع بداية الدقيقة التالية
        trade_entry_time = t_anchor
        target_result_time = trade_entry_time + timedelta(minutes=5)
        pending_direction = trade_type
        
        msg = (f"🔔 **RSI REVERSAL: {SYMBOL.replace('frx','')}**\n"
               f"----------------------------\n"
               f"🎯 Action: *{trade_type}*\n"
               f"🕐 Entry: {trade_entry_time.strftime('%H:%M:00')}\n"
               f"⏱ Duration: 5 Minutes")
        send_telegram_msg(msg)
        is_waiting_for_result = True

def check_result(history):
    global is_waiting_for_result
    df = pd.DataFrame({'price': history['prices'], 'time': [datetime.fromtimestamp(t, tz=pytz.utc).astimezone(BEIRUT_TZ) for t in history['times']]})
    trade_data = df[(df['time'] >= trade_entry_time) & (df['time'] < target_result_time)]
    
    if not trade_data.empty:
        entry_p, exit_p = trade_data['price'].iloc[0], trade_data['price'].iloc[-1]
        won = (exit_p > entry_p) if pending_direction == "CALL (BUY)" else (exit_p < entry_p)
        send_telegram_msg(f"{'✅ SUCCESS' if won else '❌ FAILED'}")
    
    is_waiting_for_result = False

def on_message(ws, message):
    data = json.loads(message)
    if 'history' in data:
        if is_waiting_for_result:
            if datetime.now(BEIRUT_TZ) >= target_result_time:
                check_result(data['history'])
        else:
            analyze_strategy(data['history'])
    ws.close()

def on_open(ws):
    ws.send(json.dumps({"ticks_history": SYMBOL, "count": 1000, "end": "latest", "style": "ticks"}))

def start_engine():
    while True:
        now = datetime.now(BEIRUT_TZ)
        if is_waiting_for_result and now >= target_result_time and now.second <= 2:
            time.sleep(1)
            ws = websocket.WebSocketApp(WS_URL, on_open=on_open, on_message=on_message)
            ws.run_forever()
            time.sleep(5)
            continue
            
        # التحليل عند بداية كل دقيقة (الثانية 1) للتأكد من إغلاق الشمعة السابقة
        if now.second == 1 and not is_waiting_for_result:
            ws = websocket.WebSocketApp(WS_URL, on_open=on_open, on_message=on_message)
            ws.run_forever(ping_timeout=15)
            time.sleep(1)
        
        time.sleep(0.5)

if __name__ == "__main__":
    threading.Thread(target=lambda: app.run(host='0.0.0.0', port=int(os.environ.get("PORT", 10000))), daemon=True).start()
    start_engine()
