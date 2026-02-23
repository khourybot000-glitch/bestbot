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
def health_check(): return "Bot Active: 30s Analysis - Next Min Entry", 200

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
    t_ref = now.replace(second=0, microsecond=0) # بداية الدقيقة الحالية (مثلاً 12:06:00)
    
    # تقسيم الفترات (30 ثانية لكل فترة)
    # الفترة C: من 12:06:00 إلى 12:06:30
    c_start, c_end = t_ref, t_ref + timedelta(seconds=30)
    # الفترة B: من 12:05:30 إلى 12:06:00
    b_start, b_end = t_ref - timedelta(seconds=30), t_ref
    # الفترة A: من 12:05:00 إلى 12:05:30
    a_start, a_end = t_ref - timedelta(seconds=60), t_ref - timedelta(seconds=30)

    def get_dir(start, end):
        data = df[(df['time'] >= start) & (df['time'] < end)]
        if len(data) < 2: return None
        return "UP" if data['price'].iloc[-1] > data['price'].iloc[0] else "DOWN"

    dir_a = get_dir(a_start, a_end)
    dir_b = get_dir(b_start, b_end)
    dir_c = get_dir(c_start, c_end)

    if not all([dir_a, dir_b, dir_c]): return

    # الشرط: تعاكس في الاتجاه (ليست كلها في نفس الاتجاه)
    if not (dir_a == dir_b == dir_c):
        trade_type = "CALL (BUY)" if dir_c == "UP" else "PUT (SELL)"
        
        # وقت الدخول: بداية الدقيقة التالية (مثلاً 12:07:00)
        trade_entry_time = t_ref + timedelta(minutes=1)
        # وقت النهاية: بعد دقيقة واحدة من الدخول (مثلاً 12:08:00)
        target_result_time = trade_entry_time + timedelta(minutes=1)
        pending_direction = trade_type
        
        msg = (f"🕒 **NEXT-MINUTE SCALP**\n"
               f"📊 30s Intervals: {dir_a} | {dir_b} | {dir_c}\n"
               f"🎯 Action: *{trade_type}*\n"
               f"🕐 **Entry Time: {trade_entry_time.strftime('%H:%M:00')}**\n"
               f"⏱ Duration: 1 Minute\n"
               f"📢 (Prepare to enter next minute)")
        send_telegram_msg(msg)
        is_waiting_for_result = True
    else:
        print(f"[{now.strftime('%H:%M:%S')}] Trend is consistent ({dir_c}), no trade.")

def check_result(history):
    global is_waiting_for_result
    df = pd.DataFrame({'price': history['prices'], 'time': [datetime.fromtimestamp(t, tz=pytz.utc).astimezone(BEIRUT_TZ) for t in history['times']]})
    
    # فحص الصفقة في دقيقتها المحددة
    trade_data = df[(df['time'] >= trade_entry_time) & (df['time'] < target_result_time)]
    if trade_data.empty: return

    entry_p, exit_p = trade_data['price'].iloc[0], trade_data['price'].iloc[-1]
    won = (exit_p > entry_p) if pending_direction == "CALL (BUY)" else (exit_p < entry_p)
    
    status = "✅ WIN" if won else "❌ LOSS"
    send_telegram_msg(f"{status} (EURGBP)\nQuick Scalp Result")
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
        
        # فحص النتيجة بعد انتهاء دقيقة الصفقة
        if is_waiting_for_result and now >= target_result_time and now.second <= 2:
            time.sleep(1)
            ws = websocket.WebSocketApp(WS_URL, on_open=on_open, on_message=on_message)
            ws.run_forever()
            time.sleep(2)
            continue
            
        # التحليل عند الثانية 30 من كل دقيقة
        if now.second == 30 and not is_waiting_for_result:
            ws = websocket.WebSocketApp(WS_URL, on_open=on_open, on_message=on_message)
            ws.run_forever(ping_timeout=15)
            time.sleep(1)
        
        time.sleep(0.5)

if __name__ == "__main__":
    threading.Thread(target=lambda: app.run(host='0.0.0.0', port=int(os.environ.get("PORT", 10000))), daemon=True).start()
    start_engine()
