import requests
import time
import threading
import os
from datetime import datetime, timedelta
from flask import Flask

app = Flask(__name__)

@app.get('/')
def home(): 
    return "KhouryBot V5.4 - English Version - ID1 Logic"

# --- CONFIGURATION ---
TOKEN = "8511172742:AAGqDR6vq4OIH5R_JbTp-YzFnnTCw2f5gF8"
CHAT_ID = "-1003731752986"
SYMBOL = "FB_otc"
API_URL = f"https://mrbeaxt.site/Qx/Qx.php?format=json&pair={SYMBOL}&timeframe=M1&limit=14"

last_trade = None

def send_telegram(text):
    url = f"https://api.telegram.org/bot{TOKEN}/sendMessage"
    try: 
        requests.post(url, json={"chat_id": CHAT_ID, "text": text, "parse_mode": "Markdown"})
    except: 
        print("❌ Telegram connection error")

def check_and_send_result(expected_direction):
    """
    At XX:06:06, this function requests a fresh API response 
    to analyze ID1 (the candle that just closed).
    """
    print(f"🔄 Fetching fresh API data for result analysis...")
    try:
        # Fresh API request
        response = requests.get(API_URL, timeout=10)
        res = response.json()
        
        if res.get('success') and len(res['data']) > 1:
            # ID1 is the completed 1-minute candle
            last_candle = res['data'][1]
            open_p = float(last_candle['open'])
            close_p = float(last_candle['close'])
            
            # Determine actual market direction for ID1
            if close_p > open_p:
                actual_direction = "UP"
            elif close_p < open_p:
                actual_direction = "DOWN"
            else:
                actual_direction = "DOJI"

            print(f"📊 ID1 Analysis: Open={open_p}, Close={close_p} -> Result={actual_direction}")

            # Final Comparison
            if actual_direction == expected_direction:
                send_telegram(f"✅ **WIN** - {SYMBOL}\n📈 Result: `{actual_direction}`\n💰 Price: `{close_p}`")
            elif actual_direction == "DOJI":
                send_telegram(f"⚖️ **DOJI/REFUND** - {SYMBOL}\n💰 Price: `{close_p}`")
            else:
                send_telegram(f"❌ **LOSS** - {SYMBOL}\n📉 Result: `{actual_direction}`\n💰 Price: `{close_p}`")
            
            return True
    except Exception as e:
        print(f"❌ Error during result fetch: {e}")
    return False

def bot_engine():
    global last_trade
    print("🚀 KhouryBot Engine V5.4 Started...")
    
    while True:
        now = datetime.now()
        
        # 1. ANALYSIS & SIGNAL (Example: 12:04:06)
        if (now.minute + 1) % 5 == 0 and now.second == 6:
            # --- YOUR STRATEGY LOGIC GOES HERE ---
            predicted_dir = "UP" 
            
            # Analysis Time is 'now'
            analysis_time = now.strftime('%H:%M:%S')
            # Result Check Time is 'now + 2 minutes' (12:06:06)
            result_time = (now + timedelta(minutes=2))
            
            last_trade = {
                'dir': predicted_dir,
                'check_at': result_time,
                'status': 'pending'
            }
            
            # Send Signal Message with Analysis Time
            msg = (
                f"🚀 **NEW SIGNAL (M1)**\n"
                f"━━━━━━━━━━━━━━━\n"
                f"📊 Asset: `Facebook inc otc`\n"
                f"🔔 Direction: `{predicted_dir}`\n"
                f"🕒 Analysis Time: `{analysis_time}`\n"
                f"🧪 Check Result: `{result_time.strftime('%H:%M:%S')}`"
            )
            send_telegram(msg)
            time.sleep(1) # Prevent double triggering

        # 2. RESULT CHECK (Exactly at 12:06:06)
        if last_trade and last_trade['status'] == 'pending':
            if now >= last_trade['check_at']:
                if check_and_send_result(last_trade['dir']):
                    last_trade = None # Reset for next cycle
        
        time.sleep(0.5) # CPU-friendly loop

if __name__ == "__main__":
    # Start Flask for uptime monitoring
    port = int(os.environ.get("PORT", 5000))
    threading.Thread(target=lambda: app.run(host='0.0.0.0', port=port, use_reloader=False)).start()
    
    # Start the trading engine
    bot_engine()
