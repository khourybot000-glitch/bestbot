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
def health_check(): return "Bot Active: Breakout-Correction Mode", 200

def run_flask():
    port = int(os.environ.get("PORT", 10000))
    app.run(host='0.0.0.0', port=port)

# --- Configuration ---
TOKEN = '8511172742:AAFxZIj8N07FB-tFnJ_l3rv13loyRMmsRYU'
CHAT_ID = '-1003731752986'
BEIRUT_TZ = pytz.timezone('Asia/Beirut')
WS_URL = "wss://blue.derivws.com/websockets/v3?app_id=16929"

SYMBOLS = [
    "frxEURGBP", "frxEURUSD", "frxGBPUSD", "frxUSDJPY", "frxAUDUSD",
    "frxUSDCAD", "frxUSDCHF", "frxEURJPY", "frxGBPJPY", "frxEURAUD",
    "frxEURCAD", "frxAUDJPY", "frxGBPCAD", "frxNZDUSD", "frxGBPAUD",
    "frxAUDCAD", "frxEURNZD", "frxAUDNZD", "frxGBPNZD", "frxCADJPY"
]

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
    open_p, close_p = last_candle['open'], last_candle['close']
    
    won = (close_p > open_p) if pending_trade_direction == "CALL (BUY)" else (close_p < open_p)
    symbol_name = symbol.replace("frx","")

    if won:
        send_telegram_msg(f"✅ **{'WIN' if not is_martingale_step else 'MTG WIN'} ({symbol_name})**")
        is_waiting_for_result = is_martingale_step = False
        pending_trade_symbol = target_result_time = None
    else:
        if not is_martingale_step:
            is_martingale_step = True
            now = datetime.now(BEIRUT_TZ)
            target_result_time = (now + timedelta(minutes=1)).strftime('%H:%M')
        else:
            send_telegram_msg(f"❌ **MTG LOSS ({symbol_name})**")
            is_waiting_for_result = is_martingale_step = False
            pending_trade_symbol = target_result_time = None

def analyze_strategy(candles, symbol):
    global is_waiting_for_result, pending_trade_direction, target_result_time, pending_trade_symbol
    if is_waiting_for_result or len(candles) < 30: return
    
    # 1. تحديد مستويات الارتداد (3 صاعد / 3 هابط)
    swing_highs, swing_lows = [], []
    for i in range(0, len(candles) - 10):
        if all(candles[j]['close'] > candles[j]['open'] for j in range(i, i+3)) and \
           all(candles[j]['close'] < candles[j]['open'] for j in range(i+3, i+6)):
            swing_highs.append(max(c['high'] for c in candles[i:i+6]))
        if all(candles[j]['close'] < candles[j]['open'] for j in range(i, i+3)) and \
           all(candles[j]['close'] > candles[j]['open'] for j in range(i+3, i+6)):
            swing_lows.append(min(c['low'] for c in candles[i:i+6]))

    if not swing_highs or not swing_lows: return
    res, sup = swing_highs[-1], swing_lows[-1]
    
    # 2. فحص الشموع الأخيرة (شمعة الاختراق وشمعة التصحيح)
    c_break = candles[-2]  # شمعة الاختراق
    c_corr = candles[-1]   # شمعة التصحيح

    direction = None
    
    # منطق CALL: اختراق المقاومة للأعلى + شمعة حمراء تصحيحية أغلقت فوق المقاومة
    if c_break['close'] > res and c_break['close'] > c_break['open']:
        if c_corr['close'] < c_corr['open'] and c_corr['close'] > res:
            direction = "CALL (BUY)"
            level = res

    # منطق PUT: كسر الدعم للأسفل + شمعة خضراء تصحيحية أغلقت تحت الدعم
    elif c_break['close'] < sup and c_break['close'] < c_break['open']:
        if c_corr['close'] > c_corr['open'] and c_corr['close'] < sup:
            direction = "PUT (SELL)"
            level = sup

    if direction:
        now = datetime.now(BEIRUT_TZ)
        entry_time = (now + timedelta(minutes=1)).strftime('%H:%M')
        target_result_time = (now + timedelta(minutes=2)).strftime('%H:%M')
        
        msg = (f"🚀 **SIGNAL FOUND**\n🏆 Asset: {symbol.replace('frx','')}\n🎯 Direction: *{direction}*\n📍 Level: {level:.5f}\n🕐 Entry: {entry_time}")
        send_telegram_msg(msg)
        is_waiting_for_result, pending_trade_direction, pending_trade_symbol = True, direction, symbol

def on_message(ws, message):
    data = json.loads(message)
    if 'history' in data:
        symbol = data.get('echo_req', {}).get('ticks_history')
        prices = data['history']['prices']
        df = pd.DataFrame(prices, columns=['price'])
        candles = []
        for i in range(0, len(df), 60):
            chunk = df.iloc[i:i+60]
            if len(chunk) == 60:
                candles.append({'open': chunk['price'].iloc[0], 'close': chunk['price'].iloc[-1], 'high': chunk['price'].max(), 'low': chunk['price'].min()})
        
        if is_waiting_for_result and symbol == pending_trade_symbol:
            check_trade_result(candles, symbol)
        elif not is_waiting_for_result:
            analyze_strategy(candles, symbol)
    ws.close()

def on_open(ws):
    if is_waiting_for_result:
        ws.send(json.dumps({"ticks_history": pending_trade_symbol, "count": 60, "end": "latest", "style": "ticks"}))
    else:
        for s in SYMBOLS:
            ws.send(json.dumps({"ticks_history": s, "count": 6000, "end": "latest", "style": "ticks"}))
            time.sleep(0.05)

def start_engine():
    global is_waiting_for_result, target_result_time
    while True:
        now = datetime.now(BEIRUT_TZ)
        if is_waiting_for_result and now.strftime('%H:%M') == target_result_time:
            time.sleep(0.5)
            ws = websocket.WebSocketApp(WS_URL, on_open=on_open, on_message=on_message)
            ws.run_forever()
            time.sleep(2)
            continue
        if 9 <= now.hour < 21 and now.weekday() <= 4:
            time.sleep(60 - now.second)
            if not is_waiting_for_result:
                ws = websocket.WebSocketApp(WS_URL, on_open=on_open, on_message=on_message)
                ws.run_forever(ping_timeout=15)
        else:
            time.sleep(30)

if __name__ == "__main__":
    threading.Thread(target=run_flask, daemon=True).start()
    start_engine()
