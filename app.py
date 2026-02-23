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
def health_check(): return "Bot Active: 150 Ticks Logic", 200

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
    won = (last_candle['close'] > last_candle['open']) if pending_trade_direction == "CALL (BUY)" else (last_candle['close'] < last_candle['open'])
    
    symbol_name = symbol.replace("frx","")
    if won:
        send_telegram_msg(f"✅ **{'WIN' if not is_martingale_step else 'MTG WIN'} ({symbol_name})**")
        is_waiting_for_result = is_martingale_step = False
        pending_trade_symbol = target_result_time = None
    else:
        if not is_martingale_step:
            is_martingale_step = True
            now = datetime.now(BEIRUT_TZ)
            # النتيجة القادمة بعد دقيقة (شمعة المضاعفة)
            target_result_time = (now + timedelta(minutes=1)).strftime('%H:%M')
        else:
            send_telegram_msg(f"❌ **MTG LOSS ({symbol_name})**")
            is_waiting_for_result = is_martingale_step = False
            pending_trade_symbol = target_result_time = None

def analyze_strategy(ticks, symbol):
    global is_waiting_for_result, pending_trade_direction, target_result_time, pending_trade_symbol
    if is_waiting_for_result or len(ticks) < 150: return
    
    # تقسيم: 60 تيك، 60 تيك، 30 تيك
    c1 = ticks[0:60]
    c2 = ticks[60:120]
    c3 = ticks[120:150]
    
    # الاتجاهات
    d1 = 1 if c1[-1] > c1[0] else -1
    d2 = 1 if c2[-1] > c2[0] else -1
    d3 = 1 if c3[-1] > c3[0] else -1
    
    # شرط: عكس بعض (مثال: صاعد-هابط-صاعد)
    if d1 != d2 and d2 != d3:
        direction = "CALL (BUY)" if d3 == 1 else "PUT (SELL)"
        now = datetime.now(BEIRUT_TZ)
        entry_time = (now + timedelta(seconds=30)).strftime('%H:%M')
        target_result_time = (now + timedelta(seconds=90)).strftime('%H:%M')
        
        msg = (f"🚀 **150-TICKS SIGNAL**\n🏆 Asset: {symbol.replace('frx','')}\n🎯 Direction: *{direction}*\n🕐 Entry: {entry_time}\n⏱ Duration: 1 Min")
        send_telegram_msg(msg)
        
        is_waiting_for_result = True
        pending_trade_direction = direction
        pending_trade_symbol = symbol

def on_message(ws, message):
    data = json.loads(message)
    if 'history' in data:
        symbol = data.get('echo_req', {}).get('ticks_history')
        prices = data['history']['prices']
        
        if is_waiting_for_result and symbol == pending_trade_symbol:
            # تحويل التيكات لشمعة للفحص
            candles = [{'open': prices[0], 'close': prices[-1]}]
            check_trade_result(candles, symbol)
        elif not is_waiting_for_result:
            analyze_strategy(prices, symbol)
    ws.close()

def on_open(ws):
    if is_waiting_for_result:
        ws.send(json.dumps({"ticks_history": pending_trade_symbol, "count": 60, "end": "latest", "style": "ticks"}))
    else:
        for s in SYMBOLS:
            ws.send(json.dumps({"ticks_history": s, "count": 150, "end": "latest", "style": "ticks"}))
            time.sleep(0.05)

def start_engine():
    global is_waiting_for_result, target_result_time
    while True:
        now = datetime.now(BEIRUT_TZ)
        current_hm = now.strftime('%H:%M')
        
        # فحص النتيجة عند الثانية 00 من الدقيقة المستهدفة
        if is_waiting_for_result and current_hm == target_result_time and now.second == 0:
            time.sleep(0.5)
            ws = websocket.WebSocketApp(WS_URL, on_open=on_open, on_message=on_message)
            ws.run_forever()
            time.sleep(2)
            continue

        # التحليل عند الثانية 30 من كل دقيقة
        if 9 <= now.hour < 21 and now.weekday() <= 4:
            if now.second == 30 and not is_waiting_for_result:
                ws = websocket.WebSocketApp(WS_URL, on_open=on_open, on_message=on_message)
                ws.run_forever(ping_timeout=15)
                time.sleep(1)
            else:
                time.sleep(0.5)
        else:
            time.sleep(30)

if __name__ == "__main__":
    threading.Thread(target=run_flask, daemon=True).start()
    start_engine()
