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

# --- 1. Flask Server for Uptime Monitoring ---
app = Flask(__name__)
@app.route('/')
def home(): return "Synced 5-Min Bot: ACTIVE", 200

def run_flask():
    port = int(os.environ.get("PORT", 8080))
    app.run(host='0.0.0.0', port=port)

# --- 2. Configuration & API Keys ---
TOKEN = '8511172742:AAFxZIj8N07FB-tFnJ_l3rv13loyRMmsRYU'
CHAT_ID = '-1003731752986'
BEIRUT_TZ = pytz.timezone('Asia/Beirut')
WS_URL = "wss://blue.derivws.com/websockets/v3?app_id=16929"
SYMBOL_ID = "frxEURJPY"
SYMBOL_NAME = "EUR/JPY"

active_trade = None
last_analysis_time = ""

def send_telegram_msg(text):
    url = f"https://api.telegram.org/bot{TOKEN}/sendMessage"
    payload = {"chat_id": CHAT_ID, "text": text, "parse_mode": "Markdown"}
    try: requests.post(url, json=payload, timeout=10)
    except: pass

# --- 3. Auto-Verification Logic (5-Minute Duration) ---
def verify_5min_result(entry_time, direction):
    try:
        start_ts = int(entry_time.timestamp())
        end_ts = start_ts + (5 * 60)
        
        ws = websocket.create_connection(WS_URL, timeout=15)
        ws.send(json.dumps({"ticks_history": SYMBOL_ID, "start": start_ts, "end": end_ts, "style": "ticks"}))
        res = json.loads(ws.recv()); ws.close()
        
        prices = res['history']['prices']
        if not prices: return "RESULT: Data Error"
        
        p_open, p_close = float(prices[0]), float(prices[-1])
        win = (p_close > p_open) if direction == "CALL" else (p_close < p_open)
        return "RESULT: WIN ✅" if win else "RESULT: LOSS ❌"
    except: return "RESULT: Verification Error"

# --- 4. Core Strategy: Time-Segment Analysis ---
def check_5min_strategy(now):
    try:
        # Syncing with the start of the current 5-min candle (e.g., 12:00:00)
        start_candle_0 = now.replace(minute=(now.minute // 5) * 5, second=0, microsecond=0)
        # Mid-point of the candle (02:30)
        mid_point = start_candle_0 + timedelta(minutes=2, seconds=30)
        # The exact moment of analysis (04:30)
        analysis_time = start_candle_0 + timedelta(minutes=4, seconds=30)
        
        ws = websocket.create_connection(WS_URL, timeout=15)
        ws.send(json.dumps({
            "ticks_history": SYMBOL_ID,
            "start": int(start_candle_0.timestamp()),
            "end": int(analysis_time.timestamp()),
            "style": "ticks"
        }))
        res = json.loads(ws.recv()); ws.close()
        
        prices = res['history']['prices']
        times = res['history']['times']
        if not prices or len(prices) < 10: return None

        df = pd.DataFrame({'price': prices, 'time': times})
        part1 = df[df['time'] < mid_point.timestamp()]
        part2 = df[df['time'] >= mid_point.timestamp()]
        
        if part1.empty or part2.empty: return None

        p1_start, p1_end = float(part1['price'].iloc[0]), float(part1['price'].iloc[-1])
        p2_start, p2_end = float(part2['price'].iloc[0]), float(part2['price'].iloc[-1])
        
        # --- PUT Strategy ---
        if (p1_end > p1_start) and (p2_end < p2_start) and (p2_end < p1_start):
            return "PUT"
            
        # --- CALL Strategy ---
        if (p1_end < p1_start) and (p2_end > p2_start) and (p2_end > p1_start):
            return "CALL"

        return None
    except: return None

# --- 5. Main Loop: Executing Analysis at Min 4 (Sec 30) ---
def start_engine():
    global last_analysis_time, active_trade
    print(f"Engine Online: Monitoring {SYMBOL_NAME} at 4:30 intervals.")

    while True:
        now = datetime.now(BEIRUT_TZ)
        
        # Check ONLY at the 4th minute of every 5-minute block at second 30
        if (now.minute % 5 == 4) and (now.second == 30) and active_trade is None:
            current_ts = now.strftime('%H:%M')
            if last_analysis_time != current_ts:
                last_analysis_time = current_ts
                
                signal = check_5min_strategy(now)
                if signal:
                    # Entry point: The start of the next 5-minute candle
                    entry_time = (now + timedelta(minutes=1)).replace(second=0, microsecond=0)
                    active_trade = {"dir": signal, "entry_time": entry_time}
                    
                    # Minimalist English Message
                    msg = (
                        f"🚀 **NEW SIGNAL**\n"
                        f"Pair: `{SYMBOL_NAME}`\n"
                        f"DIRECTION: *{signal}*\n"
                        f"ENTRY TIME: `{entry_time.strftime('%H:%M:00')}`\n"
                        f"TIME FRAME: 5M"
                    )
                    send_telegram_msg(msg)

        # Handle automatic result verification
        if active_trade:
            finish_time = active_trade['entry_time'] + timedelta(minutes=5)
            # Check 15 seconds after the trade ends to ensure data availability
            if now >= finish_time + timedelta(seconds=15):
                res_status = verify_5min_result(active_trade['entry_time'], active_trade['dir'])
                send_telegram_msg(f"📊 {res_status}")
                active_trade = None
        
        time.sleep(0.5)

if __name__ == "__main__":
    # Run Flask in a background thread to prevent port timeout on servers
    threading.Thread(target=run_flask, daemon=True).start()
    start_engine()
