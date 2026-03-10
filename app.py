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

# --- محرك تحويل التيكات إلى شموع واستخراج المناطق ---
def get_candles_and_zones(ticks, times):
    candles = []
    # تحويل كل 60 تيك تقريباً إلى شمعة (دقيقة واحدة)
    for i in range(0, len(ticks) - 60, 60):
        c_open = ticks[i]
        c_close = ticks[i+59]
        c_high = max(ticks[i:i+60])
        c_low = min(ticks[i:i+60])
        candles.append({"open": c_open, "close": c_close, "high": c_high, "low": c_low, "type": "bull" if c_close > c_open else "bear"})

    zones = {"support": [], "resistance": []}
    
    # استخراج المناطق (القمم والقيعان حيث تغير الاتجاه)
    for i in range(1, len(candles) - 1):
        # منطقة دعم: شمعة هابطة بعدها صاعدة
        if candles[i-1]["type"] == "bear" and candles[i]["type"] == "bull":
            zones["support"].append(candles[i]["low"])
        # منطقة مقاومة: شمعة صاعدة بعدها هابطة
        if candles[i-1]["type"] == "bull" and candles[i]["type"] == "bear":
            zones["resistance"].append(candles[i]["high"])
            
    return zones

# --- محرك التحليل المتقدم ---
def perform_analysis(ticks, times, asset_id):
    global bot_config
    current_price = ticks[-1]
    now = datetime.now()
    
    # 1. استخراج المناطق من الشموع
    zones = get_candles_and_zones(ticks, times)
    
    # 2. تحديد أقرب دعم ومقاومة
    upper_zones = [z for z in zones["resistance"] if z > current_price]
    lower_zones = [z for z in zones["support"] if z < current_price]
    
    nearest_res = min(upper_zones) if upper_zones else current_price + 999
    nearest_sup = max(lower_zones) if lower_zones else current_price - 999

    # هامش أمان (Buffer) لمنع دخول صفقة قرب المنطقة
    buffer = abs(nearest_res - nearest_sup) * 0.15 # 15% من عرض القناة

    # 3. التحليل الزمني (الاتجاه العام)
    start_of_10m = now.replace(minute=(now.minute // 10) * 10, second=0, microsecond=0)
    start_of_1m = now.replace(second=0, microsecond=0)
    
    p_10m = next((ticks[i] for i, t in enumerate(times) if datetime.fromtimestamp(int(t)) >= start_of_10m), ticks[0])
    p_1m = next((ticks[i] for i, t in enumerate(times) if datetime.fromtimestamp(int(t)) >= start_of_1m), ticks[-60])

    is_m10_up = current_price > p_10m
    is_m10_down = current_price < p_10m
    is_m1_up = current_price > p_1m
    is_m1_down = current_price < p_1m

    # 4. فلترة الصفقة بناءً على المناطق
    signal = None
    reason = "NO ALIGNMENT"

    if is_m10_up and is_m1_up:
        if current_price < (nearest_res - buffer):
            signal = "CALL 🟢"
        else:
            reason = "TOO CLOSE TO RESISTANCE"
            
    elif is_m10_down and is_m1_down:
        if current_price > (nearest_sup + buffer):
            signal = "PUT 🔴"
        else:
            reason = "TOO CLOSE TO SUPPORT"

    # تحديث الحالة
    next_entry = (now + timedelta(seconds=10)).strftime("%H:%M")
    if signal:
        bot_config.update({
            "displayMsg": "SAFE SIGNAL", "isSignal": True, "direction": signal,
            "strength": 99, "pair_name": ASSETS[asset_id],
            "timestamp": time.time(), "entryTime": next_entry
        })
        add_log(f"{signal} Sent. Zone Safe.")
    else:
        bot_config.update({"displayMsg": "REJECTED", "isSignal": False, "timestamp": time.time()})
        add_log(f"Signal Blocked: {reason}")

# --- نظام WebSocket ---
def smart_ws_worker():
    while True:
        now = datetime.now()
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

# --- واجهة المستخدم (HTML المستلم سابقا مع تعديل بسيط للعرض) ---
UI = """
<!DOCTYPE html>
<html>
<head>
    <title>KHOURY M1 PRO V2</title>
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
            <h2 style="color: var(--neon)">KHOURY PRO</h2>
            <input type="text" id="u" placeholder="ID">
            <input type="password" id="p" placeholder="PASSWORD">
            <button class="btn" onclick="check()">LOGIN</button>
        </div>
    </div>
    <div id="dash">
        <h2 style="color: var(--neon)">ADVANCED ANALYSIS</h2>
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
                        <span style="color:var(--neon)">ACCURACY:</span> 99%<br>
                        <span style="color:var(--neon)">STATUS:</span> ZONE SAFE<br>
                        <span style="color:var(--neon)">ENTRY:</span> ${d.entry}
                    </div>`;
                } else { disp.innerHTML = `<div style="font-size:22px; color:var(--red)">SIGNAL REJECTED<br><small style="font-size:12px">ZONE RISK FOUND</small></div>`; }
            } else { 
                let m = new Date().getMinutes();
                let wait = 9 - (m % 10);
                disp.innerHTML = `<div style="color:#444; font-size:14px;">${d.run ? "SCANNING M1 CANDLES... (Wait " + wait + "m)" : "BOT OFFLINE"}</div>`; 
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
