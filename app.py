import os
import json
import websocket
import pandas as pd
import time
from threading import Thread
from flask import Flask, render_template_string, jsonify, request

app = Flask(__name__)

# --- الإعدادات الفنية ---
DERIV_WS_URL = "wss://blue.derivws.com/websockets/v3?app_id=16929"
ASSETS = ["R_100", "R_75", "R_50", "R_10"]

# متغيرات الحالة العالمية
bot_data = {
    "is_running": False,
    "token": "",
    "base_stake": 1.0,
    "current_stake": 1.0,
    "take_profit": 10.0,
    "status": "WAITING",
    "wins": 0,
    "losses": 0,
    "total_profit": 0.0,
    "consecutive_losses": 0,
    "active_contract": None,
    "check_time": 0
}

def compute_logic(df):
    """منطق القناص: الاختراق يجب أن يكون في آخر 60 تيك (آخر شمعتين)"""
    if len(df) < 60: return "NONE"
    c = df['close']
    
    # حساب RSI 14
    delta = c.diff()
    gain = (delta.where(delta > 0, 0)).rolling(window=14).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(window=14).mean()
    rsi = 100 - (100 / (1 + (gain / loss)))
    
    # حساب EMA 50
    ema50 = c.ewm(span=50, adjust=False).mean()
    
    # تعريف المؤشرات للشموع الحالية والسابقة
    r_curr = rsi.iloc[-1]      # الشمعة الحالية
    r_prev1 = rsi.iloc[-2]     # الشمعة السابقة
    r_prev2 = rsi.iloc[-3]     # الشمعة ما قبل السابقة
    
    p_curr = c.iloc[-1]
    e_curr = ema50.iloc[-1]
    
    # شرط الشراء (CALL): الاختراق في آخر 60 تيك (بين الحالية والسابقة OR السابقة وما قبلها)
    is_call_crossover = (r_prev1 <= 50 and r_curr > 50) or (r_prev2 <= 50 and r_prev1 > 50)
    if is_call_crossover and p_curr > e_curr:
        return "CALL"
    
    # شرط البيع (PUT): الاختراق لأسفل في آخر 60 تيك
    is_put_crossover = (r_prev1 >= 50 and r_curr < 50) or (r_prev2 >= 50 and r_prev1 < 50)
    if is_put_crossover and p_curr < e_curr:
        return "PUT"
        
    return "NONE"

def bot_loop():
    global bot_data
    while True:
        if not bot_data["is_running"]:
            time.sleep(1)
            continue

        # 1. فحص نتيجة الصفقة (بعد 5 دقائق و 6 ثواني)
        if bot_data["active_contract"] and time.time() >= bot_data["check_time"]:
            check_trade_result()

        # 2. المسح الرباعي عند الثانية 00 (فقط إذا كان البوت في حالة انتظار)
        if bot_data["status"] == "WAITING" and not bot_data["active_contract"]:
            if time.strftime("%S") == "00":
                scan_markets()
                time.sleep(1) 
        
        time.sleep(0.5)

def scan_markets():
    global bot_data
    for asset in ASSETS:
        try:
            ws = websocket.create_connection(DERIV_WS_URL)
            # طلب 3000 تيك لضمان جودة البيانات الفنية
            ws.send(json.dumps({"ticks_history": asset, "count": 3000, "end": "latest", "style": "ticks"}))
            res = json.loads(ws.recv())
            ws.close()
            
            ticks = pd.DataFrame(res['history']['prices'], columns=['close'])
            candles = ticks.iloc[::30].copy() # تجميع الشموع كل 30 تيك
            
            signal = compute_logic(candles)
            
            if signal != "NONE":
                place_trade(asset, signal)
                break # التوقف فوراً عن فحص باقي الأزواج فور دخول صفقة
        except: continue

def place_trade(asset, side):
    global bot_data
    try:
        ws = websocket.create_connection(DERIV_WS_URL)
        ws.send(json.dumps({"authorize": bot_data["token"]}))
        ws.recv()
        
        # ضمان إرسال المبلغ بخانتين عشريتين فقط
        final_stake = round(bot_data["current_stake"], 2)
        
        trade_req = {
            "buy": 1, "price": final_stake,
            "parameters": {
                "amount": final_stake, "basis": "stake",
                "contract_type": side, "currency": "USD",
                "duration": 5, "duration_unit": "m", "symbol": asset
            }
        }
        ws.send(json.dumps(trade_req))
        res = json.loads(ws.recv())
        ws.close()

        if "buy" in res:
            bot_data["active_contract"] = res["buy"]["contract_id"]
            bot_data["status"] = f"IN TRADE ({asset})"
            bot_data["check_time"] = time.time() + 306 
    except: pass

def check_trade_result():
    global bot_data
    try:
        ws = websocket.create_connection(DERIV_WS_URL)
        ws.send(json.dumps({"authorize": bot_data["token"]}))
        ws.recv()
        ws.send(json.dumps({"proposal_open_contract": 1, "contract_id": bot_data["active_contract"]}))
        res = json.loads(ws.recv())
        ws.close()

        contract = res["proposal_open_contract"]
        if contract["is_sold"]:
            status = contract["status"]
            if status == "won":
                bot_data["total_profit"] += contract["profit"]
                bot_data["wins"] += 1
                bot_data["current_stake"] = round(bot_data["base_stake"], 2)
                bot_data["consecutive_losses"] = 0
            else:
                bot_data["total_profit"] -= bot_data["current_stake"]
                bot_data["losses"] += 1
                bot_data["consecutive_losses"] += 1
                # مضاعفة × 2.2 مع التقريب لخانين
                bot_data["current_stake"] = round(bot_data["current_stake"] * 2.2, 2)
            
            bot_data["active_contract"] = None
            bot_data["status"] = "WAITING"

            # شروط التوقف الإجباري
            if bot_data["consecutive_losses"] >= 4:
                bot_data["is_running"] = False
                bot_data["status"] = "STOPPED: MAX LOSS (4)"
            elif bot_data["total_profit"] >= bot_data["take_profit"]:
                bot_data["is_running"] = False
                bot_data["status"] = "STOPPED: TARGET REACHED"
    except: pass

HTML_TEMPLATE = """
<!DOCTYPE html>
<html>
<head>
    <title>KHOURY V5 PRO SNIPER</title>
    <style>
        :root { --bg: #05080a; --blue: #58a6ff; --green: #238636; --red: #da3633; --card: #0d1117; }
        body { background: var(--bg); color: #e6edf3; font-family: 'Segoe UI', sans-serif; display: flex; flex-direction: column; align-items: center; padding: 20px; }
        .container { background: var(--card); padding: 25px; border-radius: 20px; width: 420px; border: 1px solid #30363d; box-shadow: 0 10px 40px rgba(0,0,0,0.6); }
        h2 { text-align: center; color: var(--blue); letter-spacing: 1.5px; margin-bottom: 25px; }
        label { font-size: 11px; color: #8b949e; font-weight: bold; text-transform: uppercase; }
        input { width: 100%; padding: 12px; margin: 8px 0 15px 0; background: #010409; color: #79c0ff; border: 1px solid #30363d; border-radius: 8px; box-sizing: border-box; }
        .status-bar { background: #161b22; padding: 15px; border-radius: 10px; text-align: center; margin-bottom: 20px; border: 1px solid #30363d; color: #f1e05a; font-weight: bold; }
        .grid { display: grid; grid-template-columns: 1fr 1fr; gap: 15px; margin-bottom: 15px; }
        .card { background: #21262d; padding: 15px; border-radius: 12px; text-align: center; border: 1px solid #30363d; }
        .btn { width: 100%; padding: 16px; border: none; border-radius: 10px; font-weight: bold; cursor: pointer; font-size: 16px; margin-bottom: 10px; }
        .start { background: var(--green); color: white; }
        .stop { background: var(--red); color: white; }
        .reset { background: #30363d; color: #8b949e; font-size: 12px; padding: 8px; margin-top: 5px; }
    </style>
</head>
<body>
    <div class="container">
        <h2>KHOURY V5 PRO</h2>
        
        <label>Deriv API Token</label>
        <input type="password" id="token" placeholder="Paste Token with Trade Access">
        
        <div class="grid">
            <div>
                <label>Initial Stake ($)</label>
                <input type="number" id="stake" value="1.00" step="0.01">
            </div>
            <div>
                <label>Target Profit ($)</label>
                <input type="number" id="tp" value="10.00">
            </div>
        </div>

        <div class="status-bar" id="status">STATUS: WAITING</div>

        <button id="mainBtn" class="btn start" onclick="toggleBot()">START AUTO-TRADING</button>
        <button class="btn reset" onclick="resetStats()">RESET STATISTICS</button>

        <div class="grid" style="margin-top: 20px;">
            <div class="card">
                <label style="color:#3fb950;">Wins</label><br>
                <b id="wins" style="font-size:22px;">0</b>
            </div>
            <div class="card">
                <label style="color:#f85149;">Losses</label><br>
                <b id="losses" style="font-size:22px;">0</b>
            </div>
            <div class="card" style="grid-column: span 2; border-top: 2px solid var(--blue);">
                <label style="color:var(--blue);">Net Profit / Loss</label><br>
                <b id="profit" style="font-size:28px;">$ 0.00</b>
            </div>
        </div>
    </div>

    <script>
        async function updateUI() {
            try {
                const res = await fetch('/get_data');
                const data = await res.json();
                document.getElementById('status').innerText = "STATUS: " + data.status;
                document.getElementById('wins').innerText = data.wins;
                document.getElementById('losses').innerText = data.losses;
                document.getElementById('profit').innerText = "$ " + data.total_profit.toFixed(2);
                
                const btn = document.getElementById('mainBtn');
                if(data.is_running) {
                    btn.innerText = "STOP BOT";
                    btn.className = "btn stop";
                } else {
                    btn.innerText = "START AUTO-TRADING";
                    btn.className = "btn start";
                }
            } catch(e) {}
        }

        function toggleBot() {
            const token = document.getElementById('token').value;
            const stake = document.getElementById('stake').value;
            const tp = document.getElementById('tp').value;
            if(!token) { alert("Please enter API Token"); return; }
            fetch('/toggle', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({token, stake, tp})
            });
        }

        function resetStats() {
            if(confirm("Are you sure you want to reset all data?")) fetch('/reset');
        }
        setInterval(updateUI, 1000);
    </script>
</body>
</html>
"""

@app.route('/')
def index(): return render_template_string(HTML_TEMPLATE)

@app.route('/get_data')
def get_data(): return jsonify(bot_data)

@app.route('/reset')
def reset():
    global bot_data
    bot_data.update({"wins": 0, "losses": 0, "total_profit": 0.0, "consecutive_losses": 0, "current_stake": bot_data["base_stake"]})
    return jsonify({"ok": True})

@app.route('/toggle', methods=['POST'])
def toggle():
    global bot_data
    req = request.json
    if not bot_data["is_running"]:
        bot_data.update({
            "token": req['token'],
            "base_stake": float(req['stake']),
            "current_stake": float(req['stake']),
            "take_profit": float(req['tp']),
            "is_running": True,
            "status": "WAITING"
        })
    else:
        bot_data["is_running"] = False
    return jsonify({"ok": True})

if __name__ == "__main__":
    Thread(target=bot_loop, daemon=True).start()
    # تشغيل البوت على المنفذ 5000
    app.run(host='0.0.0.0', port=5000)
