import os
import json
import time
import threading
from flask import Flask, jsonify, render_template_string, request
import websocket
from datetime import datetime, timedelta

app = Flask(__name__)

# --- إعدادات النظام المركزي ---
bot_config = {
    "isRunning": False,
    "displayMsg": "WAITING", # النص الذي سيظهر على الشاشة
    "direction": "",
    "strength": 0,
    "pair_name": "",
    "pair_id": "frxEURUSD",
    "timestamp": 0,
    "entryTime": "",
    "isSignal": False, # للتمييز بين الإشارة و NO SIGNAL
    "logs": []
}

ASSETS = {"frxEURUSD": "EUR/USD", "frxEURJPY": "EUR/JPY", "frxEURGBP": "EUR/GBP"}

def add_log(msg):
    bot_config["logs"].append(f"[{time.strftime('%H:%M:%S')}] {msg}")
    if len(bot_config["logs"]) > 5: bot_config["logs"].pop(0)

# --- محرك التحليل (30 مؤشر + نمط الشموع) ---
def perform_analysis(prices, asset_id):
    global bot_config
    # تحويل التيكات لشموع (60 تيك = شمعة)
    candles = []
    for i in range(0, len(prices), 60):
        chunk = prices[i:i+60]
        if len(chunk) == 60:
            candles.append({"o": chunk[0], "c": chunk[-1]})

    if len(candles) < 30: return

    # فحص آخر شمعتين (نمط الارتداد)
    last_c = candles[-1]
    prev_c = candles[-2]
    is_last_up = last_c["c"] > last_c["o"]
    is_last_down = last_c["c"] < last_c["o"]
    is_prev_up = prev_c["c"] > prev_c["o"]
    is_prev_down = prev_c["c"] < prev_c["o"]

    # حساب تصويت الـ 30 مؤشر
    call_votes = 0
    for k in range(30):
        period = 2 + (k % 8)
        if candles[-1]["c"] > candles[-1-period]["c"]: call_votes += 1

    acc_call = round((call_votes / 30) * 100)
    acc_put = 100 - acc_call
    next_min = (datetime.now() + timedelta(minutes=1)).strftime("%H:%M")

    # تحديد النتيجة
    if acc_call >= 65 and is_last_up and is_prev_down:
        bot_config.update({
            "displayMsg": "SIGNAL FOUND", "isSignal": True, "direction": "CALL 🟢",
            "strength": acc_call, "pair_name": ASSETS[asset_id],
            "timestamp": time.time(), "entryTime": next_min
        })
        add_log(f"Match: CALL {acc_call}%")
    elif acc_put >= 65 and is_last_down and is_prev_up:
        bot_config.update({
            "displayMsg": "SIGNAL FOUND", "isSignal": True, "direction": "PUT 🔴",
            "strength": acc_put, "pair_name": ASSETS[asset_id],
            "timestamp": time.time(), "entryTime": next_min
        })
        add_log(f"Match: PUT {acc_put}%")
    else:
        # إذا لم يتحقق الشرط يطبع NO SIGNAL وتختفي أيضاً بعد 30 ثانية
        bot_config.update({
            "displayMsg": "NO SIGNAL", "isSignal": False,
            "timestamp": time.time(), "entryTime": ""
        })
        add_log("No pattern found at 40s.")

# --- نظام الاتصال المتقطع الذكي ---
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
            except Exception as e:
                add_log(f"Error: {str(e)}")
        time.sleep(0.5)

# --- واجهة المستخدم (HTML المدمج) ---
UI = """
<!DOCTYPE html>
<html>
<head>
    <title>KHOURY BOT V3.0</title>
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <style>
        :root { --neon: #00f3ff; --green: #39ff14; --red: #ff4757; }
        body { background: #06070a; color: white; font-family: 'Segoe UI', sans-serif; display: flex; justify-content: center; align-items: center; min-height: 100vh; margin: 0; }
        #login { position: fixed; inset: 0; background: #020617; z-index: 2000; display: flex; flex-direction: column; justify-content: center; align-items: center; }
        .box { background: rgba(255,255,255,0.02); padding: 35px; border-radius: 25px; border: 1px solid var(--neon); text-align: center; width: 320px; box-shadow: 0 0 20px rgba(0,243,255,0.1); }
        input, select { background: #000; border: 1px solid #222; color: var(--neon); padding: 14px; width: 100%; margin-bottom: 15px; border-radius: 10px; outline: none; text-align: center; font-size: 16px; }
        .btn { width: 100%; padding: 14px; border-radius: 10px; border: 1px solid var(--neon); background: transparent; color: var(--neon); font-weight: bold; cursor: pointer; transition: 0.3s; }
        .btn:hover { background: var(--neon); color: black; }
        #dash { display: none; width: 95%; max-width: 420px; text-align: center; }
        .clock { font-size: 38px; color: var(--neon); margin: 15px 0; font-family: monospace; letter-spacing: 2px; }
        .display-area { border: 2px solid var(--neon); padding: 20px; border-radius: 20px; margin: 20px 0; background: rgba(0,243,255,0.03); min-height: 200px; display: flex; flex-direction: column; justify-content: center; align-items: center; }
        .sig-text { font-size: 14px; line-height: 1.8; text-align: left; font-family: monospace; color: #fff; }
        .no-sig { font-size: 32px; font-weight: bold; color: #555; }
        .logs { background: #000; height: 100px; padding: 10px; font-family: monospace; font-size: 10px; overflow-y: auto; color: var(--green); border-radius: 10px; text-align: left; border: 1px solid #111; margin-top: 15px; }
    </style>
</head>
<body>
    <div id="login">
        <div class="box">
            <h2 style="color: var(--neon); margin-bottom: 30px;">KHOURY BOT LOGIN</h2>
            <input type="text" id="u" placeholder="USERNAME">
            <input type="password" id="p" placeholder="PASSWORD">
            <button class="btn" onclick="check()">ENTER SYSTEM</button>
        </div>
    </div>

    <div id="dash">
        <h2 style="color: var(--neon); text-shadow: 0 0 10px var(--neon);">KHOURY INTELLIGENCE V3</h2>
        <select id="asset">
            <option value="frxEURUSD">EUR/USD</option>
            <option value="frxEURJPY">EUR/JPY</option>
            <option value="frxEURGBP">EUR/GBP</option>
        </select>
        <div class="clock" id="clk">00:00:00</div>
        <div style="display:flex; gap:10px; margin-bottom:20px;">
            <button class="btn" style="color:var(--green); border-color:var(--green);" onclick="ctl('start')">START BOT</button>
            <button class="btn" style="color:var(--red); border-color:var(--red);" onclick="ctl('stop')">STOP BOT</button>
        </div>
        
        <div class="display-area" id="mainDisplay">
            </div>

        <div class="logs" id="lBox"></div>
    </div>

    <script>
        function check() {
            if(document.getElementById('u').value==='KHOURYBOT' && document.getElementById('p').value==='123456') {
                document.getElementById('login').style.display='none';
                document.getElementById('dash').style.display='block';
                setInterval(upd, 1000);
            } else alert('Access Denied!');
        }
        async function ctl(a) { await fetch(`/api/cmd?action=${a}&pair=${document.getElementById('asset').value}`); }
        async function upd() {
            document.getElementById('clk').innerText = new Date().toTimeString().split(' ')[0];
            const r = await fetch('/api/status');
            const d = await r.json();
            const disp = document.getElementById('mainDisplay');

            if(d.show) {
                if(d.isSignal) {
                    disp.innerHTML = `<div class="sig-text">
                        <span style="color:var(--neon)">PAIR:</span> ${d.pair}<br>
                        <span style="color:var(--neon)">DIRECTION:</span> ${d.signal}<br>
                        <span style="color:var(--neon)">ACCURACY:</span> ${d.strength}%<br>
                        <span style="color:var(--neon)">TIME FRAME:</span> M1<br>
                        <span style="color:var(--neon)">ENTRY TIME:</span> ${d.entry}
                    </div>`;
                } else {
                    disp.innerHTML = `<div class="no-sig">NO SIGNAL</div>`;
                }
            } else {
                disp.innerHTML = `<div style="color:#444">${d.run ? "ANALYZING MARKET..." : "BOT STOPPED"}</div>`;
            }
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
    return jsonify({"ok": True})

@app.route('/api/status')
def get_status():
    # الحذف التلقائي للرسالة (Signal أو No Signal) بعد 30 ثانية
    show = (time.time() - bot_config["timestamp"]) < 30 and bot_config["timestamp"] > 0
    return jsonify({
        "run": bot_config["isRunning"],
        "show": show,
        "isSignal": bot_config["isSignal"],
        "signal": bot_config["direction"],
        "strength": bot_config["strength"],
        "pair": bot_config["pair_name"],
        "entry": bot_config["entryTime"],
        "logs": bot_config["logs"]
    })

if __name__ == "__main__":
    threading.Thread(target=smart_ws_worker, daemon=True).start()
    app.run(host='0.0.0.0', port=int(os.environ.get("PORT", 5000)))
