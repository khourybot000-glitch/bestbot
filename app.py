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
def home(): return "Fibonacci Breakout Bot: ACTIVE", 200

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
    except Exception as e: 
        print(f"Telegram Connection Error: {e}")

# --- 3. Fibonacci & Candle Analysis Logic ---
def calculate_breakout_logic(tick_prices, tick_times):
    df_ticks = pd.DataFrame({'price': tick_prices, 'time': pd.to_datetime(tick_times, unit='s')})
    df_ticks.set_index('time', inplace=True)
    
    # Resample ticks into 1-Minute Candles (OHLC)
    df_candles = df_ticks['price'].resample('1Min').ohlc().dropna()
    
    if len(df_candles) < 3: 
        return "NONE", "", {}, 0, 0

    # Calculate Fibonacci levels from the last 100 candles
    recent = df_candles.tail(100)
    h, l = recent['high'].max(), recent['low'].min()
    diff = h - l

    # Define the 5 major Fibonacci Levels
    levels = {
        "23.6%": round(h - (diff * 0.236), 3),
        "38.2%": round(h - (diff * 0.382), 3),
        "50.0%": round(h - (diff * 0.500), 3),
        "61.8%": round(h - (diff * 0.618), 3),
        "78.6%": round(h - (diff * 0.786), 3)
    }

    # Data of the candle that just CLOSED (at 00s)
    last_close = round(df_candles['close'].iloc[-1], 3)
    last_open = round(df_candles['open'].iloc[-1], 3)
    
    signal = "NONE"
    level_name = ""

    # Check for Breakout on any level
    for name, val in levels.items():
        # CALL: Price broke ABOVE the level
        if last_open < val and last_close > val:
            signal = "CALL"
            level_name = name
            break
        # PUT: Price broke BELOW the level
        if last_open > val and last_close < val:
            signal = "PUT"
            level_name = name
            break

    return signal, level_name, levels, last_open, last_close

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

# --- 5. Main Engine Loop ---
def start_engine():
    global active_trade, last_signal_time
    print(f"Engine LIVE | Monitoring {SYMBOL_NAME}")
    send_telegram_msg("⚡ **Bot Deployment Successful**\nMonitoring hours: 09:00 - 21:00 (Mon-Fri)")

    while True:
        now = datetime.now(BEIRUT_TZ)
        weekday = now.weekday()
        hour = now.hour

        # Filter: Mon-Fri & 09:00-21:00 Beirut Time
        if 0 <= weekday <= 4 and 9 <= hour < 21:
            if now.second == 0 and active_trade is None:
                current_min = now.strftime('%H:%M')
                if last_signal_time != current_min:
                    last_signal_time = current_min
                    
                    try:
                        # Fetch 6000 ticks for 100-candle accuracy
                        ws = websocket.create_connection(WS_URL, timeout=10)
                        ws.send(json.dumps({"ticks_history": SYMBOL_ID, "count": 6000, "style": "ticks"}))
                        res = json.loads(ws.recv()); ws.close()
                        
                        if 'history' in res:
                            direction, lvl_name, all_levels, l_open, l_close = calculate_breakout_logic(res['history']['prices'], res['history']['times'])
                            
                            # Construct Debug Report
                            report = f"🔍 **Analysis Report: {current_min}**\n"
                            report += f"Candle Open: `{l_open}` | Close: `{l_close}`\n"
                            report += "--- Fibo Levels ---\n"
                            for n, v in all_levels.items():
                                report += f"{n}: `{v}`\n"
                            
                            if direction != "NONE":
                                # Strategy: Wait 2 minutes then enter for 1 minute
                                entry_t = now + timedelta(minutes=2)
                                exit_t = entry_t + timedelta(minutes=1)
                                
                                active_trade = {
                                    "dir": direction, "entry": entry_t, "exit": exit_t,
                                    "entry_ts": int(entry_t.timestamp()), "exit_ts": int(exit_t.timestamp()),
                                    "level": lvl_name
                                }
                                report += f"\n🚨 **SIGNAL FOUND: {direction}**\nLevel: `{lvl_name}`\nEntry Time: {entry_t.strftime('%H:%M:00')}"
                                send_telegram_msg(report)
                            else:
                                report += "\nℹ️ **Result:** `No Signal`"
                                send_telegram_msg(report)
                                
                    except Exception as e:
                        print(f"Error: {e}")

        # Check Result (Independent of work hours to ensure trade closure)
        if active_trade and now >= active_trade['exit'] + timedelta(seconds=2):
            win, p_in, p_out = verify_result(active_trade['entry_ts'], active_trade['exit_ts'], active_trade['dir'])
            if win is not None:
                status = "✅ **WIN**" if win else "❌ **LOSS**"
                send_telegram_msg(f"📊 **RESULT: {active_trade['level']}**\nOutcome: {status}\nIn: {p_in} | Out: {p_out}")
            active_trade = None 

        time.sleep(0.5)

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    threading.Thread(target=lambda: app.run(host='0.0.0.0', port=port), daemon=True).start()
    start_engine()
