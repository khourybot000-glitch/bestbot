import os, json, time, threading, websocket
from flask import Flask, jsonify, render_template_string, request
from datetime import datetime, timedelta

app = Flask(__name__)

bot_config = {
    "isRunning": False, "isSignal": False, "direction": "", 
    "win_rate": 0, "pair_id": "frxEURUSD", "pair_name": "EUR/USD",
    "timestamp": 0, "logs": ["SYSTEM READY - ALWAYS ON MODE"]
}

ASSETS = {"frxEURUSD": "EUR/USD", "frxEURJPY": "EUR/JPY", "frxEURGBP": "EUR/GBP"}

def add_log(msg):
    bot_config["logs"].append(f"[{time.strftime('%H:%M:%S')}] {msg}")
    if len(bot_config["logs"]) > 6: bot_config["logs"].pop(0)

def analyze_full_force(ticks, asset_id):
    global bot_config
    try:
        # بناء الشموع الـ 20
        candles = []
        for i in range(0, 1200, 60):
            segment = ticks[i:i+60]
            if len(segment) > 0: candles.append({'close': segment[-1]})
        
        closes = [c['close'] for c in candles[-20:]]
        curr_price = closes[-1]
        
        c_votes = 0
        p_votes = 0
        
        # تحليل الـ 50 مؤشر
        for p in range(2, 52):
            period = min(p, len(closes))
            sma = sum(closes[-period:]) / period
            if curr_price > sma: c_votes += 1
            else: p_votes += 1
        
        # اختيار الاتجاه الأقوى دائماً
        win_pct = (max(c_votes, p_votes) / 50) * 100
        direction = "CALL 🟢" if c_votes >= p_votes else "PUT 🔴"
        
        bot_config.update({
            "direction": direction,
            "win_rate": win_pct,
            "isSignal": True,
            "timestamp": time.time(),
            "pair_name": ASSETS.get(asset_id, "Unknown")
        })
        add_log(f"SIGNAL READY: {direction} ({win_pct}%)")
            
    except Exception as e:
        add_log(f"Analysis Error: {str(e)}")

def sniper_worker():
    while True:
        try:
            now = datetime.now()
            # التحليل يبدأ عند الثانية 49 من كل دقيقة
            if bot_config["isRunning"] and now.second == 49:
                add_log("Connecting...")
                ws = websocket.create_connection("wss://blue.derivws.com/websockets/v3?app_id=16929", timeout=10)
                ws.send(json.dumps({"ticks_history": bot_config["pair_id"], "count": 1200, "style": "ticks"}))
                
                res = json.loads(ws.recv())
                if "history" in res:
                    analyze_full_force(res["history"]["prices"], bot_config["pair_id"])
                
                ws.close()
                time.sleep(10) # انتظار لعدم التكرار في نفس الدقيقة
        except Exception as e:
            add_log(f"Re-connecting...")
            time.sleep(1)
        time.sleep(0.5)

UI = """
<!DOCTYPE html>
<html>
<head>
    <title>KHOURY ALWAYS-ON V15</title>
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <style>
        body { background: #05070a; color: #00f3ff; font-family: 'Segoe UI', sans-serif; text-align: center; padding: 15px; }
        .card { border: 2px solid #00f3ff; padding: 20px; border-radius: 20px; max-width: 380px; margin: auto; background: #0a0e14; box-shadow: 0 0 20px rgba(0,243,255,0.2); }
        .btn { padding: 12px; width: 45%; border: 1px solid #00f3ff; background: transparent; color: #00f3ff; border-radius: 10px; cursor: pointer; font-weight: bold; }
        .win-rate { font-size: 50px; color: #39ff14; font-weight: bold; margin: 10px 0; }
        .logs { background: #000; height: 110px; padding: 10px; font-size: 11px; overflow-y: auto; color: #39ff14; border-radius: 10px; text-align: left; margin-top: 15px; border: 1px solid #1a1a1a; }
        #mainDisp { min-height: 180px; display: flex; flex-direction: column; justify-content: center; border-top: 1px solid #222; margin-top: 15px; }
    </style>
</head>
<body>
    <div class="card">
        <h2 style="margin:0 0 10px 0;">ALWAYS-ON V15</h2>
        <select id="asset" style="width:100%; padding:10px; background:#000; color:#00f3ff; border:1px solid #333; margin-bottom:15px; border-radius:8px;">
            <option value="frxEURUSD">EUR/USD</option>
            <option value="frxEURJPY">EUR/JPY</option>
            <option value="frxEURGBP">EUR/GBP</option>
        </select>
        <div id="clk" style="font-size: 35px; margin-bottom: 15px; font-family: monospace; color:#fff;">00:00:00</div>
        <button class="btn" onclick="sendCmd('start')" style="color:#39ff14; border-color:#39ff14;">START BOT</button>
        <button class="btn" onclick="sendCmd('stop')" style="color:#ff4757; border-color:#ff4757;">STOP BOT</button>
        <div id="mainDisp">READY</div>
        <div class="logs" id="lBox"></div>
    </div>
    <script>
        async function sendCmd(a) { 
            const pair = document.getElementById('asset').value;
            await fetch(`/api/cmd?action=${a}&pair=${pair}`); 
        }
        setInterval(async () => {
            document.getElementById('clk').innerText = new Date().toLocaleTimeString('en-GB');
            try {
                const r = await fetch('/api/status');
                const d = await r.json();
                const disp = document.getElementById('mainDisp');
                if(d.run) {
                    if(d.show) {
                        disp.innerHTML = `<h3 style="margin:0; color:#aaa;">${d.pair}</h3>
                                         <b style="font-size:32px; color:#fff;">${d.signal}</b>
                                         <div class="win-rate">${d.win_rate.toFixed(1)}%</div>
                                         <small style="color:#ff4757">NEXT SIGNAL IN 1 MIN</small>`;
                    } else {
                        disp.innerHTML = "<div style='color:#555'>ANALYZING MARKET...<br><small>Signal every minute at Sec 50</small></div>";
                    }
                } else { disp.innerHTML = "SYSTEM OFFLINE"; }
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
    add_log(f"Bot {request.args.get('action').upper()}")
    return jsonify({"ok": True})

@app.route('/api/status')
def get_status():
    # تختفي بعد 30 ثانية
    show = (time.time() - bot_config["timestamp"]) < 30 and bot_config["timestamp"] > 0
    return jsonify({
        "run": bot_config["isRunning"], "show": show, 
        "signal": bot_config["direction"], "win_rate": bot_config["win_rate"],
        "pair": bot_config["pair_name"], "logs": bot_config["logs"]
    })

if __name__ == "__main__":
    threading.Thread(target=sniper_worker, daemon=True).start()
    app.run(host='0.0.0.0', port=int(os.environ.get("PORT", 5000)))
