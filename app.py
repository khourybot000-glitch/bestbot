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

# --- Flask Server for Render & Uptime Robot ---
app = Flask(__name__)

@app.route('/')
def health_check():
    return "Bot is Active and Monitoring!", 200

def run_flask():
    # Render uses the PORT environment variable
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
    # Calculate entry time for the NEXT minute
    entry_time = (now_beirut + timedelta(minutes=1)).strftime('%H:%M')
    
    message_text = (
        f"🏆 Symbol: EUR/GBP\n"
        f"⏱ Duration: 1 Minute\n"
        f"🎯 Direction: {direction}\n"
        f"🔥 Strength: Strong (Role Reversal)\n"
        f"🕐 Entry Time: {entry_time} (Beirut Time)"
    )
    
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {"chat_id": CHAT_ID, "text": message_text}
    
    try:
        requests.post(url, json=payload, timeout=10)
        print(f"✅ {direction} signal sent to Telegram.")
    except Exception as e:
        print(f"❌ Telegram send error: {e}")

def on_message(ws, message):
    data = json.loads(message)
    if 'history' in data:
        prices = data['history']['prices']
        df = pd.DataFrame(prices, columns=['price'])
        
        # Convert 6000 ticks into 100 candles (1 candle per 60 ticks)
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
        
        # Role Reversal Analysis
        if len(candles) >= 20:
            # S/R levels based on previous 98 candles
            history = candles[:-2]
            resistance = max([c['high'] for c in history])
            support = min([c['low'] for c in history])
            
            c_break = candles[-2]  # The breakout candle
            c_retest = candles[-1] # The correction candle
            
            # CALL Logic: Resistance broken by Green, Retested by Red staying ABOVE resistance
            if c_break['close'] > resistance and c_break['color'] == 'green':
                if c_retest['color'] == 'red' and c_retest['close'] > resistance:
                    send_telegram_msg("CALL (BUY)")
            
            # PUT Logic: Support broken by Red, Retested by Green staying BELOW support
            elif c_break['close'] < support and c_break['color'] == 'red':
                if c_retest['color'] == 'green' and c_retest['close'] < support:
                    send_telegram_msg("PUT (SELL)")
        
        # Close connection immediately after analysis to save resources
        print("Done. Disconnecting...")
        ws.close()

def on_open(ws):
    # Send history request as soon as connection is established
    request = {
        "ticks_history": SYMBOL_CODE,
        "count": 6000,
        "end": "latest",
        "style": "ticks"
    }
    ws.send(json.dumps(request))

def start_scheduler():
    while True:
        # Precision sync to Second 00
        now = datetime.now()
        seconds_to_wait = 60 - now.second
        time.sleep(seconds_to_wait)
        
        print(f"⏰ Connecting for analysis at: {datetime.now().strftime('%H:%M:%S')}")
        
        # Open connection on demand
        ws = websocket.WebSocketApp(
            WS_URL,
            on_open=on_open,
            on_message=on_message
        )
        # Timeout safety: If no response in 15s, kill attempt to be ready for next minute
        ws.run_forever(ping_timeout=15)

if __name__ == "__main__":
    # Start Flask for Render/UptimeRobot in background thread
    threading.Thread(target=run_flask, daemon=True).start()
    
    # Start the On-Demand Scheduler
    start_scheduler()
