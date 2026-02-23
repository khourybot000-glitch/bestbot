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
def health_check(): return "EURGBP Bot - Always Awake", 200

# --- الإعدادات ---
TOKEN = '8511172742:AAFxZIj8N07FB-tFnJ_l3rv13loyRMmsRYU'
CHAT_ID = '-1003731752986'
BEIRUT_TZ = pytz.timezone('Asia/Beirut')
WS_URL = "wss://blue.derivws.com/websockets/v3?app_id=16929"
SYMBOL = "frxEURGBP"

is_bot_busy = False
active_trade = {}
last_analyzed_min = -1
session_announced = False 
end_announced = False     

def send_telegram_msg(text):
    url = f"https://api.telegram.org/bot{TOKEN}/sendMessage"
    try: requests.post(url, json={"chat_id": CHAT_ID, "text": text, "parse_mode": "Markdown"}, timeout=10)
    except: pass

def is_market_open():
    """فحص هل السوق متاح حسب جدولك (الاثنين-الجمعة، 9ص-9م)"""
    now = datetime.now(BEIRUT_TZ)
    weekday = now.weekday() # 0=الاثنين, 4=الجمعة
    hour = now.hour
    if weekday >= 5: return False # مغلق سبت وأحد
    if hour < 9 or hour >= 21: return False # خارج وقت 9ص-9م
    return True

def get_ticks_and_build_candle(symbol, minutes_back):
    try:
        ws = websocket.create_connection(WS_URL, timeout=10)
        ws.send(json.dumps({"ticks_history": symbol, "count": 5000, "end": "latest", "style": "ticks"}))
        res = json.loads(ws.recv())
        ws.close()
        if 'history' not in res: return None
        df = pd.DataFrame({
            'price': res['history']['prices'],
            'time': [datetime.fromtimestamp(t, tz=pytz.utc).astimezone(BEIRUT_TZ) for t in res['history']['times']]
        })
        now = datetime.now(BEIRUT_TZ)
        start_time = now.replace(second=0, microsecond=0) - timedelta(minutes=minutes_back)
        period_ticks = df[df['time'] >= start_time]
        if period_ticks.empty: return None
        return {'open': period_ticks['price'].iloc[0], 'close': period_ticks['price'].iloc[-1]}
    except: return None

def analyze_reversal():
    global is_bot_busy, active_trade
    now = datetime.now(BEIRUT_TZ)
    # التحليل في الدقيقة 14، 29، 44، 59
    if (now.minute + 1) % 15 != 0: return
    
    c15 = get_ticks_and_build_candle(SYMBOL, 15)
    c1 = get_ticks_and_build_candle(SYMBOL, 1)
    
    if not c15 or not c1: return
    
    direction = None
    if c15['close'] > c15['open'] and c1['close'] > c1['open']: direction = "PUT"
    elif c15['close'] < c15['open'] and c1['close'] < c1['open']: direction = "CALL"
    
    if direction:
        is_bot_busy = True
        entry_time = (now + timedelta(minutes=1)).replace(second=0, microsecond=0)
        active_trade = {"symbol": SYMBOL, "direction": direction, "entry_time": entry_time, "exit_time": entry_time + timedelta(minutes=1), "is_martingale": False}
        send_telegram_msg(f"🚨 **EURGBP SIGNAL**\n🎯 Action: *{direction}*\n🕐 Entry: {entry_time.strftime('%H:%M:00')}\n⏱ Duration: 1m")

def check_result():
    global is_bot_busy, active_trade
    now = datetime.now(BEIRUT_TZ)
    if not is_bot_busy or now < active_trade['exit_time']: return
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
            active_trade.update({"entry_time": new_entry, "exit_time": new_entry + timedelta(minutes=1), "is_martingale": True})
        else:
            send_telegram_msg(f"❌ **LOSS** (EURGBP)")
            is_bot_busy = False

def start_engine():
    global last_analyzed_min, session_announced, end_announced
    print("Bot is Always Awake - Checking time every second...")
    
    while True:
        now = datetime.now(BEIRUT_TZ)
        weekday = now.weekday()
        hour = now.hour
        
        # 1. نظام إشعارات الجلسة (من الاثنين للجمعة فقط)
        if weekday < 5:
            if hour == 9 and not session_announced:
                send_telegram_msg("🟢 **session started**")
                session_announced, end_announced = True, False
            if hour == 21 and not end_announced:
                send_telegram_msg("🔴 **session ended**")
                end_announced, session_announced = True, False
        else:
            # تصفير الحالة في عطلة نهاية الأسبوع ليكون جاهزاً للاثنين
            session_announced, end_announced = False, False

        # 2. فحص هل نحن في وقت العمل لتنفيذ التحليل
        if is_market_open():
            # التحليل في الدقائق المطلوبة (14, 29, 44, 59)
            if (now.minute + 1) % 15 == 0 and now.second < 5 and not is_bot_busy:
                if last_analyzed_min != now.minute:
                    threading.Thread(target=analyze_reversal).start()
                    last_analyzed_min = now.minute
            
            # فحص النتيجة إذا كان هناك صفقة قائمة
            if is_bot_busy and now >= active_trade['exit_time']:
                check_result()
        
        # البوت يظل يعمل ويفحص الوقت كل ثانية (لا ينام أبداً)
        time.sleep(1)

if __name__ == "__main__":
    # تشغيل Flask للسماح للمنصة بمعرفة أن البوت يعمل
    port = int(os.environ.get("PORT", 10000))
    threading.Thread(target=lambda: app.run(host='0.0.0.0', port=port), daemon=True).start()
    start_engine()
