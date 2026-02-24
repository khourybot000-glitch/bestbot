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

# --- Flask Server for 24/7 Hosting ---
app = Flask(__name__)
@app.route('/')
def health_check(): return "EURJPY 20-Indicator Engine Online", 200

# --- Configuration ---
TOKEN = '8511172742:AAFxZIj8N07FB-tFnJ_l3rv13loyRMmsRYU'
CHAT_ID = '-1003731752986'
BEIRUT_TZ = pytz.timezone('Asia/Beirut')
WS_URL = "wss://blue.derivws.com/websockets/v3?app_id=16929"
SYMBOL_NAME = "EUR/JPY"
SYMBOL_ID = "frxEURJPY"

# Control Variables
last_signal_time = ""
active_trade = None
analysis_lock = threading.Lock()

def send_telegram_msg(text):
    url = f"https://api.telegram.org/bot{TOKEN}/sendMessage"
    try:
        requests.post(url, json={"chat_id": CHAT_ID, "text": text, "parse_mode": "Markdown"}, timeout=5)
    except Exception as e:
        print(f"Telegram Error: {e}")

def create_tick_candles(prices, ticks_per_candle=5):
    """Converts raw ticks into OHLC candles based on tick volume."""
    candles = []
    for i in range(0, len(prices), ticks_per_candle):
        chunk = prices[i:i+ticks_per_candle]
        if len(chunk) == ticks_per_candle:
            candles.append({
                'open': chunk[0],
                'high': np.max(chunk),
                'low': np.min(chunk),
                'close': chunk[-1]
            })
    return pd.DataFrame(candles)

def calculate_20_indicators(df):
    """Analysis engine applying 20 technical studies on tick-candles."""
    if len(df) < 20: return "NONE", 0
    
    c = df['close'].values
    h = df['high'].values
    l = df['low'].values
    o = df['open'].values
    up, down = 0, 0

    # 1. RSI (Relative Strength Index)
    diff = np.diff(c)
    gain = np.mean(np.where(diff > 0, diff, 0)[-14:])
    loss = np.mean(np.where(diff < 0, -diff, 0)[-14:])
    rsi = 100 - (100 / (1 + (gain / (loss + 1e-10))))
    if rsi < 50: up += 1
    else: down += 1

    # 2. SMA 10 | 3. EMA 5 | 4. Momentum | 5. Candle Color
    if c[-1] > np.mean(c[-10:]): up += 1
    else: down += 1
    if c[-1] > df['close'].ewm(span=5).mean().iloc[-1]: up += 1
    else: down += 1
    if c[-1] > c[-5]: up += 1
    else: down += 1
    if c[-1] > o[-1]: up += 1
    else: down += 1

    # 6. Bollinger Bands (Volatility Filter)
    std = np.std(c[-14:])
    mid = np.mean(c[-14:])
    if c[-1] < mid - std: up += 1
    elif c[-1] > mid + std: down += 1

    # 7. Williams %R | 8. ROC | 9. CCI | 10. Stochastic
    wpr = (np.max(h[-14:]) - c[-1]) / (np.max(h[-14:]) - np.min(l[-14:])) * -100
    if wpr < -50: up += 1
    else: down += 1
    if ((c[-1] - c[-10]) / c[-10]) > 0: up += 1
    else: down += 1
    tp = (h + l + c) / 3
    if (tp[-1] - np.mean(tp[-14:])) > 0: up += 1
    else: down += 1
    if (c[-1] - np.min(l[-14:])) / (np.max(h[-14:]) - np.min(l[-14:])) > 0.5: up += 1
    else: down += 1

    # 11-20. Micro-Trend algorithms
    for i in range(11, 21):
        if c[-1] > np.median(c[-min(i, len(c)):]): up += 1
        else: down += 1

    direction = "CALL" if up >= down else "PUT"
    strength = (max(up, down) / 20) * 100
    return direction, strength

def execute_signal_process():
    """Fetches 120 ticks, analyzes, and locks the bot if 75%+ strength is found."""
    global active_trade
    if active_trade is not None: return
    if not analysis_lock.acquire(blocking=False): return

    try:
        ws = websocket.create_connection(WS_URL, timeout=10)
        ws.send(json.dumps({"ticks_history": SYMBOL_ID, "count": 120, "end": "latest", "style": "ticks"}))
        res = json.loads(ws.recv())
        ws.close()
        
        if 'history' in res:
            prices = res['history']['prices']
            df_candles = create_tick_candles(prices, ticks_per_candle=5)
            direction, accuracy = calculate_20_indicators(df_candles)
            
            if accuracy >= 75:
                now = datetime.now(BEIRUT_TZ)
                # Entry is set for the start of the next minute
                entry_time = (now + timedelta(seconds=30)).replace(second=0, microsecond=0)
                
                active_trade = {
                    "direction": direction, 
                    "entry_time": entry_time,
                    "entry_price": prices[-1]
                }
                
                msg = f"🚀 **SIGNAL** | {SYMBOL_NAME}\n"
                msg += f"Action: *{direction}*\n"
                msg += f"Strength: `{accuracy}%` (5-Tick Analysis)\n"
                msg += f"Entry: {entry_time.strftime('%H:%M:00')}\n"
                msg += f"⚠️ *Bot locked until result...*"
                send_telegram_msg(msg)
    finally:
        analysis_lock.release()

def check_result_final():
    """Checks final price after 1 minute. No Martingale."""
    global active_trade
    now = datetime.now(BEIRUT_TZ)
    target_time = active_trade['entry_time'] + timedelta(minutes=1)
    
    if now.strftime('%H:%M:%S') == target_time.strftime('%H:%M:00'):
        time.sleep(1.2) # Sync delay
        try:
            ws = websocket.create_connection(WS_URL, timeout=5)
            ws.send(json.dumps({"ticks_history": SYMBOL_ID, "count": 5, "end": "latest", "style": "ticks"}))
            res = json.loads(ws.recv())
            ws.close()
            final_price = res['history']['prices'][-1]
            
            is_win = (final_price > active_trade['entry_price']) if active_trade['direction'] == "CALL" else (final_price < active_trade['entry_price'])
            status = "✅ **WIN**" if is_win else "❌ **LOSS**"
            
            msg = f"{status} | {SYMBOL_NAME}\n"
            msg += f"Direction: {active_trade['direction']}\n"
            msg += f"Entry: {active_trade['entry_price']}\nExit: {final_price}\n"
            msg += f"🔄 *Analyzing next opportunities...*"
            send_telegram_msg(msg)
            
            active_trade = None # Unlock the bot
        except Exception as e:
            print(f"Result Check Error: {e}")

def start_engine():
    global last_signal_time
    print(f"Bot Active: {SYMBOL_NAME} | 120-Tick Analysis | Lock Enabled")
    while True:
        now = datetime.now(BEIRUT_TZ)
        
        # Analyze only at second 30 and if no active trade is running
        if now.second == 30 and active_trade is None:
            current_min = now.strftime('%H:%M')
            if last_signal_time != current_min:
                last_signal_time = current_min
                threading.Thread(target=execute_signal_process).start()
        
        # Final result check at the end of the entry minute
        if active_trade and now.second == 0:
            check_result_final()
            
        time.sleep(0.5)

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    threading.Thread(target=lambda: app.run(host='0.0.0.0', port=port), daemon=True).start()
    start_engine()
