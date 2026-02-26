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

# --- 1. Flask Server for 24/7 Hosting ---
app = Flask(__name__)
@app.route('/')
def home(): return "TMA Final Bot: ACTIVE", 200

def run_flask():
    port = int(os.environ.get("PORT", 8080))
    app.run(host='0.0.0.0', port=port)

# --- 2. Configuration ---
TOKEN = '8511172742:AAFxZIj8N07FB-tFnJ_l3rv13loyRMmsRYU'
CHAT_ID = '-1003731752986'
BEIRUT_TZ = pytz.timezone('Asia/Beirut')
WS_URL = "wss://blue.derivws.com/websockets/v3?app_id=16929"
SYMBOL_ID = "frxEURJPY"
SYMBOL_NAME = "EUR/JPY"

# TMA Strategy Parameters
TMA_HALF_LENGTH = 9
TMA_MULTIPLIER = 1.2

active_trade = None
last_analysis_time = ""

def send_telegram_msg(text):
    url = f"https://api.telegram.org/bot{TOKEN}/sendMessage"
    payload = {"chat_id": CHAT_ID, "text": text, "parse_mode": "Markdown"}
    try: requests.post(url, json=payload, timeout=10)
    except: pass

# --- 3. Result Checking Function ---
def get_trade_result(start_min_dt):
    """Fetches 70 ticks to compare entry and exit prices of a specific minute."""
    try:
        ws = websocket.create_connection(WS_URL, timeout=15)
        start_ts = int(start_min_dt.timestamp())
        ws.send(json.dumps({
            "ticks_history": SYMBOL_ID,
            "start": start_ts,
            "end": start_ts + 70,
            "style": "ticks"
        }))
        res = json.loads(ws.recv()); ws.close()
        prices = res['history']['prices']
        if len(prices) < 2: return None, None
        return float(prices[0]), float(prices[-1])
    except: return None, None

# --- 4. Market Analysis (1000 Ticks -> Time-based Candles) ---
def analyze_market():
    try:
        ws = websocket.create_connection(WS_URL, timeout=15)
        ws.send(json.dumps({"ticks_history": SYMBOL_ID, "end": "latest", "count": 1000, "style": "ticks"}))
        res = json.loads(ws.recv()); ws.close()
        
        # Build DataFrame from ticks
        df_ticks = pd.DataFrame({
            'price': res['history']['prices'],
            'time': pd.to_datetime(res['history']['times'], unit='s', utc=True)
        })
        df_ticks['time'] = df_ticks['time'].dt.tz_convert('Asia/Beirut')
        df_ticks.set_index('time', inplace=True)
        
        # Resample into 1-Minute candles (Time-based windows)
        df_candles = df_ticks['price'].resample('1Min', closed='left', label='left').ohlc().dropna().tail(15)
        
        # Calculate TMA Bands
        closes = df_candles['close']
        tma_core = closes.rolling(window=TMA_HALF_LENGTH).mean().rolling(window=TMA_HALF_LENGTH).mean()
        diff = (df_candles['high'] - df_candles['low']).abs()
        atr_like = diff.rolling(window=TMA_HALF_LENGTH).mean()
        
        upper_band = tma_core + (atr_like * TMA_MULTIPLIER)
        lower_band = tma_core - (atr_like * TMA_MULTIPLIER)
        
        curr_candle = df_candles.iloc[-1]
        target_upper = upper_band.iloc[-1]
        target_lower = lower_band.iloc[-1]

        # Logic: Touch within 40s + Candle Color Confirmation
        if curr_candle['high'] >= target_upper and curr_candle['close'] > curr_candle['open']:
            return "PUT"
        if curr_candle['low'] <= target_lower and curr_candle['close'] < curr_candle['open']:
            return "CALL"
        return None
    except: return None

# --- 5. Main Execution Engine ---
def start_engine():
    global last_analysis_time, active_trade
    while True:
        now = datetime.now(BEIRUT_TZ)
        
        # 1. Trigger Signal Analysis at Second 40
        if now.second == 40 and active_trade is None:
            current_min = now.strftime('%H:%M')
            if last_analysis_time != current_min:
                last_analysis_time = current_min
                signal = analyze_market()
                if signal:
                    entry_time = (now + timedelta(seconds=20)).replace(second=0, microsecond=0)
                    active_trade = {"dir": signal, "entry_time": entry_time, "step": "INITIAL"}
                    send_telegram_msg(f"🚀 **NEW SIGNAL**\nPair: `{SYMBOL_NAME}`\nDirection: *{signal}*\nEntry: `{entry_time.strftime('%H:%M:00')}`")

        # 2. Results Monitoring & Silent Martingale
        if active_trade:
            # Check result 5 seconds after the minute candle closes
            check_time = active_trade['entry_time'] + timedelta(minutes=1, seconds=5)
            
            if now >= check_time:
                p_in, p_out = get_trade_result(active_trade['entry_time'])
                
                if p_in is not None and p_out is not None:
                    win = (p_out > p_in) if active_trade['dir'] == "CALL" else (p_out < p_in)
                    
                    if win:
                        result_text = "WIN ✅" if active_trade['step'] == "INITIAL" else "MTG WIN ✅"
                        send_telegram_msg(f"📊 **RESULT**: {result_text}")
                        active_trade = None
                    else:
                        if active_trade['step'] == "INITIAL":
                            # SILENT LOSS: Move to Martingale without notifying Telegram
                            active_trade['step'] = "MTG"
                            active_trade['entry_time'] = active_trade['entry_time'] + timedelta(minutes=1)
                        else:
                            # Final Loss after Martingale
                            send_telegram_msg(f"📊 **RESULT**: MTG LOSS ❌")
                            active_trade = None

        time.sleep(0.5)

if __name__ == "__main__":
    # Start Web Server for Health Checks
    threading.Thread(target=run_flask, daemon=True).start()
    # Start the Trading Engine
    start_engine()
