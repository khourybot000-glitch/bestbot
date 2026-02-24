import websocket
import json
import pandas as pd
import pandas_ta as ta
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
def health_check(): return "1M Entry Bot Active", 200

# --- Configuration ---
TOKEN = '8511172742:AAFxZIj8N07FB-tFnJ_l3rv13loyRMmsRYU'
CHAT_ID = '-1003731752986'
BEIRUT_TZ = pytz.timezone('Asia/Beirut')
WS_URL = "wss://blue.derivws.com/websockets/v3?app_id=16929"
SYMBOL = "frxEURGBP"

is_bot_busy = False
active_trade = {}
last_analyzed_min = -1

def send_telegram_msg(text):
    url = f"https://api.telegram.org/bot{TOKEN}/sendMessage"
    try: requests.post(url, json={"chat_id": CHAT_ID, "text": text, "parse_mode": "Markdown"}, timeout=10)
    except: pass

def get_data(symbol):
    try:
        ws = websocket.create_connection(WS_URL, timeout=15)
        ws.send(json.dumps({"ticks_history": symbol, "count": 200, "end": "latest", "style": "candles", "granularity": 300}))
        res = json.loads(ws.recv())
        ws.close()
        return pd.DataFrame(res['candles']) if 'candles' in res else None
    except: return None

def analyze_20_indicators(df):
    up, down = 0, 0
    c, h, l, o = df['close'], df['high'], df['low'], df['open']
    
    # 1-3: RSI (7, 14, 21)
    for p in [7, 14, 21]:
        r = ta.rsi(c, length=p).iloc[-1]
        if r < 50: up += 1
        else: down += 1

    # 4-6: EMA (8, 21, 50)
    for p in [8, 21, 50]:
        e = ta.ema(c, length=p).iloc[-1]
        if c.iloc[-1] > e: up += 1
        else: down += 1

    # 7-8: MACD
    macd = ta.macd(c).iloc[-1]
    if macd.iloc[0] > macd.iloc[1]: up += 1
    else: down += 1
    if macd.iloc[0] > 0: up += 1
    else: down += 1

    # 9-10: Bollinger Bands
    bb = ta.bbands(c, length=20).iloc[-1]
    if c.iloc[-1] < bb.iloc[1]: up += 1
    else: down += 1
    if (bb.iloc[2] - bb.iloc[0]) > (bb.iloc[2] * 0.001): up += 1
    else: down += 1

    # 11-12: Stochastic
    stoch = ta.stoch(h, l, c).iloc[-1]
    if stoch.iloc[0] < 50: up += 1
    else: down += 1
    if stoch.iloc[0] > stoch.iloc[1]: up += 1
    else: down += 1

    # 13-14: Williams %R & CCI
    wpr = ta.willr(h, l, c).iloc[-1]
    cci = ta.cci(h, l, c).iloc[-1]
    if wpr < -50: up += 1
    else: down += 1
    if cci > 0: up += 1
    else: down += 1

    # 15-16: ADX
    adx = ta.adx(h, l, c).iloc[-1]
    if adx.iloc[1] > adx.iloc[2]: up += 1
    else: down += 1
    if adx.iloc[0] > 20: up += 1
    else: down += 1

    # 17-18: Candle Color & Momentum
    if c.iloc[-1] > o.iloc[-1]: up += 1
    else: down += 1
    if c.iloc[-1] > c.iloc[-2]: up += 1
    else: down += 1

    # 19-20: MFI & Median Price
    mfi = ta.mfi(h, l, c, o).iloc[-1]
    if mfi < 50: up += 1
    else: down += 1
    if c.iloc[-1] > (h.iloc[-1] + l.iloc[-1])/2: up += 1
    else: down += 1

    total = 20
    if up >= down:
        return "CALL (BUY) 🟢", (up / total) * 100
    else:
        return "PUT (SELL) 🔴", (down / total) * 100

def run_analysis():
    global is_bot_busy, active_trade
    df = get_data(SYMBOL)
    if df is None: return

    direction, accuracy = analyze_20_indicators(df)
    now = datetime.now(BEIRUT_TZ)
    # الدخول عند بداية الدقيقة القادمة فوراً
    entry_time = (now + timedelta(minutes=1)).replace(second=0, microsecond=0)
    
    is_bot_busy = True
    # مدة الصفقة دقيقة واحدة فقط
    active_trade = {"exit_time": entry_time + timedelta(minutes=1)}

    msg = f"⚡️ **FAST SIGNAL (1M ENTRY)**\n"
    msg += f"📊 Based on 5M Analysis\n"
    msg += f"🎯 Action: *{direction}*\n"
    msg += f"📈 Confidence: `{accuracy:.1f}%`\n"
    msg += f"🕐 Entry: {entry_time.strftime('%H:%M:00')}\n"
    msg += f"⏱ Duration: 1m"
    send_telegram_msg(msg)

def start_engine():
    global last_analyzed_min, is_bot_busy
    print("Engine Running: 30s Trigger, 1m Duration.")
    while True:
        now = datetime.now(BEIRUT_TZ)
        if now.weekday() < 5 and 9 <= now.hour < 21:
            # التحليل عند الثانية 30 من الدقيقة 4، 9، 14... إلخ
            if (now.minute + 1) % 5 == 0 and now.second == 30:
                if last_analyzed_min != now.minute:
                    run_analysis()
                    last_analyzed_min = now.minute
            
            # فتح القفل للتحليل القادم بعد دقيقة واحدة من الدخول
            if is_bot_busy and now >= active_trade.get('exit_time', now):
                is_bot_busy = False
        time.sleep(1)

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    threading.Thread(target=lambda: app.run(host='0.0.0.0', port=port), daemon=True).start()
    start_engine()
