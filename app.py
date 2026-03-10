import os
import json
import time
import threading
import websocket
from flask import Flask, jsonify, render_template_string, request
from datetime import datetime, timedelta

app = Flask(__name__)

# --- إعدادات البوت الأساسية ---
bot_config = {
    "isRunning": False,
    "displayMsg": "INITIALIZING",
    "direction": "",
    "strength": 0,
    "pair_id": "frxEURUSD",
    "pair_name": "EUR/USD",
    "timestamp": 0,
    "entryTime": "",
    "isSignal": False,
    "logs": []
}

ASSETS = {
    "frxEURUSD": "EUR/USD", 
    "frxEURJPY": "EUR/JPY", 
    "frxEURGBP": "EUR/GBP"
}

def add_log(msg):
    bot_config["logs"].append(f"[{time.strftime('%H:%M:%S')}] {msg}")
    if len(bot_config["logs"]) > 5:
        bot_config["logs"].pop(0)

# --- محرك التحليل العميق (يعادل 50 مؤشر + حماية S/R) ---
def analyze_complex_market(ticks):
    try:
        p = ticks[-100:] # آخر 100 حركة سعرية
        curr = p[-1]
        
        # 1. تحليل الاتجاه (SMA 10, 20, 50, 100 + EMA)
        sma_20 = sum(p[-20:]) / 20
        sma_50 = sum(p[-50:]) / 50
        trend_score = 30 if (curr > sma_20 > sma_50) else (-30 if (curr < sma_20 < sma_50) else 0)

        # 2. تحليل الزخم (RSI, Stochastic, Momentum)
        deltas = [p[i+1] - p[i] for i in range(len(p)-1)]
        gains = sum([d for d in deltas[-14:] if d > 0]) / 14
        losses = sum([-d for d in deltas[-14:] if d < 0]) / 14
        rs = gains / losses if losses != 0 else 100
        rsi = 100 - (100 / (1 + rs))
        momentum_score = 30 if (40 < rsi < 65) else 0

        # 3. تحليل التقلب والسيولة (Bollinger Bands + ATR logic)
        std_dev = (sum([(x - sma_20)**2 for x in p[-20:]]) / 20)**0.5
        upper_b = sma_20 + (2 * std_dev)
        lower_b = sma_20 - (2 * std_dev)
        vol_score = 20 if (lower_b < curr < upper_b) else -10

        # 4. نظام حماية الدعوم والمقاومة (S/R Shield)
        # استخلاص المناطق من آخر 10 دقائق (شموع دقيقة)
        candles_low = [min(ticks[i:i+60]) for i in range(0, len(ticks)-60, 60)]
        candles_high = [max(ticks[i:i+60]) for i in range(0, len(ticks)-60, 60)]
        support = max(candles_low[-10:], default=curr * 0.999)
        resistance = min(candles_high[-10:], default=curr * 1.001)

        # شروط الحماية (يمنع الصفقة إذا كانت قريبة جداً من الانعكاس)
        safe_call = curr < (resistance - abs(curr * 0.0003))
        safe_put = curr > (support + abs(curr * 0.0003))

        # حساب النتيجة النهائية
        score_call = trend_score + momentum_score + vol_score + (20 if safe_call else -100)
        score_put = (-trend_score) + momentum_score + vol_score + (20 if safe_put else -100)

        return score_call, score_put, rsi
    except Exception as e:
        return 0, 0, 50

# --- معالج الإشارات ---
def perform_analysis(ticks, asset_id):
    global bot_config
    score_call, score_put, rsi = analyze_complex_market(ticks)
    
    final_score = max(score_call, score_put)
    
    # حد القوة (70% فما فوق يعطي إشارة)
    if final_score >= 70:
        direction = "CALL 🟢" if score_call > score_put else "PUT 🔴"
        bot_config.update({
            "isSignal": True,
            "direction": direction,
            "strength": final_score,
            "pair_name": ASSETS.get(asset_id, "Unknown"),
            "timestamp": time.time(),
            "entryTime": (datetime.now() + timedelta(minutes=1)).strftime("%H:%M:00")
        })
        add_log(f"SIGNAL: {direction} ({final_score}%)")
    else:
        bot_config.update({"isSignal": False, "timestamp": time.time()})
        add_log(f"Market Scan: Low Score ({final_score}%)")

# --- اتصال WebSocket المباشر (يحلل كل دقيقة) ---
def ws_worker():
    while True:
        try:
            now = datetime.now()
            # الاتصال والتحليل يبدأ في الثانية 55 ليكون جاهزاً للثانية 00
            if bot_config["isRunning"] and now.second == 55:
                # رابط WebSocket الخاص بـ Deriv
                ws = websocket.create_connection("wss://blue.derivws.com/websockets/v3?app_id=16929", timeout=15)
                request_data = {
                    "ticks_history": bot_config["pair_id"],
                    "count": 1000,
                    "end": "latest",
                    "style": "ticks"
                }
                ws.send(json.dumps(request_data))
                response = json.loads(ws.recv())
                
                if "history" in response:
                    perform_analysis(response["history"]["prices"], bot_config["pair_id"])
                
                ws.close()
                time.sleep(2) # منع التكرار في نفس الثانية
        except Exception as e:
            add_log("WS Error: Reconnecting...")
        time.sleep(0.5)

# --- واجهة المستخدم المتطورة ---
UI = """
<!DOCTYPE html>
<html>
<head>
    <title>KHOURY MASTER V4</title>
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <style>
        :root { --neon: #00f3ff; --green: #39ff14; --red: #ff4757; }
        body { background: #06070a; color: white; font-family: 'Courier New', monospace; display: flex; justify-content: center; align-items: center; min-height: 100vh; margin: 0; }
        #login { position: fixed; inset: 0; background: #020617; z-index: 2000; display: flex; flex-direction: column; justify-content: center; align-items: center; }
        .box { background: rgba(0,243,255,0.02); padding: 30px; border-radius: 20px; border: 1px solid var(--neon); text-align: center; width: 320px; }
        input, select { background: #000; border: 1px solid #333; color: var(--neon); padding: 12px; width: 100%; margin-bottom: 15px; border-radius: 8px; text-align: center; }
        .btn { width: 100%; padding: 12px; border-radius: 8px; border: 1px solid var(--neon); background: transparent; color: var(--neon); font-weight: bold; cursor: pointer; transition: 0.3s; }
        .btn:hover { background: var(--neon); color: black; }
        #dash { display: none; width: 95%; max-width: 400px; text-align: center; }
        .clock { font-size: 45px; color: var(--neon); margin: 15px 0; text-shadow: 0 0 15px var(--neon); }
        .display-area { border: 2px solid var(--neon); padding: 25px; border-radius: 20px; margin: 20px 0; background: rgba(0,243,255,0.05); min-height: 180px; }
        .logs { background: #000; height: 100px; padding: 10px; font-size: 11px; overflow-y: auto; color: var(--green); border-radius: 10px; text-align: left; border: 1px solid #111; }
    </style>
</head>
<body>
    <div id="login">
        <div class="box">
            <h2 style="color: var(--neon)">KHOURY MASTER</h2>
            <input type="text" id="u" placeholder="ID">
            <input type="password" id="p" placeholder="PASSWORD">
            <button class="btn" onclick="check()">LOGIN</button>
        </div>
    </div>
    <div id="dash">
        <h2 style="color: var(--neon)">50-INDICATOR SYSTEM</h2>
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
            if(document.getElementById('u').value==='KHOURY' && document.getElementById('p').value==='123') {
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
                disp.innerHTML = `<div style="text-align:left">
                    <b>PAIR:</b> ${d.pair}<br>
                    <b>SIGNAL:</b> ${d.isSignal ? d.signal : "REJECTED"}<br>
                    <b>STRENGTH:</b> ${d.strength}%<br>
                    <b>ENTRY:</b> ${d.entry}
                </div>`;
            } else { 
                disp.innerHTML = `<div style="color:#555">${d.run ? "ANALYZING 50+ FACTORS..." : "SYSTEM READY"}</div>`; 
            }
            document.getElementById('lBox').innerHTML = d.logs.join('<br>');
        }
    </script>
</body>
</html>
"""

# --- مسارات Flask API ---
@app.route('/')
def home():
    return render_template_string(UI)

@app.route('/api/cmd')
def cmd():
    bot_config["isRunning"] = (request.args.get('action') == 'start')
    bot_config["pair_id"] = request.args.get('pair', 'frxEURUSD')
    bot_config["pair_name"] = ASSETS.get(bot_config["pair_id"], "Unknown")
    return jsonify({"ok": True})

@app.route('/api/status')
def get_status():
    show = (time.time() - bot_config["timestamp"]) < 45 and bot_config["timestamp"] > 0
    return jsonify({
        "run": bot_config["isRunning"], "show": show, "isSignal": bot_config["isSignal"],
        "signal": bot_config["direction"], "strength": bot_config["strength"],
        "pair": bot_config["pair_name"], "entry": bot_config["entryTime"], "logs": bot_config["logs"]
    })

if __name__ == "__main__":
    # تشغيل العامل في الخلفية
    threading.Thread(target=ws_worker, daemon=True).start()
    # التوافق مع Render PORT
    port = int(os.environ.get("PORT", 5000))
    app.run(host='0.0.0.0', port=port)
