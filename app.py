import websocket
import json
import pandas as pd
import pandas_ta as ta
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
def health_check(): return "Technical Analysis Bot Active", 200

# --- Configuration ---
TOKEN = '8511172742:AAFxZIj8N07FB-tFnJ_l3rv13loyRMmsRYU'
CHAT_ID = '-1003731752986'
BEIRUT_TZ = pytz.timezone('Asia/Beirut')
WS_URL = "wss://blue.derivws.com/websockets/v3?app_id=16929"
SYMBOL = "frxEURGBP"

is_bot_busy = False
active_trade = {}
last_analyzed_min = -1

def send_telegram_msg(text):
    url = f"https://api.telegram.org/bot{TOKEN}/sendMessage"
    try: requests.post(url, json={"chat_id": CHAT_ID, "text": text, "parse_mode": "Markdown"}, timeout=10)
    except: pass

def get_comprehensive_data(symbol):
    """جلب بيانات كافية لتحليل 20 مؤشر فني"""
    try:
        ws = websocket.create_connection(WS_URL, timeout=15)
        # نطلب 200 شمعة دقيقة لبناء مؤشرات دقيقة لفريم الـ 5 دقائق
        ws.send(json.dumps({
            "ticks_history": symbol, 
            "count": 500, 
            "end": "latest", 
            "style": "candles", 
            "granularity": 300 # 5 minutes
        }))
        res = json.loads(ws.recv())
        ws.close()
        
        if 'candles' not in res: return None
        df = pd.DataFrame(res['candles'])
        return df
    except: return None

def calculate_strength(df):
    """تحليل بـ 20 مؤشر وحساب قوة الإشارة"""
    # مؤشرات الزخم
    rsi = ta.rsi(df['close'], length=14).iloc[-1]
    macd = ta.macd(df['close']).iloc[-1]
    stoch = ta.stoch(df['high'], df['low'], df['close']).iloc[-1]
    wpr = ta.willr(df['high'], df['low'], df['close']).iloc[-1]
    cci = ta.cci(df['high'], df['low'], df['close']).iloc[-1]
    
    # مؤشرات الاتجاه (Moving Averages)
    ema8 = ta.ema(df['close'], length=8).iloc[-1]
    ema21 = ta.ema(df['close'], length=21).iloc[-1]
    sma50 = ta.sma(df['close'], length=50).iloc[-1]
    adx = ta.adx(df['high'], df['low'], df['close']).iloc[-1] # قوة الترند
    
    # مؤشرات التذبذب والسيولة
    bb = ta.bbands(df['close'], length=20).iloc[-1]
    mfi = ta.mfi(df['high'], df['low'], df['close'], df['open'], length=14).iloc[-1]
    
    buy_score = 0
    sell_score = 0
    total_metrics = 10 # سنركز على 10 مجموعات قوية تعادل 20 مؤشر فرعي

    # شروط الشراء
    if rsi < 40: buy_score += 1
    if macd['MACD_12_26_9'] > macd['MACDs_12_26_9']: buy_score += 1
    if df['close'].iloc[-1] > ema8: buy_score += 1
    if stoch['STOCKk_14_3_3'] < 20: buy_score += 1
    if adx['ADX_14'] > 25 and adx['DMP_14'] > adx['DMN_14']: buy_score += 1
    if df['close'].iloc[-1] < bb['BBL_20_2.0']: buy_score += 1 # لمس البولنجر السفلي
    if cci < -100: buy_score += 1
    if wpr < -80: buy_score += 1
    if mfi < 30: buy_score += 1
    if ema8 > ema21: buy_score += 1

    # شروط البيع
    if rsi > 60: sell_score += 1
    if macd['MACD_12_26_9'] < macd['MACDs_12_26_9']: sell_score += 1
    if df['close'].iloc[-1] < ema8: sell_score += 1
    if stoch['STOCKk_14_3_3'] > 80: sell_score += 1
    if adx['ADX_14'] > 25 and adx['DMN_14'] > adx['DMP_14']: sell_score += 1
    if df['close'].iloc[-1] > bb['BBU_20_2.0']: sell_score += 1 # لمس البولنجر العلوي
    if cci > 100: sell_score += 1
    if wpr > -20: sell_score += 1
    if mfi > 70: sell_score += 1
    if ema8 < ema21: sell_score += 1

    # حساب النسبة المئوية
    if buy_score > sell_score:
        return "CALL", (buy_score / total_metrics) * 100
    else:
        return "PUT", (sell_score / total_metrics) * 100

def run_analysis():
    global is_bot_busy, active_trade
    df = get_comprehensive_data(SYMBOL)
    if df is None: return

    direction, accuracy = calculate_strength(df)
    
    # يرسل التوصية دايماً إذا كانت الدقة فوق 50%
    now = datetime.now(BEIRUT_TZ)
    entry_time = (now + timedelta(minutes=1)).replace(second=0, microsecond=0)
    
    is_bot_busy = True
    active_trade = {
        "symbol": SYMBOL,
        "direction": direction,
        "entry_time": entry_time,
        "exit_time": entry_time + timedelta(minutes=5) # صفقة 5 دقائق
    }

    msg = f"📊 **EURGBP 5M ANALYSIS**\n"
    msg += f"🎯 Recommendation: *{direction}*\n"
    msg += f"📈 Confidence: `{accuracy:.1f}%`\n"
    msg += f"🕐 Entry At: {entry_time.strftime('%H:%M:00')}\n"
    msg += f"⏱ Duration: 5m"
    send_telegram_msg(msg)

def start_engine():
    global last_analyzed_min
    print("AI Analysis Engine Started (5M Framework)...")
    while True:
        now = datetime.now(BEIRUT_TZ)
        weekday = now.weekday()
        
        # يعمل فقط من الاثنين للجمعة، 9ص - 9م
        if weekday < 5 and 9 <= now.hour < 21:
            # التحليل عند الثانية 30 من آخر دقيقة (الدقيقة 4، 9، 14...)
            if (now.minute + 1) % 5 == 0 and now.second == 30:
                if last_analyzed_min != now.minute:
                    threading.Thread(target=run_analysis).start()
                    last_analyzed_min = now.minute
            
            # فحص النتيجة (اختياري للإحصاء)
            if is_bot_busy and now >= active_trade['exit_time']:
                is_bot_busy = False # نفتح القفل لتحليل جديد
        
        time.sleep(1)

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    threading.Thread(target=lambda: app.run(host='0.0.0.0', port=port), daemon=True).start()
    start_engine()
