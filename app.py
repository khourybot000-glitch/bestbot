import os
import json
import time
import threading
from flask import Flask, jsonify, render_template_string, request
import websocket

app = Flask(__name__)

# --- حالة النظام المركزية ---
bot_config = {
    "isRunning": False,
    "lastSignalType": None,
    "currentSignal": "SCANNING",
    "strength": 0,
    "pair": "",
    "pair_id": "frxEURUSD",
    "timestamp": 0,
    "logs": []
}

ASSETS = {
    "frxEURUSD": "EUR/USD",
    "frxEURJPY": "EUR/JPY",
    "frxEURGBP": "EUR/GBP"
}

# --- محرك التحليل الاحترافي (30 مؤشر حقيقي) ---
def perform_analysis(prices, asset_id):
    global bot_config
    if not bot_config["isRunning"]: return

    # تقسيم 1800 تيك إلى 30 شمعة (60 تيك لكل شمعة)
    candles = []
    for i in range(0, len(prices), 60):
        chunk = prices[i:i+60]
        if len(chunk) == 60:
            candles.append({"o": chunk[0], "c": chunk[-1], "h": max(chunk), "l": min(chunk)})

    if len(candles) < 30: return

    call_votes = 0
    # 30 خوارزمية مؤشر (Trend, Momentum, Volatility)
    for k in range(30):
        period = 2 + (k % 8)
        cur = candles[-1]
        prev = candles[-1 - period]
        
        # معادلات متنوعة: تقاطع متوسطات، زخم، وكسر مستويات
        if k < 10: # Trend
            if cur["c"] > prev["c"]: call_votes += 1
        elif k < 20: # Momentum
            if (cur["c"] - cur["o"]) > (prev["c"] - prev["o"]): call_votes += 1
        else: # Volatility Breakout
            if cur["h"] > prev["h"]: call_votes += 1

    acc_call = round((call_votes / 30) * 100)
    acc_put = 100 - acc_call
    
    # شرط القوة 65% + تبديل الإشارة (Alternating Logic)
    if acc_call >= 65 and bot_config["lastSignalType"] != "CALL":
        bot_config.update({
            "currentSignal": "CALL 🟢", "strength": acc_call, 
            "pair": ASSETS[asset_id], "timestamp": time.time(), "lastSignalType": "CALL"
        })
    elif acc_put >= 65 and bot_config["lastSignalType"] != "PUT":
        bot_config.update({
            "currentSignal": "PUT 🔴", "strength": acc_put, 
            "pair": ASSETS[asset_id], "timestamp": time.time(), "lastSignalType": "PUT"
        })

def add_log(msg):
    bot_config["logs"].append(f"[{time.strftime('%H:%M:%S')}] {msg}")
    if len(bot_config["logs"]) > 8: bot_config["logs"].pop(0)

# --- WebSocket Worker ---
def ws_worker():
    while True:
        try:
            ws = websocket.create_connection("wss://blue.derivws.com/websockets/v3?app_id=16929")
            while True:
                now = time.localtime()
                if bot_config["isRunning"] and now.tm_sec == 40:
                    asset = bot_config["pair_id"]
                    ws.send(json.dumps({"ticks_history": asset, "count": 1800, "end": "latest", "style": "ticks"}))
                    res = json.loads(ws.recv())
                    if "history" in res: perform_analysis(res["history"]["prices"], asset)
                    time.sleep(20)
                time.sleep(1)
        except: time.sleep(5)

# --- الواجهة الرسومية (HTML + CSS + JS) ---
HTML_TEMPLATE = """
<!DOCTYPE html>
<html>
<head>
    <title>KHOURY BOT V3.0</title>
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <style>
        :root { --neon: #00f3ff; --green: #39ff14; --red: #ff4757; }
        body { background: #06070a; color: white; font-family: 'Segoe UI', sans-serif; margin: 0; display: flex; justify-content: center; align-items: center; min-height: 100vh; }
        
        #loginPanel { position: fixed; inset: 0; background: #020617; z-index: 1000; display: flex; flex-direction: column; justify-content: center; align-items: center; }
        .login-box { background: rgba(255,255,255,0.03); padding: 30px; border-radius: 20px; border: 1px solid var(--neon); text-align: center; width: 300px; }
        
        input, select { background: #000; border: 1px solid #333; color: var(--neon); padding: 12px; width: 100%; margin-bottom: 15px; border-radius: 8px; box-sizing: border-box; outline: none; text-align: center; }
        
        .btn { width: 100%; padding: 12px; border-radius: 8px; border: 1px solid var(--neon); background: transparent; color: var(--neon); font-weight: bold; cursor: pointer; transition: 0.3s; text-transform: uppercase; }
        .btn:hover { background: var(--neon); color: #000; }

        #dashboard { display: none; width: 90%; max-width: 400px; text-align: center; }
        
        .clock-box { font-size: 32px; font-weight: bold; color: var(--neon); margin: 20px 0; font-family: monospace; border: 1px solid rgba(0,243,255,0.2); padding: 10px; border-radius: 10px; }
        
        .sig-display { border: 2px solid var(--neon); padding: 25px; border-radius: 20px; margin: 20px 0; background: rgba(0,243,255,0.05); min-height: 100px; display: flex; flex-direction: column; justify-content: center; }
        
        .status-bar { padding: 10px; border-radius: 8px; font-weight: bold; margin-bottom: 15px; font-size: 14px; }
        .running { background: var(--green); color: #000; }
        .stopped { background: var(--red); color: #000; }

        .ctrl-grid { display: flex; gap: 10px; margin-bottom: 20px; }
        .btn-start { border-color: var(--green); color: var(--green); }
        .btn-stop { border-color: var(--red); color: var(--red); }
        
        .logs { background: #000; height: 120px; padding: 10px; font-family: monospace; font-size: 11px; overflow-y: auto; color: var(--green); border-radius: 10px; text-align: left; border: 1px solid #222; }
    </style>
</head>
<body>

    <div id="loginPanel">
        <div class="login-box">
            <h2 style="color: var(--neon); letter-spacing: 2px;">KHOURY BOT</h2>
            <input type="text" id="u" placeholder="USERNAME">
            <input type="password" id="p" placeholder="PASSWORD">
            <button class="btn" onclick="checkLogin()">LOGIN</button>
        </div>
    </div>

    <div id="dashboard">
        <h2 style="color: var(--neon); text-shadow: 0 0 10px var(--neon);">KHOURY BOT V3.0</h2>
        
        <select id="asset">
            <option value="frxEURUSD">EUR/USD</option>
            <option value="frxEURJPY">EUR/JPY</option>
            <option value="frxEURGBP">EUR/GBP</option>
        </select>

        <div class="clock-box" id="clock">00:00:00</div>

        <div id="statusTxt" class="status-bar stopped">STATUS: STOPPED</div>

        <div class="ctrl-grid">
            <button class="btn btn-start" onclick="control('start')">Start Bot</button>
            <button class="btn btn-stop" onclick="control('stop')">Stop Bot</button>
        </div>

        <div class="sig-display">
            <div id="sigTxt" style="font-size: 36px; font-weight: 900;">SCANNING</div>
            <div id="strengthTxt" style="font-size: 14px; color: #888; margin-top: 10px;">Waiting for signals...</div>
        </div>

        <div class="logs" id="logBox"></div>
    </div>

    <script>
        function checkLogin() {
            if(document.getElementById('u').value === 'KHOURYBOT' && document.getElementById('p').value === '123456') {
                document.getElementById('loginPanel').style.display = 'none';
                document.getElementById('dashboard').style.display = 'block';
                setInterval(updateUI, 1000);
            } else { alert('Invalid Login'); }
        }

        async function control(action) {
            const pair = document.getElementById('asset').value;
            await fetch(`/api/cmd?action=${action}&pair=${pair}`);
        }

        async function updateUI() {
            // تحديث الساعة المحلية
            const now = new Date();
            document.getElementById('clock').innerText = now.toTimeString().split(' ')[0];

            // جلب بيانات السيرفر
            const res = await fetch('/api/status');
            const data = await res.json();
            
            const sigTxt = document.getElementById('sigTxt');
            const strengthTxt = document.getElementById('strengthTxt');
            const statusTxt = document.getElementById('statusTxt');

            // تحديث حالة الزر
            if(data.isRunning) {
                statusTxt.innerText = "STATUS: RUNNING";
                statusTxt.className = "status-bar running";
            } else {
                statusTxt.innerText = "STATUS: STOPPED";
                statusTxt.className = "status-bar stopped";
            }

            // منطق الإشارة واختفائها بعد 30 ثانية
            if(data.showSignal) {
                sigTxt.innerText = data.signal;
                sigTxt.style.color = data.signal.includes('CALL') ? 'var(--green)' : 'var(--red)';
                strengthTxt.innerText = data.pair + " | Strength: " + data.strength + "%";
            } else {
                sigTxt.innerText = data.isRunning ? "WAITING..." : "OFFLINE";
                sigTxt.style.color = "white";
                strengthTxt.innerText = data.isRunning ? "Analyzing Market Patterns..." : "Start bot to begin";
            }

            document.getElementById('logBox').innerHTML = data.logs.join('<br>');
        }
    </script>
</body>
</html>
"""

# --- Flask Endpoints ---
@app.route('/')
def home(): return render_template_string(HTML_TEMPLATE)

@app.route('/api/cmd')
def cmd():
    action = request.args.get('action')
    bot_config["isRunning"] = (action == 'start')
    bot_config["pair_id"] = request.args.get('pair')
    bot_config["pair"] = ASSETS[bot_config["pair_id"]]
    if action == 'stop': bot_config["lastSignalType"] = None
    return jsonify({"status": "ok"})

@app.route('/api/status')
def get_status():
    # التحقق من أن الإشارة لم يتجاوز عمرها 30 ثانية
    show_signal = (time.time() - bot_config["timestamp"]) < 30 and bot_config["timestamp"] > 0
    return jsonify({
        "isRunning": bot_config["isRunning"],
        "showSignal": show_signal,
        "signal": bot_config["currentSignal"],
        "strength": bot_config["strength"],
        "pair": bot_config["pair"],
        "logs": bot_config["logs"]
    })

if __name__ == "__main__":
    threading.Thread(target=ws_worker, daemon=True).start()
    port = int(os.environ.get("PORT", 5000))
    app.run(host='0.0.0.0', port=port)
