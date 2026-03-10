import os, json, time, threading, websocket
from flask import Flask, jsonify, render_template_string, request
from datetime import datetime

app = Flask(__name__)

bot_config = {
    "isRunning": False, "isSignal": False, "direction": "", 
    "win_rate": 0, "pair_id": "frxEURUSD", "pair_name": "EUR/USD",
    "timestamp": 0, "logs": ["SYSTEM READY"]
}

ASSETS = {"frxEURUSD": "EUR/USD", "frxEURJPY": "EUR/JPY", "frxEURGBP": "EUR/GBP"}

def add_log(msg):
    bot_config["logs"].append(f"[{time.strftime('%H:%M:%S')}] {msg}")
    if len(bot_config["logs"]) > 5: bot_config["logs"].pop(0)

def analyze_logic(ticks, asset_id):
    try:
        # بناء 20 شمعة
        candles = [ticks[i:i+60][-1] for i in range(0, 1200, 60) if len(ticks[i:i+60]) > 0]
        closes = candles[-20:]
        if len(closes) < 5: return

        curr = closes[-1]
        c_votes = sum(1 for p in range(2, 52) if curr > (sum(closes[-min(p, len(closes)):]) / min(p, len(closes))))
        
        win_pct = (c_votes / 50 * 100) if c_votes > 25 else ((50 - c_votes) / 50 * 100)
        direction = "CALL 🟢" if c_votes >= 25 else "PUT 🔴"

        bot_config.update({
            "direction": direction, "win_rate": win_pct, "isSignal": True,
            "timestamp": time.time(), "pair_name": ASSETS.get(asset_id, "Unknown")
        })
        add_log(f"SUCCESS: {direction} ({win_pct}%)")
    except Exception as e: add_log(f"Logic Error")

def sniper_worker():
    while True:
        try:
            now = datetime.now()
            # محاولة الاتصال تبدأ قبل الموعد بـ 3 ثواني لضمان الاستقرار
            if bot_config["isRunning"] and now.second == 47:
                add_log("Opening Socket...")
                # استخدام رابط بديل وأكثر استقراراً لـ Render
                ws = websocket.create_connection("wss://ws.binaryws.com/websockets/v3?app_id=1089", timeout=10)
                
                request_data = json.dumps({"ticks_history": bot_config["pair_id"], "count": 1200, "end": "latest", "style": "ticks"})
                ws.send(request_data)
                
                result = ws.recv()
                data = json.loads(result)
                
                if "history" in data:
                    add_log("Data Received!")
                    analyze_logic(data["history"]["prices"], bot_config["pair_id"])
                else:
                    add_log("Server Busy - Retrying")
                
                ws.close()
                time.sleep(10) # منع التكرار
        except Exception as e:
            # تقليل اللوغات المزعجة في حالة الفشل العادي
            time.sleep(1)
        time.sleep(0.5)

# --- الواجهة (تم تحسين استجابة الأزرار) ---
UI = """
<!DOCTYPE html>
<html>
<head>
    <title>KHOURY V16 FIXED</title>
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <style>
        body { background: #06080a; color: #00f3ff; font-family: sans-serif; text-align: center; padding: 15px; }
        .card { border: 2px solid #00f3ff; padding: 20px; border-radius: 20px; max-width: 380px; margin: auto; background: #0a0e14; }
        .btn { padding: 12px; width: 45%; border: 1px solid #00f3ff; background: transparent; color: #00f3ff; border-radius: 10px; cursor: pointer; font-weight: bold; }
        .logs { background: #000; height: 110px; padding: 10px; font-size: 11px; overflow-y: auto; color: #39ff14; border-radius: 10px; text-align: left; margin-top: 15px; border: 1px solid #1a1a1a; }
        #mainDisp { min-height: 160px; display: flex; flex-direction: column; justify-content: center; border-top: 1px solid #222; margin-top: 15px; }
    </style>
</head>
<body>
    <div class="card">
        <h2>V16 FIXED</h2>
        <select id="asset" style="width:100%; padding:10px; background:#000; color:#00f3ff; margin-bottom:15px;">
            <option value="frxEURUSD">EUR/USD</option>
            <option value="frxEURJPY">EUR/JPY</option>
        </select>
        <div id="clk" style="font-size: 35px; margin-bottom: 15px;">00:00:00</div>
        <button class="btn" onclick="send('start')" style="color:#39ff14;">START</button>
        <button class="btn" onclick="send('stop')" style="color:#ff4757;">STOP</button>
        <div id="mainDisp">READY</div>
        <div class="logs" id="lBox"></div>
    </div>
    <script>
        async function send(a) { await fetch(`/api/cmd?action=${a}&pair=${document.getElementById('asset').value}`); }
        setInterval(async () => {
            document.getElementById('clk').innerText = new Date().toLocaleTimeString('en-GB');
            const r = await fetch('/api/status');
            const d = await r.json();
            const disp = document.getElementById('mainDisp');
            if(d.run) {
                if(d.show) {
                    disp.innerHTML = `<h3>${d.pair}</h3><b style="font-size:30px; color:#fff">${d.sig}</b><div style="font-size:40px; color:#39ff14">${d.rate.toFixed(1)}%</div>`;
                } else { disp.innerHTML = "WAITING FOR SEC 47..."; }
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
    add_log("Bot Started" if bot_config["isRunning"] else "Bot Stopped")
    return jsonify({"ok": True})

@app.route('/api/status')
def get_status():
    show = (time.time() - bot_config["timestamp"]) < 35
    return jsonify({
        "run": bot_config["isRunning"], "show": show, 
        "sig": bot_config["direction"], "rate": bot_config["win_rate"],
        "pair": bot_config["pair_name"], "logs": bot_config["logs"]
    })

if __name__ == "__main__":
    threading.Thread(target=sniper_worker, daemon=True).start()
    app.run(host='0.0.0.0', port=int(os.environ.get("PORT", 5000)))
