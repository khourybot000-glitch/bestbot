import os, json, time, threading, websocket
from flask import Flask, jsonify, render_template_string, request
from datetime import datetime

app = Flask(__name__)

bot_config = {
    "isRunning": False, "isSignal": False, "direction": "", 
    "up_count": 0, "down_count": 0, "pair_id": "R_100", 
    "pair_name": "Volatility 100", "timestamp": 0, "logs": ["V27 | PRECISE 7/6 SNIPER"]
}

ASSETS = {"R_100": "Volatility 100", "frxEURUSD": "EUR/USD", "frxEURJPY": "EUR/JPY"}

def add_log(msg):
    bot_config["logs"].append(f"[{time.strftime('%H:%M:%S')}] {msg}")
    if len(bot_config["logs"]) > 5: bot_config["logs"].pop(0)

def analyze_v27_precise(ticks):
    try:
        if len(ticks) < 65: return
        # نأخذ آخر 65 تيك لضمان تحليل 13 شمعة كاملة
        target_ticks = ticks[-65:]
        
        up_candles = 0
        down_candles = 0
        
        # تقسيم الـ 65 تيك إلى 13 شمعة
        for i in range(0, 65, 5):
            segment = target_ticks[i:i+5]
            if len(segment) < 2: continue
            
            open_p = segment[0]
            close_p = segment[-1]
            
            if close_p > open_p: up_candles += 1
            elif close_p < open_p: down_candles += 1
        
        bot_config["up_count"] = up_candles
        bot_config["down_count"] = down_candles
        
        is_sig = False
        direction = ""

        # المنطق الصارم: 7 مقابل 6 بالضبط
        if up_candles == 7 and down_candles == 6:
            direction = "CALL 🟢"
            is_sig = True
        elif down_candles == 7 and up_candles == 6:
            direction = "PUT 🔴"
            is_sig = True

        if is_sig:
            bot_config.update({"direction": direction, "isSignal": True, "timestamp": time.time()})
            add_log(f"TARGET HIT: {direction} (7 vs 6)")
        else:
            bot_config["isSignal"] = False
            add_log(f"Scan: U:{up_candles} D:{down_candles} (No Match)")

    except Exception as e: add_log("Logic Error")

def sniper_worker():
    while True:
        try:
            now = datetime.now()
            if bot_config["isRunning"] and now.second == 55:
                ws = websocket.create_connection("wss://ws.binaryws.com/websockets/v3?app_id=1089", timeout=10)
                ws.send(json.dumps({"ticks_history": bot_config["pair_id"], "count": 70, "style": "ticks"}))
                res = json.loads(ws.recv())
                if "history" in res:
                    analyze_v27_precise(res["history"]["prices"])
                ws.close()
                time.sleep(10)
        except: time.sleep(1)
        time.sleep(0.5)

UI = """
<!DOCTYPE html>
<html>
<head>
    <title>KHOURY V27 PRECISE</title>
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <style>
        body { background: #040508; color: #00f3ff; font-family: 'Segoe UI', sans-serif; text-align: center; padding: 15px; }
        .card { border: 2px solid #00f3ff; padding: 25px; border-radius: 30px; max-width: 380px; margin: auto; background: #0a0e14; box-shadow: 0 0 30px #00f3ff22; }
        .counter-grid { display: flex; justify-content: center; gap: 20px; margin: 20px 0; }
        .num-box { background: #000; border: 1px solid #333; padding: 10px 20px; border-radius: 15px; }
        .val { display: block; font-size: 28px; font-weight: bold; }
        .signal { font-size: 48px; min-height: 70px; margin: 20px 0; font-weight: 900; letter-spacing: 2px; }
        .btn { padding: 15px; width: 45%; border: 1px solid #00f3ff; background: transparent; color: #00f3ff; border-radius: 12px; cursor: pointer; font-weight: bold; }
        .logs { background: #000; height: 100px; padding: 10px; font-size: 11px; overflow-y: auto; color: #39ff14; border-radius: 10px; text-align: left; border: 1px solid #1a1a1a; margin-top: 15px; }
    </style>
</head>
<body>
    <div class="card">
        <h2 style="margin:0;">PRECISE SNIPER</h2>
        <p style="font-size:11px; color:#555;">13 CANDLES (5-TICK EACH)</p>
        
        <select id="asset" style="width:100%; padding:12px; background:#000; color:#00f3ff; border-radius:10px; margin:15px 0;">
            <option value="R_100">Volatility 100 Index</option>
            <option value="frxEURUSD">EUR/USD</option>
        </select>

        <div class="counter-grid">
            <div class="num-box" style="border-color:#39ff14;">
                <span style="font-size:10px; color:#39ff14;">GREEN</span>
                <span class="val" id="upC">0</span>
            </div>
            <div class="num-box" style="border-color:#ff4757;">
                <span style="font-size:10px; color:#ff4757;">RED</span>
                <span class="val" id="dnC">0</span>
            </div>
        </div>

        <button class="btn" onclick="send('start')" style="color:#39ff14; border-color:#39ff14;">START</button>
        <button class="btn" onclick="send('stop')" style="color:#ff4757; border-color:#ff4757;">STOP</button>

        <div class="signal" id="sig">...</div>
        <div class="logs" id="lBox"></div>
    </div>
    <script>
        async function send(a) { await fetch(`/api/cmd?action=${a}&pair=${document.getElementById('asset').value}`); }
        setInterval(async () => {
            const r = await fetch('/api/status');
            const d = await r.json();
            document.getElementById('upC').innerText = d.up;
            document.getElementById('dnC').innerText = d.down;
            const s = document.getElementById('sig');
            if(d.run) {
                if(d.show && d.isSig) { s.innerHTML = d.sig_dir; s.style.color = d.sig_dir.includes('🟢') ? '#39ff14' : '#ff4757'; }
                else { s.innerHTML = "<span style='color:#222'>READY</span>"; }
            } else { s.innerHTML = "OFFLINE"; }
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
    show = (time.time() - bot_config["timestamp"]) < 30
    return jsonify({
        "run": bot_config["isRunning"], "show": show, "isSig": bot_config["isSignal"],
        "sig_dir": bot_config["direction"], "up": bot_config["up_count"], 
        "down": bot_config["down_count"], "logs": bot_config["logs"]
    })

if __name__ == "__main__":
    threading.Thread(target=sniper_worker, daemon=True).start()
    app.run(host='0.0.0.0', port=int(os.environ.get("PORT", 5000)))
