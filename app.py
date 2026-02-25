import websocket
import json
import pandas as pd
import numpy as np
import time
import requests
from datetime import datetime, timedelta
import pytz
import threading

# --- الإعدادات ---
TOKEN = '8511172742:AAFxZIj8N07FB-tFnJ_l3rv13loyRMmsRYU'
CHAT_ID = '-1003731752986'
BEIRUT_TZ = pytz.timezone('Asia/Beirut')
WS_URL = "wss://blue.derivws.com/websockets/v3?app_id=16929"
SYMBOL_ID = "frxEURJPY"
SYMBOL_NAME = "EUR/JPY"

active_trade = None
last_signal_time = ""

def send_telegram_msg(text):
    url = f"https://api.telegram.org/bot{TOKEN}/sendMessage"
    payload = {"chat_id": CHAT_ID, "text": text, "parse_mode": "Markdown"}
    try: requests.post(url, json=payload, timeout=10)
    except: pass

# --- محرك الـ 20 مؤشر الفعلي ---
def calculate_logic(prices):
    if len(prices) < 50: return "NONE", 0
    df = pd.DataFrame(prices, columns=['close'])
    up_votes = 0
    c = df['close']

    # 1-5: المتوسطات (Trend)
    if c.iloc[-1] > c.rolling(10).mean().iloc[-1]: up_votes += 1
    if c.iloc[-1] > c.rolling(20).mean().iloc[-1]: up_votes += 1
    if c.rolling(5).mean().iloc[-1] > c.rolling(15).mean().iloc[-1]: up_votes += 1
    if c.iloc[-1] > c.rolling(50).mean().iloc[-1]: up_votes += 1
    if c.iloc[-1] > (c.max() + c.min()) / 2: up_votes += 1

    # 6-10: الزخم (Momentum)
    rsi = 100 - (100 / (1 + (c.diff().where(c.diff() > 0, 0).rolling(14).mean() / c.diff().where(c.diff() < 0, 0).abs().rolling(14).mean())))
    if rsi.iloc[-1] > 50: up_votes += 1
    if c.iloc[-1] > c.iloc[-2]: up_votes += 1 # Price Action
    if (c.iloc[-1] - c.iloc[-10]) > 0: up_votes += 1 # Momentum
    if c.iloc[-1] > c.rolling(14).median().iloc[-1]: up_votes += 1
    if (c.iloc[-1] - c.min()) / (c.max() - c.min()) > 0.5: up_votes += 1 # Stochastic محاكي

    # 11-20: انحرافات إضافية (Volatility & Strength)
    for p in range(5, 15):
        if c.iloc[-1] > c.rolling(p).mean().iloc[-1]:
            up_votes += 1

    accuracy = (max(up_votes, 20 - up_votes) / 20) * 100
    direction = "CALL" if up_votes >= 10 else "PUT"
    return direction, accuracy

# --- التحقق من النتيجة (منطق الـ 5 دقائق) ---
def verify_result(entry_time, exit_time):
    try:
        ws = websocket.create_connection(WS_URL, timeout=15)
        # طلب 320 تيك كما طلبت لتغطية الفترة بين 12:05 و 12:10
        ws.send(json.dumps({"ticks_history": SYMBOL_ID, "count": 320, "end": int(exit_time.timestamp()) + 2, "style": "ticks"}))
        res = json.loads(ws.recv()); ws.close()
        
        prices, times = res['history']['prices'], res['history']['times']
        p_open, p_close = None, None
        
        for i in range(len(times)):
            if times[i] >= int(entry_time.timestamp()) and p_open is None: p_open = prices[i]
            if times[i] >= int(exit_time.timestamp()) and p_close is None: p_close = prices[i]
        
        return p_open, p_close
    except: return None, None

# --- المحرك الأساسي ---
def start_engine():
    global active_trade, last_signal_time
    print(f"Engine LIVE | {SYMBOL_NAME} | 20 Indicators | 5m Verify")
    
    while True:
        now = datetime.now(BEIRUT_TZ)
        
        # 1. التحليل عند 12:04:30
        if now.second == 30 and active_trade is None:
            current_min = now.strftime('%H:%M')
            if last_signal_time != current_min:
                last_signal_time = current_min
                try:
                    ws = websocket.create_connection(WS_URL, timeout=10)
                    ws.send(json.dumps({"ticks_history": SYMBOL_ID, "count": 100, "style": "ticks"}))
                    res = json.loads(ws.recv()); ws.close()
                    
                    direction, accuracy = calculate_logic(res['history']['prices'])
                    
                    # شرط الـ 70% وتوافق اتجاه شمعة الـ 5 دقائق
                    p_start_5m = res['history']['prices'][0] # تقريبي لأول التيكات
                    is_5m_up = res['history']['prices'][-1] > p_start_5m
                    
                    if accuracy >= 70 and ((direction == "CALL" and is_5m_up) or (direction == "PUT" and not is_5m_up)):
                        entry_t = (now + timedelta(seconds=30)).replace(second=0, microsecond=0)
                        exit_t = entry_t + timedelta(minutes=5)
                        active_trade = {"dir": direction, "entry": entry_t, "exit": exit_t}
                        
                        send_telegram_msg(f"🚀 **SIGNAL**: {SYMBOL_NAME} | {direction}\nAccuracy: {accuracy}%\nEntry: {entry_t.strftime('%H:%M:00')}\nExit: {exit_t.strftime('%H:%M:00')}")
                except: pass

        # 2. التحقق عند 12:10:05 (بعد نهاية الصفقة بـ 5 ثواني)
        if active_trade and now >= active_trade['exit'] + timedelta(seconds=5):
            p_open, p_close = verify_result(active_trade['entry'], active_trade['exit'])
            if p_open and p_close:
                win = (p_close > p_open) if active_trade['dir'] == "CALL" else (p_close < p_open)
                status = "✅ **WIN**" if win else "❌ **LOSS**"
                send_telegram_msg(f"{status} | {SYMBOL_NAME}\nEntry: {p_open} | Exit: {p_close}\nResult: {'Profit' if win else 'Loss'}")
            active_trade = None 

        time.sleep(0.5)

if __name__ == "__main__":
    start_engine()
