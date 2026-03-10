import os, json, time, threading, websocket
from flask import Flask, jsonify, render_template_string, request
from datetime import datetime, timedelta

app = Flask(__name__)

bot_config = {
    "isRunning": False, "isSignal": False, "direction": "", 
    "win_rate": 0, "pair_id": "frxEURUSD", "pair_name": "EUR/USD",
    "timestamp": 0, "entryTime": "", "logs": ["SYSTEM READY"]
}

ASSETS = {"frxEURUSD": "EUR/USD", "frxEURJPY": "EUR/JPY", "frxEURGBP": "EUR/GBP"}

def add_log(msg):
    bot_config["logs"].append(f"[{time.strftime('%H:%M:%S')}] {msg}")
    if len(bot_config["logs"]) > 5: bot_config["logs"].pop(0)

# --- تحويل 1200 تيك إلى 20 شمعة دقيقة ---
def get_20_candles(ticks):
    candles = []
    for i in range(0, 1200, 60):
        segment = ticks[i:i+60]
        if len(segment) >= 60:
            candles.append({'close': segment[-1]})
    return candles[-20:] # التأكيد على 20 شمعة فقط

# --- محرك التحليل بـ 50 مؤشر (شرط الـ 70%) ---
def analyze_20_candles(ticks, asset_id):
    global bot_config
    try:
        candles = get_20_candles(ticks)
        closes = [c['close'] for c in candles]
        curr_price = closes[-1]
        
        c_votes = 0
        p_votes = 0
        
        # تشغيل 50 مؤشر (متوسطات متحركة بفترات مختلفة على الـ 20 شمعة)
        for p in range(2, 52):
            # إذا كان المؤشر أكبر من عدد الشموع المتاحة، نستخدم المتاح
            period = min(p, len(closes))
            sma = sum(closes[-period:]) / period
            if curr_price > sma: c_votes += 1
            else: p_votes += 1
        
        win_pct = (max(c_votes, p_votes) / 50) * 100
        
        # شرط الـ 70%: لا تصدر إشارة إذا كان الاتجاه ضعيفاً
        if win_pct >= 70:
            direction = "CALL 🟢" if c_votes >= p_votes else "PUT 🔴"
            bot_config.update({
                "isSignal": True, "direction": direction, "win_rate": win_pct,
                "pair_name": ASSETS.get(asset_id, "Unknown"),
                "timestamp": time.time(),
                "entryTime": (datetime.now() + timedelta(minutes=1)).strftime("%H:%M:00")
            })
            add_log(f"STRONG SIGNAL: {direction} ({win_pct}%)")
        else:
            bot_config["isSignal"] = False
            add_log(f"WEAK TREND: {win_pct}% (Below 70%)")
            
    except Exception as e:
        add_log(f"Error: {str(e)}")

def sniper_worker():
    while True:
        try:
            now = datetime.now()
            if bot_config["isRunning"] and now.second == 50:
                ws = websocket.create_connection("wss://blue.derivws.com/websockets/v3?app_id=16929", timeout=15)
                ws.send(json.dumps({"ticks_history": bot_config["pair_id"], "count": 1200, "style": "ticks"}))
                res = json.loads(ws.recv())
                if "history" in res:
                    analyze_20_candles(res["history"]["prices"], bot_config["pair_id"])
                ws.close()
                time.sleep(5)
        except: pass
        time.sleep(0.5)

UI = """
<!DOCTYPE html>
<html>
<head>
    <title>SNIPER V11 (70%+ ONLY)</title>
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <style>
        body { background: #05070a; color: #00f3ff; font-family: monospace; text-align: center; padding: 20px; }
        .card { border: 2px solid #00f3ff; padding: 25px; border-radius: 20px; max-width: 400px; margin: auto; background: rgba(0,243,255,0.03); }
        .win-rate { font-size: 45px; color: #39ff14; font-weight: bold; margin: 15px 0; }
        .btn { padding: 12px; width: 45%; border: 1px solid #00f3ff; background: transparent; color: #00f3ff; border-radius: 10px; cursor: pointer; }
        .logs { background: #000; height: 100px; padding: 10px; font-size: 11px; overflow-y: auto; color: #39ff14; border-radius: 10px; text-align: left; margin-top: 15px; border: 1px solid #111; }
    </style>
</head>
<body>
    <div class="card">
        <h2 style="margin-top:0">SNIPER V11</h2>
        <select id="asset" style="width:100%; padding:10px; background:#000; color:#00f3ff; margin-bottom:15px;">
            <option value="frxEURUSD">EUR/USD</option>
            <option value="frxEURJPY">EUR/JPY</option>
        </select>
        <div id="clk" style="font-size: 35px; margin: 10px;">00:00:00</div>
        <button class="btn" onclick="ctl('start')" style="color:#39ff14; border-color:#39ff14;">START</button>
        <button class="btn" onclick="ctl('stop')" style="color:red; border-color:red;">STOP</button>
        <div id="mainDisp" style="margin-top:25px; min-height:160px; border-top:1px solid #222; padding-top:20px;">READY</div>
        <div class="logs" id="lBox"></div>
    </div>
    <script>
        async function ctl(a) { await fetch(`/api/cmd?action=${a}&pair=${document.getElementById('asset').value}`); }
        setInterval(async () => {
            document.getElementById('clk').innerText = new Date().toTimeString().split(' ')[0];
            const r = await fetch('/api/status');
            const d = await r.json();
            const disp = document.getElementById('mainDisp');
            if(d.run) {
                if(d.show && d.isSignal) {
                    disp.innerHTML = `<h3>${d.pair}</h3><b style="font-size:24px">${d.signal}</b><div class="win-rate">${d.win_rate.toFixed(1)}%</div><small>EXPIRES SOON</small>`;
                } else {
                    disp.innerHTML = "<div style='color:#555'>SCANNING...<br><small>Only signals >= 70% are shown</small></div>";
                }
            } else { disp.innerHTML = "OFFLINE"; }
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
    return jsonify({"ok": True})

@app.route('/api/status')
def get_status():
    # الإشارة تختفي بعد 30 ثانية بالضبط من صدورها
    show = (time.time() - bot_config["timestamp"]) < 30 and bot_config["timestamp"] > 0
    return jsonify({
        "run": bot_config["isRunning"], "show": show, "isSignal": bot_config["isSignal"],
        "signal": bot_config["direction"], "win_rate": bot_config["win_rate"],
        "pair": bot_config["pair_name"], "logs": bot_config["logs"]
    })

if __name__ == "__main__":
    threading.Thread(target=sniper_worker, daemon=True).start()
    app.run(host='0.0.0.0', port=int(os.environ.get("PORT", 5000)))
