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

# --- Flask Server ---
app = Flask(__name__)
@app.route('/')
def health_check(): return "20-Indicator Bot Active", 200

# --- Configuration ---
TOKEN = '8511172742:AAFxZIj8N07FB-tFnJ_l3rv13loyRMmsRYU'
CHAT_ID = '-1003731752986'
BEIRUT_TZ = pytz.timezone('Asia/Beirut')
WS_URL = "wss://blue.derivws.com/websockets/v3?app_id=16929"
SYMBOL_NAME = "EUR/JPY"
SYMBOL_ID = "frxEURJPY"

last_signal_time = ""
active_trade = None
analysis_lock = threading.Lock()

def send_telegram_msg(text):
    url = f"https://api.telegram.org/bot{TOKEN}/sendMessage"
    try: requests.post(url, json={"chat_id": CHAT_ID, "text": text, "parse_mode": "Markdown"}, timeout=5)
    except: pass

def calculate_20_indicators(df):
    """Engine that calculates 20 different technical indicators."""
    c = df['close'].values
    h = df['high'].values
    l = df['low'].values
    o = df['open'].values
    
    up = 0
    down = 0

    # 1. RSI
    diff = np.diff(c)
    gain = np.mean(np.where(diff > 0, diff, 0)[-14:])
    loss = np.mean(np.where(diff < 0, -diff, 0)[-14:])
    rsi = 100 - (100 / (1 + (gain / (loss + 1e-10))))
    if rsi < 50: up += 1
    else: down += 1

    # 2-4. Averages (SMA, EMA, WMA)
    if c[-1] > np.mean(c[-10:]): up += 1
    else: down += 1
    ema_20 = df['close'].ewm(span=20).mean().iloc[-1]
    if c[-1] > ema_20: up += 1
    else: down += 1
    wma_30 = df['close'].rolling(window=30).mean().iloc[-1] # Approximation
    if c[-1] > wma_30: up += 1
    else: down += 1

    # 5. Momentum
    if c[-1] > c[-10]: up += 1
    else: down += 1

    # 6. CCI
    tp = (h + l + c) / 3
    ma_tp = np.mean(tp[-14:])
    md_tp = np.mean(np.abs(tp[-14:] - ma_tp))
    cci = (tp[-1] - ma_tp) / (0.015 * md_tp)
    if cci > 0: up += 1
    else: down += 1

    # 7. Williams %R
    wpr = (np.max(h[-14:]) - c[-1]) / (np.max(h[-14:]) - np.min(l[-14:])) * -100
    if wpr < -50: up += 1
    else: down += 1

    # 8-9. Stochastic (%K, %D)
    stoch_k = (c[-1] - np.min(l[-14:])) / (np.max(h[-14:]) - np.min(l[-14:])) * 100
    if stoch_k > 50: up += 1
    else: down += 1
    if stoch_k > 80: down += 1 # Overbought
    else: up += 1

    # 10-11. MACD (Simple version)
    macd = np.mean(c[-12:]) - np.mean(c[-26:])
    if macd > 0: up += 1
    else: down += 1
    if macd > np.mean(c[-9:]): up += 1 # Signal
    else: down += 1

    # 12-13. Bollinger Bands
    std = np.std(c[-20:])
    upper = np.mean(c[-20:]) + (2 * std)
    lower = np.mean(c[-20:]) - (2 * std)
    if c[-1] < lower: up += 1
    else: down += 1
    if c[-1] > upper: down += 1
    else: up += 1

    # 14. Rate of Change (ROC)
    roc = ((c[-1] - c[-12]) / c[-12]) * 100
    if roc > 0: up += 1
    else: down += 1

    # 15. Price Action (Candle)
    if c[-1] > o[-1]: up += 1
    else: down += 1

    # 16. Highest High (Last 5)
    if c[-1] >= np.max(h[-5:]): up += 1
    else: down += 1

    # 17. Lowest Low (Last 5)
    if c[-1] <= np.min(l[-5:]): down += 1
    else: up += 1

    # 18. Standard Deviation check
    if std > np.mean(c[-20:]) * 0.0001: up += 1
    else: down += 1

    # 19. Median Price vs Close
    if c[-1] > (h[-1] + l[-1]) / 2: up += 1
    else: down += 1

    # 20. Last vs Previous Close
    if c[-1] > c[-2]: up += 1
    else: down += 1

    direction = "CALL" if up >= down else "PUT"
    strength = (max(up, down) / 20) * 100
    return direction, strength

def check_result_final():
    global active_trade
    now = datetime.now(BEIRUT_TZ)
    check_time = active_trade['entry_time'] + timedelta(minutes=1)
    
    if now.strftime('%H:%M:%S') == check_time.strftime('%H:%M:00'):
        time.sleep(0.8)
        try:
            ws = websocket.create_connection(WS_URL, timeout=5)
            ws.send(json.dumps({"ticks_history": SYMBOL_ID, "count": 100, "end": "latest", "style": "ticks"}))
            res = json.loads(ws.recv())
            ws.close()
            prices = res['history']['prices']
            is_win = (prices[-1] > prices[0]) if active_trade['direction'] == "CALL" else (prices[-1] < prices[0])
            status = "✅ WIN" if is_win else "❌ LOSS"
            send_telegram_msg(f"{status}\nPair: {SYMBOL_NAME}\nResult: {active_trade['direction']}")
            active_trade = None
        except: pass

def execute_signal_process():
    global active_trade
    if not analysis_lock.acquire(blocking=False): return
    try:
        ws = websocket.create_connection(WS_URL, timeout=5)
        ws.send(json.dumps({"ticks_history": SYMBOL_ID, "count": 100, "end": "latest", "style": "candles", "granularity": 60}))
        res = json.loads(ws.recv())
        ws.close()
        df = pd.DataFrame(res['candles'])
        direction, accuracy = calculate_20_indicators(df)
        
        # Filter for 75% accuracy (15/20 indicators)
        if accuracy >= 75:
            now = datetime.now(BEIRUT_TZ)
            entry_time = (now + timedelta(seconds=30)).replace(second=0, microsecond=0)
            active_trade = {"direction": direction, "entry_time": entry_time}
            msg = f"🚀 **SIGNAL** | {SYMBOL_NAME}\nAction: *{direction}*\nStrength: `{accuracy}%` (20 Indicators)\nEntry: {entry_time.strftime('%H:%M:00')}"
            send_telegram_msg(msg)
    finally: analysis_lock.release()

def engine():
    global last_signal_time
    while True:
        now = datetime.now(BEIRUT_TZ)
        if now.second == 30:
            current_slot = now.strftime('%H:%M')
            if last_signal_time != current_slot:
                last_signal_time = current_slot
                threading.Thread(target=execute_signal_process).start()
        if active_trade and now.second == 0:
            check_result_final()
        time.sleep(0.5)

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    threading.Thread(target=lambda: app.run(host='0.0.0.0', port=port), daemon=True).start()
    engine()
