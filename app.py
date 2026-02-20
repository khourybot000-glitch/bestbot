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

# --- Flask Server ---
app = Flask(__name__)
@app.route('/')
def health_check(): return "Bot Active: Result Tracking Mode", 200

def run_flask():
    port = int(os.environ.get("PORT", 10000))
    app.run(host='0.0.0.0', port=port)

# --- Config ---
TOKEN = '8511172742:AAFxZIj8N07FB-tFnJ_l3rv13loyRMmsRYU'
CHAT_ID = '-1003731752986'
BEIRUT_TZ = pytz.timezone('Asia/Beirut')
SYMBOL = "frxEURGBP"
WS_URL = "wss://blue.derivws.com/websockets/v3?app_id=16929"

# Variables to track trade status
is_waiting_for_result = False
pending_trade_direction = None
result_time = None

def send_telegram_msg(text):
    url = f"https://api.telegram.org/bot{TOKEN}/sendMessage"
    try:
        requests.post(url, json={"chat_id": CHAT_ID, "text": text}, timeout=10)
    except Exception as e:
        print(f"❌ Telegram Error: {e}")

def check_trade_result(candles):
    global is_waiting_for_result, pending_trade_direction
    
    last_candle = candles[-1]
    is_win = False
    
    if pending_trade_direction == "CALL (BUY)":
        is_win = last_candle['close'] > last_candle['open']
    elif pending_trade_direction == "PUT (SELL)":
        is_win = last_candle['close'] < last_candle['open']
    
    if is_win:
        send_telegram_msg("WIN ✅")
    else:
        send_telegram_msg("LOSS ❌")
    
    # Reset status to look for new opportunities
    is_waiting_for_result = False
    pending_trade_direction = None

def analyze_strategy(candles):
    global is_waiting_for_result, pending_trade_direction, result_time
    
    if len(candles) < 30: return
    
    swing_highs = []
    swing_lows = []
    
    # Find 3-up 3-down patterns
    for i in range(0, len(candles) - 8):
        up_3 = all(candles[j]['close'] > candles[j]['open'] for j in range(i, i+3))
        down_3 = all(candles[j]['close'] < candles[j]['open'] for j in range(i+3, i+6))
        if up_3 and down_3:
            swing_highs.append(max(c['high'] for c in candles[i:i+6]))

        down_3_v = all(candles[j]['close'] < candles[j]['open'] for j in range(i, i+3))
        up_3_v = all(candles[j]['close'] > candles[j]['open'] for j in range(i+3, i+6))
        if down_3_v and up_3_v:
            swing_lows.append(min(c['low'] for c in candles[i:i+6]))

    if not swing_highs or not swing_lows: return

    active_res = swing_highs[-1]
    active_sup = swing_lows[-1]
    c_break = candles[-2]
    c_retest = candles[-1]

    # Signal Logic
    direction = None
    if c_break['close'] > active_res and c_break['close'] > c_break['open']:
        if c_retest['close'] < c_retest['open'] and c_retest['close'] > active_res:
            direction = "CALL (BUY)"
            level = active_res

    elif c_break['close'] < active_sup and c_break['close'] < c_break['open']:
        if c_retest['close'] > c_retest['open'] and c_retest['close'] < active_sup:
            direction = "PUT (SELL)"
            level = active_sup

    if direction:
        now_beirut = datetime.now(BEIRUT_TZ)
        entry_time = (now_beirut + timedelta(minutes=1)).strftime('%H:%M')
        # Result should be checked after the entry minute finishes (2 minutes from now)
        result_time = (now_beirut + timedelta(minutes=2)).strftime('%H:%M')
        
        msg = (f"🚀 **STRONG SIGNAL**\n"
               f"🏆 Symbol: EUR/GBP\n"
               f"🎯 Direction: {direction}\n"
               f"📍 Level: {level:.5f}\n"
               f"🕐 Entry Time: {entry_time}\n"
               f"⏳ Result at: {result_time}")
        
        send_telegram_msg(msg)
        is_waiting_for_result = True
        pending_trade_direction = direction

def on_message(ws, message):
    data = json.loads(message)
    if 'history' in data:
        prices = data['history']['prices']
        df = pd.DataFrame(prices, columns=['price'])
        candles = []
        for i in range(0, len(df), 60):
            chunk = df.iloc[i:i+60]
            if len(chunk) == 60:
                candles.append({'open': chunk['price'].iloc[0], 'high': chunk['price'].max(), 
                                'low': chunk['price'].min(), 'close': chunk['price'].iloc[-1]})
        
        if is_waiting_for_result:
            # We are in the result minute, check last 60 ticks
            check_trade_result(candles)
        else:
            # We are looking for opportunities
            analyze_strategy(candles)
    ws.close()

def on_open(ws):
    # Request 6000 ticks for analysis or just 60 if checking result
    count = 6000 if not is_waiting_for_result else 60
    ws.send(json.dumps({"ticks_history": SYMBOL, "count": count, "end": "latest", "style": "ticks"}))

def start_engine():
    while True:
        now = datetime.now(BEIRUT_TZ)
        current_time_str = now.strftime('%H:%M')
        
        # Determine if we should work: Working hours OR waiting for a result
        if (9 <= now.hour < 21 and now.weekday() <= 4) or is_waiting_for_result:
            
            # If waiting for result, only connect when current time matches result_time
            if is_waiting_for_result and current_time_str != result_time:
                time.sleep(10)
                continue
                
            # Sync to Second 01 (to ensure the minute has closed)
            time.sleep(60 - now.second + 1)
            
            ws = websocket.WebSocketApp(WS_URL, on_open=on_open, on_message=on_message)
            ws.run_forever(ping_timeout=15)
        else:
            time.sleep(60)

if __name__ == "__main__":
    threading.Thread(target=run_flask, daemon=True).start()
    start_engine()
