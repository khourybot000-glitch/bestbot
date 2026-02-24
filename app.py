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
def health_check(): return "AI Analysis Bot Active", 200

# --- Configuration ---
TOKEN = '8511172742:AAFxZIj8N07FB-tFnJ_l3rv13loyRMmsRYU'
CHAT_ID = '-1003731752986'
BEIRUT_TZ = pytz.timezone('Asia/Beirut')
WS_URL = "wss://blue.derivws.com/websockets/v3?app_id=16929"
SYMBOL = "frxEURGBP"

# المتغيرات العالمية
is_bot_busy = False
active_trade = {}
last_analyzed_min = -1
session_announced = False 
end_announced = False 

def send_telegram_msg(text):
    url = f"https://api.telegram.org/bot{TOKEN}/sendMessage"
    try: requests.post(url, json={"chat_id": CHAT_ID, "text": text, "parse_mode": "Markdown"}, timeout=10)
    except: pass

def get_comprehensive_data(symbol):
    try:
        ws = websocket.create_connection(WS_URL, timeout=15)
        ws.send(json.dumps({
            "ticks_history": symbol, 
            "count": 500, 
            "end": "latest", 
            "style": "candles", 
            "granularity": 300 
        }))
        res = json.loads(ws.recv())
        ws.close()
        if 'candles' not in res: return None
        return pd.DataFrame(res['candles'])
    except: return None

def calculate_strength(df):
    # حساب المؤشرات الفنية باستخدام pandas_ta
    rsi = ta.rsi(df['close'], length=14).iloc[-1]
    macd = ta.macd(df['close']).iloc[-1]
    stoch = ta.stoch(df['high'], df['low'], df['close']).iloc[-1]
    wpr = ta.willr(df['high'], df['low'], df['close']).iloc[-1]
    cci = ta.cci(df['high'], df['low'], df['close']).iloc[-1]
    ema8 = ta.ema(df['close'], length=8).iloc[-1]
    ema21 = ta.ema(df['close'], length=21).iloc[-1]
    adx = ta.adx(df['high'], df['low'], df['close']).iloc[-1]
    bb = ta.bbands(df['close'], length=20).iloc[-1]
    mfi = ta.mfi(df['high'], df['low'], df['close'], df['open'], length=14).iloc[-1]
    
    buy_score = 0
    sell_score = 0
    total_metrics = 10

    # شروط الشراء
    if rsi < 40: buy_score += 1
    if macd['MACD_12_26_9'] > macd['MACDs_12_26_9']: buy_score += 1
    if df['close'].iloc[-1] > ema8: buy_score += 1
    if stoch['STOCHk_14_3_3'] < 20: buy_score += 1
    if adx['ADX_14'] > 25 and adx['DMP_14'] > adx['DMN_14']: buy_score += 1
    if df['close'].iloc[-1] < bb['BBL_20_2.0']: buy_score += 1
    if cci < -100: buy_score += 1
    if wpr < -80: buy_score += 1
    if mfi < 30: buy_score += 1
    if ema8 > ema21: buy_score += 1

    # شروط البيع
    if rsi > 60: sell_score += 1
    if macd['MACD_12_26_9'] < macd['MACDs_12_26_9']: sell_score += 1
    if df['close'].iloc[-1] < ema8: sell_score += 1
    if stoch['STOCHk_14_3_3'] > 80: sell_score += 1
    if adx['ADX_14'] > 25 and adx['DMN_14'] > adx['DMP_14']: sell_score += 1
    if df['close'].iloc[-1] > bb['BBU_20_2.0']: sell_score += 1
    if cci > 100: sell_score += 1
    if wpr > -20: sell_score += 1
    if mfi > 70: sell_score += 1
    if ema8 < ema21: sell_score += 1

    if buy_score >= sell_score:
        return "CALL", (buy_score / total_metrics) * 100
    else:
        return "PUT", (sell_score / total_metrics) * 100

def run_analysis():
    global is_bot_busy, active_trade # إضافة global هنا حلت المشكلة
    df = get_comprehensive_data(SYMBOL)
    if df is None: return

    direction, accuracy = calculate_strength(df)
    now = datetime.now(BEIRUT_TZ)
    entry_time = (now + timedelta(minutes=1)).replace(second=0, microsecond=0)
    
    is_bot_busy = True
    active_trade = {
        "symbol": SYMBOL,
        "direction": direction,
        "entry_time": entry_time,
        "exit_time": entry_time + timedelta(minutes=5)
    }

    msg = f"📊 **EURGBP 5M ANALYSIS**\n"
    msg += f"🎯 Recommendation: *{direction}*\n"
    msg += f"📈 Confidence: `{accuracy:.1f}%`\n"
    msg += f"🕐 Entry At: {entry_time.strftime('%H:%M:00')}\n"
    msg += f"⏱ Duration: 5m"
    send_telegram_msg(msg)

def start_engine():
    global last_analyzed_min, session_announced, end_announced, is_bot_busy
    print("AI Analysis Engine Started...")
    while True:
        now = datetime.now(BEIRUT_TZ)
        weekday = now.weekday()
        hour = now.hour

        # إشعارات الجلسة والتحكم بالوقت
        if weekday < 5:
            if hour == 9 and not session_announced:
                send_telegram_msg("🟢 **session started**")
                session_announced, end_announced = True, False
            if hour == 21 and not end_announced:
                send_telegram_msg("🔴 **session ended**")
                end_announced, session_announced = True, False
            
            # تنفيذ التحليل بين 9 صباحاً و 9 مساءً
            if 9 <= hour < 21:
                if (now.minute + 1) % 5 == 0 and now.second == 30:
                    if last_analyzed_min != now.minute:
                        threading.Thread(target=run_analysis).start()
                        last_analyzed_min = now.minute
                
                # فحص انتهاء الصفقة لفتح القفل
                if is_bot_busy and now >= active_trade.get('exit_time', now):
                    is_bot_busy = False
        else:
            session_announced, end_announced = False, False

        time.sleep(1)

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    threading.Thread(target=lambda: app.run(host='0.0.0.0', port=port), daemon=True).start()
    start_engine()
