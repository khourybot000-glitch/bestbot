import os, json, time, threading, websocket
from flask import Flask, jsonify, render_template_string, request
from datetime import datetime, timedelta

app = Flask(__name__)

bot_config = {
    "isRunning": False, "isSignal": False, "direction": "", 
    "win_rate": 0, "pair_id": "frxEURUSD", "pair_name": "EUR/USD",
    "timestamp": 0, "logs": ["READY"]
}

ASSETS = {"frxEURUSD": "EUR/USD", "frxEURJPY": "EUR/JPY", "frxEURGBP": "EUR/GBP"}

def add_log(msg):
    bot_config["logs"].append(f"[{time.strftime('%H:%M:%S')}] {msg}")
    if len(bot_config["logs"]) > 5: bot_config["logs"].pop(0)

def get_20_candles(ticks):
    candles = []
    for i in range(0, 1200, 60):
        segment = ticks[i:i+60]
        if len(segment) >= 60: candles.append({'close': segment[-1]})
    return candles[-20:]

def analyze_20_candles(ticks, asset_id):
    global bot_config
    try:
        candles = get_20_candles(ticks)
        if len(candles) < 2: return
        closes = [c['close'] for c in candles]
        curr_price = closes[-1]
        c_votes = 0
        p_votes = 0
        for p in range(2, 52):
            period = min(p, len(closes))
            sma = sum(closes[-period:]) / period
            if curr_price > sma: c_votes += 1
            else: p_votes += 1
        
        win_pct = (max(c_votes, p_votes) / 50) * 100
        if win_pct >= 70:
            bot_config["direction"] = "CALL 🟢" if c_votes >= p_votes else "PUT 🔴"
            bot_config["win_rate"] = win_pct
            bot_config["isSignal"] = True
            bot_config["timestamp"] = time.time()
            add_log(f"SIGNAL: {bot_config['direction']} ({win_pct}%)")
        else:
            bot_config["isSignal"] = False
            add_log(f"SKIP: Trend {win_pct}%")
    except Exception as e: add_log(f"Error: {str(e)}")

def sniper_worker():
    while True:
        try:
            now = datetime.now()
            if bot_config["isRunning"] and now.second == 50:
                ws = websocket.create_connection("wss://blue.derivws.com/websockets/v3?app_id=16929", timeout=15)
                ws.send(json.dumps({"ticks_history": bot_config["pair_id"], "count": 1200, "style": "ticks"}))
                res = json.loads(ws.recv())
                if "history" in res: analyze_20_candles(res["history"]["prices"], bot_config["pair_id"])
                ws.close()
                time.sleep(5)
        except: pass
        time.sleep(0.5)

UI = """
<!DOCTYPE html>
<html>
<head>
    <title>SNIPER V13</title>
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <style>
        body { background: #06080a; color: #00f3ff; font-family: sans-serif; text-align: center; padding: 20px; }
        .card { border: 2px solid #00f3ff; padding: 25px; border-radius: 20px; max-width: 400px; margin: auto; background: #0a0e14; box-shadow: 0 0 15px #00f3ff33; }
        .btn { padding: 15px; width: 46%; border: 1px solid #00f3ff; background: transparent; color: #00f3ff; border-radius: 10px; cursor: pointer; font-weight: bold; font-size: 14px; transition: 0.2s; }
        .btn:active { transform: scale(0.9); background: #00f3ff; color: #000; }
        .win-rate { font-size: 48px; color: #39ff14; font-weight: bold; margin: 10px 0; }
        .logs { background: #000; height: 100px; padding: 10px; font-size: 11px; overflow-y: auto; color: #39ff14; border-radius: 8px; text-align: left; margin-top: 15px; border: 1px solid #1a1a1a; }
        #mainDisp { min-height: 180px; border-top: 1px solid #1a1a1a; margin-top: 20px; padding-top: 15px; }
        select { width: 100%; padding: 12px; background: #000; color: #00f3ff; border: 1px solid #333; margin-bottom: 20px; border-radius: 8px; outline: none; }
    </style>
</head>
<body>
    <div class="card">
        <h2 style="margin:0 0 20px 0; color:#fff;">SNIPER V13</h2>
        <select id="asset">
            <option value="frxEURUSD">EUR/USD (Forex)</option>
            <option value="frxEURJPY">EUR/JPY (Forex)</option>
        </select>
        <div id="clk" style="font-size: 38px; margin-bottom: 20px; font-family: monospace;">00:00:00</div>
        
        <div style="display:flex; justify-content: space-between;">
            <button class="btn" id="startBtn" onclick="sendCmd('start')">START BOT</button>
            <button class="btn" id="stopBtn" onclick="sendCmd('stop')" style="border-color:#ff4757; color:#ff4757;">STOP BOT</button>
        </div>

        <div id="mainDisp">READY</div>
        <div class="logs" id="lBox"></div>
    </div>

    <script>
        // دقيقة ومستجيبة لإرسال الأوامر
        async function sendCmd(action) {
            const pair = document.getElementById('asset').value;
            const btn = action === 'start' ? document.getElementById('startBtn') : document.getElementById('stopBtn');
            
            // تغيير لون الزر مؤقتاً للتأكيد على الضغط
            const originalColor = btn.style.borderColor;
            btn.style.background = "#00f3ff33";
            
            try {
                await fetch(`/api/cmd?action=${action}&pair=${pair}`);
                console.log("Command sent: " + action);
            } catch (e) {
                alert("Connection Error");
            }
            
            setTimeout(() => { btn.style.background = "transparent"; }, 200);
        }

        // تحديث الواجهة كل ثانية
        setInterval(async () => {
            document.getElementById('clk').innerText = new Date().toLocaleTimeString('en-GB');
            try {
                const r = await fetch('/api/status');
                const d = await r.json();
                const disp = document.getElementById('mainDisp');
                
                if(d.run) {
                    if(d.show && d.isSignal) {
                        disp.innerHTML = `<h3 style="margin:0; color:#aaa">${d.pair}</h3>
                                         <b style="font-size:32px; color:#fff">${d.signal}</b>
                                         <div class="win-rate">${d.win_rate.toFixed(1)}%</div>
                                         <small style="color:#ff4757">EXPIRES IN ${d.rem}s</small>`;
                    } else {
                        disp.innerHTML = "<div style='color:#555; font-size:18px;'>SCANNING MARKET...<br><small>Next Signal at Sec 50</small></div>";
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
    bot_config["pair_name"] = ASSETS.get(bot_config["pair_id"], "Unknown")
    add_log(f"COMMAND: {request.args.get('action').upper()}")
    return jsonify({"ok": True})

@app.route('/api/status')
def get_status():
    now = time.time()
    elapsed = now - bot_config["timestamp"]
    show = elapsed < 30 and bot_config["timestamp"] > 0
    return jsonify({
        "run": bot_config["isRunning"], "show": show, 
        "isSignal": bot_config["isSignal"], "signal": bot_config["direction"], 
        "win_rate": bot_config["win_rate"], "pair": bot_config["pair_name"], 
        "rem": max(0, int(30 - elapsed)), "logs": bot_config["logs"]
    })

if __name__ == "__main__":
    threading.Thread(target=sniper_worker, daemon=True).start()
    app.run(host='0.0.0.0', port=int(os.environ.get("PORT", 5000)))
