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
def health_check(): return "Signal Engine Active - No MTG", 200

# --- Configuration ---
TOKEN = '8511172742:AAFxZIj8N07FB-tFnJ_l3rv13loyRMmsRYU'
CHAT_ID = '-1003731752986'
BEIRUT_TZ = pytz.timezone('Asia/Beirut')
WS_URL = "wss://blue.derivws.com/websockets/v3?app_id=16929"
SYMBOL = "frxEURJPY"

# Control Variables
last_signal_time = ""
active_trade = None
analysis_lock = threading.Lock()

def send_telegram_msg(text):
    url = f"https://api.telegram.org/bot{TOKEN}/sendMessage"
    try:
        requests.post(url, json={"chat_id": CHAT_ID, "text": text, "parse_mode": "Markdown"}, timeout=5)
    except:
        pass

def fast_indicator_logic(df):
    """تحليل سريع جداً باستخدام 20 نقطة قوة"""
    c = df['close'].values
    o = df['open'].values
    up, down = 0, 0
    
    # 1. RSI (Fast Calculation)
    diff = np.diff(c)
    avg_gain = np.mean(np.where(diff > 0, diff, 0)[-14:])
    avg_loss = np.mean(np.where(diff < 0, -diff, 0)[-14:])
    rsi = 100 - (100 / (1 + (avg_gain / (avg_loss + 1e-10))))
    
    if rsi < 50: up += 10
    else: down += 10

    # 2. Price Action
    if c[-1] > o[-1]: up += 10
    else: down += 10

    direction = "CALL" if up >= down else "PUT"
    strength = (max(up, down) / 20) * 100
    return direction, strength

def check_result_final():
    """فحص النتيجة مرة واحدة فقط عند انتهاء الدقيقة"""
    global active_trade
    now = datetime.now(BEIRUT_TZ)
    # وقت الفحص بعد دقيقة واحدة من الدخول
    check_time = active_trade['entry_time'] + timedelta(minutes=1)
    
    if now.strftime('%H:%M:%S') == check_time.strftime('%H:%M:00'):
        time.sleep(0.7) # مزامنة بسيطة لضمان وصول التيك الأخير
        try:
            ws = websocket.create_connection(WS_URL, timeout=5)
            ws.send(json.dumps({"ticks_history": SYMBOL, "count": 100, "end": "latest", "style": "ticks"}))
            res = json.loads(ws.recv())
            ws.close()
            
            prices = res['history']['prices']
            # مقارنة أول سعر في الدقيقة بآخر سعر
            is_win = (prices[-1] > prices[0]) if active_trade['direction'] == "CALL" else (prices[-1] < prices[0])
            
            if is_win:
                send_telegram_msg("✅ **WIN**")
            else:
                send_telegram_msg("❌ **LOSS**")
            
            # إنهاء الصفقة تماماً وعدم انتظار مضاعفة
            active_trade = None
        except:
            pass

def execute_signal_process():
    global active_trade
    if not analysis_lock.acquire(blocking=False):
        return
    try:
        ws = websocket.create_connection(WS_URL, timeout=5)
        ws.send(json.dumps({"ticks_history": SYMBOL, "count": 50, "end": "latest", "style": "candles", "granularity": 300}))
        res = json.loads(ws.recv())
        ws.close()
        
        df = pd.DataFrame(res['candles'])
        direction, accuracy = fast_indicator_logic(df)
        
        now = datetime.now(BEIRUT_TZ)
        entry_time = (now + timedelta(seconds=30)).replace(second=0, microsecond=0)
        
        active_trade = {"direction": direction, "entry_time": entry_time}
        
        msg = f"🚀 **SIGNAL ALERT**\n🎯 Action: *{direction}*\n📈 Strength: `{accuracy}%`\n🕐 Entry: {entry_time.strftime('%H:%M:00')}\n⏱ Duration: 1m"
        send_telegram_msg(msg)
    finally:
        analysis_lock.release()

def start_engine():
    global last_signal_time
    print("Bot is LIVE (No MTG Mode). Monitoring sec 30...")
    while True:
        now = datetime.now(BEIRUT_TZ)
        
        # إرسال الإشارة عند الثانية 30 من الدقيقة الأخيرة لشمعة الـ 5 دقائق
        if (now.minute + 1) % 5 == 0 and now.second == 30:
            current_slot = now.strftime('%H:%M')
            if last_signal_time != current_slot:
                last_signal_time = current_slot
                threading.Thread(target=execute_signal_process).start()

        # فحص النتيجة النهائية عند الثانية 00 من الدقيقة التالية
        if active_trade and now.second == 0:
            check_result_final()
            
        time.sleep(0.4)

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    threading.Thread(target=lambda: app.run(host='0.0.0.0', port=port), daemon=True).start()
    start_engine()
