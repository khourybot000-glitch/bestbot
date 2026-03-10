import os
import json
import time
import threading
import websocket
from flask import Flask, jsonify, render_template_string, request
from datetime import datetime, timedelta

app = Flask(__name__)

# --- إعدادات النظام الأساسية ---
bot_config = {
    "isRunning": False,
    "displayMsg": "INITIALIZING",
    "direction": "",
    "strength": 0,
    "pair_id": "frxEURUSD",
    "pair_name": "EUR/USD",
    "timestamp": 0,
    "entryTime": "",
    "isSignal": False,
    "logs": []
}

ASSETS = {
    "frxEURUSD": "EUR/USD", 
    "frxEURJPY": "EUR/JPY", 
    "frxEURGBP": "EUR/GBP"
}

def add_log(msg):
    bot_config["logs"].append(f"[{time.strftime('%H:%M:%S')}] {msg}")
    if len(bot_config["logs"]) > 5:
        bot_config["logs"].pop(0)

# --- محرك المؤشرات الفنية (Indicators) ---
def get_indicators(ticks):
    try:
        if len(ticks) < 100: return None
        prices = ticks[-100:]
        
        # 1. المتوسطات المتحركة (SMA)
        sma_20 = sum(prices[-20:]) / 20
        sma_50 = sum(prices[-50:]) / 50
        
        # 2. مؤشر القوة النسبية (RSI)
        deltas = [prices[i+1] - prices[i] for i in range(len(prices)-1)]
        gains = sum([d for d in deltas[-14:] if d > 0]) / 14
        losses = sum([-d for d in deltas[-14:] if d < 0]) / 14
        rs = gains / losses if losses != 0 else 100
        rsi = 100 - (100 / (1 + rs))
        
        return {"rsi": rsi, "sma20": sma_20, "sma50": sma_50}
    except Exception as e:
        return None

# --- استخراج مناطق الدعم والمقاومة من الشموع ---
def get_zones(ticks):
    try:
        candles = []
        # تحويل التيكات إلى شموع (كل 60 تيك = دقيقة)
        for i in range(0, len(ticks)-60, 60):
            candles.append({
                "low": min(ticks[i:i+60]), 
                "high": max(ticks[i:i+60]),
                "open": ticks[i],
                "close": ticks[i+59]
            })
        
        # استخراج مناطق الانعكاس
        supports = []
        resistances = []
        for i in range(1, len(candles)):
            # دعم: شمعة هابطة تبعها صعود
            if candles[i-1]["close"] < candles[i-1]["open"] and candles[i]["close"] > candles[i]["open"]:
                supports.append(candles[i]["low"])
            # مقاومة: شمعة صاعدة تبعها هبوط
            if candles[i-1]["close"] > candles[i-1]["open"] and candles[i]["close"] < candles[i]["open"]:
                resistances.append(candles[i]["high"])
        
        current_price = ticks[-1]
        nearest_sup = max([s for s in supports if s < current_price], default=current_price * 0.99)
        nearest_res = min([r for r in resistances if r > current_price], default=current_price * 1.01)
        
        return nearest_sup, nearest_res
    except:
        return ticks[0], ticks[-1]

# --- التحليل فائق الدقة (Ultra Analysis) ---
def perform_ultra_analysis(ticks, asset_id):
    global bot_config
    curr = ticks[-1]
    ind = get_indicators(ticks)
    sup, res = get_zones(ticks)
    
    if not ind: return

    score_call = 0
    # شرط الاتجاه (40 نقطة)
    if curr > ind["sma20"] and ind["sma20"] > ind["sma50"]: score_call += 40
    # شرط RSI (30 نقطة)
    if 40 < ind["rsi"] < 65: score_call += 30
    # شرط البعد عن المقاومة (30 نقطة)
    if (res - curr) > (curr * 0.0004): score_call += 30

    score_put = 0
    if curr < ind["sma20"] and ind["sma20"] < ind["sma50"]: score_put += 40
    if 35 < ind["rsi"] < 60: score_put += 30
    if (curr - sup) > (curr * 0.0004): score_put += 30

    final_score = max(score_call, score_put)
    
    if final_score >= 70:
        direction = "CALL 🟢" if score_call > score_put else "PUT 🔴"
        bot_config.update({
            "isSignal": True, 
            "direction": direction, 
            "strength": final_score,
            "pair_name": ASSETS.get(asset_id, "Unknown"), 
            "timestamp": time.time(),
            "entryTime": (datetime.now() + timedelta(minutes=1)).strftime("%H:%M:00")
        })
        add_log(f"GOLDEN {direction} FOUND! ({final_score}%)")
    else:
        bot_config.update({"isSignal": False, "timestamp": time.time()})
        add_log(f"Scan Finished: Low Score ({final_score}%)")

# --- العامل المستمر (يحلل كل دقيقة) ---
def continuous_worker():
    while True:
        try:
            now = datetime.now()
            # يحلل في الثانية 55 من كل دقيقة ليكون الدخول في بداية الدقيقة التالية
            if bot_config["isRunning"] and now.second == 55:
                ws = websocket.create_connection("wss://blue.derivws.com/websockets/v3?app_id=16929", timeout=10)
                ws.send(json.dumps({"ticks_history": bot_config["pair_id"], "count": 1000, "style": "ticks"}))
                res = json.loads(ws.recv())
                if "history" in res:
                    perform_ultra_analysis(res["history"]["prices"], bot_config["pair_id"])
                ws.close()
                time.sleep(2)
        except Exception as e:
            add_log(f"Socket Error: Connection Refused")
        time.sleep(0.5)

# --- واجهة المستخدم (HTML/CSS) ---
UI = """
<!DOCTYPE html>
<html>
<head>
    <title>KHOURY ULTRA PRO V3</title>
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <style>
        :root { --neon: #00f3ff; --green: #39ff14; --red: #ff4757; }
        body { background: #06070a; color: white; font-family: 'Courier New', monospace; display: flex; justify-content: center; align-items: center; min-height: 100vh; margin: 0; }
        #login { position: fixed; inset: 0; background: #020617; z-index: 2000; display: flex; flex-direction: column; justify-content: center; align-items: center; }
        .box { background: rgba(0,243,255,0.02); padding: 30px; border-radius: 20px; border: 1px solid var(--neon); text-align: center; width: 320px; box-shadow: 0 0 20px rgba(0,243,255,0.1); }
        input, select { background: #000; border: 1px solid #333; color: var(--neon); padding: 12px; width: 100%; margin-bottom: 15px; border-radius: 8px; outline: none; text-align: center; font-size: 16px; }
        .btn { width: 100%; padding: 12px; border-radius: 8px; border: 1px solid var(--neon); background: transparent; color: var(--neon); font-weight: bold; cursor: pointer; transition: 0.3s; margin-top: 5px; }
        .btn:hover { background: var(--neon); color: black; }
        #dash { display: none; width: 90%; max-width: 400px; text-align: center; }
        .clock { font-size: 45px; color: var(--neon); margin: 15px 0; text-shadow: 0 0 15px var(--neon); }
        .display-area { border: 2px solid var(--neon); padding: 25px; border-radius: 20px; margin: 20px 0; background: rgba(0,243,255,0.05); min-height: 180px; display: flex; flex-direction: column; justify-content: center; transition: 0.5s; }
        .logs { background: #000; height: 100px; padding: 10px; font-size: 11px; overflow-y: auto; color: var(--green); border-radius: 10px; text-align: left; border: 1px solid #111; margin-top: 15px; }
    </style>
</head>
<body>
    <div id="login">
        <div class="box">
            <h2 style="color: var(--neon)">KHOURY ULTRA</h2>
            <input type="text" id="u" placeholder="USERNAME">
            <input type="password" id="p" placeholder="PASSWORD">
            <button class="btn" onclick="check()">ACCESS SYSTEM</button>
        </div>
    </div>
    <div id="dash">
        <h2 style="color: var(--neon); margin-bottom: 5px;">M1 PRECISION BOT</h2>
        <select id="asset">
            <option value="frxEURUSD">EUR/USD</option>
            <option value="frxEURJPY">EUR/JPY</option>
            <option value="frxEURGBP">EUR/GBP</option>
        </select>
        <div class="clock" id="clk">00:00:00</div>
        <div style="display:flex; gap:10px; margin-bottom:20px;">
            <button class="btn" style="color:var(--green); border-color:var(--green);" onclick="ctl('start')">START</button>
            <button class="btn" style="color:var(--red); border-color:var(--red);" onclick="ctl('stop')">STOP</button>
        </div>
        <div class="display-area" id="mainDisp"></div>
        <div class="logs" id="lBox"></div>
    </div>
    <script>
        function check() {
            if(document.getElementById('u').value==='KHOURYBOT' && document.getElementById('p').value==='123456') {
                document.getElementById('login').style.display='none';
                document.getElementById('dash').style.display='block';
                setInterval(upd, 1000);
            } else alert('Access Denied');
        }
        async function ctl(a) { await fetch(`/api/cmd?action=${a}&pair=${document.getElementById('asset').value}`); }
        async function upd() {
            document.getElementById('clk').innerText = new Date().toTimeString().split(' ')[0];
            const r = await fetch('/api/status');
            const d = await r.json();
            const disp = document.getElementById('mainDisp');
            if(d.show) {
                if(d.isSignal) {
                    disp.innerHTML = `<div style="text-align:left; font-size:18px;">
                        <span style="color:var(--neon)">PAIR:</span> ${d.pair}<br>
                        <span style="color:var(--neon)">DIRECTION:</span> ${d.signal}<br>
                        <span style="color:var(--neon)">STRENGTH:</span> ${d.strength}%<br>
                        <span style="color:var(--neon)">ENTRY:</span> ${d.entry}
                    </div>`;
                } else { 
                    disp.innerHTML = `<div style="color:var(--red); font-size:20px;">SIGNAL REJECTED<br><small style="font-size:12px; color:#555">ZONE RISK OR LOW VOLATILITY</small></div>`; 
                }
            } else { 
                disp.innerHTML = `<div style="color:#444; font-size:16px;">${d.run ? "SCANNING M1 CANDLES..." : "BOT OFFLINE"}</div>`; 
            }
            document.getElementById('lBox').innerHTML = d.logs.join('<br>');
        }
    </script>
</body>
</html>
"""

# --- مسارات الـ Flask API ---
@app.route('/')
def home():
    return render_template_string(UI)

@app.route('/api/cmd')
def cmd():
    bot_config["isRunning"] = (request.args.get('action') == 'start')
    bot_config["pair_id"] = request.args.get('pair', 'frxEURUSD')
    bot_config["pair_name"] = ASSETS.get(bot_config["pair_id"], "Unknown")
    return jsonify({"ok": True})

@app.route('/api/status')
def get_status():
    show = (time.time() - bot_config["timestamp"]) < 45 and bot_config["timestamp"] > 0
    return jsonify({
        "run": bot_config["isRunning"], "show": show, "isSignal": bot_config["isSignal"],
        "signal": bot_config["direction"], "strength": bot_config["strength"],
        "pair": bot_config["pair_name"], "entry": bot_config["entryTime"], "logs": bot_config["logs"]
    })

# --- التشغيل المتوافق مع Render ---
if __name__ == "__main__":
    threading.Thread(target=continuous_worker, daemon=True).start()
    port = int(os.environ.get("PORT", 5000))
    app.run(host='0.0.0.0', port=port)
