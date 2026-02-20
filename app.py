import websocket
import json
import pandas as pd
import time
import requests
from datetime import datetime, timedelta
import pytz
import threading
from flask import Flask

# --- Flask Server for Render & Uptime Robot ---
app = Flask(__name__)

@app.route('/')
def health_check():
    return "Bot is running!", 200

def run_flask():
    # Render provides PORT environment variable
    import os
    port = int(os.environ.get("PORT", 10000))
    app.run(host='0.0.0.0', port=port)

# --- Bot Configuration ---
APP_ID = '16929'
WS_URL = f"wss://blue.derivws.com/websockets/v3?app_id={APP_ID}"
TELEGRAM_TOKEN = '8511172742:AAFxZIj8N07FB-tFnJ_l3rv13loyRMmsRYU'
CHAT_ID = '-1003731752986'
SYMBOL_NAME = "EUR/GBP"
SYMBOL_CODE = "frxEURGBP"
BEIRUT_TZ = pytz.timezone('Asia/Beirut')

def send_telegram_msg(direction):
    now_beirut = datetime.now(BEIRUT_TZ)
    entry_time_dt = now_beirut + timedelta(minutes=1)
    entry_time_str = entry_time_dt.strftime('%H:%M')
    
    text = (
        f"🏆 Symbol: {SYMBOL_NAME}\n"
        f"⏱ Duration: 1 Minute\n"
        f"🎯 Direction: {'CALL (BUY)' if direction == 'CALL' else 'PUT (SELL)'}\n"
        f"🔥 Strength: Strong\n"
        f"🕐 Entry Time: {entry_time_str} (Beirut Time)"
    )
    
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {"chat_id": CHAT_ID, "text": text}
    
    try:
        requests.post(url, json=payload)
        print(f"✅ {direction} Signal sent.")
    except Exception as e:
        print(f"❌ Error: {e}")

def analyze_strategy(candles):
    if len(candles) < 20: return
    history = candles[:-2]
    resistance = max([c['high'] for c in history])
    support = min([c['low'] for c in history])
    
    c_break = candles[-2]
    c_retest = candles[-1]

    if c_break['close'] > resistance and c_break['color'] == 'green':
        if c_retest['color'] == 'red' and c_retest['close'] > resistance:
            send_telegram_msg("CALL")

    elif c_break['close'] < support and c_break['color'] == 'red':
        if c_retest['color'] == 'green' and c_retest['close'] < support:
            send_telegram_msg("PUT")

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
                    'open': chunk['price'].iloc[0],
                    'high': chunk['price'].max(),
                    'low': chunk['price'].min(),
                    'close': chunk['price'].iloc[-1],
                    'color': 'green' if chunk['price'].iloc[-1] > chunk['price'].iloc[0] else 'red'
                })
        analyze_strategy(candles)

def on_open(ws):
    def run():
        print("🚀 Bot Monitoring Started...")
        while True:
            now = datetime.now()
            wait = 60 - now.second
            time.sleep(wait)
            request = {"ticks_history": SYMBOL_CODE, "count": 6000, "end": "latest", "style": "ticks"}
            ws.send(json.dumps(request))
            time.sleep(5)
    threading.Thread(target=run).start()

# --- Main Execution ---
if __name__ == "__main__":
    # 1. Start Flask in a separate thread for Render/UptimeRobot
    threading.Thread(target=run_flask).start()

    # 2. Start WebSocket
    ws = websocket.WebSocketApp(WS_URL, on_open=on_open, on_message=on_message)
    ws.run_forever()
