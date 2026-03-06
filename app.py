import json
import websocket
import pandas as pd
import time
from threading import Thread
from flask import Flask, render_template_string, jsonify, request
from pymongo import MongoClient
from bson.objectid import ObjectId

app = Flask(__name__)

# --- MongoDB Configuration ---
MONGO_URI = "mongodb+srv://charbelnk111_db_user:Mano123mano@cluster0.2gzqkc8.mongodb.net/?appName=Cluster0"
client = MongoClient(MONGO_URI)
db = client['KHOURY_V6_2_2']
users_col = db['users']

DERIV_WS_URL = "wss://blue.derivws.com/websockets/v3?app_id=16929"

# --- Strategy: 30-Tick Trend Following ---
def compute_logic(ticks_list):
    df = pd.DataFrame(ticks_list, columns=['price'])
    if len(df) < 35: return "NONE"
    last_30 = df['price'].iloc[-30:].values
    if last_30[-1] > last_30[0]: return "CALL"
    if last_30[-1] < last_30[0]: return "PUT"
    return "NONE"

# --- Trade Lifecycle & Execution ---
def check_trade_status(user_id):
    try:
        user = users_col.find_one({"_id": ObjectId(user_id)})
        if not user or not user.get("active_contract"): return
        ws = websocket.create_connection(DERIV_WS_URL)
        ws.send(json.dumps({"authorize": user['token']}))
        ws.recv()
        ws.send(json.dumps({"proposal_open_contract": 1, "contract_id": user['active_contract']}))
        res = json.loads(ws.recv()); ws.close()
        contract = res.get("proposal_open_contract")
        if contract and contract.get("is_sold"):
            profit = float(contract.get("profit", 0))
            new_total = user.get('total_profit', 0) + profit
            update_data = {"active_contract": None, "total_profit": new_total}
            if profit > 0:
                update_data.update({"wins": user.get('wins', 0) + 1, "current_stake": user['base_stake'], "consecutive_losses": 0})
            else:
                losses = user.get('consecutive_losses', 0) + 1
                update_data.update({"losses": user.get('losses', 0) + 1, "consecutive_losses": losses, "current_stake": round(user['current_stake'] * 2.2, 2)})
                if losses >= 4 or new_total >= user.get('take_profit', 9999):
                    users_col.delete_one({"_id": ObjectId(user_id)})
                    return
            users_col.update_one({"_id": ObjectId(user_id)}, {"$set": update_data})
    except: pass

def user_loop(user_id):
    while True:
        try:
            user = users_col.find_one({"_id": ObjectId(user_id)})
            if not user: break
            if user.get("active_contract"):
                time.sleep(16)
                check_trade_status(user_id)
                continue
            if time.localtime().tm_sec != 30:
                time.sleep(0.5); continue
            ws = websocket.create_connection(DERIV_WS_URL, timeout=10)
            ws.send(json.dumps({"ticks_history": user['selected_asset'], "count": 50, "end": "latest", "style": "ticks"}))
            res = json.loads(ws.recv()); ws.close()
            if 'history' in res:
                signal = compute_logic(res['history']['prices'])
                if signal != "NONE":
                    ws = websocket.create_connection(DERIV_WS_URL)
                    ws.send(json.dumps({"authorize": user['token']}))
                    auth = json.loads(ws.recv())
                    if "authorize" in auth:
                        ws.send(json.dumps({"buy": 1, "price": user['current_stake'], "parameters": {"amount": user['current_stake'], "basis": "stake", "contract_type": signal, "currency": auth['authorize']['currency'], "duration": 5, "duration_unit": "t", "symbol": user['selected_asset']}}))
                        trade_res = json.loads(ws.recv())
                        if "buy" in trade_res:
                            users_col.update_one({"_id": ObjectId(user_id)}, {"$set": {"active_contract": trade_res["buy"]["contract_id"]}})
                    ws.close()
            time.sleep(1)
        except: time.sleep(5)

# --- Frontend with Auto-Update ---
HTML = """
<!DOCTYPE html>
<html>
<head><style>
    body{background:#05080a;color:white;font-family:sans-serif;padding:20px;display:flex;justify-content:center}
    .card{background:#0d1117;padding:25px;border-radius:15px;width:350px;text-align:center;border:1px solid #30363d}
    input,select{width:100%;padding:10px;margin:8px 0;background:#010409;color:#58a6ff;border:1px solid #30363d}
    .btn{width:100%;padding:15px;border:none;border-radius:8px;font-weight:bold;cursor:pointer;margin-top:10px}
    .start{background:#238636;color:white} .stop{background:#da3633;color:white}
    .stat-box{background:#161b22; padding:10px; border-radius:8px; margin-top:10px; text-align:left; border:1px solid #30363d}
</style></head>
<body>
    <div class="card">
        <h2 id="title">KHOURY PRO</h2>
        <input type="email" id="email" placeholder="Email Address">
        <div id="fields" style="display:none;">
            <input type="password" id="token" placeholder="API Token">
            <select id="asset"><option value="R_100">Volatility 100</option><option value="R_75">Volatility 75</option></select>
            <input type="number" id="stake" placeholder="Base Stake" value="1.00">
            <input type="number" id="tp" placeholder="Take Profit" value="5.00">
        </div>
        <button class="btn start" id="btn" onclick="process()">LOGIN</button>
        <div id="stats-container" style="display:none;">
            <div class="stat-box" id="live-stats">جاري التحميل...</div>
        </div>
    </div>
    <script>
        let updateInterval = null;

        async function fetchStats(email) {
            const res = await fetch('/check/' + email);
            const d = await res.json();
            if (d.found) {
                document.getElementById('live-stats').innerHTML = `
                    <b>Wins:</b> ${d.wins} <br>
                    <b>Losses:</b> ${d.losses} <br>
                    <b>Profit:</b> <span style="color:${d.total_profit >= 0 ? '#238636':'#da3633'}">${d.total_profit.toFixed(2)} USD</span><br>
                    <b>Status:</b> ${d.active_contract ? 'In Trade...' : 'Analyzing Market'}
                `;
            } else {
                // إذا لم يتم العثور على الجلسة (مثلاً تم الوصول للـ Stop Loss وحُذفت)
                clearInterval(updateInterval);
                location.reload(); 
            }
        }

        async function process() {
            const email = document.getElementById('email').value;
            const res = await fetch('/check/' + email);
            const d = await res.json();
            
            if (d.found) {
                document.getElementById('title').innerText = "LIVE DASHBOARD";
                document.getElementById('stats-container').style.display = 'block';
                document.getElementById('btn').innerText = "STOP SESSION";
                document.getElementById('btn').className = "btn stop";
                document.getElementById('btn').onclick = () => stopSession(email);
                
                // بدء التحديث التلقائي كل ثانية واحدة
                fetchStats(email);
                updateInterval = setInterval(() => fetchStats(email), 1000);
            } else {
                document.getElementById('fields').style.display = 'block';
                document.getElementById('btn').innerText = "START BOT";
                document.getElementById('btn').onclick = startBot;
            }
        }

        async function startBot() {
            const data = {
                email: document.getElementById('email').value,
                token: document.getElementById('token').value,
                asset: document.getElementById('asset').value,
                stake: document.getElementById('stake').value,
                tp: document.getElementById('tp').value
            };
            await fetch('/manage', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(data)});
            process(); // الانتقال فوراً لعرض الإحصائيات
        }

        async function stopSession(email) {
            clearInterval(updateInterval);
            await fetch('/manage', {method:'DELETE', headers:{'Content-Type':'application/json'}, body:JSON.stringify({email:email})});
            location.reload();
        }
    </script>
</body>
</html>
"""

@app.route('/')
def home(): return render_template_string(HTML)

@app.route('/check/<email>')
def check(email):
    u = users_col.find_one({"email": email})
    if u:
        u['_id'] = str(u['_id'])
        return jsonify({"found": True, **u})
    return jsonify({"found": False})

@app.route('/manage', methods=['POST', 'DELETE'])
def manage():
    if request.method == 'POST':
        req = request.json
        uid = users_col.insert_one({
            "email": req['email'], "token": req['token'], "selected_asset": req['asset'],
            "base_stake": float(req['stake']), "current_stake": float(req['stake']),
            "take_profit": float(req['tp']), "wins": 0, "losses": 0, "total_profit": 0.0,
            "consecutive_losses": 0, "active_contract": None
        }).inserted_id
        Thread(target=user_loop, args=(str(uid),), daemon=True).start()
    else:
        users_col.delete_one({"email": request.json['email']})
    return jsonify({"status": "ok"})

if __name__ == "__main__":
    app.run(host='0.0.0.0', port=5000)
