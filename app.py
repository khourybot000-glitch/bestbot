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
def health_check(): return "Bot Active: 4m 50s Timer Logic", 200

# --- Configuration ---
TOKEN = '8511172742:AAFxZIj8N07FB-tFnJ_l3rv13loyRMmsRYU'
CHAT_ID = '-1003731752986'
BEIRUT_TZ = pytz.timezone('Asia/Beirut')
WS_URL = "wss://blue.derivws.com/websockets/v3?app_id=16929"

SYMBOLS = ["frxEURGBP"]
RSI_PERIOD = 14
RSI_ALERT_LOW = 30
RSI_ALERT_HIGH = 70

is_bot_busy = False
pending_observation = {} 

def send_telegram_msg(text):
    url = f"https://api.telegram.org/bot{TOKEN}/sendMessage"
    try: requests.post(url, json={"chat_id": CHAT_ID, "text": text, "parse_mode": "Markdown"}, timeout=10)
    except: pass

def calculate_rsi(prices, period=14):
    if len(prices) < period + 1: return np.array([50] * len(prices))
    deltas = np.diff(prices)
    seed = deltas[:period+1]
    up = seed[seed >= 0].sum()/period
    down = -seed[seed < 0].sum()/period
    if down == 0: return np.array([100] * len(prices))
    rs = up/down
    rsi = np.zeros_like(prices)
    rsi[:period] = 100. - 100./(1. + rs)
    for i in range(period, len(prices)):
        delta = deltas[i-1]
        upval = delta if delta > 0 else 0.
        downval = -delta if delta < 0 else 0.
        up = (up * (period - 1) + upval) / period
        down = (down * (period - 1) + downval) / period
        if down == 0: rsi[i] = 100
        else:
            rs = up / down
            rsi[i] = 100. - 100. / (1. + rs)
    return rsi

def start_watching_trend():
    global pending_observation, is_bot_busy
    if is_bot_busy or pending_observation: return

    for symbol in SYMBOLS:
        try:
            ws = websocket.create_connection(WS_URL)
            ws.send(json.dumps({"ticks_history": symbol, "count": 500, "end": "latest", "style": "ticks"}))
            res = json.loads(ws.recv()); ws.close()
            
            prices = res['history']['prices']
            rsi_val = calculate_rsi(prices, RSI_PERIOD)[-1]

            # تفعيل المراقبة عند لمس التشبع
            if rsi_val <= RSI_ALERT_LOW or rsi_val >= RSI_ALERT_HIGH:
                pending_observation = {
                    "symbol": symbol,
                    "start_price": prices[-1],
                    # المؤقت لـ 4 دقائق و 50 ثانية
                    "obs_end_time": datetime.now(BEIRUT_TZ) + timedelta(minutes=4, seconds=50)
                }
                print(f"Monitoring {symbol} for 4m 50s...")
                break
        except: pass

def analyze_and_send_signal():
    global pending_observation, is_bot_busy, active_trade_data
    if not pending_observation: return
    
    now = datetime.now(BEIRUT_TZ)
    # التأكد من مرور الـ 4:50 دقيقة والوصول للثانية 40 المطلوبة
    if now < pending_observation['obs_end_time'] or now.second < 40: return

    symbol = pending_observation['symbol']
    try:
        ws = websocket.create_connection(WS_URL)
        ws.send(json.dumps({"ticks_history": symbol, "count": 5, "style": "ticks"}))
        res = json.loads(ws.recv()); ws.close()
        current_price = res['history']['prices'][-1]
        start_price = pending_observation['start_price']
        
        direction = "CALL (BUY)" if current_price > start_price else "PUT (SELL)"

        is_bot_busy = True
        entry_time = (now + timedelta(minutes=1)).replace(second=0, microsecond=0)
        active_trade_data = {
            "symbol": symbol,
            "entry_time": entry_time,
            "exit_time": entry_time + timedelta(minutes=5),
            "direction": "CALL" if "CALL" in direction else "PUT"
        }
        send_telegram_msg(f"🎯 **SIGNAL CONFIRMED**\n🌍 Pair: {symbol.replace('frx','')}\n📈 Direction: *{direction}*\n🕐 Entry: {entry_time.strftime('%H:%M:00')}\n⏱ Duration: 5m")
        
        pending_observation = {} 
    except: pending_observation = {}

def check_result():
    global is_bot_busy, active_trade_data
    now = datetime.now(BEIRUT_TZ)
    if not is_bot_busy or now < active_trade_data['exit_time']: return

    try:
        ws = websocket.create_connection(WS_URL)
        ws.send(json.dumps({"ticks_history": active_trade_data['symbol'], "count": 500, "style": "ticks"}))
        res = json.loads(ws.recv()); ws.close()
        prices = res['history']['prices']
        
        # مقارنة بسيطة للنتيجة بناءً على آخر تيك متاح
        win = (prices[-1] > prices[0]) if active_trade_data['direction'] == "CALL" else (prices[-1] < prices[0])
        send_telegram_msg(f"{'✅ WIN' if win else '❌ LOSS'} ({active_trade_data['symbol'].replace('frx','')})")
        
        is_bot_busy = False
    except: is_bot_busy = False

def start_engine():
    while True:
        now = datetime.now(BEIRUT_TZ)
        # الرصد عند الثانية 50
        if now.second == 50 and not pending_observation and not is_bot_busy:
            threading.Thread(target=start_watching_trend).start()
        
        # إرسال الإشارة بعد انتهاء المؤقت وعند الثانية 40
        if pending_observation:
            analyze_and_send_signal()
        
        # فحص النتيجة
        if is_bot_busy and now >= active_trade_data['exit_time'] and now.second == 5:
            check_result()
            
        time.sleep(1)

if __name__ == "__main__":
    threading.Thread(target=lambda: app.run(host='0.0.0.0', port=int(os.environ.get("PORT", 10000))), daemon=True).start()
    start_engine()
