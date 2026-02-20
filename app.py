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
def health_check(): return "Bot is Active!", 200

def run_flask():
    port = int(os.environ.get("PORT", 10000))
    app.run(host='0.0.0.0', port=port)

# --- Config ---
TOKEN = '8511172742:AAFxZIj8N07FB-tFnJ_l3rv13loyRMmsRYU'
CHAT_ID = '-1003731752986'
BEIRUT_TZ = pytz.timezone('Asia/Beirut')
SYMBOL = "frxEURGBP"
WS_URL = "wss://blue.derivws.com/websockets/v3?app_id=16929"

def send_telegram_msg(direction, level_price):
    now_beirut = datetime.now(BEIRUT_TZ)
    entry_time = (now_beirut + timedelta(minutes=1)).strftime('%H:%M')
    text = (f"🏆 Symbol: EUR/GBP\n"
            f"⏱ Duration: 1 Minute\n"
            f"🎯 Direction: {direction}\n"
            f"📍 Level: {level_price:.5f}\n"
            f"🔥 Strength: Strong (Retest)\n"
            f"🕐 Entry: {entry_time} (Beirut Time)")
    requests.post(f"https://api.telegram.org/bot{TOKEN}/sendMessage", json={"chat_id": CHAT_ID, "text": text})

def analyze_strategy(candles):
    if len(candles) < 20: return
    
    # Identify Swing Highs (Resistance) and Swing Lows (Support)
    # A swing high is a high higher than the high of candles before and after it
    swing_highs = []
    swing_lows = []
    
    for i in range(2, len(candles) - 2):
        # Resistance points
        if candles[i]['high'] > candles[i-1]['high'] and candles[i]['high'] > candles[i+1]['high']:
            swing_highs.append(candles[i]['high'])
        # Support points
        if candles[i]['low'] < candles[i-1]['low'] and candles[i]['low'] < candles[i+1]['low']:
            swing_lows.append(candles[i]['low'])

    if not swing_highs or not swing_lows: return

    # Get the latest broken level (nearest to current price)
    nearest_resistance = swing_highs[-1] 
    nearest_support = swing_lows[-1]

    c_break = candles[-2]  # Breakout candle
    c_retest = candles[-1] # Retest candle

    # CALL Logic: Price breaks a previous Swing High, then retests it as Support
    if c_break['close'] > nearest_resistance and c_break['color'] == 'green':
        if c_retest['color'] == 'red' and c_retest['close'] > nearest_resistance:
            send_telegram_msg("CALL (BUY)", nearest_resistance)

    # PUT Logic: Price breaks a previous Swing Low, then retests it as Resistance
    elif c_break['close'] < nearest_support and c_break['color'] == 'red':
        if c_retest['color'] == 'green' and c_retest['close'] < nearest_support:
            send_telegram_msg("PUT (SELL)", nearest_support)

def on_message(ws, message):
    data = json.loads(message)
    if 'history' in data:
        prices = data['history']['prices']
        df = pd.DataFrame(prices, columns=['price'])
        candles = []
        for i in range(0, len(df), 60):
            chunk = df.iloc[i:i+60]
            if len(chunk) == 60:
                candles.append({
                    'open': chunk['price'].iloc[0], 'high': chunk['price'].max(),
                    'low': chunk['price'].min(), 'close': chunk['price'].iloc[-1],
                    'color': 'green' if chunk['price'].iloc[-1] > chunk['price'].iloc[0] else 'red'
                })
        analyze_strategy(candles)
    ws.close()

def on_open(ws):
    ws.send(json.dumps({"ticks_history": SYMBOL, "count": 6000, "end": "latest", "style": "ticks"}))

def start_scheduler():
    while True:
        now = datetime.now(BEIRUT_TZ)
        if now.weekday() <= 4 and 9 <= now.hour < 21:
            time.sleep(60 - now.second)
            ws = websocket.WebSocketApp(WS_URL, on_open=on_open, on_message=on_message)
            ws.run_forever(ping_timeout=15)
        else:
            time.sleep(60)

if __name__ == "__main__":
    threading.Thread(target=run_flask, daemon=True).start()
    start_scheduler()
