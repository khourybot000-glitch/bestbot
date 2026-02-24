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
def home():
    return "Bot is Running - Signal Repeat Allowed", 200

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
    payload = {"chat_id": CHAT_ID, "text": text, "parse_mode": "Markdown"}
    try:
        requests.post(url, json=payload, timeout=10)
    except:
        pass

# --- 3. Analysis Engine (300 Ticks / 20-Tick Candles) ---
def create_20_tick_candles(prices):
    """Groups 300 ticks into 15 candles (20 ticks each)"""
    candles = []
    for i in range(0, len(prices), 20):
        chunk = prices[i:i+20]
        if len(chunk) == 20:
            candles.append({
                'open': chunk[0],
                'high': np.max(chunk),
                'low': np.min(chunk),
                'close': chunk[-1]
            })
    return pd.DataFrame(candles)

def calculate_20_indicators(df):
    """Applies 20 detailed technical indicators"""
    if len(df) < 14: return "NONE", 0
    c, o = df['close'].values, df['open'].values
    up = 0
    
    # Core Indicators (RSI, SMA, EMA, Momentum, Color)
    # 1. RSI
    diff = np.diff(c)
    gain = np.mean(np.where(diff > 0, diff, 0)[-14:])
    loss = np.mean(np.where(diff < 0, -diff, 0)[-14:])
    rsi = 100 - (100 / (1 + (gain / (loss + 1e-10))))
    if rsi < 50: up += 1

    # 2. SMA 10 | 3. EMA 5 | 4. Momentum | 5. Candle Color
    if c[-1] > np.mean(c[-10:]): up += 1
    if c[-1] > df['close'].ewm(span=5).mean().iloc[-1]: up += 1
    if c[-1] > c[-5]: up += 1
    if c[-1] > o[-1]: up += 1

    # 6-20. Micro-Trend Confirmations
    for i in range(6, 21):
        if c[-1] > np.median(c[-min(i, len(c)):]):
            up += 1
            
    direction = "CALL" if up >= 10 else "PUT"
    strength = (max(up, 20-up) / 20) * 100
    return direction, strength

# --- 4. Historical Result Verification (70 Ticks) ---
def get_historical_result(open_ts, close_ts):
    """Fetches 70 fresh ticks to compare 12:06:00 vs 12:07:00 prices"""
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
    except:
        return None, None

def check_trade_cycle():
    global active_trade
    now = datetime.now(BEIRUT_TZ)
    target_close = active_trade['entry_time'] + timedelta(minutes=1)
    
    # Verify result at 12:07:02
    if now >= target_close + timedelta(seconds=2):
        o_ts, c_ts = int(active_trade['entry_time'].timestamp()), int(target_close.timestamp())
        p_open, p_close = get_historical_result(o_ts, c_ts)
        
        if p_open and p_close:
            win = (p_close > p_open) if active_trade['direction'] == "CALL" else (p_close < p_open)
            status = "✅ WIN" if win else "❌ LOSS"
            if p_open == p_close: status = "⚖️ DRAW"
            
            msg = f"{status} | {SYMBOL_NAME}\n"
            msg += f"Dir: {active_trade['direction']}\n"
            msg += f"Open: {p_open} | Close: {p_close}\n"
            msg += f"🔄 *Scanning for next signal...*"
            send_telegram_msg(msg)
            active_trade = None

# --- 5. Main Engine (Analysis & Execution) ---
def start_engine():
    global last_signal_time, active_trade
    print(f"Engine LIVE: Monitoring {SYMBOL_NAME}...")
    
    while True:
        now = datetime.now(BEIRUT_TZ)
        
        if now.second == 30 and active_trade is None:
            current_min = now.strftime('%H:%M')
            if last_signal_time != current_min:
                last_signal_time = current_min
                try:
                    ws = websocket.create_connection(WS_URL, timeout=15)
                    ws.send(json.dumps({"ticks_history": SYMBOL_ID, "count": 300, "end": "latest", "style": "ticks"}))
                    res = json.loads(ws.recv())
                    ws.close()
                    
                    df_candles = create_20_tick_candles(res['history']['prices'])
                    direction, accuracy = calculate_20_indicators(df_candles)
                    
                    # Send Signal if accuracy >= 80% (Repeat direction is allowed now)
                    if accuracy >= 80:
                        entry_dt = (now + timedelta(seconds=30)).replace(second=0, microsecond=0)
                        active_trade = {"direction": direction, "entry_time": entry_dt}
                        
                        msg = f"🚀 **SIGNAL**: {direction}\nStrength: {accuracy}%\nAnalysis: 20-Tick Candles\nEntry: {entry_dt.strftime('%H:%M:00')}"
                        send_telegram_msg(msg)
                except:
                    pass
        
        if active_trade:
            check_trade_cycle()
            
        time.sleep(0.5)

if __name__ == "__main__":
    threading.Thread(target=run_flask, daemon=True).start()
    start_engine()
