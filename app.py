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

# --- Flask Server (للحفاظ على استمرارية العمل) ---
app = Flask(__name__)
@app.route('/')
def health_check(): return "EURJPY Professional Engine Online", 200

# --- الإعدادات (Configuration) ---
TOKEN = '8511172742:AAFxZIj8N07FB-tFnJ_l3rv13loyRMmsRYU'
CHAT_ID = '-1003731752986'
BEIRUT_TZ = pytz.timezone('Asia/Beirut')
WS_URL = "wss://blue.derivws.com/websockets/v3?app_id=16929"
SYMBOL_NAME = "EUR/JPY"
SYMBOL_ID = "frxEURJPY"

# متغيرات التحكم (Control)
last_signal_time = ""
active_trade = None
analysis_lock = threading.Lock()

def send_telegram_msg(text):
    url = f"https://api.telegram.org/bot{TOKEN}/sendMessage"
    try:
        requests.post(url, json={"chat_id": CHAT_ID, "text": text, "parse_mode": "Markdown"}, timeout=5)
    except:
        pass

def create_20_tick_candles(prices):
    """تحويل 300 تيك إلى 15 شمعة (كل شمعة 20 تيك) لفلترة الضوضاء"""
    candles = []
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

def calculate_detailed_20_indicators(df):
    """محرك الـ 20 مؤشر فني بالتفصيل"""
    if len(df) < 14: return "NONE", 0
    c, h, l, o = df['close'].values, df['high'].values, df['low'].values, df['open'].values
    up, down = 0, 0

    # 1. RSI (14) - قوة النسبية
    diff = np.diff(c)
    gain = np.mean(np.where(diff > 0, diff, 0)[-14:])
    loss = np.mean(np.where(diff < 0, -diff, 0)[-14:])
    rsi = 100 - (100 / (1 + (gain / (loss + 1e-10))))
    if rsi < 50: up += 1
    else: down += 1

    # 2. SMA 10 (متوسط متحرك بسيط)
    if c[-1] > np.mean(c[-10:]): up += 1
    else: down += 1

    # 3. EMA 5 (متوسط متحرك أسي سريع)
    ema5 = df['close'].ewm(span=5).mean().iloc[-1]
    if c[-1] > ema5: up += 1
    else: down += 1

    # 4. Momentum (الزخم)
    if c[-1] > c[-5]: up += 1
    else: down += 1

    # 5. Candle Color (اتجاه آخر شمعة)
    if c[-1] > o[-1]: up += 1
    else: down += 1

    # 6. Bollinger Bands (نطاقات بولينجر)
    mid_bb = np.mean(c[-14:])
    if c[-1] < mid_bb: up += 1 # تحت المتوسط (احتمال صعود)
    else: down += 1

    # 7. ROC (معدل التغير)
    roc = ((c[-1] - c[-10]) / c[-10]) * 100
    if roc > 0: up += 1
    else: down += 1

    # 8. Williams %R
    w_r = (np.max(h[-14:]) - c[-1]) / (np.max(h[-14:]) - np.min(l[-14:])) * -100
    if w_r < -50: up += 1
    else: down += 1

    # 9. CCI (مؤشر قناة السلع)
    tp = (h + l + c) / 3
    sma_tp = np.mean(tp[-14:])
    mad = np.mean(np.abs(tp[-14:] - sma_tp))
    cci = (tp[-1] - sma_tp) / (0.015 * mad)
    if cci > 0: up += 1
    else: down += 1

    # 10. Stochastic Oscillator (%K)
    stoch_k = ((c[-1] - np.min(l[-14:])) / (np.max(h[-14:]) - np.min(l[-14:]))) * 100
    if stoch_k < 50: up += 1
    else: down += 1

    # 11-20. Micro-Trend Analysis (تحليل الاتجاه المصغر عبر فترات مختلفة)
    for i in range(11, 21):
        if c[-1] > np.median(c[-min(i, len(c)):]): up += 1
        else: down += 1

    direction = "CALL" if up >= down else "PUT"
    strength = (max(up, down) / 20) * 100
    return direction, strength

def get_exact_historical_result(open_ts, close_ts):
    """جلب 70 تيك تاريخية عند انتهاء الوقت للمقارنة الدقيقة"""
    try:
        ws = websocket.create_connection(WS_URL, timeout=10)
        ws.send(json.dumps({"ticks_history": SYMBOL_ID, "count": 70, "end": "latest", "style": "ticks"}))
        res = json.loads(ws.recv())
        ws.close()
        prices, times = res['history']['prices'], res['history']['times']
        p_open, p_close = None, None
        for i in range(len(times)):
            if times[i] >= open_ts and p_open is None: p_open = prices[i]
            if times[i] >= close_ts and p_close is None: p_close = prices[i]
        return p_open, p_close
    except: return None, None

def check_result_cycle():
    global active_trade
    now = datetime.now(BEIRUT_TZ)
    close_time_target = active_trade['entry_time'] + timedelta(minutes=1)
    
    # عند الساعة 12:07:02 (ثانيتين بعد الإغلاق)
    if now >= close_time_target + timedelta(seconds=2):
        o_ts, c_ts = int(active_trade['entry_time'].timestamp()), int(close_time_target.timestamp())
        p_open, p_close = get_exact_historical_prices(o_ts, c_ts)
        
        if p_open is not None and p_close is not None:
            if active_trade['direction'] == "CALL": win = p_close > p_open
            else: win = p_close < p_open
            
            status = "✅ WIN" if win else "❌ LOSS"
            if p_open == p_close: status = "⚖️ DRAW"
            
            msg = f"{status} | {SYMBOL_NAME}\n"
            msg += f"Dir: {active_trade['direction']} | Open: {p_open} | Close: {p_close}\n"
            msg += f"🔄 *Scanning for next setup...*"
            send_telegram_msg(msg)
            active_trade = None

def start_engine():
    global last_signal_time, active_trade
    print(f"Professional Engine LIVE | 300 Ticks | 20 Indicators")
    while True:
        now = datetime.now(BEIRUT_TZ)
        if now.second == 30 and active_trade is None:
            current_min = now.strftime('%H:%M')
            if last_signal_time != current_min:
                last_signal_time = current_min
                with analysis_lock:
                    try:
                        ws = websocket.create_connection(WS_URL, timeout=10)
                        ws.send(json.dumps({"ticks_history": SYMBOL_ID, "count": 300, "end": "latest", "style": "ticks"}))
                        res = json.loads(ws.recv())
                        ws.close()
                        df_candles = create_20_tick_candles(res['history']['prices'])
                        direction, accuracy = calculate_detailed_20_indicators(df_candles)
                        
                        if accuracy >= 80: # رفع الدقة لـ 80%
                            entry_dt = (now + timedelta(seconds=30)).replace(second=0, microsecond=0)
                            active_trade = {"direction": direction, "entry_time": entry_dt}
                            msg = f"🚀 **SIGNAL**: {direction}\nStrength: {accuracy}%\nEntry: {entry_dt.strftime('%H:%M:00')}"
                            send_telegram_msg(msg)
                    except: pass
        if active_trade: check_result_cycle()
        time.sleep(0.5)

if __name__ == "__main__":
    start_engine()
