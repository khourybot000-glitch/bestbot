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
def health_check(): return "Bot Active: 20 Pairs Mode", 200

def run_flask():
    port = int(os.environ.get("PORT", 10000))
    app.run(host='0.0.0.0', port=port)

# --- Configuration ---
TOKEN = '8511172742:AAFxZIj8N07FB-tFnJ_l3rv13loyRMmsRYU'
CHAT_ID = '-1003731752986'
BEIRUT_TZ = pytz.timezone('Asia/Beirut')
WS_URL = "wss://blue.derivws.com/websockets/v3?app_id=16929"

# قائمة الـ 20 زوجاً (Forex)
SYMBOLS = [
    "frxEURGBP", "frxEURUSD", "frxGBPUSD", "frxUSDJPY", "frxAUDUSD",
    "frxUSDCAD", "frxUSDCHF", "frxEURJPY", "frxGBPJPY", "frxEURAUD",
    "frxEURCAD", "frxAUDJPY", "frxGBPCAD", "frxNZDUSD", "frxGBPAUD",
    "frxAUDCAD", "frxEURNZD", "frxAUDNZD", "frxGBPNZD", "frxCADJPY"
]

# Variables for status tracking
is_waiting_for_result = False
is_martingale_step = False
pending_trade_direction = None
pending_trade_symbol = None
target_result_time = None

def send_telegram_msg(text):
    url = f"https://api.telegram.org/bot{TOKEN}/sendMessage"
    try:
        requests.post(url, json={"chat_id": CHAT_ID, "text": text, "parse_mode": "Markdown"}, timeout=10)
    except: pass

def check_trade_result(candles, symbol):
    global is_waiting_for_result, is_martingale_step, pending_trade_direction, target_result_time, pending_trade_symbol
    if not candles: return
    
    last_candle = candles[-1]
    if pending_trade_direction == "CALL (BUY)":
        won = last_candle['close'] > last_candle['open']
    else:
        won = last_candle['close'] < last_candle['open']
    
    if won:
        msg = f"✅ **WIN ({symbol.replace('frx','')})**" if not is_martingale_step else f"✅ **MTG WIN ({symbol.replace('frx','')})**"
        send_telegram_msg(msg)
        is_waiting_for_result = False
        is_martingale_step = False
        pending_trade_symbol = None
    else:
        if not is_martingale_step:
            is_martingale_step = True
            now = datetime.now(BEIRUT_TZ)
            target_result_time = (now + timedelta(minutes=1)).strftime('%H:%M')
        else:
            send_telegram_msg(f"❌ **MTG LOSS ({symbol.replace('frx','')})**")
            is_waiting_for_result = False
            is_martingale_step = False
            pending_trade_symbol = None

def analyze_strategy(candles, symbol):
    global is_waiting_for_result, pending_trade_direction, target_result_time, pending_trade_symbol
    
    # إذا وجدنا صفقة بالفعل في زوج آخر، نتوقف
    if is_waiting_for_result: return

    if len(candles) < 30: return
    
    swing_highs, swing_lows = [], []
    for i in range(0, len(candles) - 8):
        if all(candles[j]['close'] > candles[j]['open'] for j in range(i, i+3)) and \
           all(candles[j]['close'] < candles[j]['open'] for j in range(i+3, i+6)):
            swing_highs.append(max(c['high'] for c in candles[i:i+6]))
        if all(candles[j]['close'] < candles[j]['open'] for j in range(i, i+3)) and \
           all(candles[j]['close'] > candles[j]['open'] for j in range(i+3, i+6)):
            swing_lows.append(min(c['low'] for c in candles[i:i+6]))

    if not swing_highs or not swing_lows: return
    active_res, active_sup = swing_highs[-1], swing_lows[-1]
    c_break, c_retest = candles[-2], candles[-1]

    direction = None
    if c_break['close'] > active_res and c_break['close'] > c_break['open'] and \
       c_retest['close'] < c_retest['open'] and c_retest['close'] > active_res:
        direction = "CALL (BUY)"
        level = active_res
    elif c_break['close'] < active_sup and c_break['close'] < c_break['open'] and \
         c_retest['close'] > c_retest['open'] and c_retest['close'] < active_sup:
        direction = "PUT (SELL)"
        level = active_sup

    if direction and not is_waiting_for_result:
        now_beirut = datetime.now(BEIRUT_TZ)
        entry_time = (now_beirut + timedelta(minutes=1)).strftime('%H:%M')
        target_result_time = (now_beirut + timedelta(minutes=2)).strftime('%H:%M')
        
        asset_name = symbol.replace("frx", "")
        signal_msg = (
            f"🚀 **NEW SIGNAL FOUND**\n\n"
            f"🏆 Asset: {asset_name}\n"
            f"🎯 Direction: *{direction}*\n"
            f"📍 Entry Level: `{level:.5f}`\n"
            f"🕐 Entry Time: {entry_time}\n"
            f"⚠️ Duration: 1 Min (+1 MTG)"
        )
        send_telegram_msg(signal_msg)
        
        is_waiting_for_result = True
        pending_trade_direction = direction
        pending_trade_symbol = symbol

def on_message(ws, message):
    data = json.loads(message)
    
    # معالجة بيانات تاريخ الأسعار
    if 'history' in data:
        symbol = data.get('echo_req', {}).get('ticks_history')
        prices = data['history']['prices']
        df = pd.DataFrame(prices, columns=['price'])
        candles = []
        for i in range(0, len(df), 60):
            chunk = df.iloc[i:i+60]
            if len(chunk) == 60:
                candles.append({'open': chunk['price'].iloc[0], 'close': chunk['price'].iloc[-1], 
                                'high': chunk['price'].max(), 'low': chunk['price'].min()})
        
        if is_waiting_for_result and symbol == pending_trade_symbol:
            check_trade_result(candles, symbol)
        elif not is_waiting_for_result:
            analyze_strategy(candles, symbol)

def on_open(ws):
    global is_waiting_for_result, pending_trade_symbol
    if is_waiting_for_result:
        # إذا ننتظر نتيجة، نطلب بيانات الزوج المفتوح فقط
        req = {"ticks_history": pending_trade_symbol, "count": 60, "end": "latest", "style": "ticks"}
        ws.send(json.dumps(req))
    else:
        # إذا نبحث عن فرص، نطلب بيانات الـ 20 زوجاً
        for symbol in SYMBOLS:
            req = {"ticks_history": symbol, "count": 6000, "end": "latest", "style": "ticks"}
            ws.send(json.dumps(req))
            time.sleep(0.1) # تأخير بسيط لتجنب ضغط الطلبات

def start_engine():
    global is_waiting_for_result, target_result_time
    while True:
        now = datetime.now(BEIRUT_TZ)
        current_time_str = now.strftime('%H:%M')
        
        if is_waiting_for_result and current_time_str == target_result_time:
            ws = websocket.WebSocketApp(WS_URL, on_open=on_open, on_message=on_message)
            ws.run_forever()
            time.sleep(1)
            continue

        if 9 <= now.hour < 21 and now.weekday() <= 4:
            wait = 60 - now.second
            time.sleep(wait)
            if not is_waiting_for_result:
                ws = websocket.WebSocketApp(WS_URL, on_open=on_open, on_message=on_message)
                ws.run_forever(ping_timeout=15)
        else:
            time.sleep(30)

if __name__ == "__main__":
    threading.Thread(target=run_flask, daemon=True).start()
    start_engine()
