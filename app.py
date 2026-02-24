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
def home(): return "Martingale Silent Mode Bot is Active", 200

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

# --- 3. Analysis Engine (5-min Cycle) ---
def create_20_tick_candles(prices):
    candles = []
    for i in range(0, len(prices), 20):
        chunk = prices[i:i+20]
        if len(chunk) == 20:
            candles.append({'open': chunk[0], 'high': np.max(chunk), 'low': np.min(chunk), 'close': chunk[-1]})
    return pd.DataFrame(candles)

def get_5min_start_price():
    try:
        now = datetime.now(BEIRUT_TZ)
        start_min = (now.minute // 5) * 5
        start_dt = now.replace(minute=start_min, second=0, microsecond=0)
        ws = websocket.create_connection(WS_URL, timeout=10)
        ws.send(json.dumps({"ticks_history": SYMBOL_ID, "count": 10, "end": int(start_dt.timestamp()) + 10, "style": "ticks"}))
        res = json.loads(ws.recv()); ws.close()
        return res['history']['prices'][0]
    except: return None

def analyze_logic(df, start_price_5m):
    if len(df) < 14 or start_price_5m is None: return "NONE", 0
    current_price = df['close'].iloc[-1]
    up_votes = 0
    is_bullish_5m = current_price > start_price_5m
    close_prices = df['close'].values
    for i in range(1, 21):
        if close_prices[-1] > np.median(close_prices[-min(i+3, len(close_prices)):]): up_votes += 1
    raw_dir = "CALL" if up_votes >= 10 else "PUT"
    strength = (max(up_votes, 20-up_votes) / 20) * 100
    if raw_dir == "CALL" and is_bullish_5m: return "CALL", strength
    elif raw_dir == "PUT" and not is_bullish_5m: return "PUT", strength
    return "MISMATCH", 0

# --- 4. Result Verification & Silent Martingale Logic ---
def verify_result(open_ts, close_ts):
    try:
        ws = websocket.create_connection(WS_URL, timeout=15)
        ws.send(json.dumps({"ticks_history": SYMBOL_ID, "count": 70, "end": "latest", "style": "ticks"}))
        res = json.loads(ws.recv()); ws.close()
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
            
            # Scenario A: First attempt Wins
            if win and not active_trade.get('is_mtg', False):
                send_telegram_msg(f"✅ **WIN** | {SYMBOL_NAME}\nDir: {active_trade['dir']}\nPrice: {p_open} -> {p_close}")
                active_trade = None
            
            # Scenario B: First attempt Loses (Silent Martingale Start)
            elif not win and not active_trade.get('is_mtg', False):
                # We stay silent in Telegram, but set up the MTG internally
                mtg_time = target_close.replace(second=0, microsecond=0)
                active_trade = {"dir": active_trade['dir'], "time": mtg_time, "is_mtg": True}
                print(f"DEBUG: First trade lost. Starting internal Martingale for {mtg_time}")
            
            # Scenario C: Martingale result (Final)
            elif active_trade.get('is_mtg', False):
                status = "✅ **MTG WIN**" if win else "❌ **MTG LOSS**"
                send_telegram_msg(f"{status} | {SYMBOL_NAME}\nDir: {active_trade['dir']}\nPrice: {p_open} -> {p_close}")
                active_trade = None

# --- 5. Main Loop ---
def start_bot():
    global last_signal_time, active_trade
    print("Bot LIVE: 5-Min Cycle with Silent Martingale Logic")
    while True:
        now = datetime.now(BEIRUT_TZ)
        if (now.minute % 5 == 4) and now.second == 30 and active_trade is None:
            current_time_key = now.strftime('%H:%M')
            if last_signal_time != current_time_key:
                last_signal_time = current_time_key
                try:
                    start_5m = get_5min_start_price()
                    ws = websocket.create_connection(WS_URL, timeout=15)
                    ws.send(json.dumps({"ticks_history": SYMBOL_ID, "count": 300, "end": "latest", "style": "ticks"}))
                    res = json.loads(ws.recv()); ws.close()
                    df = create_20_tick_candles(res['history']['prices'])
                    direction, accuracy = analyze_logic(df, start_5m)
                    if accuracy >= 80:
                        entry_time = (now + timedelta(seconds=30)).replace(second=0, microsecond=0)
                        active_trade = {"dir": direction, "time": entry_time, "is_mtg": False}
                        send_telegram_msg(f"🚀 **SIGNAL**: {direction}\nStrength: {accuracy}%\nEntry: {entry_time.strftime('%H:%M:00')}")
                except Exception as e: print(f"Error: {e}")
        
        if active_trade: check_active_trade()
        time.sleep(0.5)

if __name__ == "__main__":
    threading.Thread(target=run_flask, daemon=True).start()
    start_bot()
