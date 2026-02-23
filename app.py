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

# --- Flask Server ---
app = Flask(__name__)
@app.route('/')
def health_check(): return "EURGBP Tick Bot - Fixed", 200

# --- Configuration ---
TOKEN = '8511172742:AAFxZIj8N07FB-tFnJ_l3rv13loyRMmsRYU'
CHAT_ID = '-1003731752986'
BEIRUT_TZ = pytz.timezone('Asia/Beirut')
WS_URL = "wss://blue.derivws.com/websockets/v3?app_id=16929"
SYMBOL = "frxEURGBP"

is_bot_busy = False
active_trade = {}
last_analyzed_min = -1 # لمنع التكرار في نفس الدقيقة

def send_telegram_msg(text):
    url = f"https://api.telegram.org/bot{TOKEN}/sendMessage"
    try: requests.post(url, json={"chat_id": CHAT_ID, "text": text, "parse_mode": "Markdown"}, timeout=10)
    except: pass

def get_ticks_and_build_candle(symbol, minutes_back):
    try:
        ws = websocket.create_connection(WS_URL, timeout=10)
        # زيادة عدد التيكات لضمان تغطية الوقت
        ws.send(json.dumps({"ticks_history": symbol, "count": 5000, "end": "latest", "style": "ticks"}))
        res = json.loads(ws.recv())
        ws.close()

        if 'history' not in res: return None
        
        df = pd.DataFrame({
            'price': res['history']['prices'],
            'time': [datetime.fromtimestamp(t, tz=pytz.utc).astimezone(BEIRUT_TZ) for t in res['history']['times']]
        })

        # تحديد بداية الفترة
        now = datetime.now(BEIRUT_TZ)
        start_time = now.replace(second=0, microsecond=0) - timedelta(minutes=minutes_back)
        period_ticks = df[df['time'] >= start_time]

        if period_ticks.empty: 
            print(f"No ticks found for the last {minutes_back} minutes")
            return None
        
        return {
            'open': period_ticks['price'].iloc[0],
            'close': period_ticks['price'].iloc[-1]
        }
    except Exception as e:
        print(f"Error in get_ticks: {e}")
        return None

def analyze_reversal():
    global is_bot_busy, active_trade
    now = datetime.now(BEIRUT_TZ)
    
    # فحص الدقائق 14, 29, 44, 59
    if (now.minute + 1) % 15 != 0: return

    print(f"Analyzing EURGBP at {now.strftime('%H:%M:%S')}...")
    
    c15 = get_ticks_and_build_candle(SYMBOL, 15)
    c1 = get_ticks_and_build_candle(SYMBOL, 1)

    if not c15 or not c1: 
        print("Could not build candles from ticks.")
        return

    direction = None
    # طباعة القيم في الكونسول للتأكد
    print(f"15m: Open {c15['open']}, Close {c15['close']} | 1m: Open {c1['open']}, Close {c1['close']}")

    if c15['close'] > c15['open'] and c1['close'] > c1['open']:
        direction = "PUT"
    elif c15['close'] < c15['open'] and c1['close'] < c1['open']:
        direction = "CALL"

    if direction:
        is_bot_busy = True
        entry_time = (now + timedelta(minutes=1)).replace(second=0, microsecond=0)
        active_trade = {
            "symbol": SYMBOL,
            "direction": direction,
            "entry_time": entry_time,
            "exit_time": entry_time + timedelta(minutes=1),
            "is_martingale": False
        }
        send_telegram_msg(f"🚨 **EURGBP SIGNAL CONFIRMED**\n🎯 Action: *{direction}*\n🕐 Entry: {entry_time.strftime('%H:%M:00')}\n⏱ Duration: 1m")
    else:
        print("Conditions not met: Candles not in the same direction.")

def check_result():
    global is_bot_busy, active_trade
    now = datetime.now(BEIRUT_TZ)
    if not is_bot_busy or now < active_trade['exit_time']: return

    # انتظر ثانيتين لضمان وصول تيك الإغلاق
    time.sleep(2)
    c_result = get_ticks_and_build_candle(SYMBOL, 1)
    if not c_result: return

    win = (c_result['close'] > c_result['open']) if active_trade['direction'] == "CALL" else (c_result['close'] < c_result['open'])

    if win:
        send_telegram_msg(f"✅ **WIN** (EURGBP)")
        is_bot_busy = False
    else:
        if not active_trade['is_martingale']:
            new_entry = active_trade['exit_time']
            active_trade.update({
                "entry_time": new_entry,
                "exit_time": new_entry + timedelta(minutes=1),
                "is_martingale": True
            })
            print("Entering Martingale...")
        else:
            send_telegram_msg(f"❌ **LOSS** (EURGBP)")
            is_bot_busy = False

def start_engine():
    global last_analyzed_min
    print("Bot Engine Started...")
    while True:
        now = datetime.now(BEIRUT_TZ)
        
        # تحليل في أول 5 ثوانٍ من الدقيقة المطلوبة لمنع فوات الفرصة
        if (now.minute + 1) % 15 == 0 and now.second < 5 and not is_bot_busy:
            if last_analyzed_min != now.minute:
                threading.Thread(target=analyze_reversal).start()
                last_analyzed_min = now.minute
        
        if is_bot_busy and now >= active_trade['exit_time']:
            check_result()
            
        time.sleep(1)

if __name__ == "__main__":
    threading.Thread(target=lambda: app.run(host='0.0.0.0', port=10000), daemon=True).start()
    start_engine()
