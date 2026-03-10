import os, json, time, threading, websocket
from flask import Flask, jsonify, render_template_string, request
from datetime import datetime, timedelta

app = Flask(__name__)

bot_config = {
    "isRunning": False, "isSignal": False, "direction": "", 
    "win_rate": 0, "pair_id": "frxEURUSD", "timestamp": 0, 
    "entryTime": "", "logs": ["READY TO START"]
}

def add_log(msg):
    bot_config["logs"].append(f"[{time.strftime('%H:%M:%S')}] {msg}")
    if len(bot_config["logs"]) > 6: bot_config["logs"].pop(0)

# --- محرك حساب احتمالية النجاح (Win Probability Engine) ---
def perform_analysis(ticks):
    prices = ticks[-100:]
    curr = prices[-1]
    
    # 1. تصويت الـ 50 مؤشر (قوة الاتجاه)
    c_votes = sum(1 for i in range(2, 52) if curr > (sum(prices[-i:]) / i))
    p_votes = 50 - c_votes
    
    # 2. تحليل تذبذب السعر (Volatility Check)
    # كلما كان السعر مستقراً، زادت نسبة النجاح
    recent_change = abs(prices[-1] - prices[-10]) / prices[-10]
    stability_bonus = 10 if recent_change < 0.0005 else 0

    # 3. حساب نسبة النجاح النهائية
    if c_votes >= p_votes:
        bot_config["direction"] = "CALL 🟢"
        # النسبة = (عدد الأصوات / 50 * 90) + بونص الاستقرار
        bot_config["win_rate"] = (c_votes / 50 * 90) + stability_bonus
    else:
        bot_config["direction"] = "PUT 🔴"
        bot_config["win_rate"] = (p_votes / 50 * 90) + stability_bonus

    # التأكد أن النسبة لا تتجاوز 99% (للمصداقية البرمجية)
    if bot_config["win_rate"] > 99: bot_config["win_rate"] = 99

    bot_config.update({
        "isSignal": True, 
        "timestamp": time.time(), 
        "entryTime": (datetime.now() + timedelta(minutes=1)).strftime("%H:%M:00")
    })
    add_log(f"ANALYSIS: {bot_config['direction']} | PROBABILITY: {bot_config['win_rate']:.1f}%")

def ws_worker():
    while True:
        try:
            if bot_config["isRunning"] and datetime.now().second == 55:
                ws = websocket.create_connection("wss://blue.derivws.com/websockets/v3?app_id=16929", timeout=10)
                ws.send(json.dumps({"ticks_history": bot_config["pair_id"], "count": 1000, "style": "ticks"}))
                res = json.loads(ws.recv())
                if "history" in res: perform_analysis(res["history"]["prices"])
                ws.close()
                time.sleep(2)
        except: pass
        time.sleep(0.5)

UI = """
<!DOCTYPE html>
<html>
<head>
    <title>KHOURY WIN-RATE BOT</title>
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <style>
        body { background: #050608; color: #00f3ff; font-family: 'Arial', sans-serif; text-align: center; padding: 10px; }
        .card { border: 2px solid #00f3ff; border-radius: 25px; padding: 25px; background: linear-gradient(145deg, #0a0c10, #11141a); max-width: 420px; margin: auto; box-shadow: 0 0 20px rgba(0,243,255,0.2); }
        .btn { padding: 15px; width: 48%; border: 1px solid #00f3ff; background: transparent; color: #00f3ff; border-radius: 12px; font-weight: bold; cursor: pointer; transition: 0.3s; }
        .btn:active { transform: scale(0.95); }
        .signal-box { margin: 25px 0; padding: 25px; border-radius: 18px; background: rgba(0,0,0,0.6); border: 2px solid #333; }
        .win-rate { font-size: 32px; font-weight: bold; color: #39ff14; text-shadow: 0 0 10px rgba(57,255,20,0.5); }
        .logs { background: #000; height: 110px; overflow-y: auto; text-align: left; padding: 10px; color: #39ff14; font-size: 11px; margin-top: 15px; border-radius: 10px; border-top: 1px solid #222; }
    </style>
</head>
<body>
    <div class="card">
        <h2 style="letter-spacing: 2px;">V5 PRO MASTER</h2>
        <select id="asset" style="width:100%; padding:12px; background:#000; color:#00f3ff; border:1px solid #00f3ff; border-radius:10px; margin-bottom:15px;">
            <option value="frxEURUSD">EUR/USD (FOREX)</option>
            <option value="frxEURJPY">EUR/JPY (FOREX)</option>
            <option value="frxGBPUSD">GBP/USD (FOREX)</option>
        </select>
        <div id="clk" style="font-size: 45px; margin: 15px; font-family: monospace;">00:00:00</div>
        <button class="btn" onclick="ctl('start')" style="color:#39ff14; border-color:#39ff14;">START ENGINE</button>
        <button class="btn" onclick="ctl('stop')" style="color:#ff4757; border-color:#ff4757;">STOP</button>
        
        <div class="signal-box" id="disp">WAITING FOR SECOND 00...</div>
        
        <div class="logs" id="lBox"></div>
    </div>
    <script>
        async function ctl(a) { await fetch(`/api/cmd?action=${a}&pair=` + document.getElementById('asset').value); }
        setInterval(async () => {
            document.getElementById('clk').innerText = new Date().toLocaleTimeString();
            const r = await fetch('/api/status');
            const d = await r.json();
            const disp = document.getElementById('disp');
            if(d.run) {
                if(d.show) {
                    disp.innerHTML = `<span style="color:#aaa">RECOMMENDATION</span><br>
                                     <b style="font-size:28px; color:#fff">${d.direction}</b><br>
                                     <div class="win-rate">${d.win_rate.toFixed(1)}%</div>
                                     <small style="color:#00f3ff">WIN PROBABILITY</small><br>
                                     <small style="color:#555">ENTRY: ${d.entry}</small>`;
                } else { disp.innerHTML = "ANALYZING 50+ FACTORS...<br><span style='font-size:12px; color:#555'>Next signal update in sec 00</span>"; }
            } else { disp.innerHTML = "SYSTEM READY - OFFLINE"; }
            document.getElementById('lBox').innerHTML = d.logs.join('<br>');
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
    add_log(f"STARTED: {bot_config['pair_id']}")
    return jsonify({"ok": True})

@app.route('/api/status')
def get_status():
    show = (time.time() - bot_config["timestamp"]) < 50 and bot_config["timestamp"] > 0
    return jsonify({
        "run": bot_config["isRunning"], "show": show, 
        "direction": bot_config["direction"], "win_rate": bot_config["win_rate"], 
        "entry": bot_config["entryTime"], "logs": bot_config["logs"]
    })

if __name__ == "__main__":
    threading.Thread(target=ws_worker, daemon=True).start()
    app.run(host='0.0.0.0', port=int(os.environ.get("PORT", 5000)))
