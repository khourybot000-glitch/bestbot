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
def health_check(): return "Bot Active: Strong 3-Candle Reversal Mode", 200

def run_flask():
    port = int(os.environ.get("PORT", 10000))
    app.run(host='0.0.0.0', port=port)

# --- Config ---
TOKEN = '8511172742:AAFxZIj8N07FB-tFnJ_l3rv13loyRMmsRYU'
CHAT_ID = '-1003731752986'
BEIRUT_TZ = pytz.timezone('Asia/Beirut')
SYMBOL = "frxEURGBP"
WS_URL = "wss://blue.derivws.com/websockets/v3?app_id=16929"

def send_telegram_msg(direction, level):
    now_beirut = datetime.now(BEIRUT_TZ)
    entry_time = (now_beirut + timedelta(minutes=1)).strftime('%H:%M')
    msg = (f"🚀 **STRONG SIGNAL**\n"
           f"🏆 Symbol: EUR/GBP\n"
           f"🎯 Direction: {direction}\n"
           f"📍 Based on 3-Candle Reversal Level: {level:.5f}\n"
           f"🕐 Entry Time: {entry_time} (Beirut)")
    requests.post(f"https://api.telegram.org/bot{TOKEN}/sendMessage", json={"chat_id": CHAT_ID, "text": msg})

def analyze_strategy(candles):
    if len(candles) < 30: return
    
    swing_highs = []
    swing_lows = []
    
    # نمر على الشموع للبحث عن نمط 3 صاعد + 3 هابط (أو العكس)
    for i in range(0, len(candles) - 8): # نترك مساحة لآخر شمعتين (الاختراق والاختبار)
        # فحص المقاومة (3 صاعد ⬆️ ثم 3 هابط ⬇️)
        up_3 = all(candles[j]['close'] > candles[j]['open'] for j in range(i, i+3))
        down_3 = all(candles[j]['close'] < candles[j]['open'] for j in range(i+3, i+6))
        
        if up_3 and down_3:
            peak = max(c['high'] for c in candles[i:i+6])
            swing_highs.append(peak)

        # فحص الدعم (3 هابط ⬇️ ثم 3 صاعد ⬆️)
        down_3_v = all(candles[j]['close'] < candles[j]['open'] for j in range(i, i+3))
        up_3_v = all(candles[j]['close'] > candles[j]['open'] for j in range(i+3, i+6))
        
        if down_3_v and up_3_v:
            valley = min(c['low'] for c in candles[i:i+6])
            swing_lows.append(valley)

    if not swing_highs or not swing_lows: return

    # أقرب المستويات القوية المتشكلة ضمن الـ 6000 تيك
    active_res = swing_highs[-1]
    active_sup = swing_lows[-1]

    c_break = candles[-2] # شمعة الاختراق
    c_retest = candles[-1] # شمعة إعادة الاختبار

    # منطق تبادل الأدوار (Role Reversal)
    if c_break['close'] > active_res and c_break['close'] > c_break['open']:
        if c_retest['close'] < c_retest['open'] and c_retest['close'] > active_res:
            send_telegram_msg("CALL (BUY)", active_res)

    elif c_break['close'] < active_sup and c_break['close'] < c_break['open']:
        if c_retest['close'] > c_retest['open'] and c_retest['close'] < active_sup:
            send_telegram_msg("PUT (SELL)", active_sup)

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
        analyze_strategy(candles)
    ws.close()

def on_open(ws):
    ws.send(json.dumps({"ticks_history": SYMBOL, "count": 6000, "end": "latest", "style": "ticks"}))

def start_engine():
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
    start_engine()
