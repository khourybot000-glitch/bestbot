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
def health_check(): return "Tick-to-Candle Bot Active", 200

# --- Configuration ---
TOKEN = '8511172742:AAFxZIj8N07FB-tFnJ_l3rv13loyRMmsRYU'
CHAT_ID = '-1003731752986'
BEIRUT_TZ = pytz.timezone('Asia/Beirut')
WS_URL = "wss://blue.derivws.com/websockets/v3?app_id=16929"
SYMBOL = "frxEURGBP"

is_bot_busy = False
active_trade = {}

def send_telegram_msg(text):
    url = f"https://api.telegram.org/bot{TOKEN}/sendMessage"
    try: requests.post(url, json={"chat_id": CHAT_ID, "text": text, "parse_mode": "Markdown"}, timeout=10)
    except: pass

def get_ticks_and_build_candle(symbol, minutes_back):
    """يجلب التيكات ويحولها إلى شمعة (Open, Close) بناءً على الوقت"""
    try:
        ws = websocket.create_connection(WS_URL)
        # نطلب تيكات تكفي للفترة المطلوبة (تقريباً تيك كل ثانية)
        count = (minutes_back * 60) + 100 
        ws.send(json.dumps({"ticks_history": symbol, "count": count, "end": "latest", "style": "ticks"}))
        res = json.loads(ws.recv())
        ws.close()

        prices = res['history']['prices']
        times = res['history']['times']
        
        df = pd.DataFrame({
            'price': prices,
            'time': [datetime.fromtimestamp(t, tz=pytz.utc).astimezone(BEIRUT_TZ) for t in times]
        })

        # تحديد بداية الفترة المطلوبة
        start_time = datetime.now(BEIRUT_TZ).replace(second=0, microsecond=0) - timedelta(minutes=minutes_back)
        period_ticks = df[df['time'] >= start_time]

        if period_ticks.empty: return None
        
        return {
            'open': period_ticks['price'].iloc[0],
            'close': period_ticks['price'].iloc[-1]
        }
    except Exception as e:
        print(f"Tick Error: {e}")
        return None

def analyze_reversal():
    global is_bot_busy, active_trade
    now = datetime.now(BEIRUT_TZ)
    
    if (now.minute + 1) % 15 != 0: return

    try:
        # بناء شمعة الـ 15 دقيقة من التيكات
        c15 = get_ticks_and_build_candle(SYMBOL, 15)
        # بناء شمعة الدقيقة الماضية (الدقيقة 13) من التيكات
        c1 = get_ticks_and_build_candle(SYMBOL, 1)

        if not c15 or not c1: return

        direction = None
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
            send_telegram_msg(f"🚨 **TICK-BASED SIGNAL**\n🌍 Pair: EURGBP\n🎯 Action: *{direction}*\n🕐 Entry: {entry_time.strftime('%H:%M:00')}\n⏱ Duration: 1m")
            
    except Exception as e: print(f"Analysis Error: {e}")

def check_result():
    global is_bot_busy, active_trade
    now = datetime.now(BEIRUT_TZ)
    if not is_bot_busy or now < active_trade['exit_time']: return

    try:
        # فحص نتيجة الدقيقة التي انتهت بناءً على التيكات
        c_result = get_ticks_and_build_candle(SYMBOL, 1)
        if not c_result: return

        win = (c_result['close'] > c_result['open']) if active_trade['direction'] == "CALL" else (c_result['close'] < c_result['open'])

        if win:
            msg = "✅ **WIN**" if not active_trade['is_martingale'] else "✅ **MTG WIN**"
            send_telegram_msg(f"{msg} (EURGBP)")
            is_bot_busy = False
        else:
            if not active_trade['is_martingale']:
                # دخول المضاعفة
                new_entry = active_trade['exit_time']
                active_trade.update({
                    "entry_time": new_entry,
                    "exit_time": new_entry + timedelta(minutes=1),
                    "is_martingale": True
                })
            else:
                send_telegram_msg(f"❌ **MTG LOSS** (EURGBP)")
                is_bot_busy = False
    except Exception as e: print(f"Check Error: {e}")

def start_engine():
    print("Bot is building candles from raw ticks...")
    while True:
        now = datetime.now(BEIRUT_TZ)
        if now.second == 0 and not is_bot_busy:
            analyze_reversal()
            time.sleep(1)
        if is_bot_busy and now >= active_trade['exit_time'] and now.second == 2:
            check_result()
        time.sleep(0.5)

if __name__ == "__main__":
    threading.Thread(target=lambda: app.run(host='0.0.0.0', port=10000), daemon=True).start()
    start_engine()
