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
def health_check(): return "Bot Active: Full Time-Range Logic", 200

def run_flask():
    port = int(os.environ.get("PORT", 10000))
    app.run(host='0.0.0.0', port=port)

# --- Configuration ---
TOKEN = '8511172742:AAFxZIj8N07FB-tFnJ_l3rv13loyRMmsRYU'
CHAT_ID = '-1003731752986'
BEIRUT_TZ = pytz.timezone('Asia/Beirut')
WS_URL = "wss://blue.derivws.com/websockets/v3?app_id=16929"

SYMBOLS = [
    "frxEURGBP", "frxEURUSD", "frxGBPUSD", "frxUSDJPY", "frxAUDUSD",
    "frxUSDCAD", "frxUSDCHF", "frxEURJPY", "frxGBPJPY", "frxEURAUD",
    "frxEURCAD", "frxAUDJPY", "frxGBPCAD", "frxNZDUSD", "frxGBPAUD",
    "frxAUDCAD", "frxEURNZD", "frxAUDNZD", "frxGBPNZD", "frxCADJPY"
]

is_waiting_for_result = False
is_martingale_step = False
pending_trade_direction = None
pending_trade_symbol = None
trade_entry_time = None  # وقت بداية الصفقة الفعلي
target_result_time = None # وقت نهاية الصفقة الفعلي

def send_telegram_msg(text):
    url = f"https://api.telegram.org/bot{TOKEN}/sendMessage"
    try:
        requests.post(url, json={"chat_id": CHAT_ID, "text": text, "parse_mode": "Markdown"}, timeout=10)
    except: pass

def analyze_strategy(history, symbol):
    global is_waiting_for_result, pending_trade_direction, target_result_time, pending_trade_symbol, trade_entry_time
    
    df = pd.DataFrame({
        'price': history['prices'],
        'time': [datetime.fromtimestamp(t, tz=pytz.utc).astimezone(BEIRUT_TZ) for t in history['times']]
    })
    
    now = datetime.now(BEIRUT_TZ)
    t_end = now  # 12:05:30
    t_m1 = t_end.replace(second=0, microsecond=0) # 12:05:00
    t_m2 = t_m1 - timedelta(minutes=1) # 12:04:00
    t_m3 = t_m2 - timedelta(minutes=1) # 12:03:00

    try:
        # تقسيم التيكات بناءً على الوقت
        c1 = df[(df['time'] >= t_m3) & (df['time'] < t_m2)] # 12:03 to 12:04
        c2 = df[(df['time'] >= t_m2) & (df['time'] < t_m1)] # 12:04 to 12:05
        c3 = df[(df['time'] >= t_m1) & (df['time'] <= t_end)] # 12:05 to 12:05:30

        if c1.empty or c2.empty or c3.empty: return

        d1 = 1 if c1['price'].iloc[-1] > c1['price'].iloc[0] else -1
        d2 = 1 if c2['price'].iloc[-1] > c2['price'].iloc[0] else -1
        d3 = 1 if c3['price'].iloc[-1] > c3['price'].iloc[0] else -1

        if d1 != d2 and d2 != d3:
            direction = "CALL (BUY)" if d3 == 1 else "PUT (SELL)"
            
            # ضبط أوقات الصفقة
            trade_entry_time = t_m1 + timedelta(minutes=1) # 12:06:00
            target_result_time = trade_entry_time + timedelta(minutes=1) # 12:07:00
            
            msg = (f"🚀 **SIGNAL FOUND**\n🏆 Asset: {symbol.replace('frx','')}\n🎯 Direction: *{direction}*\n🕐 Entry: {trade_entry_time.strftime('%H:%M')}\n📊 Analysis: 1m|1m|30s")
            send_telegram_msg(msg)
            
            is_waiting_for_result = True
            pending_trade_direction = direction
            pending_trade_symbol = symbol
            
    except Exception as e: print(f"Analysis Error: {e}")

def check_trade_result(history, symbol):
    global is_waiting_for_result, is_martingale_step, pending_trade_direction, target_result_time, pending_trade_symbol, trade_entry_time
    
    df = pd.DataFrame({
        'price': history['prices'],
        'time': [datetime.fromtimestamp(t, tz=pytz.utc).astimezone(BEIRUT_TZ) for t in history['times']]
    })
    
    # تحديد نطاق الفحص: من وقت الدخول إلى وقت النتيجة
    # مثال: من 12:06:00 إلى 12:07:00
    trade_data = df[(df['time'] >= trade_entry_time) & (df['time'] < target_result_time)]
    
    if trade_data.empty: return

    open_p = trade_data['price'].iloc[0]
    close_p = trade_data['price'].iloc[-1]
    
    won = (close_p > open_p) if pending_trade_direction == "CALL (BUY)" else (close_p < open_p)
    s_name = symbol.replace("frx","")

    if won:
        send_telegram_msg(f"✅ **{'WIN' if not is_martingale_step else 'MTG WIN'} ({s_name})**")
        is_waiting_for_result = is_martingale_step = False
        pending_trade_symbol = target_result_time = trade_entry_time = None
    else:
        if not is_martingale_step:
            # بدء المضاعفة: النطاق الجديد من 12:07 إلى 12:08
            is_martingale_step = True
            trade_entry_time = target_result_time
            target_result_time = trade_entry_time + timedelta(minutes=1)
            print(f"Loss. Martingale Range: {trade_entry_time.strftime('%H:%M')} to {target_result_time.strftime('%H:%M')}")
        else:
            send_telegram_msg(f"❌ **MTG LOSS ({s_name})**")
            is_waiting_for_result = is_martingale_step = False
            pending_trade_symbol = target_result_time = trade_entry_time = None

def on_message(ws, message):
    data = json.loads(message)
    if 'history' in data:
        symbol = data.get('echo_req', {}).get('ticks_history')
        if is_waiting_for_result and symbol == pending_trade_symbol:
            check_trade_result(data['history'], symbol)
        elif not is_waiting_for_result:
            analyze_strategy(data['history'], symbol)
    ws.close()

def on_open(ws):
    if is_waiting_for_result:
        # نطلب 150 تيك للتأكد من تغطية الدقيقة الماضية بالكامل زمنياً
        ws.send(json.dumps({"ticks_history": pending_trade_symbol, "count": 150, "end": "latest", "style": "ticks"}))
    else:
        for s in SYMBOLS:
            ws.send(json.dumps({"ticks_history": s, "count": 350, "end": "latest", "style": "ticks"}))
            time.sleep(0.05)

def start_engine():
    global is_waiting_for_result, target_result_time
    while True:
        now = datetime.now(BEIRUT_TZ)
        
        # فحص النتيجة عند الثانية 01 من الدقيقة المستهدفة لضمان اكتمال البيانات
        if is_waiting_for_result and now >= target_result_time and now.second <= 2:
            time.sleep(1)
            ws = websocket.WebSocketApp(WS_URL, on_open=on_open, on_message=on_message)
            ws.run_forever()
            time.sleep(5)
            continue

        # التحليل عند الثانية 30 من كل دقيقة
        if 9 <= now.hour < 21 and now.weekday() <= 4:
            if now.second == 30 and not is_waiting_for_result:
                ws = websocket.WebSocketApp(WS_URL, on_open=on_open, on_message=on_message)
                ws.run_forever(ping_timeout=15)
                time.sleep(1)
            else:
                time.sleep(0.5)
        else:
            time.sleep(30)

if __name__ == "__main__":
    threading.Thread(target=run_flask, daemon=True).start()
    start_engine()
