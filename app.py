import os
import json
import time
import threading
from flask import Flask, jsonify, render_template_string, request
import websocket
from datetime import datetime, timedelta

app = Flask(__name__)

bot_config = {
    "isRunning": False,
    "displayMsg": "WAITING",
    "direction": "",
    "strength": 0,
    "pair_name": "",
    "pair_id": "frxEURUSD",
    "timestamp": 0,
    "entryTime": "",
    "isSignal": False,
    "logs": []
}

ASSETS = {"frxEURUSD": "EUR/USD", "frxEURJPY": "EUR/JPY", "frxEURGBP": "EUR/GBP"}

def add_log(msg):
    bot_config["logs"].append(f"[{time.strftime('%H:%M:%S')}] {msg}")
    if len(bot_config["logs"]) > 5: bot_config["logs"].pop(0)

def perform_analysis(ticks, times, asset_id):
    global bot_config
    
    # 1. السعر الحالي اللحظي (عند ثانية 50)
    current_price = ticks[-1] 
    
    # 2. تحديد الأوقات المستهدفة
    now = datetime.now()
    # بداية شمعة الـ 10 دقائق (مثلاً 12:00:00)
    target_10m = now.replace(minute=(now.minute // 10) * 10, second=0, microsecond=0).timestamp()
    # بداية الدقيقة الأخيرة (مثلاً 12:09:00)
    target_1m = now.replace(second=0, microsecond=0).timestamp()
    
    price_at_10m = None
    price_at_1m = None

    # البحث العكسي (من الأحدث للأقدم) لضمان دقة "آخر" تيك في تلك اللحظة
    for i in range(len(times)-1, -1, -1):
        if price_at_1m is None and times[i] <= target_1m:
            price_at_1m = ticks[i]
        if price_at_10m is None and times[i] <= target_10m:
            price_at_10m = ticks[i]
            break # وجدنا الأبعد، نتوقف

    # تأمين البيانات في حال وجود فجوة (Gap)
    if price_at_1m is None: price_at_1m = ticks[-60]
    if price_at_10m is None: price_at_10m = ticks[0]

    # --- المنطق الحسابي الصارم ---
    # شمعة 10 دقائق: (السعر الآن) أكبر من (السعر قبل 10 دقائق)
    is_m10_up = current_price > price_at_10m
    is_m10_down = current_price < price_at_10m
    
    # شمعة الدقيقة الأخيرة: (السعر الآن) أكبر من (السعر قبل 50 ثانية)
    is_m1_up = current_price > price_at_1m
    is_m1_down = current_price < price_at_1m

    next_entry = (now + timedelta(seconds=10)).strftime("%H:%M")

    # تطبيق الفلتر المزدوج (يجب أن يتفقا تماماً)
    if is_m10_up and is_m1_up:
        bot_config.update({
            "displayMsg": "SIGNAL FOUND", "isSignal": True, "direction": "CALL 🟢",
            "strength": 98, "pair_name": ASSETS[asset_id],
            "timestamp": time.time(), "entryTime": next_entry
        })
        add_log(f"SUCCESS: M10 UP & M1 UP")
    elif is_m10_down and is_m1_down:
        bot_config.update({
            "displayMsg": "SIGNAL FOUND", "isSignal": True, "direction": "PUT 🔴",
            "strength": 98, "pair_name": ASSETS[asset_id],
            "timestamp": time.time(), "entryTime": next_entry
        })
        add_log(f"SUCCESS: M10 DOWN & M1 DOWN")
    else:
        # هنا الحالة التي حدثت معك: اختلاف الاتجاهين
        bot_config.update({
            "displayMsg": "NO SIGNAL", "isSignal": False,
            "timestamp": time.time(), "entryTime": ""
        })
        reason = "M10 UP but M1 DOWN" if is_m10_up else "M10 DOWN but M1 UP"
        add_log(f"REJECTED: {reason}")

def smart_ws_worker():
    while True:
        now = datetime.now()
        if bot_config["isRunning"] and (now.minute % 10 == 9) and (now.second == 50):
            try:
                ws = websocket.create_connection("wss://blue.derivws.com/websockets/v3?app_id=16929", timeout=15)
                ws.send(json.dumps({"ticks_history": bot_config["pair_id"], "count": 1000, "end": "latest", "style": "ticks"}))
                res = json.loads(ws.recv())
                if "history" in res:
                    perform_analysis(res["history"]["prices"], res["history"]["times"], bot_config["pair_id"])
                ws.close()
                time.sleep(5)
            except: pass
        time.sleep(0.5)

# --- واجهة المستخدم (تعديل M1 و LOGS) ---
UI = """
<!DOCTYPE html>
<html>
<head>
    <title>KHOURY V4 PRECISION</title>
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <style>
        :root { --neon: #00f3ff; --green: #39ff14; --red: #ff4757; }
        body { background: #010409; color: white; font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif; display: flex; justify-content: center; align-items: center; min-height: 100vh; margin: 0; }
        #login { position: fixed; inset: 0; background: #0d1117; z-index: 2000; display: flex; flex-direction: column; justify-content: center; align-items: center; }
        .box { background: #161b22; padding: 40px; border-radius: 20px; border: 1px solid var(--neon); text-align: center; width: 320px; box-shadow: 0 0 20px rgba(0,243,255,0.2); }
        input, select { background: #0d1117; border: 1px solid #30363d; color: var(--neon); padding: 12px; width: 100%; margin-bottom: 20px; border-radius: 8px; text-align: center; outline: none; }
        .btn { width: 100%; padding: 14px; border-radius: 8px; border: 1px solid var(--neon); background: transparent; color: var(--neon); font-weight: bold; cursor: pointer; transition: 0.3s; }
        .btn:hover { background: var(--neon); color: black; }
        #dash { display: none; width: 95%; max-width: 420px; text-align: center; }
        .clock { font-size: 45px; color: var(--neon); margin: 20px 0; font-family: monospace; text-shadow: 0 0 10px var(--neon); }
        .display-area { border: 2px solid var(--neon); padding: 30px; border-radius: 25px; margin: 20px 0; background: rgba(0,243,255,0.02); min-height: 220px; display: flex; flex-direction: column; justify-content: center; }
        .sig-text { font-size: 16px; line-height: 2; text-align: left; font-family: monospace; }
        .logs { background: #000; height: 100px; padding: 10px; font-family: monospace; font-size: 11px; overflow-y: auto; color: var(--green); border-radius: 10px; text-align: left; border: 1px solid #30363d; }
    </style>
</head>
<body>
    <div id="login">
        <div class="box">
            <h2 style="color: var(--neon)">KHOURY PRO V4</h2>
            <input type="text" id="u" placeholder="USERNAME">
            <input type="password" id="p" placeholder="PASSWORD">
            <button class="btn" onclick="check()">LOGIN</button>
        </div>
    </div>
    <div id="dash">
        <h2 style="color: var(--neon)">KHOURY INTELLIGENCE</h2>
        <select id="asset">
            <option value="frxEURUSD">EUR/USD</option>
            <option value="frxEURJPY">EUR/JPY</option>
            <option value="frxEURGBP">EUR/GBP</option>
        </select>
        <div class="clock" id="clk">00:00:00</div>
        <div style="display:flex; gap:10px; margin-bottom:20px;">
            <button class="btn" style="color:var(--green); border-color:var(--green);" onclick="ctl('start')">START</button>
            <button class="btn" style="color:var(--red); border-color:var(--red);" onclick="ctl('stop')">STOP</button>
        </div>
        <div class="display-area" id="mainDisp"></div>
        <div class="logs" id="lBox"></div>
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
            if(d.show) {
                if(d.isSignal) {
                    disp.innerHTML = `<div class="sig-text">
                        <span style="color:var(--neon)">PAIR:</span> ${d.pair}<br>
                        <span style="color:var(--neon)">DIRECTION:</span> ${d.signal}<br>
                        <span style="color:var(--neon)">ACCURACY:</span> 98%<br>
                        <span style="color:var(--neon)">TIME FRAME:</span> M1<br>
                        <span style="color:var(--neon)">ENTRY TIME:</span> ${d.entry}
                    </div>`;
                } else { disp.innerHTML = `<div style="font-size:32px; color:#444">NO SIGNAL</div>`; }
            } else { 
                let wait = 9 - (new Date().getMinutes() % 10);
                disp.innerHTML = `<div style="color:#333; font-size:14px;">ANALYZING TREND... (${wait}m remaining)</div>`; 
            }
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
    bot_config["pair_name"] = ASSETS[bot_config["pair_id"]]
    return jsonify({"ok": True})

@app.route('/api/status')
def get_status():
    show = (time.time() - bot_config["timestamp"]) < 40 and bot_config["timestamp"] > 0
    return jsonify({
        "run": bot_config["isRunning"], "show": show, "isSignal": bot_config["isSignal"],
        "signal": bot_config["direction"], "strength": bot_config["strength"],
        "pair": bot_config["pair_name"], "entry": bot_config["entryTime"], "logs": bot_config["logs"]
    })

if __name__ == "__main__":
    threading.Thread(target=smart_ws_worker, daemon=True).start()
    app.run(host='0.0.0.0', port=int(os.environ.get("PORT", 5000)))
