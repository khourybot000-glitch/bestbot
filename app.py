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

# --- 1. Flask Server for Port Binding ---
app = Flask(__name__)
@app.route('/')
def home(): return "Bot Status: ACTIVE", 200

# --- 2. Configuration ---
TOKEN = '8511172742:AAFxZIj8N07FB-tFnJ_l3rv13loyRMmsRYU'
CHAT_ID = '-1003731752986'
BEIRUT_TZ = pytz.timezone('Asia/Beirut')
WS_URL = "wss://blue.derivws.com/websockets/v3?app_id=16929"
SYMBOL_ID = "frxEURJPY"
SYMBOL_NAME = "EUR/JPY"

active_trade = None
last_signal_time = ""

def send_telegram_msg(text):
    url = f"https://api.telegram.org/bot{TOKEN}/sendMessage"
    payload = {"chat_id": CHAT_ID, "text": text, "parse_mode": "Markdown"}
    try:
        r = requests.post(url, json=payload, timeout=10)
        if r.status_code != 200: print(f"Telegram Error: {r.text}")
    except Exception as e: print(f"Connection Error: {e}")

# --- 3. Fibonacci Breakout Logic ---
def calculate_breakout_logic(tick_prices, tick_times):
    df_ticks = pd.DataFrame({'price': tick_prices, 'time': pd.to_datetime(tick_times, unit='s')})
    df_ticks.set_index('time', inplace=True)
    
    # Resample ticks into 1-minute candles
    df_candles = df_ticks['price'].resample('1Min').ohlc().dropna()
    
    if len(df_candles) < 3: 
        return "NONE", ""

    # Calculate Fibonacci levels for the last 100 candles
    recent = df_candles.tail(100)
    h, l = recent['high'].max(), recent['low'].min()
    diff = h - l

    levels = {
        "23.6%": h - (diff * 0.236),
        "38.2%": h - (diff * 0.382),
        "50.0%": h - (diff * 0.500),
        "61.8%": h - (diff * 0.618),
        "78.6%": h - (diff * 0.786)
    }

    # Analyze the candle that just closed (Index -1 at 00s)
    last_close = df_candles['close'].iloc[-1]
    last_open = df_candles['open'].iloc[-1]
    
    for name, val in levels.items():
        # CALL: Candle opened below level and closed above it
        if last_open < val and last_close > val:
            return "CALL", name
        
        # PUT: Candle opened above level and closed below it
        if last_open > val and last_close < val:
            return "PUT", name

    return "NONE", ""

# --- 4. Result Verification ---
def verify_result(entry_ts, exit_ts, direction):
    try:
        ws = websocket.create_connection(WS_URL, timeout=15)
        ws.send(json.dumps({"ticks_history": SYMBOL_ID, "count": 100, "end": exit_ts + 2, "style": "ticks"}))
        res = json.loads(ws.recv()); ws.close()
        
        prices, times = res['history']['prices'], res['history']['times']
        p_open, p_close = None, None
        
        for i in range(len(times)):
            if times[i] >= entry_ts and p_open is None: p_open = prices[i]
            if times[i] >= exit_ts and p_close is None: p_close = prices[i]
            
        if p_open and p_close:
            win = (p_close > p_open) if direction == "CALL" else (p_close < p_open)
            return win, p_open, p_close
    except: pass
    return None, None, None

# --- 5. Main Loop ---
def start_engine():
    global active_trade, last_signal_time
    print(f"Engine LIVE | {SYMBOL_NAME}")
    send_telegram_msg("⚡ **Bot Started Successfully**\nWaiting for analysis at 00s...")

    while True:
        now = datetime.now(BEIRUT_TZ)
        
        # 1. Trigger Analysis at 00 seconds
        if now.second == 0 and active_trade is None:
            current_min = now.strftime('%H:%M')
            if last_signal_time != current_min:
                last_signal_time = current_min
                
                send_telegram_msg(f"🔍 **Analyzing {current_min}**...") # Debug Message
                
                try:
                    # 2. Fetch Data
                    ws = websocket.create_connection(WS_URL, timeout=10)
                    ws.send(json.dumps({"ticks_history": SYMBOL_ID, "count": 6000, "style": "ticks"}))
                    res = json.loads(ws.recv()); ws.close()
                    
                    if 'history' in res:
                        direction, level_name = calculate_breakout_logic(res['history']['prices'], res['history']['times'])
                        
                        if direction != "NONE":
                            # Set Entry to T+2 minutes
                            entry_t = now + timedelta(minutes=2)
                            exit_t = entry_t + timedelta(minutes=1)
                            
                            active_trade = {
                                "dir": direction,
                                "entry": entry_t,
                                "exit": exit_t,
                                "entry_ts": int(entry_t.timestamp()),
                                "exit_ts": int(exit_t.timestamp()),
                                "level": level_name
                            }
                            
                            send_telegram_msg(f"🚨 **SIGNAL FOUND**\n"
                                              f"Level: `{level_name}`\n"
                                              f"Dir: *{direction}*\n"
                                              f"Entry Time: {entry_t.strftime('%H:%M:00')}\n"
                                              f"Note: Entry in 2 minutes.")
                        else:
                            send_telegram_msg(f"ℹ️ Analysis complete: No breakout detected.") # Status Message
                except Exception as e:
                    send_telegram_msg(f"⚠️ Error during analysis: {e}")

        # 3. Verify Result after Trade Closes
        if active_trade and now >= active_trade['exit'] + timedelta(seconds=2):
            win, p_in, p_out = verify_result(active_trade['entry_ts'], active_trade['exit_ts'], active_trade['dir'])
            if win is not None:
                status = "✅ **WIN**" if win else "❌ **LOSS**"
                send_telegram_msg(f"📊 **TRADE RESULT**\nLevel: {active_trade['level']}\nOutcome: {status}\nIn: {p_in} | Out: {p_out}")
            active_trade = None 

        time.sleep(0.5)

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    threading.Thread(target=lambda: app.run(host='0.0.0.0', port=port), daemon=True).start()
    start_engine()
