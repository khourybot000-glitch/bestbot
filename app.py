import os, json, time, threading, websocket
import numpy as np
from flask import Flask, jsonify, render_template_string, request
from datetime import datetime

app = Flask(__name__)

bot_config = {
    "isRunning": False, "isSignal": False, "direction": "", 
    "rsi_val": 50.0, "ema_val": 0.0, "pair_id": "R_100", 
    "pair_name": "Volatility 100", "timestamp": 0, "logs": ["V25 | EMA 200 + RSI 5-TICK"]
}

ASSETS = {"R_100": "Volatility 100", "frxEURUSD": "EUR/USD"}

def add_log(msg):
    bot_config["logs"].append(f"[{time.strftime('%H:%M:%S')}] {msg}")
    if len(bot_config["logs"]) > 5: bot_config["logs"].pop(0)

# --- حساب RSI ---
def get_rsi(prices, period=20):
    if len(prices) < period + 1: return 50
    deltas = np.diff(prices)
    up = deltas.clip(min=0)
    down = -deltas.clip(max=0)
    ema_up = up[-period:].mean()
    ema_down = down[-period:].mean()
    if ema_down == 0: return 100
    rs = ema_up / ema_down
    return 100 - (100 / (1 + rs))

# --- حساب EMA ---
def get_ema(prices, period=200):
    if len(prices) < period: return prices[-1]
    multiplier = 2 / (period + 1)
    ema = sum(prices[:period]) / period
    for price in prices[period:]:
        ema = (price - ema) * multiplier + ema
    return ema

def analyze_v25(ticks, asset_id):
    try:
        # 1. تحويل التيكات لشموع (كل 5 تيكات شمعة واحدة)
        candles = [ticks[i:i+5][-1] for i in range(0, len(ticks), 5)]
        if len(candles) < 205: return

        # الشمعة الأخيرة (5 تيك)
        current_minute_ticks = ticks[-5:]
        open_p = current_minute_ticks[0]
        close_p = current_minute_ticks[-1]

        # حساب EMA 200 على الشموع
        ema_200 = get_ema(candles, 200)
        
        # حساب RSI عند الافتتاح والاغلاق (للشمعة الـ 5 تيك الأخيرة)
        rsi_open = get_rsi(candles[:-1] + [open_p], 20)
        rsi_close = get_rsi(candles[:-1] + [close_p], 20)

        bot_config["rsi_val"] = round(rsi_close, 2)
        bot_config["ema_val"] = round(ema_200, 4)

        is_sig = False
        direction = ""

        # شرط الصعود: فوق EMA + اختراق RSI 50 للأعلى
        if close_p > ema_200 and rsi_open < 50 and rsi_close >= 50:
            direction = "CALL 🟢"
            is_sig = True
        # شرط الهبوط: تحت EMA + اختراق RSI 50 للأسفل
        elif close_p < ema_200 and rsi_open > 50 and rsi_close <= 50:
            direction = "PUT 🔴"
            is_sig = True

        if is_sig:
            bot_config.update({"direction": direction, "isSignal": True, "timestamp": time.time()})
            add_log(f"SIGNAL: {direction} (Trend Match)")
        else:
            bot_config["isSignal"] = False
            trend = "UP" if close_p > ema_200 else "DOWN"
            add_log(f"Trend: {trend} | RSI: {bot_config['rsi_val']}")

    except Exception as e: add_log(f"Error")

def sniper_worker():
    while True:
        try:
            # بما أن الشمعة 5 تيكات، سنقوم بالتحليل كل 5 ثواني تقريباً بدلاً من دقيقة
            if bot_config["isRunning"]:
                ws = websocket.create_connection("wss://ws.binaryws.com/websockets/v3?app_id=1089", timeout=10)
                # نحتاج 210 شمعة * 5 تيكات = 1050 تيك
                ws.send(json.dumps({"ticks_history": bot_config["pair_id"], "count": 1100, "style": "ticks"}))
                res = json.loads(ws.recv())
                if "history" in res:
                    analyze_v25(res["history"]["prices"], bot_config["pair_id"])
                ws.close()
                time.sleep(4) # فحص كل 4-5 ثواني لملاحقة شموع الـ 5 تيك
        except: time.sleep(1)
        time.sleep(1)

UI = """
<!DOCTYPE html>
<html>
<head>
    <title>KHOURY V25 SCALPER</title>
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <style>
        body { background: #05070a; color: #00f3ff; font-family: monospace; text-align: center; padding: 15px; }
        .card { border: 2px solid #00f3ff; padding: 20px; border-radius: 20px; max-width: 380px; margin: auto; background: #0a0e14; }
        .data-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 10px; margin: 15px 0; font-size: 12px; }
        .val { color: #fff; font-weight: bold; }
        .signal { font-size: 40px; color: #39ff14; font-weight: bold; margin: 15px 0; min-height: 60px; }
        .btn { padding: 12px; width: 45%; border: 1px solid #00f3ff; background: transparent; color: #00f3ff; border-radius: 10px; cursor: pointer; }
        .logs { background: #000; height: 100px; padding: 10px; font-size: 11px; overflow-y: auto; color: #39ff14; border-radius: 8px; text-align: left; border: 1px solid #1a1a1a; }
    </style>
</head>
<body>
    <div class="card">
        <h3>SCALPER V25 (5-TICK)</h3>
        <select id="asset" style="width:100%; padding:10px; background:#000; color:#00f3ff; margin-bottom:15px; border-radius:8px;">
            <option value="R_100">Volatility 100 Index</option>
            <option value="frxEURUSD">EUR/USD</option>
        </select>
        
        <div class="data-grid">
            <div>EMA 200: <span class="val" id="ev">--</span></div>
            <div>RSI (20): <span class="val" id="rv">--</span></div>
        </div>

        <button class="btn" onclick="send('start')" style="border-color:#39ff14;">START</button>
        <button class="btn" onclick="send('stop')" style="border-color:#ff4757;">STOP</button>

        <div class="signal" id="sig">...</div>
        <div class="logs" id="lBox"></div>
    </div>
    <script>
        async function send(a) { await fetch(`/api/cmd?action=${a}&pair=${document.getElementById('asset').value}`); }
        setInterval(async () => {
            const r = await fetch('/api/status');
            const d = await r.json();
            document.getElementById('ev').innerText = d.ema;
            document.getElementById('rv').innerText = d.rsi;
            const s = document.getElementById('sig');
            if(d.run) {
                if(d.show && d.isSig) { s.innerHTML = d.sig_dir; }
                else { s.innerHTML = "<span style='color:#333'>SCANNING</span>"; }
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
    show = (time.time() - bot_config["timestamp"]) < 10 # الإشارة تختفي أسرع لأن الشموع سريعة
    return jsonify({
        "run": bot_config["isRunning"], "show": show, "isSig": bot_config["isSignal"],
        "sig_dir": bot_config["direction"], "rsi": bot_config["rsi_val"], 
        "ema": bot_config["ema_val"], "logs": bot_config["logs"]
    })

if __name__ == "__main__":
    threading.Thread(target=sniper_worker, daemon=True).start()
    app.run(host='0.0.0.0', port=int(os.environ.get("PORT", 5000)))
