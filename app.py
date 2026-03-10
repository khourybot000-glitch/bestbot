import os, json, time, threading, websocket
from flask import Flask, jsonify, render_template_string, request
from datetime import datetime, timedelta

app = Flask(__name__)

bot_config = {
    "isRunning": False, "isSignal": False, "direction": "", 
    "strength": 0, "pair_id": "frxEURUSD", "timestamp": 0, 
    "entryTime": "", "logs": ["SYSTEM READY"]
}

def add_log(msg):
    bot_config["logs"].append(f"[{time.strftime('%H:%M:%S')}] {msg}")
    if len(bot_config["logs"]) > 6: bot_config["logs"].pop(0)

# --- محرك الـ 50 مؤشر والحماية ---
def perform_analysis(ticks):
    prices = ticks[-100:]
    curr = prices[-1]
    c_votes = sum(1 for i in range(2, 52) if curr > (sum(prices[-i:]) / i))
    p_votes = 50 - c_votes
    
    # حساب المناطق (دعم/مقاومة)
    candles = [{"o": ticks[i], "c": ticks[i+59], "h": max(ticks[i:i+60]), "l": min(ticks[i:i+60])} for i in range(0, len(ticks)-60, 60)]
    res = [c['h'] for i, c in enumerate(candles[1:]) if candles[i-1]['c'] > candles[i-1]['o'] and c['c'] < c['o']]
    sup = [c['l'] for i, c in enumerate(candles[1:]) if candles[i-1]['c'] < candles[i-1]['o'] and c['c'] > c['o']]
    
    n_res = any(abs(curr - r) < (curr * 0.0001) for r in res[-3:])
    n_sup = any(abs(curr - s) < (curr * 0.0001) for s in sup[-3:])
    
    c_pct, p_pct = (c_votes/50)*100, (p_votes/50)*100
    bot_config["strength"] = max(c_pct, p_pct)
    
    if c_pct >= 70 and not n_res:
        bot_config.update({"isSignal": True, "direction": "CALL 🟢", "timestamp": time.time(), "entryTime": (datetime.now() + timedelta(minutes=1)).strftime("%H:%M:00")})
        add_log(f"SIGNAL: CALL ({c_pct}%)")
    elif p_pct >= 70 and not n_sup:
        bot_config.update({"isSignal": True, "direction": "PUT 🔴", "timestamp": time.time(), "entryTime": (datetime.now() + timedelta(minutes=1)).strftime("%H:%M:00")})
        add_log(f"SIGNAL: PUT ({p_pct}%)")
    else:
        bot_config["isSignal"] = False
        add_log(f"SCAN COMPLETE: {max(c_pct, p_pct)}%")

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
    <title>KHOURY MASTER V5</title>
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <style>
        body { background: #06070a; color: #00f3ff; font-family: monospace; text-align: center; padding: 15px; }
        .card { border: 1px solid #00f3ff; border-radius: 15px; padding: 20px; background: rgba(0,243,255,0.02); }
        .btn { padding: 12px; width: 45%; border: 1px solid #00f3ff; background: transparent; color: #00f3ff; border-radius: 8px; font-weight: bold; cursor: pointer; }
        .logs { background: #000; height: 100px; overflow-y: auto; text-align: left; padding: 10px; color: #39ff14; font-size: 11px; margin-top: 15px; border: 1px solid #111; }
    </style>
</head>
<body>
    <div class="card">
        <h3>50-INDICATOR SYSTEM</h3>
        <select id="asset" style="width:100%; padding:10px; background:#000; color:#00f3ff; margin-bottom:15px;">
            <option value="frxEURUSD">EUR/USD</option>
            <option value="frxEURJPY">EUR/JPY</option>
        </select>
        <div id="clk" style="font-size: 35px; margin: 15px;">00:00:00</div>
        <button class="btn" onclick="ctl('start')">START SCAN</button>
        <button class="btn" onclick="ctl('stop')" style="border-color:red; color:red;">STOP</button>
        <div id="disp" style="margin: 25px 0; font-size: 18px; color: white;">READY</div>
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
                disp.innerHTML = d.show ? `<b style="color:#39ff14">${d.signal}</b><br><small>${d.strength}% - ${d.entry}</small>` : "ANALYZING MARKET...";
            } else { disp.innerHTML = "SYSTEM OFFLINE"; }
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
    add_log("SCANNER STARTED" if bot_config["isRunning"] else "STOPPED")
    return jsonify({"ok": True})

@app.route('/api/status')
def get_status():
    show = (time.time() - bot_config["timestamp"]) < 45 and bot_config["timestamp"] > 0
    return jsonify({"run": bot_config["isRunning"], "show": show, "signal": bot_config["direction"], "strength": bot_config["strength"], "entry": bot_config["entryTime"], "logs": bot_config["logs"]})

if __name__ == "__main__":
    threading.Thread(target=ws_worker, daemon=True).start()
    app.run(host='0.0.0.0', port=int(os.environ.get("PORT", 5000)))
