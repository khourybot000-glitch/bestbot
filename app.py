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
    "lastSignalType": None,
    "currentSignal": "WAITING",
    "strength": 0,
    "pair": "",
    "pair_id": "frxEURUSD",
    "timestamp": 0,
    "entryTime": "",
    "logs": []
}

ASSETS = {"frxEURUSD": "EUR/USD", "frxEURJPY": "EUR/JPY", "frxEURGBP": "EUR/GBP"}

def add_log(msg):
    bot_config["logs"].append(f"[{time.strftime('%H:%M:%S')}] {msg}")
    if len(bot_config["logs"]) > 5: bot_config["logs"].pop(0)

# --- محرك التحليل (30 مؤشر) ---
def perform_analysis(prices, asset_id):
    global bot_config
    candles = []
    for i in range(0, len(prices), 60):
        chunk = prices[i:i+60]
        if len(chunk) == 60:
            candles.append({"o": chunk[0], "c": chunk[-1], "h": max(chunk), "l": min(chunk)})

    if len(candles) < 30: return

    call_votes = 0
    for k in range(30):
        period = 2 + (k % 8)
        if candles[-1]["c"] > candles[-1-period]["c"]: call_votes += 1

    acc_call = round((call_votes / 30) * 100)
    acc_put = 100 - acc_call
    
    # حساب وقت الدخول للدقيقة التالية
    next_min = (datetime.now() + timedelta(minutes=1)).strftime("%H:%M")

    if acc_call >= 65 and bot_config["lastSignalType"] != "CALL":
        bot_config.update({
            "currentSignal": "CALL 🟢", "strength": acc_call, 
            "pair": ASSETS[asset_id], "timestamp": time.time(), 
            "lastSignalType": "CALL", "entryTime": next_min
        })
        add_log(f"New Signal: CALL {acc_call}%")
    elif acc_put >= 65 and bot_config["lastSignalType"] != "PUT":
        bot_config.update({
            "currentSignal": "PUT 🔴", "strength": acc_put, 
            "pair": ASSETS[asset_id], "timestamp": time.time(), 
            "lastSignalType": "PUT", "entryTime": next_min
        })
        add_log(f"New Signal: PUT {acc_put}%")
    else:
        add_log("Analysis complete: No signal.")

# --- نظام الاتصال الذكي (Connect-Analyze-Disconnect) ---
def smart_ws_worker():
    while True:
        now = datetime.now()
        # يبدأ الاتصال فقط عند الثانية 39 لتجهيز الطلب عند الثانية 40
        if bot_config["isRunning"] and now.second == 39:
            try:
                ws = websocket.create_connection("wss://blue.derivws.com/websockets/v3?app_id=16929", timeout=10)
                asset = bot_config["pair_id"]
                add_log(f"Connecting to analyze {ASSETS[asset]}...")
                
                # إرسال الطلب عند الثانية 40
                ws.send(json.dumps({"ticks_history": asset, "count": 1800, "end": "latest", "style": "ticks"}))
                
                # استقبال البيانات والتحليل
                result = ws.recv()
                data = json.loads(result)
                if "history" in data:
                    perform_analysis(data["history"]["prices"], asset)
                
                # قطع الاتصال فوراً لتوفير الموارد ومنع التعليق
                ws.close()
                add_log("Analysis done. Connection closed.")
                time.sleep(2) # منع التكرار في نفس الثانية
            except Exception as e:
                add_log(f"Connection Error: {str(e)}")
        
        time.sleep(0.5)

# --- الواجهة الرسومية ---
HTML_TEMPLATE = """
<!DOCTYPE html>
<html>
<head>
    <title>KHOURY BOT V3.0</title>
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <style>
        :root { --neon: #00f3ff; --green: #39ff14; --red: #ff4757; }
        body { background: #06070a; color: white; font-family: sans-serif; margin: 0; display: flex; justify-content: center; align-items: center; min-height: 100vh; }
        #loginPanel { position: fixed; inset: 0; background: #020617; z-index: 1000; display: flex; flex-direction: column; justify-content: center; align-items: center; }
        .box { background: rgba(255,255,255,0.03); padding: 30px; border-radius: 20px; border: 1px solid var(--neon); text-align: center; width: 300px; }
        input, select { background: #000; border: 1px solid #333; color: var(--neon); padding: 12px; width: 100%; margin-bottom: 15px; border-radius: 8px; box-sizing: border-box; outline: none; text-align: center; }
        .btn { width: 100%; padding: 12px; border-radius: 8px; border: 1px solid var(--neon); background: transparent; color: var(--neon); font-weight: bold; cursor: pointer; text-transform: uppercase; }
        #dashboard { display: none; width: 90%; max-width: 400px; text-align: center; }
        .clock { font-size: 32px; color: var(--neon); margin: 15px 0; font-family: monospace; border: 1px solid rgba(0,243,255,0.2); padding: 10px; border-radius: 10px; }
        .sig-card { border: 2px solid var(--neon); padding: 20px; border-radius: 20px; margin: 20px 0; background: rgba(0,243,255,0.05); min-height: 120px; }
        .status { padding: 8px; border-radius: 5px; font-weight: bold; margin-bottom: 15px; font-size: 13px; }
        .running { background: var(--green); color: #000; }
        .stopped { background: var(--red); color: #000; }
        .entry-tag { color: var(--neon); font-weight: bold; font-size: 20px; margin-top: 15px; display: block; border-top: 1px solid #222; padding-top: 10px; }
        .logs { background: #000; height: 100px; padding: 10px; font-family: monospace; font-size: 10px; overflow-y: auto; color: var(--green); border-radius: 10px; text-align: left; border: 1px solid #222; margin-top: 10px; }
    </style>
</head>
<body>
    <div id="loginPanel">
        <div class="box">
            <h2 style="color: var(--neon)">KHOURY BOT</h2>
            <input type="text" id="u" placeholder="USERNAME">
            <input type="password" id="p" placeholder="PASSWORD">
            <button class="btn" onclick="check()">LOGIN</button>
        </div>
    </div>

    <div id="dashboard">
        <h2 style="color: var(--neon)">KHOURY BOT V3.0</h2>
        <select id="asset">
            <option value="frxEURUSD">EUR/USD</option>
            <option value="frxEURJPY">EUR/JPY</option>
            <option value="frxEURGBP">EUR/GBP</option>
        </select>
        <div class="clock" id="clk">00:00:00</div>
        <div id="st" class="status stopped">STATUS: STOPPED</div>
        <div style="display:flex; gap:10px;">
            <button class="btn" style="border-color:var(--green); color:var(--green);" onclick="ctl('start')">START</button>
            <button class="btn" style="border-color:var(--red); color:var(--red);" onclick="ctl('stop')">STOP</button>
        </div>
        <div class="sig-card">
            <div id="sig" style="font-size: 38px; font-weight: 900;">WAITING</div>
            <div id="inf" style="font-size: 14px; color: #aaa;">Analysis at 40s mark</div>
            <div id="entry" class="entry-tag"></div>
        </div>
        <div class="logs" id="logBox"></div>
    </div>

    <script>
        function check() {
            if(document.getElementById('u').value==='KHOURYBOT' && document.getElementById('p').value==='123456') {
                document.getElementById('loginPanel').style.display='none';
                document.getElementById('dashboard').style.display='block';
                setInterval(upd, 1000);
            } else alert('Access Denied');
        }
        async function ctl(a) { 
            const p = document.getElementById('asset').value;
            await fetch(`/api/cmd?action=${a}&pair=${p}`); 
        }
        async function upd() {
            document.getElementById('clk').innerText = new Date().toTimeString().split(' ')[0];
            const r = await fetch('/api/status');
            const d = await r.json();
            
            document.getElementById('st').innerText = "STATUS: " + (d.isRunning ? "RUNNING" : "STOPPED");
            document.getElementById('st').className = "status " + (d.isRunning ? "running" : "stopped");

            if(d.show) {
                document.getElementById('sig').innerText = d.signal;
                document.getElementById('sig').style.color = d.signal.includes('CALL') ? 'var(--green)' : 'var(--red)';
                document.getElementById('inf').innerText = d.pair + " | Strength: " + d.strength + "%";
                document.getElementById('entry').innerText = "ENTRY TIME: " + d.entry;
            } else {
                document.getElementById('sig').innerText = d.isRunning ? "ANALYZING..." : "OFFLINE";
                document.getElementById('sig').style.color = "white";
                document.getElementById('inf').innerText = "Monitoring Market Patterns...";
                document.getElementById('entry').innerText = "";
            }
            document.getElementById('logBox').innerHTML = d.logs.join('<br>');
            document.getElementById('logBox').scrollTop = document.getElementById('logBox').scrollHeight;
        }
    </script>
</body>
</html>
"""

@app.route('/')
def home(): return render_template_string(HTML_TEMPLATE)

@app.route('/api/cmd')
def cmd():
    action = request.args.get('action')
    bot_config["isRunning"] = (action == 'start')
    bot_config["pair_id"] = request.args.get('pair')
    bot_config["pair"] = ASSETS[bot_config["pair_id"]]
    if action == 'stop': bot_config["lastSignalType"] = None
    return jsonify({"ok": True})

@app.route('/api/status')
def get_status():
    show = (time.time() - bot_config["timestamp"]) < 30 and bot_config["timestamp"] > 0
    return jsonify({
        "isRunning": bot_config["isRunning"],
        "show": show,
        "signal": bot_config["currentSignal"],
        "strength": bot_config["strength"],
        "pair": bot_config["pair"],
        "entry": bot_config["entryTime"],
        "logs": bot_config["logs"]
    })

if __name__ == "__main__":
    threading.Thread(target=smart_ws_worker, daemon=True).start()
    app.run(host='0.0.0.0', port=int(os.environ.get("PORT", 5000)))
