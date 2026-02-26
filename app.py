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

# --- 1. Flask Server ---
app = Flask(__name__)
@app.route('/')
def home(): return "TMA 24/7 BOT: ACTIVE", 200

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

# TMA Parameters
TMA_HALF_LENGTH = 9
TMA_MULTIPLIER = 1.2

active_trade = None
last_analysis_time = ""

def send_telegram_msg(text):
    url = f"https://api.telegram.org/bot{TOKEN}/sendMessage"
    payload = {"chat_id": CHAT_ID, "text": text, "parse_mode": "Markdown"}
    try: requests.post(url, json=payload, timeout=10)
    except: pass

# --- 3. TMA Calculation ---
def calculate_tma_signal(prices):
    df = pd.Series(prices)
    # TMA Calculation (Double Smoothing)
    tma_core = df.rolling(window=TMA_HALF_LENGTH).mean().rolling(window=TMA_HALF_LENGTH).mean()
    
    # Deviation Calculation
    diff = df.diff().abs()
    atr_like = diff.rolling(window=TMA_HALF_LENGTH).mean()
    
    upper_band = tma_core + (atr_like * TMA_MULTIPLIER)
    lower_band = tma_core - (atr_like * TMA_MULTIPLIER)
    
    current_price = df.iloc[-1]
    
    if current_price >= upper_band.iloc[-1]:
        return "PUT"
    elif current_price <= lower_band.iloc[-1]:
        return "CALL"
    return None

# --- 4. Logic Engine ---
def check_strategy(now):
    try:
        start_ts = int((now - timedelta(minutes=30)).timestamp())
        ws = websocket.create_connection(WS_URL, timeout=15)
        ws.send(json.dumps({"ticks_history": SYMBOL_ID, "start": start_ts, "end": int(now.timestamp()), "style": "ticks"}))
        res = json.loads(ws.recv()); ws.close()
        prices = res['history']['prices']
        return calculate_tma_signal(prices)
    except: return None

# --- 5. Main Loop ---
def start_engine():
    global last_analysis_time, active_trade

    while True:
        now = datetime.now(BEIRUT_TZ)
        
        # REMOVED TIME RESTRICTION: Now runs anytime the market is open
        # We only check if it is a trading day (Monday to Friday)
        if 0 <= now.weekday() <= 4:
            
            # Analyze at Second 40
            if now.second == 40:
                current_min = now.strftime('%H:%M')
                
                if last_analysis_time != current_min and active_trade is None:
                    last_analysis_time = current_min
                    signal = check_strategy(now)
                    
                    if signal:
                        # Entry at the start of the next minute
                        entry_t = (now + timedelta(seconds=20)).replace(second=0, microsecond=0)
                        active_trade = {"dir": signal, "entry_time": entry_t, "step": "INITIAL"}
                        
                        send_telegram_msg(
                            f"🔔 **24/7 TMA SIGNAL**\n"
                            f"Pair: `{SYMBOL_NAME}`\n"
                            f"Direction: *{signal}*\n"
                            f"Entry: `{entry_t.strftime('%H:%M:00')}`\n"
                            f"Duration: 1 min"
                        )

        # Result Checking Logic
        if active_trade:
            finish_t = active_trade['entry_time'] + timedelta(minutes=1)
            if now >= finish_t + timedelta(seconds=5):
                # Reset active_trade for next signal 
                # (You can re-add your Martingale results logic here)
                active_trade = None
        
        time.sleep(0.5)

if __name__ == "__main__":
    threading.Thread(target=run_flask, daemon=True).start()
    start_engine()
