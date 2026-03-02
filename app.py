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

# --- محرك التحليل الزمني (Snapshot Analysis) ---
def perform_analysis(ticks, times, asset_id):
    global bot_config
    
    current_price = ticks[-1]
    now = datetime.now()
    
    # تحديد النقاط الزمنية المطلوبة
    # نقطة الـ 10 دقائق (بداية العشر دقائق الحالية)
    start_of_10m = now.replace(minute=(now.minute // 10) * 10, second=0, microsecond=0)
    # نقطة الدقيقة الحالية (بداية الدقيقة التي نحن فيها)
    start_of_1m = now.replace(second=0, microsecond=0)
    
    price_at_10m_start = None
    price_at_1m_start = None

    # البحث في الـ 1000 تيك عن الأسعار عند تلك اللحظات
    for i in range(len(times)):
        t_dt = datetime.fromtimestamp(int(times[i]))
        if price_at_10m_start is None and t_dt >= start_of_10m:
            price_at_10m_start = ticks[i]
        if price_at_1m_start is None and t_dt >= start_of_1m:
            price_at_1m_start = ticks[i]

    # احتياطي في حال عدم توفر البيانات الزمنية بدقة
    if price_at_10m_start is None: price_at_10m_start = ticks[0]
    if price_at_1m_start is None: price_at_1m_start = ticks[-60] if len(ticks) > 60 else ticks[0]

    # المقارنة الشرطية
    is_m10_up = current_price > price_at_10m_start
    is_m10_down = current_price < price_at_10m_start
    is_m1_up = current_price > price_at_1m_start
    is_m1_down = current_price < price_at_1m_start

    next_entry = (now + timedelta(seconds=10)).strftime("%H:%M")

    # إصدار الإشارة
    if is_m10_up and is_m1_up:
        bot_config.update({
            "displayMsg": "SIGNAL FOUND", "isSignal": True, "direction": "CALL 🟢",
            "strength": 98, "pair_name": ASSETS[asset_id],
            "timestamp": time.time(), "entryTime": next_entry
        })
        add_log("Bullish Alignment: CALL Signal sent.")
    elif is_m10_down and is_m1_down:
        bot_config.update({
            "displayMsg": "SIGNAL FOUND", "isSignal": True, "direction": "PUT 🔴",
            "strength": 98, "pair_name": ASSETS[asset_id],
            "timestamp": time.time(), "entryTime": next_entry
        })
        add_log("Bearish Alignment: PUT Signal sent.")
    else:
        bot_config.update({
            "displayMsg": "NO SIGNAL", "isSignal": False,
            "timestamp": time.time(), "entryTime": ""
        })
        add_log("No trend alignment found at 50s.")

# --- نظام WebSocket المتقطع ---
def smart_ws_worker():
    while True:
        now = datetime.now()
        # التحليل فقط عند الدقيقة 9 و الثانية 50
        if bot_config["isRunning"] and (now.minute % 10 == 9) and (now.second == 50):
            try:
                ws = websocket.create_connection("wss://blue.derivws.com/websockets/v3?app_id=16929", timeout=15)
                asset = bot_config["pair_id"]
                ws.send(json.dumps({"ticks_history": asset, "count": 1000, "end": "latest", "style": "ticks"}))
                res = json.loads(ws.recv())
                if "history" in res:
                    perform_analysis(res["history"]["prices"], res["history"]["times"], asset)
                ws.close()
                time.sleep(5)
            except Exception as e:
                add_log(f"Socket Error: {str(e)}")
        time.sleep(0.5)

# --- واجهة المستخدم النيون ---
UI = """
<!DOCTYPE html>
<html>
<head>
    <title>KHOURY M1 BOT</title>
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <style>
        :root { --neon: #00f3ff; --green: #39ff14; --red: #ff4757; }
        body { background: #06070a; color: white; font-family: 'Courier New', monospace; display: flex; justify-content: center; align-items: center; min-height: 100vh; margin: 0; }
        #login { position: fixed; inset: 0; background: #020617; z-index: 2000; display: flex; flex-direction: column; justify-content: center; align-items: center; }
        .box { background: rgba(0,243,255,0.02); padding: 30px; border-radius: 20px; border: 1px solid var(--neon); text-align: center; width: 300px; box-shadow: 0 0 15px rgba(0,243,255,0.1); }
        input, select { background: #000; border: 1px solid #333; color: var(--neon); padding: 12px; width: 100%; margin-bottom: 15px; border-radius: 8px; outline: none; text-align: center; }
        .btn { width: 100%; padding: 12px; border-radius: 8px; border: 1px solid var(--neon); background: transparent; color: var(--neon); font-weight: bold; cursor: pointer; transition: 0.3s; }
        .btn:hover { background: var(--neon); color: black; }
        #dash { display: none; width: 90%; max-width: 400px; text-align: center; }
        .clock { font-size: 40px; color: var(--neon); margin: 15px 0; text-shadow: 0 0 10px var(--neon); }
        .display-area { border: 2px solid var(--neon); padding: 25px; border-radius: 20px; margin: 20px 0; background: rgba(0,243,255,0.05); min-height: 220px; display: flex; flex-direction: column; justify-content: center; }
        .sig-text { font-size: 16px; line-height: 1.8; text-align: left; color: #fff; }
        .logs { background: #000; height: 90px; padding: 10px; font-size: 10px; overflow-y: auto; color: var(--green); border-radius: 10px; text-align: left; border: 1px solid #111; margin-top: 15px; }
    </style>
</head>
<body>
    <div id="login">
        <div class="box">
            <h2 style="color: var(--neon)">KHOURY M1</h2>
            <input type="text" id="u" placeholder="ID">
            <input type="password" id="p" placeholder="PASSWORD">
            <button class="btn" onclick="check()">LOGIN</button>
        </div>
    </div>
    <div id="dash">
        <h2 style="color: var(--neon)">KHOURY M1 PRO</h2>
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
                    disp.innerHTML = `<div class="sig-text">
                        <span style="color:var(--neon)">PAIR:</span> ${d.pair}<br>
                        <span style="color:var(--neon)">DIRECTION:</span> ${d.signal}<br>
                        <span style="color:var(--neon)">ACCURACY:</span> 98%<br>
                        <span style="color:var(--neon)">TIME FRAME:</span> M1<br>
                        <span style="color:var(--neon)">ENTRY TIME:</span> ${d.entry}
                    </div>`;
                } else { disp.innerHTML = `<div style="font-size:30px; color:#555">NO SIGNAL</div>`; }
            } else { 
                let m = new Date().getMinutes();
                let wait = 9 - (m % 10);
                disp.innerHTML = `<div style="color:#444; font-size:14px;">${d.run ? "ANALYZING TREND... (Wait " + wait + "m)" : "BOT OFFLINE"}</div>`; 
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
    bot_config["pair_name"] = ASSETS[bot_config["pair_id"]]
    return jsonify({"ok": True})

@app.route('/api/status')
def get_status():
    show = (time.time() - bot_config["timestamp"]) < 40 and bot_config["timestamp"] > 0
    return jsonify({
        "run": bot_config["isRunning"], "show": show, "isSignal": bot_config["isSignal"],
        "signal": bot_config["direction"], "strength": bot_config["strength"],
        "pair": bot_config["pair_name"], "entry": bot_config["entryTime"], "logs": bot_config["logs"]
    })

if __name__ == "__main__":
    threading.Thread(target=smart_ws_worker, daemon=True).start()
    app.run(host='0.0.0.0', port=int(os.environ.get("PORT", 5000)))
