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
def health_check(): return "Bot Active: 150 Ticks Strategy", 200

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
target_result_time = None # سيحتوي على الدقيقة والساعة

def send_telegram_msg(text):
    url = f"https://api.telegram.org/bot{TOKEN}/sendMessage"
    try:
        requests.post(url, json={"chat_id": CHAT_ID, "text": text, "parse_mode": "Markdown"}, timeout=10)
    except: pass

def check_trade_result(ticks, symbol):
    global is_waiting_for_result, is_martingale_step, pending_trade_direction, target_result_time, pending_trade_symbol
    if not ticks or len(ticks) < 30: return
    
    # الفحص عند الثانية 30 يعني نحتاج أول 30 تيك من الدقيقة الجديدة
    open_p = ticks[0]
    close_p = ticks[29] if len(ticks) >= 30 else ticks[-1]
    
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
            # المضاعفة تنتهي بعد 30 ثانية أخرى
            target_result_time = (now + timedelta(seconds=60)).strftime('%H:%M:%S')
        else:
            send_telegram_msg(f"❌ **MTG LOSS ({symbol_name})**")
            is_waiting_for_result = is_martingale_step = False
            pending_trade_symbol = target_result_time = None

def analyze_strategy(ticks, symbol):
    global is_waiting_for_result, pending_trade_direction, target_result_time, pending_trade_symbol
    if is_waiting_for_result or len(ticks) < 150: return
    
    # تقسيم 150 تيك: 60 (ش1), 60 (ش2), 30 (ش3)
    c1_ticks = ticks[0:60]
    c2_ticks = ticks[60:120]
    c3_ticks = ticks[120:150]
    
    # تحديد اتجاه كل شمعة (Open vs Close)
    c1_dir = 1 if c1_ticks[-1] > c1_ticks[0] else -1 # 1 صاعدة، -1 هابطة
    c2_dir = 1 if c2_ticks[-1] > c2_ticks[0] else -1
    c3_dir = 1 if c3_ticks[-1] > c3_ticks[0] else -1
    
    # الشرط: كل شمعة عكس الثانية (1, -1, 1) أو (-1, 1, -1)
    if c1_dir != c2_dir and c2_dir != c3_dir:
        direction = "CALL (BUY)" if c3_dir == 1 else "PUT (SELL)"
        
        now = datetime.now(BEIRUT_TZ)
        entry_time = (now + timedelta(seconds=0)).strftime('%H:%M:%S')
        # الفحص عند الثانية 30 من الدقيقة القادمة
        res_dt = now + timedelta(minutes=1)
        target_result_time = res_dt.replace(second=30).strftime('%H:%M:%S')
        
        asset_name = symbol.replace("frx", "")
        msg = (f"🚀 **150-TICKS SIGNAL**\n🏆 Asset: {asset_name}\n🎯 Direction: *{direction}*\n🕐 Entry: {entry_time}\n⏱ Result at: Second :30")
        send_telegram_msg(msg)
        
        is_waiting_for_result = True
        pending_trade_direction = direction
        pending_trade_symbol = symbol

def on_message(ws, message):
    data = json.loads(message)
    if 'history' in data:
        symbol = data.get('echo_req', {}).get('ticks_history')
        ticks = data['history']['prices']
        
        if is_waiting_for_result and symbol == pending_trade_symbol:
            check_trade_result(ticks, symbol)
        elif not is_waiting_for_result:
            analyze_strategy(ticks, symbol)
    ws.close()

def on_open(ws):
    if is_waiting_for_result:
        # نحتاج 30 تيك للفحص
        ws.send(json.dumps({"ticks_history": pending_trade_symbol, "count": 30, "end": "latest", "style": "ticks"}))
    else:
        for s in SYMBOLS:
            ws.send(json.dumps({"ticks_history": s, "count": 150, "end": "latest", "style": "ticks"}))
            time.sleep(0.05)

def start_engine():
    global is_waiting_for_result, target_result_time
    while True:
        now = datetime.now(BEIRUT_TZ)
        current_full_time = now.strftime('%H:%M:%S')
        
        # فحص النتيجة عند الثانية 30 المحددة
        if is_waiting_for_result and current_full_time == target_result_time:
            ws = websocket.WebSocketApp(WS_URL, on_open=on_open, on_message=on_message)
            ws.run_forever()
            time.sleep(2)
            continue

        # البحث عن فرص عند بداية كل دقيقة (الثانية 00)
        if 9 <= now.hour < 21 and now.weekday() <= 4:
            if now.second == 0 and not is_waiting_for_result:
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
