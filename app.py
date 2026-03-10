import os, json, time, threading, websocket
import numpy as np
from flask import Flask, jsonify, render_template_string, request
from datetime import datetime

app = Flask(__name__)

# إعدادات البوت
bot_config = {
    "isRunning": False, 
    "isSignal": False, 
    "direction": "", 
    "rsi_curr": 50.0,
    "rsi_prev": 50.0,
    "pair_id": "R_100", 
    "pair_name": "Volatility 100",
    "timestamp": 0, 
    "logs": ["SYSTEM READY - V23"]
}

ASSETS = {
    "R_100": "Volatility 100", 
    "frxEURUSD": "EUR/USD", 
    "frxEURJPY": "EUR/JPY"
}

def add_log(msg):
    bot_config["logs"].append(f"[{time.strftime('%H:%M:%S')}] {msg}")
    if len(bot_config["logs"]) > 5: bot_config["logs"].pop(0)

# --- محرك حساب RSI لفترة 20 ---
def calculate_rsi_series(prices, period=20):
    if len(prices) < period + 1: return [50] * len(prices)
    deltas = np.diff(prices)
    up = deltas.copy()
    down = deltas.copy()
    up[up < 0] = 0
    down[down > 0] = 0
    
    # المتوسط الأول (Simple Moving Average)
    avg_gain = np.mean(up[:period])
    avg_loss = np.abs(np.mean(down[:period]))
    
    rsi = []
    # التنعيم بطريقة ويلدر (Wilder's Smoothing)
    for i in range(period, len(deltas)):
        if i == period:
            current_gain, current_loss = avg_gain, avg_loss
        else:
            current_gain = (avg_gain * (period - 1) + up[i]) / period
            current_loss = (avg_loss * (period - 1) + np.abs(down[i])) / period
            avg_gain, avg_loss = current_gain, current_loss
            
        rs = current_gain / current_loss if current_loss != 0 else 100
        rsi.append(100 - (100 / (1 + rs)))
    return rsi

# --- تحليل التقاطع (Crossover Logic) ---
def analyze_rsi_logic(ticks, asset_id):
    try:
        # تحويل 1200 تيك إلى 20 شمعة (كل 60 تيك شمعة)
        candles = []
        for i in range(0, len(ticks), 60):
            segment = ticks[i:i+60]
            if len(segment) > 0:
                candles.append(segment[-1]) # سعر الإغلاق للشمعة
        
        if len(candles) < 21: return

        # حساب قيم RSI للشموع
        rsi_values = calculate_rsi_series(candles, 20)
        rsi_p = rsi_values[-2] # RSI الشمعة 19
        rsi_c = rsi_values[-1] # RSI الشمعة 20 (الحالية)

        bot_config["rsi_curr"] = round(rsi_c, 2)
        bot_config["rsi_prev"] = round(rsi_p, 2)

        is_signal = False
        direction = ""

        # شرط الاختراق (Cross)
        if rsi_p < 50 and rsi_c >= 50:
            direction = "CALL 🟢"
            is_signal = True
        elif rsi_p > 50 and rsi_c <= 50:
            direction = "PUT 🔴"
            is_signal = True

        if is_signal:
            bot_config.update({
                "direction": direction, "isSignal": True,
                "timestamp": time.time(), "pair_name": ASSETS.get(asset_id, "Unknown")
            })
            add_log(f"ALERT: {direction} (Cross 50)")
        else:
            bot_config["isSignal"] = False
            add_log(f"Check: RSI {bot_config['rsi_curr']}")

    except Exception as e: add_log("Calc Error")

# --- الخادم الخلفي (Worker) ---
def sniper_worker():
    while True:
        try:
            now = datetime.now()
            # جلب البيانات عند الثانية 50 من كل دقيقة
            if bot_config["isRunning"] and now.second == 50:
                ws = websocket.create_connection("wss://ws.binaryws.com/websockets/v3?app_id=1089", timeout=10)
                # طلب تيكات تكفي لـ 20 شمعة وأكثر للدقة
                ws.send(json.dumps({"ticks_history": bot_config["pair_id"], "count": 1300, "style": "ticks"}))
                res = json.loads(ws.recv())
                if "history" in res:
                    analyze_rsi_logic(res["history"]["prices"], bot_config["pair_id"])
                ws.close()
                time.sleep(5) 
        except: time.sleep(1)
        time.sleep(0.5)

# --- واجهة المستخدم (UI) ---
UI = """
<!DOCTYPE html>
<html>
<head>
    <title>KHOURY RSI V23</title>
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <style>
        body { background: #06080a; color: #00f3ff; font-family: 'Segoe UI', Tahoma, sans-serif; text-align: center; padding: 15px; }
        .card { border: 2px solid #00f3ff; padding: 25px; border-radius: 25px; max-width: 400px; margin: auto; background: #0a0e14; box-shadow: 0 0 25px rgba(0,243,255,0.2); }
        .rsi-display { display: flex; justify-content: space-around; margin: 15px 0; font-size: 14px; color: #aaa; }
        .rsi-val { color: #fff; font-weight: bold; font-family: monospace; }
        .signal-box { min-height: 150px; border-top: 1px solid #222; margin-top: 20px; display: flex; flex-direction: column; justify-content: center; }
        .btn { padding: 15px; width: 46%; border: 1px solid #00f3ff; background: transparent; color: #00f3ff; border-radius: 12px; cursor: pointer; font-weight: bold; transition: 0.3s; }
        .btn:active { transform: scale(0.95); background: #00f3ff; color: #000; }
        .logs { background: #000; height: 100px; padding: 10px; font-size: 11px; overflow-y: auto; color: #39ff14; border-radius: 10px; text-align: left; margin-top: 20px; border: 1px solid #1a1a1a; }
        select { width: 100%; padding: 12px; background: #000; color: #00f3ff; border: 1px solid #333; margin-bottom: 20px; border-radius: 10px; outline: none; }
    </style>
</head>
<body>
    <div class="card">
        <h2 style="margin-top:0;">RSI CROSS V23</h2>
        <select id="asset">
            <option value="R_100">Volatility 100 Index</option>
            <option value="frxEURUSD">EUR/USD (Forex)</option>
        </select>
        <div id="clk" style="font-size: 38px; margin-bottom: 20px; font-family: monospace; font-weight: bold;">00:00:00</div>
        
        <div style="display:flex; justify-content: space-between;">
            <button class="btn" onclick="send('start')" style="border-color:#39ff14; color:#39ff14;">START BOT</button>
            <button class="btn" onclick="send('stop')" style="border-color:#ff4757; color:#ff4757;">STOP BOT</button>
        </div>

        <div class="rsi-display">
            <div>Prev RSI: <span id="p_rsi" class="rsi-val">--</span></div>
            <div>Curr RSI: <span id="c_rsi" class="rsi-val">--</span></div>
        </div>

        <div class="signal-box" id="disp">READY</div>
        <div class="logs" id="lBox"></div>
    </div>

    <script>
        async function send(a) { 
            const pair = document.getElementById('asset').value;
            await fetch(`/api/cmd?action=${a}&pair=${pair}`); 
        }

        setInterval(async () => {
            document.getElementById('clk').innerText = new Date().toLocaleTimeString('en-GB');
            try {
                const r = await fetch('/api/status');
                const d = await r.json();
                
                document.getElementById('p_rsi').innerText = d.p_rsi;
                document.getElementById('c_rsi').innerText = d.c_rsi;
                
                const disp = document.getElementById('disp');
                if(d.run) {
                    if(d.show && d.isSig) {
                        disp.innerHTML = `<h3 style="margin:0; color:#00f3ff;">${d.pair}</h3>
                                         <b style="font-size:45px; color:#fff;">${d.sig}</b>
                                         <div style="color:#39ff14; font-weight:bold; margin-top:5px;">50-LEVEL CROSSOVER!</div>`;
                    } else {
                        disp.innerHTML = "<div style='color:#555; font-size:18px;'>WAITING FOR CROSS...<br><small>Analyzing 20 Candles</small></div>";
                    }
                } else {
                    disp.innerHTML = "<span style='color:#444'>SYSTEM OFFLINE</span>";
                }
                document.getElementById('lBox').innerHTML = d.logs.join('<br>');
            } catch(e) {}
        }, 1000);
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
    show = (time.time() - bot_config["timestamp"]) < 30 and bot_config["timestamp"] > 0
    return jsonify({
        "run": bot_config["isRunning"], "show": show, 
        "isSig": bot_config["isSignal"], "sig": bot_config["direction"],
        "p_rsi": bot_config["rsi_prev"], "c_rsi": bot_config["rsi_curr"],
        "pair": bot_config["pair_name"], "logs": bot_config["logs"]
    })

if __name__ == "__main__":
    threading.Thread(target=sniper_worker, daemon=True).start()
    app.run(host='0.0.0.0', port=int(os.environ.get("PORT", 5000)))
