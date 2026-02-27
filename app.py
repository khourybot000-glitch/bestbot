import os
import json
import time
import threading
from flask import Flask, jsonify, render_template_string, request
import websocket
from datetime import datetime, timedelta

app = Flask(__name__)

# --- إعدادات النظام ---
bot_config = {
    "isRunning": False,
    "displayMsg": "WAITING",
    "direction": "",
    "strength": 0,
    "pair_name": "",
    "pair_id": "frxEURUSD",
    "timestamp": 0,
    "entryTime": "",
    "isSignal": False,
    "logs": []
}

ASSETS = {"frxEURUSD": "EUR/USD", "frxEURJPY": "EUR/JPY", "frxEURGBP": "EUR/GBP"}

def add_log(msg):
    bot_config["logs"].append(f"[{time.strftime('%H:%M:%S')}] {msg}")
    if len(bot_config["logs"]) > 5: bot_config["logs"].pop(0)

# --- محرك التحليل المعتمد على كتل التيكات ---
def perform_analysis(all_prices, asset_id):
    global bot_config
    
    if len(all_prices) < 120: return

    # 1. تقسيم آخر 120 تيك إلى شمعتين افتراضيتين
    # الشمعة الحالية (آخر 60 تيك)
    current_ticks = all_prices[-60:]
    c_open = current_ticks[0]
    c_close = current_ticks[-1]
    
    # الشمعة السابقة (الـ 60 تيك التي قبلها)
    previous_ticks = all_prices[-120:-60]
    p_open = previous_ticks[0]
    p_close = previous_ticks[-1]

    # تحديد الاتجاهات
    is_current_up = c_close > c_open
    is_current_down = c_close < c_open
    is_prev_up = p_close > p_open
    is_prev_down = p_close < p_open

    # 2. تحليل الـ 30 مؤشر (باستخدام الـ 1800 تيك كاملة للقوة الفنية)
    candles_for_ind = []
    for i in range(0, len(all_prices), 60):
        chunk = all_prices[i:i+60]
        if len(chunk) == 60:
            candles_for_ind.append({"o": chunk[0], "c": chunk[-1]})

    call_votes = 0
    if len(candles_for_ind) >= 30:
        for k in range(30):
            period = 2 + (k % 8)
            if candles_for_ind[-1]["c"] > candles_for_ind[-1-period]["c"]: call_votes += 1

    acc_call = round((call_votes / 30) * 100)
    acc_put = 100 - acc_call
    next_min = (datetime.now() + timedelta(minutes=1)).strftime("%H:%M")

    # 3. تطبيق الشروط بناءً على تقسيم الـ 120 تيك
    
    # شرط CALL: (آخر 60 تيك صعود) + (الـ 60 تيك السابقة هبوط)
    if acc_call >= 65 and is_current_up and is_prev_down:
        bot_config.update({
            "displayMsg": "SIGNAL FOUND", "isSignal": True, "direction": "CALL 🟢",
            "strength": acc_call, "pair_name": ASSETS[asset_id],
            "timestamp": time.time(), "entryTime": next_min
        })
        add_log(f"CALL Pattern Match: {acc_call}%")

    # شرط PUT: (آخر 60 تيك هبوط) + (الـ 60 تيك السابقة صعود)
    elif acc_put >= 65 and is_current_down and is_prev_up:
        bot_config.update({
            "displayMsg": "SIGNAL FOUND", "isSignal": True, "direction": "PUT 🔴",
            "strength": acc_put, "pair_name": ASSETS[asset_id],
            "timestamp": time.time(), "entryTime": next_min
        })
        add_log(f"PUT Pattern Match: {acc_put}%")
    
    else:
        # إذا لم يتحقق تقسيم الـ 120 تيك أو القوة
        bot_config.update({
            "displayMsg": "NO SIGNAL", "isSignal": False,
            "timestamp": time.time(), "entryTime": ""
        })
        add_log("No Signal: Pattern or Strength mismatch.")

# --- نظام WebSocket الذكي ---
def smart_ws_worker():
    while True:
        now = datetime.now()
        if bot_config["isRunning"] and now.second == 39:
            try:
                ws = websocket.create_connection("wss://blue.derivws.com/websockets/v3?app_id=16929", timeout=10)
                asset = bot_config["pair_id"]
                ws.send(json.dumps({"ticks_history": asset, "count": 1800, "end": "latest", "style": "ticks"}))
                data = json.loads(ws.recv())
                if "history" in data:
                    perform_analysis(data["history"]["prices"], asset)
                ws.close()
                time.sleep(2) 
            except: pass
        time.sleep(0.5)

# --- الواجهة البرمجية (HTML المعتمد سابقاً) ---
UI = """
<!DOCTYPE html>
<html>
<head>
    <title>KHOURY BOT V3.0</title>
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <style>
        :root { --neon: #00f3ff; --green: #39ff14; --red: #ff4757; }
        body { background: #06070a; color: white; font-family: sans-serif; display: flex; justify-content: center; align-items: center; min-height: 100vh; margin: 0; }
        #login { position: fixed; inset: 0; background: #020617; z-index: 2000; display: flex; flex-direction: column; justify-content: center; align-items: center; }
        .box { background: rgba(255,255,255,0.02); padding: 35px; border-radius: 25px; border: 1px solid var(--neon); text-align: center; width: 300px; }
        input, select { background: #000; border: 1px solid #222; color: var(--neon); padding: 12px; width: 100%; margin-bottom: 15px; border-radius: 10px; outline: none; text-align: center; }
        .btn { width: 100%; padding: 12px; border-radius: 10px; border: 1px solid var(--neon); background: transparent; color: var(--neon); font-weight: bold; cursor: pointer; }
        #dash { display: none; width: 95%; max-width: 400px; text-align: center; }
        .clock { font-size: 35px; color: var(--neon); margin: 15px 0; font-family: monospace; }
        .display-area { border: 2px solid var(--neon); padding: 20px; border-radius: 20px; margin: 20px 0; background: rgba(0,243,255,0.03); min-height: 180px; display: flex; flex-direction: column; justify-content: center; align-items: center; }
        .sig-text { font-size: 14px; line-height: 1.7; text-align: left; font-family: monospace; color: #fff; }
        .logs { background: #000; height: 80px; padding: 10px; font-family: monospace; font-size: 10px; overflow-y: auto; color: var(--green); border-radius: 10px; text-align: left; border: 1px solid #111; margin-top: 15px; }
    </style>
</head>
<body>
    <div id="login">
        <div class="box">
            <h2 style="color: var(--neon)">KHOURY BOT</h2>
            <input type="text" id="u" placeholder="USERNAME">
            <input type="password" id="p" placeholder="PASSWORD">
            <button class="btn" onclick="check()">LOGIN</button>
        </div>
    </div>
    <div id="dash">
        <h2 style="color: var(--neon)">KHOURY BOT V3.0</h2>
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
            } else alert('Error');
        }
        async function ctl(a) { await fetch(`/api/cmd?action=${a}&pair=${document.getElementById('asset').value}`); }
        async function upd() {
            document.getElementById('clk').innerText = new Date().toTimeString().split(' ')[0];
            const r = await fetch('/api/status');
            const d = await r.json();
            const disp = document.getElementById('mainDisp');
            if(d.show) {
                if(d.isSignal) {
                    disp.innerHTML = `<div class="sig-text">
                        <span style="color:var(--neon)">PAIR:</span> ${d.pair}<br>
                        <span style="color:var(--neon)">DIRECTION:</span> ${d.signal}<br>
                        <span style="color:var(--neon)">ACCURACY:</span> ${d.strength}%<br>
                        <span style="color:var(--neon)">TIME FRAME:</span> M1<br>
                        <span style="color:var(--neon)">ENTRY TIME:</span> ${d.entry}
                    </div>`;
                } else { disp.innerHTML = `<div style="font-size:30px; color:#555">NO SIGNAL</div>`; }
            } else { disp.innerHTML = `<div style="color:#333">${d.run ? "ANALYZING..." : "OFFLINE"}</div>`; }
            document.getElementById('lBox').innerHTML = d.logs.join('<br>');
        }
    </script>
</body>
</html>
"""

@app.route('/')
def home(): return render_template_string(UI)

@app.route('/api/cmd')
def cmd():
    bot_config["isRunning"] = (request.args.get('action') == 'start')
    bot_config["pair_id"] = request.args.get('pair')
    bot_config["pair_name"] = ASSETS[bot_config["pair_id"]]
    return jsonify({"ok": True})

@app.route('/api/status')
def get_status():
    show = (time.time() - bot_config["timestamp"]) < 30 and bot_config["timestamp"] > 0
    return jsonify({
        "run": bot_config["isRunning"], "show": show, "isSignal": bot_config["isSignal"],
        "signal": bot_config["direction"], "strength": bot_config["strength"],
        "pair": bot_config["pair_name"], "entry": bot_config["entryTime"], "logs": bot_config["logs"]
    })

if __name__ == "__main__":
    threading.Thread(target=smart_ws_worker, daemon=True).start()
    app.run(host='0.0.0.0', port=int(os.environ.get("PORT", 5000)))
