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

# --- Flask Server for Deployment ---
app = Flask(__name__)
@app.route('/')
def health_check(): return "EURGBP Bot: Final Strategy Active", 200

def run_flask():
    port = int(os.environ.get("PORT", 10000))
    app.run(host='0.0.0.0', port=port)

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
    try:
        requests.post(url, json={"chat_id": CHAT_ID, "text": text, "parse_mode": "Markdown"}, timeout=10)
    except:
        pass

def analyze_strategy(history):
    global is_waiting_for_result, trade_entry_time, target_result_time, pending_direction
    
    # تحويل التيكات إلى DataFrame
    df = pd.DataFrame({
        'price': history['prices'],
        'time': [datetime.fromtimestamp(t, tz=pytz.utc).astimezone(BEIRUT_TZ) for t in history['times']]
    })
    
    now = datetime.now(BEIRUT_TZ)
    # تصفير الثواني للدقيقة التي نحن فيها الآن (مثلاً 14:04:00)
    t_anchor = now.replace(second=0, microsecond=0)
    
    candle_results = [] # 1 للخصراء، -1 للحمراء

    # فحص آخر 4 شموع دقيقة أغلقت قبل هذه الدقيقة
    # إذا نحن في 14:04، سنفحص شموع: 14:00، 14:01، 14:02، 14:03
    for i in range(1, 5):
        start_range = t_anchor - timedelta(minutes=i)
        end_range = t_anchor - timedelta(minutes=i-1)
        
        minute_data = df[(df['time'] >= start_range) & (df['time'] < end_range)]
        
        if len(minute_data) < 2:
            candle_results.append(0)
            continue
            
        open_p = minute_data['price'].iloc[0]
        close_p = minute_data['price'].iloc[-1]
        
        if close_p > open_p:
            candle_results.append(1)
        elif close_p < open_p:
            candle_results.append(-1)
        else:
            candle_results.append(0)

    # تحديد الإشارة
    direction = None
    if all(r == 1 for r in candle_results):
        direction = "CALL (BUY)"
    elif all(r == -1 for r in candle_results):
        direction = "PUT (SELL)"

    if direction:
        # وقت الدخول: الدقيقة القادمة (مثلاً 14:05:00)
        trade_entry_time = t_anchor + timedelta(minutes=1)
        # وقت النتيجة: بعد 5 دقائق من الدخول (مثلاً 14:10:00)
        target_result_time = trade_entry_time + timedelta(minutes=5)
        pending_direction = direction
        
        msg = (f"🔮 **EURGBP NEXT-MIN SIGNAL**\n"
               f"📊 Analysis Period: Last 4 Mins\n"
               f"🕯 Candles: {'🟢' if direction == 'CALL (BUY)' else '🔴'} x 4\n"
               f"🎯 Action: *{direction}*\n"
               f"🕐 **Entry At: {trade_entry_time.strftime('%H:%M:00')}**\n"
               f"⏱ Duration: 5 Minutes")
        send_telegram_msg(msg)
        is_waiting_for_result = True
    else:
        # لوج داخلي للمراقبة في سيرفر Render
        print(f"Time: {now.strftime('%H:%M:%S')} - Pattern not found. Results: {candle_results}")

def check_result(history):
    global is_waiting_for_result
    df = pd.DataFrame({
        'price': history['prices'],
        'time': [datetime.fromtimestamp(t, tz=pytz.utc).astimezone(BEIRUT_TZ) for t in history['times']]
    })
    
    # فحص النطاق السعري للصفقة الفعلية
    trade_window = df[(df['time'] >= trade_entry_time) & (df['time'] < target_result_time)]
    
    if trade_window.empty: return

    entry_p = trade_window['price'].iloc[0]
    exit_p = trade_window['price'].iloc[-1]
    
    won = (exit_p > entry_p) if pending_direction == "CALL (BUY)" else (exit_p < entry_p)
    status = "✅ WIN" if won else "❌ LOSS"
    
    send_telegram_msg(f"{status} (EURGBP)\nEntry: {entry_p}\nExit: {exit_p}\nDuration: 5m")
    is_waiting_for_result = False

def on_message(ws, message):
    data = json.loads(message)
    if 'history' in data:
        if is_waiting_for_result:
            # نتأكد أن الوقت الحالي تجاوز وقت النتيجة
            if datetime.now(BEIRUT_TZ) >= target_result_time:
                check_result(data['history'])
        else:
            analyze_strategy(data['history'])
    ws.close()

def on_open(ws):
    # جلب بيانات كافية للتحليل أو لفحص النتيجة
    ws.send(json.dumps({"ticks_history": SYMBOL, "count": 1500, "end": "latest", "style": "ticks"}))

def start_engine():
    while True:
        now = datetime.now(BEIRUT_TZ)
        
        # 1. فحص النتيجة (بعد 5 دقائق من وقت الدخول المخطط له)
        if is_waiting_for_result and now >= target_result_time and now.second <= 2:
            time.sleep(1)
            ws = websocket.WebSocketApp(WS_URL, on_open=on_open, on_message=on_message)
            ws.run_forever()
            time.sleep(5)
            continue
            
        # 2. التحليل عند الثانية 01 من كل دقيقة
        if now.second == 1 and not is_waiting_for_result:
            ws = websocket.WebSocketApp(WS_URL, on_open=on_open, on_message=on_message)
            ws.run_forever(ping_timeout=15)
            time.sleep(1)
        
        time.sleep(0.5)

if __name__ == "__main__":
    # تشغيل السيرفر لضمان عدم توقف البوت على Render
    threading.Thread(target=run_flask, daemon=True).start()
    start_engine()
