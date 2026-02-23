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
def health_check(): return "Bot Active: Martingale Logic Fixed", 200

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
    except Exception as e:
        print(f"Telegram Error: {e}")

def check_trade_result(candles, symbol):
    global is_waiting_for_result, is_martingale_step, pending_trade_direction, target_result_time, pending_trade_symbol
    if not candles: return
    
    # جلب آخر شمعة مكتملة (آخر 60 تيك)
    last_candle = candles[-1]
    open_p = last_candle['open']
    close_p = last_candle['close']
    
    won = (close_p > open_p) if pending_trade_direction == "CALL (BUY)" else (close_p < open_p)
    symbol_name = symbol.replace("frx","")

    if won:
        msg = f"✅ **WIN ({symbol_name})**" if not is_martingale_step else f"✅ **MTG WIN ({symbol_name})**"
        send_telegram_msg(msg)
        # تصفير كل شيء للبحث عن صفقة جديدة
        is_waiting_for_result = False
        is_martingale_step = False
        pending_trade_symbol = None
        target_result_time = None
    else:
        if not is_martingale_step:
            # خسارة أولى -> لا نرسل شيئاً ونبرمج وقت النتيجة للمضاعفة بعد دقيقة واحدة
            is_martingale_step = True
            now = datetime.now(BEIRUT_TZ)
            target_result_time = (now + timedelta(minutes=1)).strftime('%H:%M')
            print(f"Loss on first try. Waiting for MTG at {target_result_time}")
        else:
            # خسارة بعد المضاعفة
            send_telegram_msg(f"❌ **MTG LOSS ({symbol_name})**")
            is_waiting_for_result = False
            is_martingale_step = False
            pending_trade_symbol = None
            target_result_time = None

def analyze_strategy(candles, symbol):
    global is_waiting_for_result, pending_trade_direction, target_result_time, pending_trade_symbol
    if is_waiting_for_result or len(candles) < 30: return
    
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

    if direction:
        now_beirut = datetime.now(BEIRUT_TZ)
        entry_time = (now_beirut + timedelta(minutes=1)).strftime('%H:%M')
        target_result_time = (now_beirut + timedelta(minutes=2)).strftime('%H:%M')
        
        asset_name = symbol.replace("frx", "")
        msg = (f"🚀 **SIGNAL FOUND**\n🏆 Asset: {asset_name}\n🎯 Direction: *{direction}*\n📍 Entry: {entry_time}")
        send_telegram_msg(msg)
        
        is_waiting_for_result = True
        pending_trade_direction = direction
        pending_trade_symbol = symbol

def on_message(ws, message):
    data = json.loads(message)
    if 'history' in data:
        symbol = data.get('echo_req', {}).get('ticks_history')
        prices = data['history']['prices']
        df = pd.DataFrame(prices, columns=['price'])
        
        # تحويل التيكات إلى شمعة واحدة (آخر 60 تيك) أو أكثر للتحليل
        candles = []
        for i in range(0, len(df), 60):
            chunk = df.iloc[i:i+60]
            if len(chunk) == 60:
                candles.append({'open': chunk['price'].iloc[0], 'close': chunk['price'].iloc[-1]})
        
        if is_waiting_for_result and symbol == pending_trade_symbol:
            check_trade_result(candles, symbol)
        elif not is_waiting_for_result:
            analyze_strategy(candles, symbol)
    ws.close()

def on_open(ws):
    if is_waiting_for_result:
        # لطلب النتيجة: نحتاج فقط آخر 60 تيك (الدقيقة التي انتهت)
        ws.send(json.dumps({"ticks_history": pending_trade_symbol, "count": 60, "end": "latest", "style": "ticks"}))
    else:
        for symbol in SYMBOLS:
            ws.send(json.dumps({"ticks_history": symbol, "count": 6000, "end": "latest", "style": "ticks"}))
            time.sleep(0.05)

def start_engine():
    global is_waiting_for_result, target_result_time
    while True:
        now = datetime.now(BEIRUT_TZ)
        current_time = now.strftime('%H:%M')
        
        # لحظة فحص النتيجة (عند الثانية 00 بالضبط)
        if is_waiting_for_result and current_time == target_result_time:
            time.sleep(0.5) # تأخير بسيط جداً لضمان تسجيل السعر
            ws = websocket.WebSocketApp(WS_URL, on_open=on_open, on_message=on_message)
            ws.run_forever()
            time.sleep(2) # تجنب التكرار
            continue

        # وضع البحث عن الإشارات
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
