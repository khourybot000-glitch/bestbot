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

# --- 1. إعداد السيرفر (Flask) لضمان عمل البوت 24/7 ---
app = Flask(__name__)

@app.route('/')
def home():
    # هذا الرابط هو ما ستضعه في UptimeRobot لضمان عدم توقف السيرفر
    return "EURJPY Trading Engine is Online & Monitoring 300 Ticks", 200

def run_flask():
    # Render يطلب بورت ديناميكي، الكود سيتعرف عليه تلقائياً
    port = int(os.environ.get("PORT", 8080))
    app.run(host='0.0.0.0', port=port)

# --- 2. الإعدادات الأساسية (Configuration) ---
TOKEN = '8511172742:AAFxZIj8N07FB-tFnJ_l3rv13loyRMmsRYU'
CHAT_ID = '-1003731752986'
BEIRUT_TZ = pytz.timezone('Asia/Beirut')
WS_URL = "wss://blue.derivws.com/websockets/v3?app_id=16929"
SYMBOL_NAME = "EUR/JPY"
SYMBOL_ID = "frxEURJPY"

# متغيرات الحالة للتحكم في الصفقات والوقت
active_trade = None
last_signal_time = ""

def send_telegram_msg(text):
    """إرسال التنبيهات والنتائج إلى تلغرام"""
    url = f"https://api.telegram.org/bot{TOKEN}/sendMessage"
    payload = {"chat_id": CHAT_ID, "text": text, "parse_mode": "Markdown"}
    try:
        requests.post(url, json=payload, timeout=10)
    except:
        pass

# --- 3. محرك التحليل الفني (20 مؤشر فني مفصل) ---
def create_20_tick_candles(prices):
    """تحويل 300 تيك إلى 15 شمعة قوية (كل شمعة تحتوي 20 تيك)"""
    candles = []
    # القفز بمقدار 20 تيك لبناء شمعة حقيقية
    for i in range(0, len(prices), 20):
        chunk = prices[i:i+20]
        if len(chunk) == 20:
            candles.append({
                'open': chunk[0],
                'high': np.max(chunk),
                'low': np.min(chunk),
                'close': chunk[-1]
            })
    return pd.DataFrame(candles)

def calculate_20_indicators(df):
    """تحليل 20 مؤشر فني على شموع الـ 20 تيك"""
    if len(df) < 14: return "NONE", 0
    c, h, l, o = df['close'].values, df['high'].values, df['low'].values, df['open'].values
    up, down = 0, 0

    # 1. RSI (14)
    diff = np.diff(c)
    gain = np.mean(np.where(diff > 0, diff, 0)[-14:])
    loss = np.mean(np.where(diff < 0, -diff, 0)[-14:])
    rsi = 100 - (100 / (1 + (gain / (loss + 1e-10))))
    if rsi < 50: up += 1
    else: down += 1

    # 2. SMA 10 | 3. EMA 5 | 4. Momentum | 5. Candle Color
    if c[-1] > np.mean(c[-10:]): up += 1
    else: down += 1
    ema5 = df['close'].ewm(span=5).mean().iloc[-1]
    if c[-1] > ema5: up += 1
    else: down += 1
    if c[-1] > c[-5]: up += 1
    else: down += 1
    if c[-1] > o[-1]: up += 1
    else: down += 1

    # 6. Bollinger Bands
    mid_bb = np.mean(c[-14:])
    if c[-1] < mid_bb: up += 1
    else: down += 1

    # 7-20. Micro-Trend Logic (14 مؤشر إضافي)
    for i in range(7, 21):
        if c[-1] > np.median(c[-min(i, len(c)):]): up += 1
        else: down += 1

    direction = "CALL" if up >= down else "PUT"
    strength = (max(up, down) / 20) * 100
    return direction, strength

# --- 4. نظام التحقق من النتيجة التاريخية (70 تيك) ---
def get_historical_result(open_ts, close_ts):
    """جلب 70 تيك جديدة تماماً عند انتهاء الوقت لاستخراج تيك 12:06 وتيك 12:07"""
    try:
        ws = websocket.create_connection(WS_URL, timeout=15)
        ws.send(json.dumps({"ticks_history": SYMBOL_ID, "count": 70, "end": "latest", "style": "ticks"}))
        res = json.loads(ws.recv())
        ws.close()
        
        prices, times = res['history']['prices'], res['history']['times']
        p_open, p_close = None, None
        
        # البحث عن التيك الذي سجل عند ثانية الافتتاح بالضبط وثانية الإغلاق
        for i in range(len(times)):
            if times[i] >= open_ts and p_open is None: p_open = prices[i]
            if times[i] >= close_ts and p_close is None: p_close = prices[i]
            
        return p_open, p_close
    except:
        return None, None

def check_trade_cycle():
    """مراقبة وقت انتهاء الصفقة وإرسال النتيجة فوراً"""
    global active_trade
    now = datetime.now(BEIRUT_TZ)
    target_close_time = active_trade['entry_time'] + timedelta(minutes=1)
    
    # ننتظر ثانيتين بعد الإغلاق لضمان وصول كافة التيكات لسيرفر الوسيط
    if now >= target_close_time + timedelta(seconds=2):
        open_ts = int(active_trade['entry_time'].timestamp())
        close_ts = int(target_close_time.timestamp())
        
        p_open, p_close = get_historical_result(open_ts, close_ts)
        
        if p_open and p_close:
            win = (p_close > p_open) if active_trade['direction'] == "CALL" else (p_close < p_open)
            status = "✅ WIN" if win else "❌ LOSS"
            if p_open == p_close: status = "⚖️ DRAW"
            
            msg = f"{status} | {SYMBOL_NAME}\n"
            msg += f"Dir: {active_trade['direction']} | Open: {p_open} | Close: {p_close}\n"
            msg += f"🔄 *Scanning for next opportunity...*"
            send_telegram_msg(msg)
            
            active_trade = None # فك القفل لبدء تحليل جديد

# --- 5. المحرك الأساسي (التحليل والإرسال) ---
def start_engine():
    global last_signal_time, active_trade
    print(f"Engine LIVE: Monitoring {SYMBOL_NAME} (300 Ticks / 20-Tick Candles)...")
    
    while True:
        now = datetime.now(BEIRUT_TZ)
        
        # التحليل عند الثانية 30 لضمان دخول الصفقة عند رأس الدقيقة 00
        if now.second == 30 and active_trade is None:
            current_min = now.strftime('%H:%M')
            if last_signal_time != current_min:
                last_signal_time = current_min
                try:
                    ws = websocket.create_connection(WS_URL, timeout=15)
                    ws.send(json.dumps({"ticks_history": SYMBOL_ID, "count": 300, "end": "latest", "style": "ticks"}))
                    res = json.loads(ws.recv())
                    ws.close()
                    
                    # تحويل الـ 300 تيك لـ 15 شمعة (كل واحدة 20 تيك)
                    df_candles = create_20_tick_candles(res['history']['prices'])
                    direction, accuracy = calculate_20_indicators(df_candles)
                    
                    if accuracy >= 80: # شروط قوة الإشارة
                        entry_dt = (now + timedelta(seconds=30)).replace(second=0, microsecond=0)
                        active_trade = {"direction": direction, "entry_time": entry_dt}
                        
                        msg = f"🚀 **SIGNAL**: {direction}\nStrength: {accuracy}%\nAnalysis: 300 Ticks / 20-Tick Candles\nEntry: {entry_dt.strftime('%H:%M:00')}"
                        send_telegram_msg(msg)
                except:
                    pass
        
        # مراجعة النتيجة إذا كانت هناك صفقة جارية
        if active_trade:
            check_trade_cycle()
            
        time.sleep(0.5)

if __name__ == "__main__":
    # تشغيل سيرفر Flask لفتح Port لـ UptimeRobot
    threading.Thread(target=run_flask, daemon=True).start()
    # تشغيل محرك البوت الأساسي
    start_engine()
