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

# --- Flask Server (لإبقاء البوت يعمل على السيرفر) ---
app = Flask(__name__)
@app.route('/')
def health_check(): return "Bot Active: Single-Trade & Silent MTG", 200

# --- الإعدادات الأساسية ---
TOKEN = '8511172742:AAFxZIj8N07FB-tFnJ_l3rv13loyRMmsRYU'
CHAT_ID = '-1003731752986'
BEIRUT_TZ = pytz.timezone('Asia/Beirut')
WS_URL = "wss://blue.derivws.com/websockets/v3?app_id=16929"

SYMBOLS = ["frxEURGBP", "frxEURUSD", "frxEURJPY"]
RSI_PERIOD = 14
RSI_OB = 70
RSI_OS = 30

# متغيرات الحالة للتحكم في القفل والصفقات
is_bot_busy = False
active_trade_data = {}

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

def analyze_all_pairs():
    global is_bot_busy, active_trade_data
    if is_bot_busy: return

    for symbol in SYMBOLS:
        try:
            ws = websocket.create_connection(WS_URL)
            ws.send(json.dumps({"ticks_history": symbol, "count": 1000, "end": "latest", "style": "ticks"}))
            res = json.loads(ws.recv()); ws.close()
            
            if 'history' not in res: continue
            df = pd.DataFrame({
                'price': res['history']['prices'],
                'time': [datetime.fromtimestamp(t, tz=pytz.utc).astimezone(BEIRUT_TZ) for t in res['history']['times']]
            })
            df['rsi'] = calculate_rsi(df['price'].values, RSI_PERIOD)

            t_now = datetime.now(BEIRUT_TZ).replace(second=0, microsecond=0)
            
            # تحليل الشمعة السابقة (مغلقة) والشمعة الحالية (تأكيد)
            m1_data = df[(df['time'] >= t_now - timedelta(minutes=1)) & (df['time'] < t_now)]
            m0_data = df[df['time'] >= t_now]

            if m1_data.empty or m0_data.empty: continue

            m1_open, m1_close = m1_data['price'].iloc[0], m1_data['price'].iloc[-1]
            m1_rsi = m1_data['rsi'].iloc[-1]
            m0_open, m0_now = m0_data['price'].iloc[0], m0_data['price'].iloc[-1]

            trade_type = None
            # شمعة حمراء في تشبع بيعي + شمعة حالية خضراء => CALL
            if m1_rsi <= RSI_OS and m1_close < m1_open and m0_now > m0_open:
                trade_type = "CALL (BUY)"
            # شمعة خضراء في تشبع شرائي + شمعة حالية حمراء => PUT
            elif m1_rsi >= RSI_OB and m1_close > m1_open and m0_now < m0_open:
                trade_type = "PUT (SELL)"

            if trade_type:
                is_bot_busy = True # تفعيل القفل الشامل
                entry_time = t_now + timedelta(minutes=1)
                active_trade_data = {
                    "symbol": symbol,
                    "entry_time": entry_time,
                    "exit_time": entry_time + timedelta(minutes=5),
                    "direction": trade_type,
                    "is_martingale": False
                }
                send_telegram_msg(f"🔔 **SIGNAL: {symbol.replace('frx','')}**\n🎯 Action: *{trade_type}*\n🕐 Entry: {entry_time.strftime('%H:%M:00')}\n⏱ Duration: 5m")
                break # وجدنا فرصة، توقف عن فحص البقية
        except Exception as e: print(f"Analysis Error: {e}")

def check_single_result():
    global is_bot_busy, active_trade_data
    if not is_bot_busy: return

    now = datetime.now(BEIRUT_TZ)
    if now < active_trade_data['exit_time']: return

    symbol = active_trade_data['symbol']
    try:
        ws = websocket.create_connection(WS_URL)
        ws.send(json.dumps({"ticks_history": symbol, "count": 1200, "end": "latest", "style": "ticks"}))
        res = json.loads(ws.recv()); ws.close()
        
        df = pd.DataFrame({
            'price': res['history']['prices'],
            'time': [datetime.fromtimestamp(t, tz=pytz.utc).astimezone(BEIRUT_TZ) for t in res['history']['times']]
        })
        
        # استخراج بيانات التيكات للفترة المحددة لتمثيل الشمعة
        trade_period = df[(df['time'] >= active_trade_data['entry_time']) & (df['time'] < active_trade_data['exit_time'])]
        
        if trade_period.empty: return

        open_tick = trade_period['price'].iloc[0]
        close_tick = trade_period['price'].iloc[-1]
        
        is_win = (close_tick > open_tick) if "CALL" in active_trade_data['direction'] else (close_tick < open_tick)
        
        if is_win:
            msg = "✅ **WIN**" if not active_trade_data['is_martingale'] else "✅ **MTG WIN**"
            send_telegram_msg(f"{msg} ({symbol.replace('frx','')})")
            is_bot_busy = False # فك القفل للبحث عن فرص جديدة
        else:
            if not active_trade_data['is_martingale']:
                # صمت: لا رسالة خسارة، فقط تمديد الوقت للمضاعفة
                new_entry = active_trade_data['exit_time']
                active_trade_data.update({
                    "entry_time": new_entry,
                    "exit_time": new_entry + timedelta(minutes=5),
                    "is_martingale": True
                })
            else:
                # خسارة المضاعفة النهائية
                send_telegram_msg(f"❌ **MTG LOSS** ({symbol.replace('frx','')})")
                is_bot_busy = False # فك القفل للبحث عن فرص جديدة
                
    except Exception as e: print(f"Check Result Error: {e}")

def start_engine():
    while True:
        now = datetime.now(BEIRUT_TZ)
        
        # التحليل فقط عند الثانية 50 وإذا كان البوت حراً
        if now.second == 50 and not is_bot_busy:
            threading.Thread(target=analyze_all_pairs).start()
            time.sleep(1)
            
        # فحص النتيجة عند انتهاء الوقت (الثانية 02 للتأكيد)
        if is_bot_busy and now >= active_trade_data['exit_time'] and now.second == 2:
            check_single_result()
            time.sleep(1)
            
        time.sleep(0.5)

if __name__ == "__main__":
    # تشغيل Flask في الخلفية لإبقاء الرابط فعالاً
    threading.Thread(target=lambda: app.run(host='0.0.0.0', port=int(os.environ.get("PORT", 10000))), daemon=True).start()
    start_engine()
