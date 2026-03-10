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

def get_indicators(ticks):
    try:
        if len(ticks) < 60: return None
        close_prices = ticks[-100:]
        sma_20 = sum(close_prices[-20:]) / 20
        sma_50 = sum(close_prices[-50:]) / 50
        
        deltas = [close_prices[i+1] - close_prices[i] for i in range(len(close_prices)-1)]
        gains = sum([d for d in deltas[-14:] if d > 0]) / 14
        losses = sum([-d for d in deltas[-14:] if d < 0]) / 14
        rs = gains / losses if losses != 0 else 100
        rsi = 100 - (100 / (1 + rs))
        
        return {"rsi": rsi, "sma20": sma_20, "sma50": sma_50}
    except: return None

def get_zones(ticks):
    try:
        candles = []
        for i in range(0, len(ticks)-60, 60):
            candles.append({"l": min(ticks[i:i+60]), "h": max(ticks[i:i+60])})
        sup = max([c['l'] for c in candles[-10:]])
        res = min([c['h'] for c in candles[-10:]])
        return sup, res
    except: return ticks[0], ticks[-1]

def perform_ultra_analysis(ticks, asset_id):
    global bot_config
    curr = ticks[-1]
    ind = get_indicators(ticks)
    sup, res = get_zones(ticks)
    
    if not ind: return

    score_call = 0
    if curr > ind["sma20"] and ind["sma20"] > ind["sma50"]: score_call += 40
    if 40 < ind["rsi"] < 60: score_call += 30
    if abs(curr - res) > (res * 0.0005): score_call += 30

    score_put = 0
    if curr < ind["sma20"] and ind["sma20"] < ind["sma50"]: score_put += 40
    if 40 < ind["rsi"] < 60: score_put += 30
    if abs(curr - sup) > (sup * 0.0005): score_put += 30

    final_score = max(score_call, score_put)
    if final_score >= 95:
        direction = "CALL 🟢" if score_call > score_put else "PUT 🔴"
        bot_config.update({
            "isSignal": True, "direction": direction, "strength": final_score,
            "pair_name": ASSETS[asset_id], "timestamp": time.time(),
            "entryTime": (datetime.now() + timedelta(minutes=1)).strftime("%H:%M:00")
        })
        add_log(f"SIGNAL: {direction} ({final_score}%)")
    else:
        bot_config.update({"isSignal": False, "timestamp": time.time()})

def continuous_worker():
    while True:
        try:
            now = datetime.now()
            if bot_config["isRunning"] and now.second == 55:
                ws = websocket.create_connection("wss://blue.derivws.com/websockets/v3?app_id=16929", timeout=10)
                ws.send(json.dumps({"ticks_history": bot_config["pair_id"], "count": 1000, "style": "ticks"}))
                res = json.loads(ws.recv())
                if "history" in res:
                    perform_ultra_analysis(res["history"]["prices"], bot_config["pair_id"])
                ws.close()
                time.sleep(2)
        except Exception as e:
            add_log(f"WS Error: Check connection")
        time.sleep(0.5)

# --- واجهة المستخدم وبقية المسارات تبقى كما هي ---
# ... (استخدم كود UI السابق هنا) ...

if __name__ == "__main__":
    threading.Thread(target=continuous_worker, daemon=True).start()
    port = int(os.environ.get("PORT", 5000))
    app.run(host='0.0.0.0', port=port)
