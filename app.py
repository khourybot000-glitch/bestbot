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
def health_check(): return "RSI Confirm Bot Active", 200

# --- Configuration ---
TOKEN = '8511172742:AAFxZIj8N07FB-tFnJ_l3rv13loyRMmsRYU'
CHAT_ID = '-1003731752986'
BEIRUT_TZ = pytz.timezone('Asia/Beirut')
WS_URL = "wss://blue.derivws.com/websockets/v3?app_id=16929"

SYMBOLS = ["frxEURGBP", "frxEURUSD", "frxEURJPY"]
RSI_PERIOD = 14
RSI_OB = 70
RSI_OS = 30

active_trades = {}

def send_telegram_msg(text):
    url = f"https://api.telegram.org/bot{TOKEN}/sendMessage"
    try: requests.post(url, json={"chat_id": CHAT_ID, "text": text, "parse_mode": "Markdown"}, timeout=10)
    except: pass

def calculate_rsi(prices, period=14):
    if len(prices) < period + 1: return 50
    deltas = np.diff(prices)
    seed = deltas[:period+1]
    up = seed[seed >= 0].sum()/period
    down = -seed[seed < 0].sum()/period
    if down == 0: return 100
    rs = up/down
    rsi = np.zeros_like(prices)
    rsi[:period] = 100. - 100./(1. + rs)
    for i in range(period, len(prices)):
        delta = deltas[i-1]
        upval = delta if delta > 0 else 0.
        downval = -delta if delta < 0 else 0.
        up = (up * (period - 1) + upval) / period
        down = (down * (period - 1) + downval) / period
        if down == 0: rsi[i] = 100
        else:
            rs = up / down
            rsi[i] = 100. - 100. / (1. + rs)
    return rsi

def analyze_pair(symbol):
    global active_trades
    try:
        ws = websocket.create_connection(WS_URL)
        ws.send(json.dumps({"ticks_history": symbol, "count": 1000, "end": "latest", "style": "ticks"}))
        res = json.loads(ws.recv())
        ws.close()
        
        if 'history' not in res: return
        prices = res['history']['prices']
        times = [datetime.fromtimestamp(t, tz=pytz.utc).astimezone(BEIRUT_TZ) for t in res['history']['times']]
        df = pd.DataFrame({'price': prices, 'time': times})
        
        rsi_values = calculate_rsi(df['price'].values, RSI_PERIOD)
        df['rsi'] = rsi_values

        now = datetime.now(BEIRUT_TZ)
        t_00 = now.replace(second=0, microsecond=0)
        
        # 1. الشمعة السابقة (التي أغلقت تماماً)
        m1_start, m1_end = t_00 - timedelta(minutes=1), t_00
        m1_data = df[(df['time'] >= m1_start) & (df['time'] < m1_end)]
        
        # 2. الشمعة الحالية (من الثانية 00 إلى الثانية 50)
        m0_start = t_00
        m0_data = df[df['time'] >= m0_start]

        if m1_data.empty or m0_data.empty: return

        # بيانات الشمعة السابقة
        m1_open, m1_close = m1_data['price'].iloc[0], m1_data['price'].iloc[-1]
        m1_rsi = m1_data['rsi'].iloc[-1]
        
        # بيانات الشمعة الحالية (التي نحن فيها الآن)
        m0_open, m0_now = m0_data['price'].iloc[0], m0_data['price'].iloc[-1]

        trade_type = None

        # شرط الـ CALL: سابقة حمراء في تشبع + حالية خضراء (تأكيد)
        if m1_rsi <= RSI_OS and m1_close < m1_open and m0_now > m0_open:
            trade_type = "CALL (BUY)"
        
        # شرط الـ PUT: سابقة خضراء في تشبع + حالية حمراء (تأكيد)
        elif m1_rsi >= RSI_OB and m1_close > m1_open and m0_now < m0_open:
            trade_type = "PUT (SELL)"

        if trade_type and symbol not in active_trades:
            entry_time = t_00 + timedelta(minutes=1)
            active_trades[symbol] = {
                "entry_time": entry_time,
                "exit_time": entry_time + timedelta(minutes=5),
                "direction": trade_type,
                "entry_price": m0_now # سيتحدد السعر الفعلي عند الدخول
            }
            
            msg = (f"🔔 **SIGNAL: {symbol.replace('frx','')}**\n"
                   f"🎯 Action: *{trade_type}*\n"
                   f"🕐 Entry: {entry_time.strftime('%H:%M:00')}\n"
               f"⏱ Duration: 5 Minutes")
            send_telegram_msg(msg)

    except Exception as e: print(f"Error: {e}")

def check_results():
    global active_trades
    now = datetime.now(BEIRUT_TZ)
    to_remove = []
    for symbol, data in active_trades.items():
        if now >= data['exit_time']:
            try:
                ws = websocket.create_connection(WS_URL); ws.send(json.dumps({"ticks_history": symbol, "count": 100, "end": "latest", "style": "ticks"}))
                res = json.loads(ws.recv()); ws.close()
                final_p = res['history']['prices'][-1]
                # ملاحظة: للحصول على نتيجة دقيقة 100% يفضل جلب السعر عند وقت الدخول بالضبط
                won = (final_p > data['entry_price']) if "CALL" in data['direction'] else (final_p < data['entry_price'])
                send_telegram_msg(f"{'✅ SUCCESS' if won else '❌ FAILED'} ({symbol.replace('frx','')})")
                to_remove.append(symbol)
            except: pass
    for s in to_remove: del active_trades[s]

def start_engine():
    while True:
        now = datetime.now(BEIRUT_TZ)
        if now.second == 50:
            for s in SYMBOLS: threading.Thread(target=analyze_pair, args=(s,)).start()
            time.sleep(1)
        if now.second % 15 == 0: check_results()
        time.sleep(0.5)

if __name__ == "__main__":
    threading.Thread(target=lambda: app.run(host='0.0.0.0', port=int(os.environ.get("PORT", 10000))), daemon=True).start()
    start_engine()
