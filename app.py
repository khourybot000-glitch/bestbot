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
def health_check(): return "Bot Active: Manual Ticks Splitting", 200

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
target_result_time = None

def send_telegram_msg(text):
    url = f"https://api.telegram.org/bot{TOKEN}/sendMessage"
    try:
        requests.post(url, json={"chat_id": CHAT_ID, "text": text, "parse_mode": "Markdown"}, timeout=10)
    except: pass

def analyze_strategy(history, symbol):
    global is_waiting_for_result, pending_trade_direction, target_result_time, pending_trade_symbol
    
    # تحويل البيانات إلى DataFrame مع الوقت
    df = pd.DataFrame({
        'price': history['prices'],
        'time': [datetime.fromtimestamp(t, tz=pytz.utc).astimezone(BEIRUT_TZ) for t in history['times']]
    })
    
    now = datetime.now(BEIRUT_TZ)
    # تعريف نقاط التقسيم زمنياً
    t_end = now  # 12:05:30
    t_m1 = t_end.replace(second=0, microsecond=0) # 12:05:00
    t_m2 = t_m1 - timedelta(minutes=1) # 12:04:00
    t_m3 = t_m2 - timedelta(minutes=1) # 12:03:00

    try:
        # الشمعة 1: من 12:03:00 إلى 12:04:00
        c1_data = df[(df['time'] >= t_m3) & (df['time'] < t_m2)]
        # الشمعة 2: من 12:04:00 إلى 12:05:00
        c2_data = df[(df['time'] >= t_m2) & (df['time'] < t_m1)]
        # الشمعة 3 (نصف دقيقة): من 12:05:00 إلى 12:05:30
        c3_data = df[(df['time'] >= t_m1) & (df['time'] <= t_end)]

        if c1_data.empty or c2_data.empty or c3_data.empty: return

        # حساب الاتجاهات (سعر الإغلاق - سعر الافتتاح لكل فترة)
        d1 = 1 if c1_data['price'].iloc[-1] > c1_data['price'].iloc[0] else -1
        d2 = 1 if c2_data['price'].iloc[-1] > c2_data['price'].iloc[0] else -1
        d3 = 1 if c3_data['price'].iloc[-1] > c3_data['price'].iloc[0] else -1

        # شرط الاستراتيجية: تتابع عكسي (صاعد-هابط-صاعد) أو (هابط-صاعد-هابط)
        if d1 != d2 and d2 != d3:
            direction = "CALL (BUY)" if d3 == 1 else "PUT (SELL)"
            
            entry_time = (t_end + timedelta(seconds=30)).strftime('%H:%M')
            target_result_time = (t_end + timedelta(seconds=90)).strftime('%H:%M')
            
            msg = (f"🚀 **MANUAL SPLIT SIGNAL**\n🏆 Asset: {symbol.replace('frx','')}\n🎯 Direction: *{direction}*\n🕐 Entry: {entry_time}\n📊 Logic: 1m | 1m | 30s")
            send_telegram_msg(msg)
            
            is_waiting_for_result = True
            pending_trade_direction = direction
            pending_trade_symbol = symbol
            
    except Exception as e:
        print(f"Error in splitting: {e}")

def check_trade_result(history, symbol):
    global is_waiting_for_result, is_martingale_step, pending_trade_direction, target_result_time, pending_trade_symbol
    
    # فحص شمعة الدقيقة التي انتهت (60 ثانية كاملة)
    prices = history['prices']
    open_p, close_p = prices[0], prices[-1]
    
    won = (close_p > open_p) if pending_trade_direction == "CALL (BUY)" else (close_p < open_p)
    s_name = symbol.replace("frx","")

    if won:
        send_telegram_msg(f"✅ **{'WIN' if not is_martingale_step else 'MTG WIN'} ({s_name})**")
        is_waiting_for_result = is_martingale_step = False
        pending_trade_symbol = target_result_time = None
    else:
        if not is_martingale_step:
            is_martingale_step = True
            now = datetime.now(BEIRUT_TZ)
            target_result_time = (now + timedelta(minutes=1)).strftime('%H:%M')
        else:
            send_telegram_msg(f"❌ **MTG LOSS ({s_name})**")
            is_waiting_for_result = is_martingale_step = False
            pending_trade_symbol = target_result_time = None

def on_message(ws, message):
    data = json.loads(message)
    if 'history' in data:
        symbol = data.get('echo_req', {}).get('ticks_history')
        history = data['history']
        
        if is_waiting_for_result and symbol == pending_trade_symbol:
            check_trade_result(history, symbol)
        elif not is_waiting_for_result:
            analyze_strategy(history, symbol)
    ws.close()

def on_open(ws):
    if is_waiting_for_result:
        # طلب تيكات الدقيقة الأخيرة للفحص
        ws.send(json.dumps({"ticks_history": pending_trade_symbol, "count": 100, "end": "latest", "style": "ticks"}))
    else:
        for s in SYMBOLS:
            # طلب 300 تيك لضمان تغطية الـ 150 ثانية الماضية بالكامل
            ws.send(json.dumps({"ticks_history": s, "count": 350, "end": "latest", "style": "ticks"}))
            time.sleep(0.05)

def start_engine():
    global is_waiting_for_result, target_result_time
    while True:
        now = datetime.now(BEIRUT_TZ)
        current_hm = now.strftime('%H:%M')
        
        # 1. وقت النتيجة (عند الثانية 00)
        if is_waiting_for_result and current_hm == target_result_time and now.second == 0:
            time.sleep(1)
            ws = websocket.WebSocketApp(WS_URL, on_open=on_open, on_message=on_message)
            ws.run_forever()
            time.sleep(2)
            continue

        # 2. وقت التحليل (عند الثانية 30)
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
