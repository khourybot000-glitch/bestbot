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

# --- Flask Server for Render Health Check ---
app = Flask(__name__)
@app.route('/')
def health_check(): return "Bot Status: Active - 5m Time-Based Strategy", 200

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
pending_trade_direction = None
pending_trade_symbol = None
trade_entry_time = None
target_result_time = None

def send_telegram_msg(text):
    url = f"https://api.telegram.org/bot{TOKEN}/sendMessage"
    try:
        requests.post(url, json={"chat_id": CHAT_ID, "text": text, "parse_mode": "Markdown"}, timeout=10)
    except: pass

def analyze_strategy(history, symbol):
    global is_waiting_for_result, pending_trade_direction, target_result_time, pending_trade_symbol, trade_entry_time
    
    # تحويل التيكات إلى DataFrame
    df = pd.DataFrame({
        'price': history['prices'],
        'time': [datetime.fromtimestamp(t, tz=pytz.utc).astimezone(BEIRUT_TZ) for t in history['times']]
    })
    
    now = datetime.now(BEIRUT_TZ)
    # تصفير الثواني للحصول على بداية الدقيقة الحالية (الدقيقة 4، 9، 14...)
    t_anchor = now.replace(second=0, microsecond=0)
    
    candle_dirs = []
    # فحص آخر 4 دقائق بناءً على توقيت الساعة الحقيقي
    for i in range(1, 5):
        start_range = t_anchor - timedelta(minutes=i)
        end_range = t_anchor - timedelta(minutes=i-1)
        
        # فلترة التيكات لكل دقيقة على حدة
        minute_data = df[(df['time'] >= start_range) & (df['time'] < end_range)]
        if not minute_data.empty:
            # إذا سعر الإغلاق (آخر تيك في الدقيقة) أكبر من الافتتاح (أول تيك)
            direction = "UP" if minute_data['price'].iloc[-1] > minute_data['price'].iloc[0] else "DOWN"
            candle_dirs.append(direction)

    # يجب أن يكون لدينا تحليل لـ 4 شموع كاملة
    if len(candle_dirs) < 4: return

    # منطق الإشارة: 4 شموع متتالية بنفس الاتجاه
    trade_type = None
    if all(d == "UP" for d in candle_dirs):
        trade_type = "CALL (BUY)"
    elif all(d == "DOWN" for d in candle_dirs):
        trade_type = "PUT (SELL)"

    if trade_type:
        # الدخول يكون مع بداية الدقيقة الخامسة (التي تبدأ الآن)
        trade_entry_time = t_anchor 
        # النتيجة بعد 5 دقائق بالضبط
        target_result_time = trade_entry_time + timedelta(minutes=5)
        
        msg = (f"🎯 **TIME-ALIGNED SIGNAL**\n"
               f"🏆 Asset: {symbol.replace('frx','')}\n"
               f"🕯 Pattern: 4 Consecutive Candles\n"
               f"🎯 Action: *{trade_type}*\n"
               f"🕐 Entry: {trade_entry_time.strftime('%H:%M')}\n"
               f"⏱ Duration: 5 Minutes\n"
               f"🚫 No Martingale")
        send_telegram_msg(msg)
        
        is_waiting_for_result = True
        pending_trade_direction = trade_type
        pending_trade_symbol = symbol

def check_trade_result(history, symbol):
    global is_waiting_for_result, pending_trade_direction, target_result_time, pending_trade_symbol, trade_entry_time
    
    df = pd.DataFrame({
        'price': history['prices'],
        'time': [datetime.fromtimestamp(t, tz=pytz.utc).astimezone(BEIRUT_TZ) for t in history['times']]
    })
    
    # فحص النطاق السعري للصفقة (من بداية الدقيقة 0 إلى نهاية الدقيقة 5)
    trade_window = df[(df['time'] >= trade_entry_time) & (df['time'] < target_result_time)]
    
    if trade_window.empty: return

    entry_p = trade_window['price'].iloc[0]
    exit_p = trade_window['price'].iloc[-1]
    
    won = (exit_p > entry_p) if pending_trade_direction == "CALL (BUY)" else (exit_p < entry_p)
    status = "✅ WIN" if won else "❌ LOSS"
    
    send_telegram_msg(f"{status} ({symbol.replace('frx','')})\nExit Price: {exit_p}\nTime: {target_result_time.strftime('%H:%M')}")
    
    # فتح القفل للبحث عن صفقات جديدة
    is_waiting_for_result = False
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
        # جلب تيكات كافية لتغطية الـ 5 دقائق الماضية
        ws.send(json.dumps({"ticks_history": pending_trade_symbol, "count": 1000, "end": "latest", "style": "ticks"}))
    else:
        # جلب تيكات لـ 20 زوجاً (آخر 10 دقائق للتحليل)
        for s in SYMBOLS:
            ws.send(json.dumps({"ticks_history": s, "count": 1000, "end": "latest", "style": "ticks"}))
            time.sleep(0.05)

def start_engine():
    while True:
        now = datetime.now(BEIRUT_TZ)
        
        # 1. فحص النتيجة (بعد 5 دقائق من الدخول)
        if is_waiting_for_result and now >= target_result_time and now.second <= 2:
            time.sleep(1)
            ws = websocket.WebSocketApp(WS_URL, on_open=on_open, on_message=on_message)
            ws.run_forever()
            time.sleep(5)
            continue
        
        # 2. لحظة التحليل (الدقيقة الرابعة من كل دورة 5 دقائق)
        # 04, 09, 14, 19, 24, 29, 34, 39, 44, 49, 54, 59
        if (now.minute + 1) % 5 == 0 and now.second == 0 and not is_waiting_for_result:
            print(f"Triggering analysis for all pairs at {now.strftime('%H:%M:%S')}")
            ws = websocket.WebSocketApp(WS_URL, on_open=on_open, on_message=on_message)
            ws.run_forever(ping_timeout=15)
            time.sleep(1)
        else:
            time.sleep(0.5)

if __name__ == "__main__":
    # تشغيل سيرفر Flask في الخلفية لضمان بقاء Render نشطاً
    threading.Thread(target=run_flask, daemon=True).start()
    start_engine()
