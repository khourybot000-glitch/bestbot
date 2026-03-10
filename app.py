import os, json, time, threading, websocket
from flask import Flask, jsonify, render_template_string, request
from datetime import datetime, timedelta

app = Flask(__name__)

bot_config = {
    "isRunning": False, "displayMsg": "SCANNING", "direction": "",
    "strength": 0, "pair_id": "frxEURUSD", "timestamp": 0,
    "entryTime": "", "isSignal": False, "logs": []
}

ASSETS = {"frxEURUSD": "EUR/USD", "frxEURJPY": "EUR/JPY", "frxEURGBP": "EUR/GBP"}

def add_log(msg):
    bot_config["logs"].append(f"[{time.strftime('%H:%M:%S')}] {msg}")
    if len(bot_config["logs"]) > 5: bot_config["logs"].pop(0)

# --- الأدوات الحسابية (المؤشرات) ---
def get_indicators(ticks):
    close_prices = ticks[-100:]
    
    # 1. حساب SMA (المتوسطات المتحركة)
    sma_20 = sum(close_prices[-20:]) / 20
    sma_50 = sum(close_prices[-50:]) / 50
    
    # 2. حساب RSI (14)
    deltas = [close_prices[i+1] - close_prices[i] for i in range(len(close_prices)-1)]
    gains = sum([d for d in deltas[-14:] if d > 0]) / 14
    losses = sum([-d for d in deltas[-14:] if d < 0]) / 14
    rs = gains / losses if losses != 0 else 100
    rsi = 100 - (100 / (1 + rs))
    
    # 3. بولنجر باند (مبسط)
    std_dev = (sum([(p - sma_20)**2 for p in close_prices[-20:]]) / 20)**0.5
    upper_band = sma_20 + (2 * std_dev)
    lower_band = sma_20 - (2 * std_dev)
    
    return {"rsi": rsi, "sma20": sma_20, "sma50": sma_50, "upper": upper_band, "lower": lower_band}

def get_zones(ticks):
    # تقسيم التيكات لشموع (كل 60 تيك شمعة)
    candles = []
    for i in range(0, len(ticks)-60, 60):
        candles.append({"o": ticks[i], "c": ticks[i+59], "h": max(ticks[i:i+60]), "l": min(ticks[i:i+60])})
    
    sup = max([c['l'] for c in candles[-10:]]) # دعم قريب
    res = min([c['h'] for c in candles[-10:]]) # مقاومة قريبة
    return sup, res

# --- المحرك التحليلي فائق الدقة ---
def perform_ultra_analysis(ticks, asset_id):
    global bot_config
    curr = ticks[-1]
    ind = get_indicators(ticks)
    sup, res = get_zones(ticks)
    
    score = 0
    reasons = []

    # منطق الصعود (CALL)
    if curr > ind["sma20"] and ind["sma20"] > ind["sma50"]: score += 30; reasons.append("Trend Up")
    if 35 < ind["rsi"] < 65: score += 20; reasons.append("RSI Stable")
    if curr < ind["upper"] * 0.998: score += 25; reasons.append("Space to Grow")
    if abs(curr - res) > abs(res * 0.001): score += 25; reasons.append("No Resistance Near")

    # منطق الهبوط (PUT)
    score_put = 0
    if curr < ind["sma20"] and ind["sma20"] < ind["sma50"]: score_put += 30
    if 35 < ind["rsi"] < 65: score_put += 20
    if curr > ind["lower"] * 1.002: score_put += 25
    if abs(curr - sup) > abs(sup * 0.001): score_put += 25

    final_score = max(score, score_put)
    direction = "CALL 🟢" if score > score_put else "PUT 🔴"

    # لا تعطي إشارة إلا إذا تجاوزت القوة 95%
    if final_score >= 95:
        bot_config.update({
            "isSignal": True, "direction": direction, "strength": final_score,
            "pair_name": ASSETS[asset_id], "timestamp": time.time(),
            "entryTime": (datetime.now() + timedelta(minutes=1)).strftime("%H:%M:00")
        })
        add_log(f"STRONG {direction} FOUND! Score: {final_score}")
    else:
        bot_config.update({"isSignal": False, "timestamp": time.time()})
        add_log(f"Wait.. Weak Opportunity ({final_score}%)")

# --- العمل المستمر كل دقيقة ---
def continuous_worker():
    while True:
        now = datetime.now()
        # التحليل في الثانية 55 من كل دقيقة ليكون جاهزاً للدقيقة التالية
        if bot_config["isRunning"] and now.second == 55:
            try:
                ws = websocket.create_connection("wss://blue.derivws.com/websockets/v3?app_id=16929")
                ws.send(json.dumps({"ticks_history": bot_config["pair_id"], "count": 1000, "style": "ticks"}))
                res = json.loads(ws.recv())
                if "history" in res:
                    perform_ultra_analysis(res["history"]["prices"], bot_config["pair_id"])
                ws.close()
                time.sleep(2)
            except: pass
        time.sleep(0.5)

# (بقية أكواد Flask و UI تبقى كما هي مع تحديث المسميات)
