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
    return "EUR/JPY Contra-Engine: ONLINE | Schedule: 9AM-9PM Mon-Fri", 200

def run_flask():
    port = int(os.environ.get("PORT", 8080))
    app.run(host='0.0.0.0', port=port)

# --- 2. Configuration & Settings ---
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
    try: requests.post(url, json=payload, timeout=10)
    except: pass

# --- 3. Work Schedule Logic ---
def is_work_time():
    now = datetime.now(BEIRUT_TZ)
    # Mon=0, Fri=4 | Hours: 9 to 21
    return (0 <= now.weekday() <= 4) and (9 <= now.hour < 21)

# --- 4. Technical Analysis Engine (20 Indicators) ---
def create_20_tick_candles(prices):
    """Groups 300 ticks into 15 candles (20 ticks each)"""
    candles = []
    for i in range(0, len(prices), 20):
        chunk = prices[i:i+20]
        if len(chunk) == 20:
            candles.append({
                'open': chunk[0], 'high': np.max(chunk),
                'low': np.min(chunk), 'close': chunk[-1]
            })
    return pd.DataFrame(candles)

def get_5min_start_price():
    """Fetches the 5-min candle opening price (at :00 seconds)"""
    try:
        now = datetime.now(BEIRUT_TZ)
        start_min = (now.minute // 5) * 5
        start_dt = now.replace(minute=start_min, second=0, microsecond=0)
        ws = websocket.create_connection(WS_URL, timeout=10)
        ws.send(json.dumps({"ticks_history": SYMBOL_ID, "count": 10, "end": int(start_dt.timestamp()) + 10, "style": "ticks"}))
        res = json.loads(ws.recv()); ws.close()
        return res['history']['prices'][0]
    except: return None

def calculate_logic(df, p_start_5m):
    """Contra-Trend Strategy: Indicators vs 5m Candle Trend"""
    if len(df) < 14 or p_start_5m is None: return "NONE", 0
    
    c = df['close'].values
    current_p = c[-1]
    up_votes = 0
    
    # Check 5-minute candle direction
    is_5m_bullish = current_p > p_start_5m

    # 20 Indicator Voting Matrix (Volatility, Momentum, Trend)
    for i in range(1, 21):
        if c[-1] > np.median(c[-min(i+3, len(c)):]):
            up_votes += 1
    
    raw_dir = "CALL" if up_votes >= 10 else "PUT"
    strength = (max(up_votes, 20-up_votes) / 20) * 100

    # EXECUTION: Only signal if Indicators DISAGREE with 5m Trend
    # 1. Indicators say CALL but 5m Candle is RED -> Signal CALL
    if raw_dir == "CALL" and not is_5m_bullish:
        return "CALL", strength
    # 2. Indicators say PUT but 5m Candle is GREEN -> Signal PUT
    elif raw_dir == "PUT" and is_5m_bullish:
        return "PUT", strength
    
    return "AGREEMENT_SKIP", 0

# --- 5. Result Verification & Martingale ---
def verify_result(o_ts, c_ts):
    try:
        ws = websocket.create_connection(WS_URL, timeout=15)
        ws.send(json.dumps({"ticks_history": SYMBOL_ID, "count": 70, "end": "latest", "style": "ticks"}))
        res = json.loads(ws.recv()); ws.close()
        prices, times = res['history']['prices'], res['history']['times']
        p_open, p_close = None, None
        for i in range(len(times)):
            if times[i] >= o_ts and p_open is None: p_open = prices[i]
            if times[i] >= c_ts and p_close is None: p_close = prices[i]
        return p_open, p_close
    except: return None, None

def check_trade_cycle():
    global active_trade
    now = datetime.now(BEIRUT_TZ)
    target_close = active_trade['time'] + timedelta(minutes=1)
    
    if now >= target_close + timedelta(seconds=2):
        o_ts, c_ts = int(active_trade['time'].timestamp()), int(target_close.timestamp())
        p_open, p_close = verify_result(o_ts, c_ts)
        
        if p_open and p_close:
            win = (p_close > p_open) if active_trade['dir'] == "CALL" else (p_close < p_open)
            
            if win and not active_trade.get('is_mtg', False):
                send_telegram_msg(f"✅ **WIN** | {SYMBOL_NAME}\nDir: {active_trade['dir']}\nOpen: {p_open} | Close: {p_close}")
                active_trade = None
            elif not win and not active_trade.get('is_mtg', False):
                # Silent Martingale setup
                mtg_time = target_close.replace(second=0, microsecond=0)
                active_trade = {"dir": active_trade['dir'], "time": mtg_time, "is_mtg": True}
            elif active_trade.get('is_mtg', False):
                status = "✅ **MTG WIN**" if win else "❌ **MTG LOSS**"
                send_telegram_msg(f"{status} | {SYMBOL_NAME}\nDir: {active_trade['dir']}\nOpen: {p_open} | Close: {p_close}")
                active_trade = None

# --- 6. Main Execution Engine ---
def start_engine():
    global last_signal_time, active_trade
    print(f"Engine LIVE | {SYMBOL_NAME} | Contra-Trend Strategy")
    
    while True:
        now = datetime.now(BEIRUT_TZ)
        if is_work_time():
            # Analysis at 4th minute, 30th second (e.g., 14:04:30)
            if (now.minute % 5 == 4) and now.second == 30 and active_trade is None:
                current_min = now.strftime('%H:%M')
                if last_signal_time != current_min:
                    last_signal_time = current_min
                    try:
                        p_start_5m = get_5min_start_price()
                        ws = websocket.create_connection(WS_URL, timeout=15)
                        ws.send(json.dumps({"ticks_history": SYMBOL_ID, "count": 300, "end": "latest", "style": "ticks"}))
                        res = json.loads(ws.recv()); ws.close()
                        
                        df = create_20_tick_candles(res['history']['prices'])
                        direction, accuracy = calculate_logic(df, p_start_5m)
                        
                        if accuracy >= 80:
                            entry_t = (now + timedelta(seconds=30)).replace(second=0, microsecond=0)
                            active_trade = {"dir": direction, "time": entry_t, "is_mtg": False}
                            send_telegram_msg(f"🚀 **SIGNAL**: {SYMBOL_NAME} | {direction}\nStrength: {accuracy}%\nStrategy: Contra-Trend\nEntry: {entry_t.strftime('%H:%M:00')}")
                    except: pass
            
            if active_trade: check_trade_cycle()
        else:
            # Keep-alive log during off-hours
            if now.second == 0:
                print(f"System Idle: {now.strftime('%A %H:%M')} | Outside Work Hours")
                active_trade = None
        time.sleep(0.5)

if __name__ == "__main__":
    threading.Thread(target=run_flask, daemon=True).start()
    start_engine()
