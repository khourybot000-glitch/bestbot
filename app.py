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

# --- 1. Flask Server for UptimeRobot (Keeps the bot alive) ---
app = Flask(__name__)
@app.route('/')
def home(): return "EUR/JPY 5-Min Alignment Bot is Active", 200

def run_flask():
    port = int(os.environ.get("PORT", 8080))
    app.run(host='0.0.0.0', port=port)

# --- 2. Configuration ---
TOKEN = '8511172742:AAFxZIj8N07FB-tFnJ_l3rv13loyRMmsRYU'
CHAT_ID = '-1003731752986'
BEIRUT_TZ = pytz.timezone('Asia/Beirut')
WS_URL = "wss://blue.derivws.com/websockets/v3?app_id=16929"
SYMBOL_NAME = "EUR/JPY"
SYMBOL_ID = "frxEURJPY"

active_trade = None
last_signal_time = ""

def send_telegram_msg(text):
    url = f"https://api.telegram.org/bot{TOKEN}/sendMessage"
    try: requests.post(url, json={"chat_id": CHAT_ID, "text": text, "parse_mode": "Markdown"}, timeout=10)
    except: pass

# --- 3. Strategy Engine (20 Indicators + 5m Filter) ---
def create_20_tick_candles(prices):
    """Groups 300 ticks into 15 candles (20 ticks each)"""
    candles = []
    for i in range(0, len(prices), 20):
        chunk = prices[i:i+20]
        if len(chunk) == 20:
            candles.append({'open': chunk[0], 'high': np.max(chunk), 'low': np.min(chunk), 'close': chunk[-1]})
    return pd.DataFrame(candles)

def get_5min_start_price():
    """Fetches the opening price of the current 5-minute cycle (e.g., 12:00:00)"""
    try:
        now = datetime.now(BEIRUT_TZ)
        start_min = (now.minute // 5) * 5
        start_dt = now.replace(minute=start_min, second=0, microsecond=0)
        start_ts = int(start_dt.timestamp())
        
        ws = websocket.create_connection(WS_URL, timeout=10)
        ws.send(json.dumps({"ticks_history": SYMBOL_ID, "count": 10, "end": start_ts + 10, "style": "ticks"}))
        res = json.loads(ws.recv())
        ws.close()
        return res['history']['prices'][0]
    except: return None

def analyze_logic(df, start_price_5m):
    """Core logic with 20 indicators and 5-min candle alignment"""
    if len(df) < 14 or start_price_5m is None: return "NONE", 0
    
    current_price = df['close'].iloc[-1]
    up_votes = 0
    
    # 5-Minute Candle Direction (Filter)
    is_bullish_5m = current_price > start_price_5m
    is_bearish_5m = current_price < start_price_5m

    # 20 Indicators Check
    close_prices = df['close'].values
    for i in range(1, 21):
        if close_prices[-1] > np.median(close_prices[-min(i+3, len(close_prices)):]):
            up_votes += 1
    
    raw_dir = "CALL" if up_votes >= 10 else "PUT"
    strength = (max(up_votes, 20-up_votes) / 20) * 100

    # Alignment Enforcement
    if raw_dir == "CALL" and is_bullish_5m:
        return "CALL", strength
    elif raw_dir == "PUT" and is_bearish_5m:
        return "PUT", strength
    else:
        return "MISMATCH", 0

# --- 4. Historical Verification (70 Ticks) ---
def verify_result(open_ts, close_ts):
    try:
        ws = websocket.create_connection(WS_URL, timeout=15)
        ws.send(json.dumps({"ticks_history": SYMBOL_ID, "count": 70, "end": "latest", "style": "ticks"}))
        res = json.loads(ws.recv())
        ws.close()
        prices, times = res['history']['prices'], res['history']['times']
        p_open, p_close = None, None
        for i in range(len(times)):
            if times[i] >= open_ts and p_open is None: p_open = prices[i]
            if times[i] >= close_ts and p_close is None: p_close = prices[i]
        return p_open, p_close
    except: return None, None

def check_active_trade():
    global active_trade
    now = datetime.now(BEIRUT_TZ)
    target_close = active_trade['time'] + timedelta(minutes=1)
    
    if now >= target_close + timedelta(seconds=2):
        o_ts, c_ts = int(active_trade['time'].timestamp()), int(target_close.timestamp())
        p_open, p_close = verify_result(o_ts, c_ts)
        
        if p_open and p_close:
            win = (p_close > p_open) if active_trade['dir'] == "CALL" else (p_close < p_open)
            status = "✅ WIN" if win else "❌ LOSS"
            if p_open == p_close: status = "⚖️ DRAW"
            send_telegram_msg(f"{status} | {SYMBOL_NAME}\nDir: {active_trade['dir']}\nOpen: {p_open} | Close: {p_close}")
            active_trade = None

# --- 5. Main Execution ---
def start_bot():
    global last_signal_time, active_trade
    print(f"Bot Monitoring {SYMBOL_NAME} (5min Cycle Mode)...")
    
    while True:
        now = datetime.now(BEIRUT_TZ)
        # Only analyze at Minute 4, 9, 14, etc., at Second 30
        if (now.minute % 5 == 4) and now.second == 30 and active_trade is None:
            current_time_key = now.strftime('%H:%M')
            if last_signal_time != current_time_key:
                last_signal_time = current_time_key
                try:
                    start_5m = get_5min_start_price()
                    ws = websocket.create_connection(WS_URL, timeout=15)
                    ws.send(json.dumps({"ticks_history": SYMBOL_ID, "count": 300, "end": "latest", "style": "ticks"}))
                    res = json.loads(ws.recv())
                    ws.close()
                    
                    df = create_20_tick_candles(res['history']['prices'])
                    direction, accuracy = analyze_logic(df, start_5m)
                    
                    if accuracy >= 80:
                        entry_time = (now + timedelta(seconds=30)).replace(second=0, microsecond=0)
                        active_trade = {"dir": direction, "time": entry_time}
                        send_telegram_msg(f"🚀 **SIGNAL**: {direction}\nStrength: {accuracy}%\nFilter: 5m Candle Alignment\nEntry: {entry_time.strftime('%H:%M:00')}")
                except Exception as e: print(f"Analysis Error: {e}")
        
        if active_trade: check_active_trade()
        time.sleep(0.5)

if __name__ == "__main__":
    threading.Thread(target=run_flask, daemon=True).start()
    start_bot()
