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

# --- 1. Flask Server for UptimeRobot ---
app = Flask(__name__)
@app.route('/')
def home(): return "EMA 50 Trend Bot - Strategy 5min Cycle", 200

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

# --- 3. Analysis Engine (EMA 50 + 20 Indicators) ---
def create_20_tick_candles(prices):
    candles = []
    for i in range(0, len(prices), 20):
        chunk = prices[i:i+20]
        if len(chunk) == 20:
            candles.append({'open': chunk[0], 'high': np.max(chunk), 'low': np.min(chunk), 'close': chunk[-1]})
    return pd.DataFrame(candles)

def calculate_strategy(df):
    if len(df) < 50: return "NONE", 0 # Need at least 50 candles for EMA 50
    
    c, o = df['close'].values, df['open'].values
    up = 0
    
    # EMA 50 - The Trend Master
    ema50 = df['close'].ewm(span=50).mean().iloc[-1]
    current_price = c[-1]
    
    # Trend Bias
    is_bullish = current_price > ema50
    is_bearish = current_price < ema50

    # 20 Indicators Check
    for i in range(1, 21):
        if c[-1] > np.median(c[-min(i+3, len(c)):]): up += 1
    
    raw_direction = "CALL" if up >= 10 else "PUT"
    strength = (max(up, 20-up) / 20) * 100

    # Apply EMA 50 Filter
    if raw_direction == "CALL" and is_bullish:
        return "CALL", strength
    elif raw_direction == "PUT" and is_bearish:
        return "PUT", strength
    else:
        return "NO_TREND_MATCH", 0

# --- 4. Historical Result Verification ---
def get_historical_result(open_ts, close_ts):
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

def check_trade_cycle():
    global active_trade
    now = datetime.now(BEIRUT_TZ)
    target_close = active_trade['entry_time'] + timedelta(minutes=1)
    
    if now >= target_close + timedelta(seconds=2):
        o_ts, c_ts = int(active_trade['entry_time'].timestamp()), int(target_close.timestamp())
        p_open, p_close = get_historical_result(o_ts, c_ts)
        
        if p_open and p_close:
            win = (p_close > p_open) if active_trade['direction'] == "CALL" else (p_close < p_open)
            status = "✅ WIN" if win else "❌ LOSS"
            if p_open == p_close: status = "⚖️ DRAW"
            send_telegram_msg(f"{status} | {SYMBOL_NAME}\nOpen: {p_open} | Close: {p_close}")
            active_trade = None

# --- 5. Main Execution Loop ---
def start_engine():
    global last_signal_time, active_trade
    print(f"Engine LIVE: EMA 50 Filter + 4th Minute Entry Mode")
    
    while True:
        now = datetime.now(BEIRUT_TZ)
        minute = now.minute
        
        # Strategy: Only analyze at the 4th minute (e.g., 12:04:30, 12:09:30, 12:14:30)
        # This targets the end of the 5-minute candle cycle
        if (minute % 5 == 4) and now.second == 30 and active_trade is None:
            current_min_str = now.strftime('%H:%M')
            if last_signal_time != current_min_str:
                last_signal_time = current_min_str
                try:
                    ws = websocket.create_connection(WS_URL, timeout=15)
                    # We request 1000 ticks to ensure we have enough for EMA 50 (15 candles * 20 ticks = 300, but EMA 50 needs more)
                    ws.send(json.dumps({"ticks_history": SYMBOL_ID, "count": 1000, "end": "latest", "style": "ticks"}))
                    res = json.loads(ws.recv())
                    ws.close()
                    
                    df_candles = create_20_tick_candles(res['history']['prices'])
                    direction, accuracy = calculate_strategy(df_candles)
                    
                    if accuracy >= 80:
                        entry_dt = (now + timedelta(seconds=30)).replace(second=0, microsecond=0)
                        active_trade = {"direction": direction, "entry_time": entry_dt}
                        send_telegram_msg(f"🚀 **SIGNAL**: {direction}\nStrength: {accuracy}%\nFilter: EMA 50 Trend\nEntry: {entry_dt.strftime('%H:%M:00')}")
                except Exception as e: print(f"Error: {e}")
        
        if active_trade: check_trade_cycle()
        time.sleep(0.5)

if __name__ == "__main__":
    threading.Thread(target=run_flask, daemon=True).start()
    start_engine()
