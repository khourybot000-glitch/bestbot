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

# --- Flask Server for Render ---
app = Flask(__name__)
@app.route('/')
def health_check():
    return "Bot is Active and Monitoring!", 200

def run_flask():
    port = int(os.environ.get("PORT", 10000))
    app.run(host='0.0.0.0', port=port)

# --- Configuration ---
TELEGRAM_TOKEN = '8511172742:AAFxZIj8N07FB-tFnJ_l3rv13loyRMmsRYU'
CHAT_ID = '-1003731752986'
BEIRUT_TZ = pytz.timezone('Asia/Beirut')
SYMBOL_CODE = "frxEURGBP"
APP_ID = "16929"
WS_URL = f"wss://blue.derivws.com/websockets/v3?app_id={APP_ID}"

def send_telegram_msg(direction):
    now_beirut = datetime.now(BEIRUT_TZ)
    entry_time = (now_beirut + timedelta(minutes=1)).strftime('%H:%M')
    
    message_text = (
        f"🏆 Symbol: EUR/GBP\n"
        f"⏱ Duration: 1 Minute\n"
        f"🎯 Direction: {direction}\n"
        f"🔥 Strength: Strong (Role Reversal)\n"
        f"🕐 Entry Time: {entry_time} (Beirut Time)"
    )
    
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    try:
        requests.post(url, json={"chat_id": CHAT_ID, "text": message_text}, timeout=10)
    except Exception as e:
        print(f"❌ Telegram error: {e}")

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
        
        if len(candles) >= 20:
            history = candles[:-2]
            res = max([c['high'] for c in history])
            sup = min([c['low'] for c in history])
            c_brk, c_ret = candles[-2], candles[-1]
            
            if c_brk['close'] > res and c_brk['color'] == 'green' and c_ret['color'] == 'red' and c_ret['close'] > res:
                send_telegram_msg("CALL (BUY)")
            elif c_brk['close'] < sup and c_brk['color'] == 'red' and c_ret['color'] == 'green' and c_ret['close'] < sup:
                send_telegram_msg("PUT (SELL)")
        
        ws.close()

def on_open(ws):
    request = {"ticks_history": SYMBOL_CODE, "count": 6000, "end": "latest", "style": "ticks"}
    ws.send(json.dumps(request))

def start_scheduler():
    while True:
        now_beirut = datetime.now(BEIRUT_TZ)
        
        # 1. Check if it's a weekday (0=Monday, 4=Friday)
        is_weekday = now_beirut.weekday() <= 4 
        # 2. Check if the time is between 09:00 and 21:00
        is_working_hours = 9 <= now_beirut.hour < 21

        if is_weekday and is_working_hours:
            # Sync to Second 00
            seconds_to_wait = 60 - now_beirut.second
            time.sleep(seconds_to_wait)
            
            print(f"⏰ Analyzing Market at: {datetime.now(BEIRUT_TZ).strftime('%H:%M:%S')}")
            ws = websocket.WebSocketApp(WS_URL, on_open=on_open, on_message=on_message)
            ws.run_forever(ping_timeout=15)
        else:
            # If outside hours, sleep for 1 minute and check again
            if not is_weekday:
                print("💤 Weekend: Bot is resting...")
            else:
                print("🌙 Outside working hours: Bot is sleeping...")
            time.sleep(60)

if __name__ == "__main__":
    threading.Thread(target=run_flask, daemon=True).start()
    start_scheduler()
