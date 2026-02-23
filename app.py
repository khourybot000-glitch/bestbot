import websocket
import json
import pandas as pd
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
def health_check(): return "Bot Running", 200

# --- Configuration ---
TOKEN = '8511172742:AAFxZIj8N07FB-tFnJ_l3rv13loyRMmsRYU'
CHAT_ID = '-1003731752986'
BEIRUT_TZ = pytz.timezone('Asia/Beirut')
WS_URL = "wss://blue.derivws.com/websockets/v3?app_id=16929"

SYMBOL = "frxEURGBP"

is_waiting_for_result = False
trade_entry_time = None
target_result_time = None
pending_direction = None

def send_telegram_msg(text):
    url = f"https://api.telegram.org/bot{TOKEN}/sendMessage"
    try: requests.post(url, json={"chat_id": CHAT_ID, "text": text, "parse_mode": "Markdown"}, timeout=10)
    except: pass

def analyze_strategy(history):
    global is_waiting_for_result, trade_entry_time, target_result_time, pending_direction
    
    df = pd.DataFrame({
        'price': history['prices'],
        'time': [datetime.fromtimestamp(t, tz=pytz.utc).astimezone(BEIRUT_TZ) for t in history['times']]
    })
    
    now = datetime.now(BEIRUT_TZ)
    t_ref = now.replace(second=0, microsecond=0) 
    
    # التقسيم (30 ثانية لكل فترة)
    c_start, c_end = t_ref, t_ref + timedelta(seconds=30)
    b_start, b_end = t_ref - timedelta(seconds=30), t_ref
    a_start, a_end = t_ref - timedelta(seconds=60), t_ref - timedelta(seconds=30)

    def get_dir(start, end):
        data = df[(df['time'] >= start) & (df['time'] < end)]
        if len(data) < 2: return None
        return "UP" if data['price'].iloc[-1] > data['price'].iloc[0] else "DOWN"

    dir_a = get_dir(a_start, a_end)
    dir_b = get_dir(b_start, b_end)
    dir_c = get_dir(c_start, c_end)

    if not all([dir_a, dir_b, dir_c]): return

    # التحقق من نمط الزجزاج (بدون إظهار التفاصيل)
    is_up_down_up = (dir_a == "UP" and dir_b == "DOWN" and dir_c == "UP")
    is_down_up_down = (dir_a == "DOWN" and dir_b == "UP" and dir_c == "DOWN")

    if is_up_down_up or is_down_up_down:
        trade_type = "CALL (BUY)" if dir_c == "UP" else "PUT (SELL)"
        
        trade_entry_time = t_ref + timedelta(minutes=1)
        target_result_time = trade_entry_time + timedelta(minutes=1)
        pending_direction = trade_type
        
        # رسالة مشفرة ومختصرة جداً
        msg = (f"🔔 **NEW SIGNAL: {SYMBOL.replace('frx','')}**\n"
               f"----------------------------\n"
               f"🎯 Action: *{trade_type}*\n"
               f"🕐 Entry: {trade_entry_time.strftime('%H:%M:00')}\n"
               f"⏱ Duration: 1 Minute")
        
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
    ws.send(json.dumps({"ticks_history": SYMBOL, "count": 600, "end": "latest", "style": "ticks"}))

def start_engine():
    while True:
        now = datetime.now(BEIRUT_TZ)
        if is_waiting_for_result and now >= target_result_time and now.second <= 2:
            time.sleep(1)
            ws = websocket.WebSocketApp(WS_URL, on_open=on_open, on_message=on_message)
            ws.run_forever()
            time.sleep(2)
            continue
            
        if now.second == 30 and not is_waiting_for_result:
            ws = websocket.WebSocketApp(WS_URL, on_open=on_open, on_message=on_message)
            ws.run_forever(ping_timeout=15)
            time.sleep(1)
        
        time.sleep(0.5)

if __name__ == "__main__":
    threading.Thread(target=lambda: app.run(host='0.0.0.0', port=int(os.environ.get("PORT", 10000))), daemon=True).start()
    start_engine()
