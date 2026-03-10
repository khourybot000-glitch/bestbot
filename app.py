import os, json, time, threading, websocket
from flask import Flask, jsonify, render_template_string, request
from datetime import datetime, timedelta

app = Flask(__name__)

bot_config = {
    "isRunning": False, "direction": "", "strength": 0, 
    "pair_id": "frxEURUSD", "pair_name": "EUR/USD",
    "timestamp": 0, "entryTime": "", "isSignal": False, "logs": []
}

ASSETS = {"frxEURUSD": "EUR/USD", "frxEURJPY": "EUR/JPY", "frxEURGBP": "EUR/GBP"}

def add_log(msg):
    bot_config["logs"].append(f"[{time.strftime('%H:%M:%S')}] {msg}")
    if len(bot_config["logs"]) > 5: bot_config["logs"].pop(0)

# --- محرك الـ 50 مؤشر (Voting Engine) ---
def calculate_50_indicators(prices):
    call_votes = 0
    put_votes = 0
    curr = prices[-1]
    
    # محاكاة 50 مؤشر عبر تغيير الإعدادات (RSI, SMA, EMA, ROC, Momentum)
    for i in range(2, 27): # 25 مؤشر متوسط متحرك (SMA)
        sma = sum(prices[-i:]) / i
        if curr > sma: call_votes += 1
        else: put_votes += 1
        
    for i in range(5, 30): # 25 مؤشر زخم وقوة نسبية (RSI/ROC)
        prev_p = prices[-i]
        if curr > prev_p: call_votes += 1
        else: put_votes += 1
    
    return call_votes, put_votes

# --- استخراج المناطق (60 Ticks per Candle) ---
def get_safe_zones(ticks):
    candles = []
    # تحويل التيكات لشموع
    for i in range(0, len(ticks)-60, 60):
        candles.append({"o": ticks[i], "c": ticks[i+59], "h": max(ticks[i:i+60]), "l": min(ticks[i:i+60])})
    
    # تحديد الدعم والمقاومة (كل شمعة عكس الثانية)
    supports = []
    resistances = []
    for i in range(1, len(candles)):
        if candles[i-1]['c'] < candles[i-1]['o'] and candles[i]['c'] > candles[i]['o']: # هابطة ثم صاعدة
            supports.append(candles[i]['l'])
        if candles[i-1]['c'] > candles[i-1]['o'] and candles[i]['c'] < candles[i]['o']: # صاعدة ثم هابطة
            resistances.append(candles[i]['h'])
            
    curr = ticks[-1]
    # الفلتر: هل السعر بعيد عن المناطق؟
    near_res = any(abs(curr - r) < (curr * 0.0002) for r in resistances[-5:])
    near_sup = any(abs(curr - s) < (curr * 0.0002) for s in supports[-5:])
    
    return near_sup, near_res

# --- المعالج النهائي ---
def perform_analysis(ticks, asset_id):
    global bot_config
    call_v, put_v = calculate_50_indicators(ticks[-100:])
    near_sup, near_res = get_safe_zones(ticks)
    
    # حساب النسب المئوية
    call_pct = (call_v / 50) * 100
    put_pct = (put_v / 50) * 100
    
    is_entry = False
    if call_pct >= 70 and not near_res:
        bot_config["direction"] = "CALL 🟢"
        bot_config["strength"] = call_pct
        is_entry = True
    elif put_pct >= 70 and not near_sup:
        bot_config["direction"] = "PUT 🔴"
        bot_config["strength"] = put_pct
        is_entry = True
        
    if is_entry:
        bot_config.update({
            "isSignal": True, "timestamp": time.time(),
            "pair_name": ASSETS.get(asset_id, "Unknown"),
            "entryTime": (datetime.now() + timedelta(minutes=1)).strftime("%H:%M:00")
        })
        add_log(f"Signal Found! Score: {max(call_pct, put_pct)}%")
    else:
        bot_config["isSignal"] = False
        bot_config["strength"] = max(call_pct, put_pct)
        add_log(f"Waiting... Best Score: {bot_config['strength']}%")

# --- WebSocket Worker ---
def ws_worker():
    while True:
        try:
            now = datetime.now()
            if bot_config["isRunning"] and now.second == 55:
                ws = websocket.create_connection("wss://blue.derivws.com/websockets/v3?app_id=16929", timeout=10)
                ws.send(json.dumps({"ticks_history": bot_config["pair_id"], "count": 1000, "style": "ticks"}))
                res = json.loads(ws.recv())
                if "history" in res:
                    perform_analysis(res["history"]["prices"], bot_config["pair_id"])
                ws.close()
                time.sleep(2)
        except: pass
        time.sleep(0.5)

# --- UI و المسارات (Flask) ---
UI = """
<!DOCTYPE html>
<html>
<head>
    <title>KHOURY 50-INDICATOR V4</title>
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <style>
        :root { --neon: #00f3ff; --green: #39ff14; --red: #ff4757; }
        body { background: #06070a; color: white; font-family: 'Courier New', monospace; text-align: center; display: flex; flex-direction: column; align-items: center; justify-content: center; min-height: 100vh; margin: 0; }
        .container { width: 90%; max-width: 400px; padding: 20px; border: 1px solid var(--neon); border-radius: 20px; background: rgba(0,243,255,0.02); }
        .clock { font-size: 40px; margin: 20px 0; color: var(--neon); text-shadow: 0 0 10px var(--neon); }
        .btn { padding: 12px 25px; border: 1px solid var(--neon); background: transparent; color: var(--neon); cursor: pointer; border-radius: 8px; font-weight: bold; margin: 5px; }
        .display { border: 1px solid #333; padding: 20px; margin: 20px 0; border-radius: 15px; min-height: 120px; }
        .logs { background: #000; height: 80px; font-size: 10px; overflow-y: auto; text-align: left; padding: 10px; color: var(--green); }
    </style>
</head>
<body>
    <div class="container">
        <h2>50-INDICATOR SYSTEM</h2>
        <select id="asset" style="background:#000; color:var(--neon); padding:10px; width:100%;">
            <option value="frxEURUSD">EUR/USD</option>
            <option value="frxEURJPY">EUR/JPY</option>
            <option value="frxEURGBP">EUR/GBP</option>
        </select>
        <div class="clock" id="clk">00:00:00</div>
        <button class="btn" style="border-color:var(--green); color:var(--green);" onclick="ctl('start')">START SCAN</button>
        <button class="btn" style="border-color:var(--red); color:var(--red);" onclick="ctl('stop')">STOP</button>
        <div class="display" id="mainDisp">READY</div>
        <div class="logs" id="lBox"></div>
    </div>
    <script>
        async function ctl(a) { await fetch(`/api/cmd?action=${a}&pair=${document.getElementById('asset').value}`); }
        setInterval(async () => {
            document.getElementById('clk').innerText = new Date().toTimeString().split(' ')[0];
            const r = await fetch('/api/status');
            const d = await r.json();
            const disp = document.getElementById('mainDisp');
            if(d.show) {
                disp.innerHTML = `<b>DIRECTION:</b> ${d.isSignal ? d.signal : "REJECTED"}<br><b>STRENGTH:</b> ${d.strength}%<br><b>TIME:</b> ${d.entry}`;
            } else { disp.innerHTML = d.run ? "ANALYZING MARKET..." : "OFFLINE"; }
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
    show = (time.time() - bot_config["timestamp"]) < 45 and bot_config["timestamp"] > 0
    return jsonify({
        "run": bot_config["isRunning"], "show": show, "isSignal": bot_config["isSignal"],
        "signal": bot_config["direction"], "strength": bot_config["strength"],
        "entry": bot_config["entryTime"], "logs": bot_config["logs"]
    })

if __name__ == "__main__":
    threading.Thread(target=ws_worker, daemon=True).start()
    app.run(host='0.0.0.0', port=int(os.environ.get("PORT", 5000)))
