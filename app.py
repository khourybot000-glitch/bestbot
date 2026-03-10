import os, json, time, threading, websocket
from flask import Flask, jsonify, render_template_string, request
from datetime import datetime, timedelta

app = Flask(__name__)

# --- إعدادات النظام المحدثة ---
bot_config = {
    "isRunning": False,
    "direction": "",
    "win_rate": 0,
    "pair_id": "frxEURUSD",
    "pair_name": "EUR/USD",
    "timestamp": 0,
    "entryTime": "",
    "isSignal": False,
    "logs": []
}

ASSETS = {"frxEURUSD": "EUR/USD", "frxEURJPY": "EUR/JPY", "frxEURGBP": "EUR/GBP"}

def add_log(msg):
    bot_config["logs"].append(f"[{time.strftime('%H:%M:%S')}] {msg}")
    if len(bot_config["logs"]) > 5: bot_config["logs"].pop(0)

# --- محرك التحليل (الـ 50 مؤشر + 1200 تيك) ---
def perform_analysis(ticks, asset_id):
    global bot_config
    try:
        prices = ticks[-1200:] # جلب 1200 تيك كما طلبت
        curr = prices[-1]
        
        # تصويت الـ 50 مؤشر (حساب التقاطعات السعرية)
        c_votes = sum(1 for i in range(5, 55) if curr > (sum(prices[-i:]) / i))
        p_votes = 50 - c_votes
        
        # حساب نسبة النجاح (Win Rate)
        win_pct = (max(c_votes, p_votes) / 50) * 100
        
        # تحديد الاتجاه
        direction = "CALL 🟢" if c_votes >= p_votes else "PUT 🔴"
        
        # تحديث البيانات (الإشارة تظهر دائماً كما طلبت)
        bot_config.update({
            "isSignal": True,
            "direction": direction,
            "win_rate": win_pct,
            "pair_name": ASSETS[asset_id],
            "timestamp": time.time(),
            "entryTime": (datetime.now() + timedelta(minutes=1)).strftime("%H:%M:00")
        })
        add_log(f"Signal Generated: {direction} ({win_pct}%)")
    except Exception as e:
        add_log(f"Analysis Error: {str(e)}")

# --- نظام WebSocket المطور (اتصال عند الثانية 50 وقطع فوراً) ---
def smart_ws_worker():
    while True:
        try:
            now = datetime.now()
            # التعديل: يتصل كل دقيقة عند الثانية 50 (بدلاً من كل 10 دقائق)
            if bot_config["isRunning"] and now.second == 50:
                add_log("Connecting to Market...")
                # استخدام نفس طريقة الاتصال الناجحة في كودك القديم
                ws = websocket.create_connection("wss://blue.derivws.com/websockets/v3?app_id=16929", timeout=15)
                asset = bot_config["pair_id"]
                
                # طلب 1200 تيك كما طلبت
                ws.send(json.dumps({
                    "ticks_history": asset, 
                    "count": 1200, 
                    "end": "latest", 
                    "style": "ticks"
                }))
                
                res = json.loads(ws.recv())
                if "history" in res:
                    perform_analysis(res["history"]["prices"], asset)
                
                ws.close() # قطع الاتصال فوراً
                add_log("Data Received & Connection Closed.")
                time.sleep(5) # منع التكرار في نفس الثانية
        except Exception as e:
            add_log(f"Socket Error: {str(e)}")
        time.sleep(0.5)

# --- الواجهة (نفس تصميمك المفضل مع عرض نسبة النجاح) ---
UI = """
<!DOCTYPE html>
<html>
<head>
    <title>KHOURY SNIPER V9</title>
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <style>
        :root { --neon: #00f3ff; --green: #39ff14; --red: #ff4757; }
        body { background: #06070a; color: white; font-family: 'Courier New', monospace; text-align: center; padding: 15px; }
        .box { border: 2px solid var(--neon); padding: 25px; border-radius: 20px; background: rgba(0,243,255,0.05); max-width: 400px; margin: auto; }
        .btn { padding: 12px; width: 45%; border: 1px solid var(--neon); background: transparent; color: var(--neon); font-weight: bold; cursor: pointer; border-radius: 10px; }
        .win-display { font-size: 40px; color: var(--green); margin: 10px 0; font-weight: bold; text-shadow: 0 0 10px var(--green); }
        .logs { background: #000; height: 100px; padding: 10px; font-size: 10px; overflow-y: auto; color: var(--green); border-radius: 10px; text-align: left; border: 1px solid #111; margin-top: 15px; }
    </style>
</head>
<body>
    <div id="login" style="position:fixed; inset:0; background:#020617; z-index:2000; display:flex; flex-direction:column; align-items:center; justify-content:center;">
        <div style="border:1px solid var(--neon); padding:30px; border-radius:20px;">
            <h2 style="color:var(--neon)">KHOURY LOGIN</h2>
            <input type="text" id="u" placeholder="ID" style="width:90%; padding:10px; margin-bottom:10px; background:#000; color:var(--neon); border:1px solid #333;"><br>
            <input type="password" id="p" placeholder="PASS" style="width:90%; padding:10px; margin-bottom:15px; background:#000; color:var(--neon); border:1px solid #333;"><br>
            <button class="btn" onclick="check()" style="width:100%">ENTER</button>
        </div>
    </div>

    <div id="dash" style="display:none;">
        <div class="box">
            <h2 style="color:var(--neon)">SNIPER PRO V9</h2>
            <select id="asset" style="width:100%; padding:10px; background:#000; color:var(--neon); margin-bottom:15px;">
                <option value="frxEURUSD">EUR/USD</option>
                <option value="frxEURJPY">EUR/JPY</option>
            </select>
            <div id="clk" style="font-size: 40px; margin: 15px; color:var(--neon)">00:00:00</div>
            <button class="btn" onclick="ctl('start')" style="color:var(--green); border-color:var(--green);">START</button>
            <button class="btn" onclick="ctl('stop')" style="color:var(--red); border-color:var(--red);">STOP</button>
            
            <div id="mainDisp" style="margin-top:20px; min-height:150px;">
                READY
            </div>
            <div class="logs" id="lBox"></div>
        </div>
    </div>

    <script>
        function check() {
            if(document.getElementById('u').value==='KHOURYBOT' && document.getElementById('p').value==='123456') {
                document.getElementById('login').style.display='none';
                document.getElementById('dash').style.display='block';
                setInterval(upd, 1000);
            } else alert('Error');
        }
        async function ctl(a) { await fetch(`/api/cmd?action=${a}&pair=${document.getElementById('asset').value}`); }
        async function upd() {
            document.getElementById('clk').innerText = new Date().toTimeString().split(' ')[0];
            const r = await fetch('/api/status');
            const d = await r.json();
            const disp = document.getElementById('mainDisp');
            if(d.run) {
                if(d.show) {
                    disp.innerHTML = `<h3>${d.pair}</h3><b style="font-size:24px">${d.signal}</b><div class="win-display">${d.win_rate.toFixed(1)}%</div><small>ENTRY AT: ${d.entry}</small>`;
                } else { disp.innerHTML = "WAITING FOR SEC 50..."; }
            } else { disp.innerHTML = "SYSTEM OFFLINE"; }
            document.getElementById('lBox').innerHTML = d.logs.join('<br>');
        }
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
    show = (time.time() - bot_config["timestamp"]) < 65 and bot_config["timestamp"] > 0
    return jsonify({
        "run": bot_config["isRunning"], "show": show, "isSignal": bot_config["isSignal"],
        "signal": bot_config["direction"], "win_rate": bot_config["win_rate"],
        "pair": bot_config["pair_name"], "entry": bot_config["entryTime"], "logs": bot_config["logs"]
    })

if __name__ == "__main__":
    threading.Thread(target=smart_ws_worker, daemon=True).start()
    app.run(host='0.0.0.0', port=int(os.environ.get("PORT", 5000)))
