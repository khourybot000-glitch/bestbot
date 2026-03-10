import os, json, time, threading, websocket
from flask import Flask, jsonify, render_template_string, request
from datetime import datetime, timedelta

app = Flask(__name__)

bot_config = {
    "isRunning": False, "isSignal": False, "direction": "", 
    "win_rate": 0, "pair_id": "frxEURUSD", "timestamp": 0, 
    "entryTime": "", "logs": ["SYSTEM READY"]
}

def add_log(msg):
    bot_config["logs"].append(f"[{time.strftime('%H:%M:%S')}] {msg}")
    if len(bot_config["logs"]) > 6: bot_config["logs"].pop(0)

# --- محرك التحليل الاحترافي ---
def perform_analysis(ticks):
    try:
        # جلب آخر 1200 تيك كما طلبت
        prices = ticks[-1200:]
        curr = prices[-1]
        
        # تصويت الـ 50 مؤشر (حساب تقاطع السعر مع 50 متوسط مختلف)
        c_votes = sum(1 for i in range(10, 60) if curr > (sum(prices[-i:]) / i))
        p_votes = 50 - c_votes
        
        # حساب نسبة النجاح (Win Rate)
        if c_votes >= p_votes:
            bot_config["direction"] = "CALL 🟢"
            bot_config["win_rate"] = (c_votes / 50 * 100)
        else:
            bot_config["direction"] = "PUT 🔴"
            bot_config["win_rate"] = (p_votes / 50 * 100)

        bot_config["isSignal"] = True
        bot_config["timestamp"] = time.time()
        # وقت الدخول يكون الدقيقة القادمة (الثانية 00)
        bot_config["entryTime"] = (datetime.now() + timedelta(minutes=1)).strftime("%H:%M:00")
        
        add_log(f"SIGNAL READY: {bot_config['direction']} ({bot_config['win_rate']:.1f}%)")
    except Exception as e:
        add_log(f"Analysis Error: {str(e)}")

# --- نظام الاتصال المتقطع (عند الثانية 50 فقط) ---
def ws_worker():
    while True:
        try:
            now = datetime.now()
            # الاتصال يبدأ فقط عند الثانية 50
            if bot_config["isRunning"] and now.second == 50:
                add_log("CONNECTING (SEC 50)...")
                
                # فتح الاتصال
                ws = websocket.create_connection("wss://blue.derivws.com/websockets/v3?app_id=1089", timeout=10)
                
                # طلب 1200 تيك
                ws.send(json.dumps({
                    "ticks_history": bot_config["pair_id"], 
                    "count": 1200, 
                    "end": "latest", 
                    "style": "ticks"
                }))
                
                data = json.loads(ws.recv())
                
                if "history" in data:
                    perform_analysis(data["history"]["prices"])
                    add_log("DATA ANALYZED & DISCONNECTED.")
                
                # قطع الاتصال فوراً بعد جلب البيانات
                ws.close()
                
                # الانتظار لضمان عدم تكرار الاتصال في نفس الدقيقة
                time.sleep(10) 
        except Exception as e:
            add_log("Conn Error: Retrying next minute")
        time.sleep(0.5)

UI = """
<!DOCTYPE html>
<html>
<head>
    <title>KHOURY SNIPER V7</title>
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <style>
        body { background: #06070a; color: #00f3ff; font-family: 'Courier New', monospace; text-align: center; padding: 10px; }
        .card { border: 2px solid #00f3ff; border-radius: 20px; padding: 20px; background: rgba(0,243,255,0.05); max-width: 400px; margin: auto; box-shadow: 0 0 15px #00f3ff44; }
        .btn { padding: 12px; width: 48%; border: 1px solid #00f3ff; background: transparent; color: #00f3ff; border-radius: 10px; font-weight: bold; cursor: pointer; }
        .win-display { font-size: 35px; color: #39ff14; font-weight: bold; margin: 10px 0; }
        .logs { background: #000; height: 120px; overflow-y: auto; text-align: left; padding: 10px; color: #39ff14; font-size: 11px; margin-top: 15px; border-top: 1px solid #222; }
    </style>
</head>
<body>
    <div class="card">
        <h2>SNIPER V7 (50-IND)</h2>
        <select id="asset" style="width:100%; padding:10px; background:#000; color:#00f3ff; border:1px solid #00f3ff; margin-bottom:10px;">
            <option value="frxEURUSD">EUR/USD</option>
            <option value="frxEURJPY">EUR/JPY</option>
            <option value="frxGBPUSD">GBP/USD</option>
        </select>
        <div id="clk" style="font-size: 40px; margin: 15px; font-weight: bold;">00:00:00</div>
        <button class="btn" onclick="ctl('start')" style="border-color:#39ff14; color:#39ff14;">START BOT</button>
        <button class="btn" onclick="ctl('stop')" style="border-color:red; color:red;">STOP</button>
        
        <div id="mainDisp" style="margin: 20px 0; min-height: 120px; border: 1px dashed #333; padding: 15px; border-radius: 15px;">
            READY
        </div>
        
        <div class="logs" id="lBox"></div>
    </div>
    <script>
        async function ctl(a) { await fetch(`/api/cmd?action=${a}&pair=` + document.getElementById('asset').value); }
        setInterval(async () => {
            document.getElementById('clk').innerText = new Date().toTimeString().split(' ')[0];
            try {
                const r = await fetch('/api/status');
                const d = await r.json();
                const disp = document.getElementById('mainDisp');
                if(d.run) {
                    if(d.show) {
                        disp.innerHTML = `<b style="font-size:26px">${d.signal}</b><br>
                                         <div class="win-display">${d.win_rate.toFixed(1)}%</div>
                                         <small style="color:#aaa">ENTRY AT: ${d.entry}</small>`;
                    } else {
                        disp.innerHTML = "WAITING FOR SEC 50...<br><small>Sniper Mode: ON</small>";
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
    add_log("SNIPER ACTIVE" if bot_config["isRunning"] else "SNIPER STOPPED")
    return jsonify({"ok": True})

@app.route('/api/status')
def get_status():
    # تظهر الإشارة من الثانية 51 حتى الثانية 59 من الدقيقة التالية
    show = (time.time() - bot_config["timestamp"]) < 65 and bot_config["timestamp"] > 0
    return jsonify({
        "run": bot_config["isRunning"], "show": show, 
        "signal": bot_config["direction"], "win_rate": bot_config["win_rate"], 
        "entry": bot_config["entryTime"], "logs": bot_config["logs"]
    })

if __name__ == "__main__":
    threading.Thread(target=ws_worker, daemon=True).start()
    app.run(host='0.0.0.0', port=int(os.environ.get("PORT", 5000)))
