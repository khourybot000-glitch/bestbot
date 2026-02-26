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

# --- 1. Flask Server ---
app = Flask(__name__)
@app.route('/')
def home(): return "MTG 5-Min Bot: ACTIVE", 200

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
STATE_FILE = "trade_state.json"

active_trade = None
last_analysis_time = ""

def send_telegram_msg(text):
    url = f"https://api.telegram.org/bot{TOKEN}/sendMessage"
    payload = {"chat_id": CHAT_ID, "text": text, "parse_mode": "Markdown"}
    try: requests.post(url, json=payload, timeout=10)
    except: pass

# --- 3. Persistence Logic ---
def save_state(trade):
    try:
        if trade:
            temp_trade = trade.copy()
            temp_trade['entry_time'] = trade['entry_time'].strftime('%Y-%m-%d %H:%M:%S')
            with open(STATE_FILE, 'w') as f: json.dump(temp_trade, f)
        else:
            if os.path.exists(STATE_FILE): os.remove(STATE_FILE)
    except: pass

def load_state():
    try:
        if os.path.exists(STATE_FILE):
            with open(STATE_FILE, 'r') as f:
                trade = json.load(f)
                trade['entry_time'] = datetime.strptime(trade['entry_time'], '%Y-%m-%d %H:%M:%S').replace(tzinfo=BEIRUT_TZ)
                return trade
    except: return None

# --- 4. Market Hours ---
def is_market_open(now):
    return 0 <= now.weekday() <= 4 and 9 <= now.hour < 21

# --- 5. Data Retrieval & Verification ---
def get_price_move(start_time, duration_mins):
    try:
        start_ts = int(start_time.timestamp())
        end_ts = start_ts + (duration_mins * 60)
        ws = websocket.create_connection(WS_URL, timeout=15)
        ws.send(json.dumps({"ticks_history": SYMBOL_ID, "start": start_ts, "end": end_ts, "style": "ticks"}))
        res = json.loads(ws.recv()); ws.close()
        prices = res['history']['prices']
        if not prices: return None, None
        return float(prices[0]), float(prices[-1])
    except: return None, None

# --- 6. Core Strategy Logic ---
def check_5min_strategy(now):
    try:
        start_candle_0 = now.replace(minute=(now.minute // 5) * 5, second=0, microsecond=0)
        mid_point = start_candle_0 + timedelta(minutes=2, seconds=30)
        analysis_time = start_candle_0 + timedelta(minutes=4, seconds=30)
        
        ws = websocket.create_connection(WS_URL, timeout=15)
        ws.send(json.dumps({"ticks_history": SYMBOL_ID, "start": int(start_candle_0.timestamp()), "end": int(analysis_time.timestamp()), "style": "ticks"}))
        res = json.loads(ws.recv()); ws.close()
        
        prices, times = res['history']['prices'], res['history']['times']
        df = pd.DataFrame({'price': prices, 'time': times})
        part1 = df[df['time'] < mid_point.timestamp()]
        part2 = df[df['time'] >= mid_point.timestamp()]
        
        p1_start, p1_end = float(part1['price'].iloc[0]), float(part1['price'].iloc[-1])
        p2_start, p2_end = float(part2['price'].iloc[0]), float(part2['price'].iloc[-1])
        
        if (p1_end > p1_start) and (p2_end < p2_start) and (p2_end < p1_start): return "PUT"
        if (p1_end < p1_start) and (p2_end > p2_start) and (p2_end > p1_start): return "CALL"
        return None
    except: return None

# --- 7. Main Loop ---
def start_engine():
    global last_analysis_time, active_trade
    active_trade = load_state()

    while True:
        now = datetime.now(BEIRUT_TZ)
        
        # 1. Analysis Logic
        if is_market_open(now) and active_trade is None:
            if (now.minute % 5 == 4) and (now.second == 30):
                current_ts = now.strftime('%H:%M')
                if last_analysis_time != current_ts:
                    last_analysis_time = current_ts
                    signal = check_5min_strategy(now)
                    if signal:
                        entry_t = (now + timedelta(minutes=1)).replace(second=0, microsecond=0)
                        active_trade = {"dir": signal, "entry_time": entry_t, "step": "INITIAL"}
                        save_state(active_trade)
                        send_telegram_msg(f"🚀 **NEW SIGNAL**\nPair: `{SYMBOL_NAME}`\nDIRECTION: *{signal}*\nENTRY: `{entry_t.strftime('%H:%M:00')}`\nTF: 5M")

        # 2. Results & MTG Logic
        if active_trade:
            # Check Initial Result
            if active_trade['step'] == "INITIAL":
                finish_t = active_trade['entry_time'] + timedelta(minutes=5)
                if now >= finish_t + timedelta(seconds=6):
                    p_in, p_out = get_price_move(active_trade['entry_time'], 5)
                    win = (p_out > p_in) if active_trade['dir'] == "CALL" else (p_out < p_in)
                    
                    if win:
                        send_telegram_msg(f"📊 **TRADE RESULT**\nStatus: *WIN ✅*")
                        active_trade = None
                        save_state(None)
                    else:
                        # Silent loss, move to MTG
                        active_trade['step'] = "MTG"
                        save_state(active_trade)
            
            # Check MTG Result
            elif active_trade['step'] == "MTG":
                mtg_start_t = active_trade['entry_time'] + timedelta(minutes=5)
                mtg_finish_t = mtg_start_t + timedelta(minutes=5)
                if now >= mtg_finish_t + timedelta(seconds=6):
                    p_in, p_out = get_price_move(mtg_start_t, 5)
                    win = (p_out > p_in) if active_trade['dir'] == "CALL" else (p_out < p_in)
                    
                    status = "MTG WIN ✅" if win else "MTG LOSS ❌"
                    send_telegram_msg(f"📊 **TRADE RESULT**\nStatus: *{status}*")
                    active_trade = None
                    save_state(None)
        
        time.sleep(0.5)

if __name__ == "__main__":
    threading.Thread(target=run_flask, daemon=True).start()
    start_engine()
