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

# --- Flask Server for Deployment ---
app = Flask(__name__)
@app.route('/')
def health_check(): return "Ultra-Fast Signal Engine Active", 200

# --- Configuration ---
TOKEN = '8511172742:AAFxZIj8N07FB-tFnJ_l3rv13loyRMmsRYU'
CHAT_ID = '-1003731752986'
BEIRUT_TZ = pytz.timezone('Asia/Beirut')
WS_URL = "wss://blue.derivws.com/websockets/v3?app_id=16929"
SYMBOL = "frxEURGBP"

# Control Variables
last_signal_time = ""
active_trade = None

def send_telegram_msg(text):
    url = f"https://api.telegram.org/bot{TOKEN}/sendMessage"
    try:
        requests.post(url, json={"chat_id": CHAT_ID, "text": text, "parse_mode": "Markdown"}, timeout=5)
    except:
        pass

def fast_analyze(df):
    """Highly optimized indicator calculation using NumPy."""
    close = df['close'].values
    high = df['high'].values
    low = df['low'].values
    open_p = df['open'].values
    
    up_points = 0
    down_points = 0

    # 1. Fast RSI Calculation (14 periods)
    diff = np.diff(close)
    gain = np.where(diff > 0, diff, 0)
    loss = np.where(diff < 0, -diff, 0)
    avg_gain = np.mean(gain[-14:])
    avg_loss = np.mean(loss[-14:])
    rsi = 100 - (100 / (1 + (avg_gain / (avg_loss + 1e-10))))
    
    if rsi < 50: up_points += 5
    else: down_points += 5

    # 2. Fast EMA 8/21 (using pandas ewm for speed)
    ema8 = df['close'].ewm(span=8).mean().iloc[-1]
    ema21 = df['close'].ewm(span=21).mean().iloc[-1]
    
    if close[-1] > ema8: up_points += 5
    else: down_points += 5
    if ema8 > ema21: up_points += 5
    else: down_points += 5

    # 3. Momentum & Price Action
    if close[-1] > open_p[-1]: up_points += 5
    else: down_points += 5

    total_indicators = 20
    direction = "CALL" if up_points >= down_points else "PUT"
    strength = (max(up_points, down_points) / total_indicators) * 100
    return direction, strength

def check_result_instant():
    """Checks the outcome at exactly second 00 using Tick data."""
    global active_trade
    now = datetime.now(BEIRUT_TZ)
    
    # Define the 1-minute window to check
    # If entry was 12:05, check at 12:06
    check_minute = active_trade['entry_time'] + timedelta(minutes=1)
    
    if now.strftime('%H:%M:%S') == check_minute.strftime('%H:%M:00'):
        time.sleep(0.6) # Wait slightly for the final tick to register
        try:
            ws = websocket.create_connection(WS_URL, timeout=5)
            ws.send(json.dumps({"ticks_history": SYMBOL, "count": 100, "end": "latest", "style": "ticks"}))
            res = json.loads(ws.recv())
            ws.close()
            
            prices = res['history']['prices']
            # Compare first tick of the minute vs last tick of the minute
            is_win = (prices[-1] > prices[0]) if active_trade['direction'] == "CALL" else (prices[-1] < prices[0])
            
            if is_win:
                send_telegram_msg("✅ **WIN**")
                active_trade = None
            else:
                if active_trade['status'] == "WAITING":
                    active_trade['status'] = "MTG"
                    print("Lost first minute, waiting for MTG result...")
                else:
                    send_telegram_msg("❌ **MTG LOSS**")
                    active_trade = None
        except:
            pass

def process_signal():
    """Main signal handler triggered at second 30."""
    global active_trade
    try:
        ws = websocket.create_connection(WS_URL, timeout=5)
        ws.send(json.dumps({"ticks_history": SYMBOL, "count": 50, "end": "latest", "style": "candles", "granularity": 300}))
        res = json.loads(ws.recv())
        ws.close()
        
        df = pd.DataFrame(res['candles'])
        direction, accuracy = fast_analyze(df)
        
        now = datetime.now(BEIRUT_TZ)
        entry_time = (now + timedelta(seconds=30)).replace(second=0, microsecond=0)
        
        active_trade = {
            "direction": direction,
            "entry_time": entry_time,
            "status": "WAITING"
        }
        
        msg = f"🚀 **SIGNAL ALERT**\n"
        msg += f"🎯 Action: *{direction}*\n"
        msg += f"📈 Confidence: `{accuracy}%`\n"
        msg += f"🕐 Entry: {entry_time.strftime('%H:%M:00')}\n"
        msg += f"⏱ Duration: 1m"
        send_telegram_msg(msg)
    except:
        pass

def engine():
    global last_signal_time
    print("Bot Core Running. Analysis at sec 30, Result at sec 00.")
    while True:
        now = datetime.now(BEIRUT_TZ)
        
        # 1. Trigger Signal Analysis (at XX:X4:30)
        if (now.minute + 1) % 5 == 0 and now.second == 30:
            current_min = now.strftime('%H:%M')
            if last_signal_time != current_min:
                threading.Thread(target=process_signal).start()
                last_signal_time = current_min

        # 2. Check Trade Results (at XX:XX:00)
        if active_trade and now.second == 0:
            check_result_instant()
            
        time.sleep(0.2)

if __name__ == "__main__":
    # Start Flask in background
    port = int(os.environ.get("PORT", 10000))
    threading.Thread(target=lambda: app.run(host='0.0.0.0', port=port), daemon=True).start()
    # Start the Bot Engine
    engine()
