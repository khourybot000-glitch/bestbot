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
def home(): return "Bot Status: ACTIVE with Error Logging", 200

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
        if r.status_code != 200:
            print(f"Telegram API Error: {r.text}")
    except Exception as e:
        print(f"Failed to send Telegram message: {e}")

# --- 3. Fibonacci & Breakout Logic ---
def calculate_breakout_logic(tick_prices, tick_times):
    try:
        df_ticks = pd.DataFrame({'price': tick_prices, 'time': pd.to_datetime(tick_times, unit='s')})
        df_ticks.set_index('time', inplace=True)
        
        # Resample to 1-Minute Candles
        df_candles = df_ticks['price'].resample('1Min').ohlc().dropna()
        
        if len(df_candles) < 3:
            return "ERROR_DATA", "Not enough candles", {}, 0, 0

        # Calculate Fibonacci for the last 100 minutes
        recent = df_candles.tail(100)
        h, l = recent['high'].max(), recent['low'].min()
        diff = h - l

        levels = {
            "23.6%": round(h - (diff * 0.236), 3),
            "38.2%": round(h - (diff * 0.382), 3),
            "50.0%": round(h - (diff * 0.500), 3),
            "61.8%": round(h - (diff * 0.618), 3),
            "78.6%": round(h - (diff * 0.786), 3)
        }

        last_close = round(df_candles['close'].iloc[-1], 3)
        last_open = round(df_candles['open'].iloc[-1], 3)
        
        signal = "NONE"
        lvl_name = ""

        for name, val in levels.items():
            if last_open < val and last_close > val:
                signal = "CALL"
                lvl_name = name
                break
            if last_open > val and last_close < val:
                signal = "PUT"
                lvl_name = name
                break

        return signal, lvl_name, levels, last_open, last_close
    except Exception as e:
        return "ERROR_LOGIC", str(e), {}, 0, 0

# --- 4. Main Loop with Full Error Reporting ---
def start_engine():
    global active_trade, last_signal_time
    print(f"Engine LIVE | Time: {datetime.now(BEIRUT_TZ)}")
    send_telegram_msg("🚀 **Bot Online - Monitoring Mode Activated**\nError alerts are enabled.")

    while True:
        now = datetime.now(BEIRUT_TZ)
        weekday = now.weekday()
        hour = now.hour

        # Monday to Friday | 09:00 to 21:00 Beirut
        if 0 <= weekday <= 4 and 9 <= hour < 21:
            if now.second == 0 and active_trade is None:
                current_min = now.strftime('%H:%M')
                if last_signal_time != current_min:
                    last_signal_time = current_min
                    
                    try:
                        # Fetch Data
                        ws = websocket.create_connection(WS_URL, timeout=12)
                        ws.send(json.dumps({"ticks_history": SYMBOL_ID, "count": 5000, "style": "ticks"}))
                        res = json.loads(ws.recv())
                        ws.close()
                        
                        if 'history' not in res:
                            send_telegram_msg(f"❌ **Data Error at {current_min}**\nDeriv API did not return history. Check App ID/Token.")
                            continue

                        prices = res['history']['prices']
                        times = res['history']['times']
                        
                        # Logic Analysis
                        direction, lvl_name, all_levels, l_open, l_close = calculate_breakout_logic(prices, times)
                        
                        if direction == "ERROR_DATA":
                            send_telegram_msg(f"⚠️ **Analysis Warning at {current_min}**\n`{lvl_name}` (Need more historical ticks)")
                            continue
                        elif direction == "ERROR_LOGIC":
                            send_telegram_msg(f"❌ **Logic Error at {current_min}**\n`{lvl_name}`")
                            continue

                        # Detailed Report
                        report = f"🔍 **Analysis Report: {current_min}**\n"
                        report += f"Open: `{l_open}` | Close: `{l_close}`\n"
                        report += "--- Fibo Levels ---\n"
                        for n, v in all_levels.items():
                            report += f"{n}: `{v}`\n"
                        
                        if direction != "NONE":
                            entry_t = now + timedelta(minutes=2)
                            exit_t = entry_t + timedelta(minutes=1)
                            active_trade = {
                                "dir": direction, "entry": entry_t, "exit": exit_t,
                                "entry_ts": int(entry_t.timestamp()), "exit_ts": int(exit_t.timestamp()),
                                "level": lvl_name
                            }
                            report += f"\n🚨 **SIGNAL FOUND: {direction}**\nLevel: `{lvl_name}`\nEntry: {entry_t.strftime('%H:%M:00')}"
                        else:
                            report += "\nℹ️ **Result:** `No Signal`"
                        
                        send_telegram_msg(report)

                    except Exception as e:
                        send_telegram_msg(f"💥 **Connection Crash at {current_min}**\nError: `{str(e)}`")

        # Result Verification
        if active_trade and now >= active_trade['exit'] + timedelta(seconds=2):
            try:
                # Basic result check logic here...
                active_trade = None 
            except: active_trade = None

        time.sleep(0.5)

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    threading.Thread(target=lambda: app.run(host='0.0.0.0', port=port), daemon=True).start()
    start_engine()
