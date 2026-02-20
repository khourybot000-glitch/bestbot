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

# --- Flask Server for Render Deployment ---
app = Flask(__name__)

@app.route('/')
def health_check():
    return "Trading Bot is Active!", 200

def run_flask():
    # Render binds to the PORT environment variable
    port = int(os.environ.get("PORT", 10000))
    app.run(host='0.0.0.0', port=port)

# --- Configuration & Credentials ---
TELEGRAM_TOKEN = '8511172742:AAFxZIj8N07FB-tFnJ_l3rv13loyRMmsRYU'
CHAT_ID = '-1003731752986'
BEIRUT_TZ = pytz.timezone('Asia/Beirut')
SYMBOL_CODE = "frxEURGBP"
APP_ID = "16929"
WS_URL = f"wss://blue.derivws.com/websockets/v3?app_id={APP_ID}"

def send_telegram_msg(direction, level):
    now_beirut = datetime.now(BEIRUT_TZ)
    entry_time = (now_beirut + timedelta(minutes=1)).strftime('%H:%M')
    
    msg = (
        f"🏆 Symbol: EUR/GBP\n"
        f"⏱ Duration: 1 Minute\n"
        f"🎯 Direction: {direction}\n"
        f"📍 Level: {level:.5f}\n"
        f"🔥 Strength: Strong (Retest)\n"
        f"🕐 Entry Time: {entry_time} (Beirut Time)"
    )
    
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    try:
        requests.post(url, json={"chat_id": CHAT_ID, "text": msg}, timeout=10)
        print(f"✅ Signal {direction} sent to Telegram.")
    except Exception as e:
        print(f"❌ Telegram Error: {e}")

def analyze_price_action(candles):
    if len(candles) < 20: return
    
    swing_highs = [] # Resistance Zones
    swing_lows = []  # Support Zones
    
    # Identify Reversal Zones within the 6000 ticks (100 candles)
    for i in range(1, len(candles) - 3):
        # Resistance: Price went Up then Down (Directional Reversal)
        if candles[i]['close'] > candles[i]['open'] and candles[i+1]['close'] < candles[i+1]['open']:
            res_price = max(candles[i]['high'], candles[i+1]['high'])
            swing_highs.append(res_price)
            
        # Support: Price went Down then Up (Directional Reversal)
        if candles[i]['close'] < candles[i]['open'] and candles[i+1]['close'] > candles[i+1]['open']:
            sup_price = min(candles[i]['low'], candles[i+1]['low'])
            swing_lows.append(sup_price)

    if not swing_highs or not swing_lows: return

    # Nearest active levels to current price
    active_res = swing_highs[-1]
    active_sup = swing_lows[-1]

    c_break = candles[-2] # The breakout candle
    c_retest = candles[-1] # The correction (retest) candle

    # CALL LOGIC: Break Resistance UP + Retest stays ABOVE
    if c_break['close'] > active_res and c_break['close'] > c_break['open']:
        if c_retest['close'] < c_retest['open'] and c_retest['close'] > active_res:
            send_telegram_msg("CALL (BUY)", active_res)

    # PUT LOGIC: Break Support DOWN + Retest stays BELOW
    elif c_break['close'] < active_sup and c_break['close'] < c_break['open']:
        if c_retest['close'] > c_retest['open'] and c_retest['close'] < active_sup:
            send_telegram_msg("PUT (SELL)", active_sup)

def on_message(ws, message):
    data = json.loads(message)
    if 'history' in data:
        prices = data['history']['prices']
        df = pd.DataFrame(prices, columns=['price'])
        
        # Mapping 6000 ticks into 100 candles (60 ticks each)
        candles = []
        for i in range(0, len(df), 60):
            chunk = df.iloc[i:i+60]
            if len(chunk) == 60:
                candles.append({
                    'open': chunk['price'].iloc[0],
                    'high': chunk['price'].max(),
                    'low': chunk['price'].min(),
                    'close': chunk['price'].iloc[-1]
                })
        analyze_price_action(candles)
    ws.close()

def on_open(ws):
    # Requesting 6000 ticks from the API
    req = {"ticks_history": SYMBOL_CODE, "count": 6000, "end": "latest", "style": "ticks"}
    ws.send(json.dumps(req))

def start_engine():
    while True:
        now = datetime.now(BEIRUT_TZ)
        # Operating hours: Mon-Fri, 09:00 to 21:00 Beirut Time
        if now.weekday() <= 4 and 9 <= now.hour < 21:
            # Sleep until Second 00 of the next minute
            wait_sec = 60 - now.second
            time.sleep(wait_sec)
            
            print(f"📡 Analyzing Market - {datetime.now(BEIRUT_TZ).strftime('%H:%M:%S')}")
            ws = websocket.WebSocketApp(WS_URL, on_open=on_open, on_message=on_message)
            ws.run_forever(ping_timeout=15)
        else:
            # Check every 60 seconds during off-hours
            time.sleep(60)

if __name__ == "__main__":
    # 1. Run Web Server for Port Binding (Render Requirement)
    threading.Thread(target=run_flask, daemon=True).start()
    
    # 2. Start the On-Demand Analysis Engine
    start_engine()
