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

# --- Flask Health Check for Deployment ---
app = Flask(__name__)
@app.route('/')
def health_check(): return "Bot Active: 5m Time-Based Strategy", 200

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
    
    # Create DataFrame from Ticks
    df = pd.DataFrame({
        'price': history['prices'],
        'time': [datetime.fromtimestamp(t, tz=pytz.utc).astimezone(BEIRUT_TZ) for t in history['times']]
    })
    
    # 1. Calculate General Trend (First vs Last of 6000 ticks)
    general_trend = "UP" if df['price'].iloc[-1] > df['price'].iloc[0] else "DOWN"
    
    # 2. Time-Based Candle Splitting (Last 4 Minutes)
    now = datetime.now(BEIRUT_TZ)
    t0 = now.replace(second=0, microsecond=0)
    
    candle_dirs = []
    for i in range(1, 5):
        start_range = t0 - timedelta(minutes=i)
        end_range = t0 - timedelta(minutes=i-1)
        
        # Filter ticks for this specific minute
        minute_data = df[(df['time'] >= start_range) & (df['time'] < end_range)]
        if not minute_data.empty:
            direction = "UP" if minute_data['price'].iloc[-1] > minute_data['price'].iloc[0] else "DOWN"
            candle_dirs.append(direction)

    if len(candle_dirs) < 4: return

    # 3. Entry Conditions
    signal_direction = None
    # If Trend is UP and last 4 1m-candles are UP -> CALL
    if general_trend == "UP" and all(d == "UP" for d in candle_dirs):
        signal_direction = "CALL (BUY)"
    # If Trend is DOWN and last 4 1m-candles are DOWN -> PUT
    elif general_trend == "DOWN" and all(d == "DOWN" for d in candle_dirs):
        signal_direction = "PUT (SELL)"

    if signal_direction:
        trade_entry_time = t0  # Current minute 00:00
        target_result_time = trade_entry_time + timedelta(minutes=5)
        
        msg = (f"🎯 **NEW SIGNAL**\n"
               f"🏆 Asset: {symbol.replace('frx','')}\n"
               f"📈 Trend: {general_trend}\n"
               f"🕯 Pattern: 4 Same-Color Candles\n"
               f"🎯 Action: *{signal_direction}*\n"
               f"🕐 Entry: {trade_entry_time.strftime('%H:%M')}\n"
               f"⏱ Duration: 5 Minutes\n"
               f"🚫 No Martingale")
        send_telegram_msg(msg)
        
        is_waiting_for_result = True
        pending_trade_direction = signal_direction
        pending_trade_symbol = symbol

def check_trade_result(history, symbol):
    global is_waiting_for_result, pending_trade_direction, target_result_time, pending_trade_symbol, trade_entry_time
    
    df = pd.DataFrame({
        'price': history['prices'],
        'time': [datetime.fromtimestamp(t, tz=pytz.utc).astimezone(BEIRUT_TZ) for t in history['times']]
    })
    
    # Filter Ticks strictly within the 5-minute trade window
    trade_window = df[(df['time'] >= trade_entry_time) & (df['time'] < target_result_time)]
    
    if trade_window.empty: return

    entry_price = trade_window['price'].iloc[0]
    exit_price = trade_window['price'].iloc[-1]
    
    won = (exit_price > entry_price) if pending_trade_direction == "CALL (BUY)" else (exit_price < entry_price)
    asset_name = symbol.replace("frx","")

    status = "✅ WIN" if won else "❌ LOSS"
    send_telegram_msg(f"{status} ({asset_name})\nCalculation: Time-Based (5m)")
    
    # Reset lock to analyze 20 pairs again
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
        # Fetch 600 ticks to ensure coverage of the 5-minute result window
        ws.send(json.dumps({"ticks_history": pending_trade_symbol, "count": 600, "end": "latest", "style": "ticks"}))
    else:
        for s in SYMBOLS:
            # Fetch 6000 ticks for trend and 4-minute candle analysis
            ws.send(json.dumps({"ticks_history": s, "count": 6000, "end": "latest", "style": "ticks"}))
            time.sleep(0.05)

def start_engine():
    while True:
        now = datetime.now(BEIRUT_TZ)
        
        # 1. Result Check: 5 minutes after Entry
        if is_waiting_for_result and now >= target_result_time and now.second <= 2:
            time.sleep(1)
            ws = websocket.WebSocketApp(WS_URL, on_open=on_open, on_message=on_message)
            ws.run_forever()
            time.sleep(5)
            continue
        
        # 2. Analysis Trigger: At minute 4, 9, 14, 19... etc. (End of the 4th candle)
        # This triggers right at second 00 of the 5th candle start.
        if now.minute % 5 == 0 and now.second == 0 and not is_waiting_for_result:
            ws = websocket.WebSocketApp(WS_URL, on_open=on_open, on_message=on_message)
            ws.run_forever(ping_timeout=15)
            time.sleep(1)
        else:
            time.sleep(0.5)

if __name__ == "__main__":
    threading.Thread(target=run_flask, daemon=True).start()
    start_engine()
