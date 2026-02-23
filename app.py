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
def health_check(): return "Bot Active: Inverted Signals & No MTG", 200

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
        c1 = df[(df['time'] >= t_m3) & (df['time'] < t_m2)]
        c2 = df[(df['time'] >= t_m2) & (df['time'] < t_m1)]
        c3 = df[(df['time'] >= t_m1) & (df['time'] <= t_end)]

        if c1.empty or c2.empty or c3.empty: return

        d1 = 1 if c1['price'].iloc[-1] > c1['price'].iloc[0] else -1
        d2 = 1 if c2['price'].iloc[-1] > c2['price'].iloc[0] else -1
        d3 = 1 if c3['price'].iloc[-1] > c3['price'].iloc[0] else -1

        if d1 != d2 and d2 != d3:
            # تم عكس الإشارة هنا: إذا كان d3 صاعد (1) نرسل PUT، وإذا كان هابط (-1) نرسل CALL
            direction = "PUT (SELL)" if d3 == 1 else "CALL (BUY)"
            
            trade_entry_time = t_m1 + timedelta(minutes=1)
            target_result_time = trade_entry_time + timedelta(minutes=1)
            
            msg = (f"🔄 **INVERTED SIGNAL**\n🏆 Asset: {symbol.replace('frx','')}\n🎯 Direction: *{direction}*\n🕐 Entry: {trade_entry_time.strftime('%H:%M')}\n🚫 No Martingale")
            send_telegram_msg(msg)
            
            is_waiting_for_result = True
            pending_trade_direction = direction
            pending_trade_symbol = symbol
            
    except Exception as e: print(f"Analysis Error: {e}")

def check_trade_result(history, symbol):
    global is_waiting_for_result, pending_trade_direction, target_result_time, pending_trade_symbol, trade_entry_time
    
    df = pd.DataFrame({
        'price': history['prices'],
        'time': [datetime.fromtimestamp(t, tz=pytz.utc).astimezone(BEIRUT_TZ) for t in history['times']]
    })
    
    trade_data = df[(df['time'] >= trade_entry_time) & (df['time'] < target_result_time)]
    
    if trade_data.empty: return

    open_p, close_p = trade_data['price'].iloc[0], trade_data['price'].iloc[-1]
    won = (close_p > open_p) if pending_trade_direction == "CALL (BUY)" else (close_p < open_p)
    s_name = symbol.replace("frx","")

    if won:
        send_telegram_msg(f"✅ **WIN ({s_name})**")
    else:
        send_telegram_msg(f"❌ **LOSS ({s_name})**")

    # تصفير القفل فوراً بعد أول نتيجة (بدون مضاعفة)
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
        ws.send(json.dumps({"ticks_history": pending_trade_symbol, "count": 150, "end": "latest", "style": "ticks"}))
    else:
        for s in SYMBOLS:
            ws.send(json.dumps({"ticks_history": s, "count": 350, "end": "latest", "style": "ticks"}))
            time.sleep(0.05)

def start_engine():
    global is_waiting_for_result, target_result_time
    while True:
        now = datetime.now(BEIRUT_TZ)
        if is_waiting_for_result and now >= target_result_time and now.second <= 2:
            time.sleep(1)
            ws = websocket.WebSocketApp(WS_URL, on_open=on_open, on_message=on_message)
            ws.run_forever()
            time.sleep(5)
            continue
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
