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

# --- 1. Flask Server (Uptime Management) ---
app = Flask(__name__)
@app.route('/')
def home(): return "Synced 5-Min Professional Bot: ACTIVE", 200

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

# --- 3. Persistence Logic (Redeploy Protection) ---
def save_state(trade):
    try:
        if trade:
            temp_trade = trade.copy()
            temp_trade['entry_time'] = trade['entry_time'].strftime('%Y-%m-%d %H:%M:%S')
            with open(STATE_FILE, 'w') as f:
                json.dump(temp_trade, f)
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
    return None

# --- 4. Market Schedule Check ---
def is_market_open(now):
    is_weekday = 0 <= now.weekday() <= 4  # Monday to Friday
    is_hours = 9 <= now.hour < 21        # 09:00 to 21:00
    return is_weekday and is_hours

# --- 5. Auto-Verification Logic ---
def verify_5min_result(entry_time, direction):
    try:
        start_ts = int(entry_time.timestamp())
        end_ts = start_ts + (5 * 60)
        ws = websocket.create_connection(WS_URL, timeout=15)
        ws.send(json.dumps({"ticks_history": SYMBOL_ID, "start": start_ts, "end": end_ts, "style": "ticks"}))
        res = json.loads(ws.recv()); ws.close()
        prices = res['history']['prices']
        if not prices: return "RESULT: Data Unavailable"
        
        p_in, p_out = float(prices[0]), float(prices[-1])
        win = (p_out > p_in) if direction == "CALL" else (p_out < p_in)
        
        # Detailed English Result Message
        status = "WIN ✅" if win else "LOSS ❌"
        return f"Final Status: *{status}*"
    except: return "RESULT: Verification Error"

# --- 6. Core Strategy Logic ---
def check_5min_strategy(now):
    try:
        start_candle_0 = now.replace(minute=(now.minute // 5) * 5, second=0, microsecond=0)
        mid_point = start_candle_0 + timedelta(minutes=2, seconds=30)
        analysis_time = start_candle_0 + timedelta(minutes=4, seconds=30)
        
        ws = websocket.create_connection(WS_URL, timeout=15)
        ws.send(json.dumps({
            "ticks_history": SYMBOL_ID,
            "start": int(start_candle_0.timestamp()),
            "end": int(analysis_time.timestamp()),
            "style": "ticks"
        }))
        res = json.loads(ws.recv()); ws.close()
        
        prices, times = res['history']['prices'], res['history']['times']
        if not prices or len(prices) < 10: return None
        
        df = pd.DataFrame({'price': prices, 'time': times})
        part1 = df[df['time'] < mid_point.timestamp()]
        part2 = df[df['time'] >= mid_point.timestamp()]
        
        if part1.empty or part2.empty: return None
        p1_start, p1_end = float(part1['price'].iloc[0]), float(part1['price'].iloc[-1])
        p2_start, p2_end = float(part2['price'].iloc[0]), float(part2['price'].iloc[-1])
        
        if (p1_end > p1_start) and (p2_end < p2_start) and (p2_end < p1_start): return "PUT"
        if (p1_end < p1_start) and (p2_end > p2_start) and (p2_end > p1_start): return "CALL"
        return None
    except: return None

# --- 7. Main Engine Loop ---
def start_engine():
    global last_analysis_time, active_trade
    active_trade = load_state() # Resume if redeployed
    
    print("Bot is monitoring EUR/JPY (9 AM - 9 PM, Mon-Fri)...")

    while True:
        now = datetime.now(BEIRUT_TZ)
        
        if is_market_open(now):
            # Analyze ONLY at Minute 4 (or 9, 14, 19...) at Second 30
            if (now.minute % 5 == 4) and (now.second == 30) and active_trade is None:
                current_ts = now.strftime('%H:%M')
                if last_analysis_time != current_ts:
                    last_analysis_time = current_ts
                    signal = check_5min_strategy(now)
                    
                    if signal:
                        entry_time = (now + timedelta(minutes=1)).replace(second=0, microsecond=0)
                        active_trade = {"dir": signal, "entry_time": entry_time}
                        save_state(active_trade) # Immediate save
                        
                        msg = (
                            f"🔔 **PREMIUM SIGNAL FOUND**\n"
                            f"━━━━━━━━━━━━━━━━━━\n"
                            f"📈 **Asset:** `{SYMBOL_NAME}`\n"
                            f"🧭 **Direction:** *{signal}*\n"
                            f"🕒 **Entry Time:** `{entry_time.strftime('%H:%M:00')}`\n"
                            f"⌛ **Duration:** 5 Minutes\n"
                            f"━━━━━━━━━━━━━━━━━━"
                        )
                        send_telegram_msg(msg)
        
        # Verify Results (Always runs if a trade is pending)
        if active_trade:
            finish_time = active_trade['entry_time'] + timedelta(minutes=5)
            if now >= finish_time + timedelta(seconds=15):
                res_status = verify_5min_result(active_trade['entry_time'], active_trade['dir'])
                send_telegram_msg(f"📊 **TRADE RESULT**\n{res_status}")
                active_trade = None
                save_state(None) # Clear state after reporting
        
        time.sleep(0.5)

if __name__ == "__main__":
    threading.Thread(target=run_flask, daemon=True).start()
    start_engine()
