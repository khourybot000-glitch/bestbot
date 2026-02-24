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

# --- Flask Server for Hosting ---
app = Flask(__name__)
@app.route('/')
def health_check(): return "Bot is Running", 200

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
        requests.post(url, json={"chat_id": CHAT_ID, "text": text, "parse_mode": "Markdown"}, timeout=10)
    except Exception as e:
        print(f"Telegram Error: {e}")

def get_candle_from_ticks(symbol, start_time_dt):
    """Fetch raw ticks to calculate the Open and Close of a specific minute."""
    try:
        ws = websocket.create_connection(WS_URL, timeout=10)
        ws.send(json.dumps({"ticks_history": symbol, "count": 500, "end": "latest", "style": "ticks"}))
        res = json.loads(ws.recv())
        ws.close()
        
        df = pd.DataFrame({
            'price': res['history']['prices'],
            'time': [datetime.fromtimestamp(t, tz=pytz.utc).astimezone(BEIRUT_TZ) for t in res['history']['times']]
        })

        end_time_dt = start_time_dt + timedelta(minutes=1)
        # Filter ticks belonging to the target minute
        minute_ticks = df[(df['time'] >= start_time_dt) & (df['time'] < end_time_dt)]

        if minute_ticks.empty: return None
        
        return {
            'open': minute_ticks['price'].iloc[0],
            'close': minute_ticks['price'].iloc[-1]
        }
    except:
        return None

def analyze_20_indicators():
    """Technical analysis using 20 indicators on 5M timeframe."""
    try:
        ws = websocket.create_connection(WS_URL, timeout=10)
        ws.send(json.dumps({"ticks_history": SYMBOL, "count": 200, "end": "latest", "style": "candles", "granularity": 300}))
        res = json.loads(ws.recv())
        ws.close()
        
        df = pd.DataFrame(res['candles'])
        c, h, l, o = df['close'], df['high'], df['low'], df['open']
        
        up, down = 0, 0
        
        # 1-3: RSI (7, 14, 21)
        for p in [7, 14, 21]:
            val = ta.rsi(c, length=p).iloc[-1]
            if val < 50: up += 1
            else: down += 1
            
        # 4-6: EMA (8, 21, 50)
        for p in [8, 21, 50]:
            val = ta.ema(c, length=p).iloc[-1]
            if c.iloc[-1] > val: up += 1
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
        if (bb.iloc[2] - bb.iloc[0]) > (bb.iloc[2] * 0.0001): up += 1
        else: down += 1
        
        # 11-12: Stochastic
        st = ta.stoch(h, l, c).iloc[-1]
        if st.iloc[0] < 50: up += 1
        else: down += 1
        if st.iloc[0] > st.iloc[1]: up += 1
        else: down += 1
        
        # 13-14: CCI & Williams %R
        if ta.cci(h, l, c).iloc[-1] > 0: up += 1
        else: down += 1
        if ta.willr(h, l, c).iloc[-1] < -50: up += 1
        else: down += 1
        
        # 15-16: ADX
        adx = ta.adx(h, l, c).iloc[-1]
        if adx.iloc[1] > adx.iloc[2]: up += 1
        else: down += 1
        if adx.iloc[0] > 20: up += 1
        else: down += 1
        
        # 17-18: Price Action
        if c.iloc[-1] > o.iloc[-1]: up += 1
        else: down += 1
        if c.iloc[-1] > c.iloc[-2]: up += 1
        else: down += 1
        
        # 19-20: MFI & Median
        if ta.mfi(h, l, c, o).iloc[-1] < 50: up += 1
        else: down += 1
        if c.iloc[-1] > (h.iloc[-1] + l.iloc[-1])/2: up += 1
        else: down += 1

        total = 20
        if up >= down:
            return "CALL", (up / total) * 100
        else:
            return "PUT", (down / total) * 100
    except Exception as e:
        print(f"Analysis Error: {e}")
        return None, 0

def check_result_logic():
    global active_trade
    now = datetime.now(BEIRUT_TZ)
    
    # 1. First Minute Result (at exactly XX:00:00)
    check_time_1 = active_trade['entry_time'] + timedelta(minutes=1)
    if now.strftime('%H:%M:%S') == check_time_1.strftime('%H:%M:00') and active_trade['status'] == "WAITING":
        time.sleep(0.5) # Wait for final tick
        res = get_candle_from_ticks(SYMBOL, active_trade['entry_time'])
        if res:
            win = (res['close'] > res['open']) if active_trade['direction'] == "CALL" else (res['close'] < res['open'])
            if win:
                send_telegram_msg("✅ **WIN**")
                active_trade = None
            else:
                active_trade['status'] = "MTG_WAITING"
                print("First attempt lost. Monitoring MTG...")

    # 2. Martingale Result (at exactly XX:00:00 of the next minute)
    mtg_check_time = active_trade['entry_time'] + timedelta(minutes=2) if active_trade else None
    if active_trade and active_trade['status'] == "MTG_WAITING" and now.strftime('%H:%M:%S') == mtg_check_time.strftime('%H:%M:00'):
        time.sleep(0.5)
        mtg_start = active_trade['entry_time'] + timedelta(minutes=1)
        res = get_candle_from_ticks(SYMBOL, mtg_start)
        if res:
            win = (res['close'] > res['open']) if active_trade['direction'] == "CALL" else (res['close'] < res['open'])
            msg = "✅ **MTG WIN**" if win else "❌ **MTG LOSS**"
            send_telegram_msg(msg)
            active_trade = None

def start_engine():
    global last_signal_time, active_trade
    print("Bot Engine Started (English Version)...")
    
    while True:
        now = datetime.now(BEIRUT_TZ)
        
        # Schedule: Mon-Fri, 9 AM to 9 PM
        if now.weekday() < 5 and 9 <= now.hour < 21:
            
            # Trigger: 30 seconds before the 5M candle ends
            if (now.minute + 1) % 5 == 0 and now.second == 30:
                current_min = now.strftime('%H:%M')
                if last_signal_time != current_min:
                    direction, accuracy = analyze_20_indicators()
                    if direction:
                        entry_time = (now + timedelta(minutes=1)).replace(second=0, microsecond=0)
                        active_trade = {"direction": direction, "entry_time": entry_time, "status": "WAITING"}
                        
                        msg = f"🚀 **NEW SIGNAL**\n"
                        msg += f"🎯 Direction: *{direction}*\n"
                        msg += f"📊 Strength: `{accuracy:.1f}%`\n"
                        msg += f"🕐 Entry: {entry_time.strftime('%H:%M:00')}\n"
                        msg += f"⏱ Duration: 1m"
                        send_telegram_msg(msg)
                        last_signal_time = current_min
            
            # Check results at second 00
            if active_trade and now.second == 0:
                check_result_logic()
                
        time.sleep(0.1)

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    threading.Thread(target=lambda: app.run(host='0.0.0.0', port=port), daemon=True).start()
    start_engine()
