import os
import json
import websocket
import pandas as pd
import time
from threading import Thread
from flask import Flask, render_template_string, jsonify, request
from pymongo import MongoClient
from bson.objectid import ObjectId

app = Flask(__name__)

# --- إعدادات MongoDB ---
MONGO_URI = "mongodb+srv://charbelnk111_db_user:Mano123mano@cluster0.2gzqkc8.mongodb.net/?appName=Cluster0"
client = MongoClient(MONGO_URI)
db = client['KHOURY_V6_FIXED'] 
users_col = db['users']

DERIV_WS_URL = "wss://blue.derivws.com/websockets/v3?app_id=16929"

# --- منطق التحليل (5 تيك) ---
def compute_logic(df):
    if len(df) < 20: return "NONE"
    c = df['close']
    delta = c.diff()
    gain = (delta.where(delta > 0, 0)).rolling(window=14).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(window=14).mean()
    rsi = 100 - (100 / (1 + (gain / loss)))
    sma = c.rolling(window=10).mean()
    
    r_curr, p_curr, s_curr = rsi.iloc[-1], c.iloc[-1], sma.iloc[-1]
    
    if r_curr > 70 and p_curr < s_curr: return "CALL" # Reversed to PUT
    if r_curr < 30 and p_curr > s_curr: return "PUT"  # Reversed to CALL
    return "NONE"

# --- فحص وتحديث الصفقات (إصلاح مشكلة عدم ظهور النتيجة) ---
def check_trade_status(user_id):
    try:
        user = users_col.find_one({"_id": ObjectId(user_id)})
        if not user or not user.get("active_contract"): return

        ws = websocket.create_connection(DERIV_WS_URL)
        ws.send(json.dumps({"authorize": user['token']}))
        ws.recv()
        ws.send(json.dumps({"proposal_open_contract": 1, "contract_id": user['active_contract']}))
        res = json.loads(ws.recv())
        ws.close()

        contract = res.get("proposal_open_contract")
        if contract and contract.get("is_sold"):
            profit = float(contract.get("profit", 0))
            new_total = user['total_profit'] + profit
            
            if profit > 0:
                update_data = {
                    "active_contract": None,
                    "total_profit": new_total,
                    "wins": user['wins'] + 1,
                    "current_stake": user['base_stake'],
                    "consecutive_losses": 0,
                    "status": "WIN - WAITING"
                }
            else:
                update_data = {
                    "active_contract": None,
                    "total_profit": new_total,
                    "losses": user['losses'] + 1,
                    "consecutive_losses": user['consecutive_losses'] + 1,
                    "current_stake": round(user['current_stake'] * 2.1, 2),
                    "status": "LOSS - RETRYING"
                }
            
            # محو البيانات عند النهاية
            if update_data["consecutive_losses"] >= 4 or new_total >= user['take_profit']:
                users_col.delete_one({"_id": ObjectId(user_id)})
            else:
                users_col.update_one({"_id": ObjectId(user_id)}, {"$set": update_data})
    except Exception as e:
        print(f"Trade Sync Error: {e}")

def user_loop(user_id):
    while True:
        try:
            user = users_col.find_one({"_id": ObjectId(user_id)})
            if not user: break

            if user.get("active_contract"):
                check_trade_status(user_id)
                time.sleep(2)
                continue

            # التحليل الفني
            ws = websocket.create_connection(DERIV_WS_URL, timeout=10)
            ws.send(json.dumps({"ticks_history": user['selected_asset'], "count": 200, "end": "latest", "style": "ticks"}))
            res = json.loads(ws.recv())
            ws.close()

            if 'history' in res:
                ticks = pd.DataFrame(res['history']['prices'], columns=['close'])
                signal = compute_logic(ticks.iloc[::5])
                
                if signal != "NONE":
                    side = "PUT" if signal == "CALL" else "CALL"
                    # تنفيذ الصفقة
                    ws = websocket.create_connection(DERIV_WS_URL)
                    ws.send(json.dumps({"authorize": user['token']}))
                    auth = json.loads(ws.recv())
                    if "authorize" in auth:
                        ws.send(json.dumps({
                            "buy": 1, "price": user['current_stake'],
                            "parameters": {
                                "amount": user['current_stake'], "basis": "stake", "contract_type": side,
                                "currency": auth['authorize']['currency'], "duration": 10, "duration_unit": "t", "symbol": user['selected_asset']
                            }
                        }))
                        trade_res = json.loads(ws.recv())
                        if "buy" in trade_res:
                            users_col.update_one({"_id": ObjectId(user_id)}, {"$set": {
                                "active_contract": trade_res["buy"]["contract_id"],
                                "status": f"IN TRADE ({side})"
                            }})
                    ws.close()
            
            time.sleep(1)
        except: time.sleep(5)

# --- واجهة المستخدم (HTML مصلح) ---
HTML = """
<!DOCTYPE html>
<html>
<head>
    <title>KHOURY V6 FIXED</title>
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <style>
        body { background: #05080a; color: white; font-family: sans-serif; display: flex; justify-content: center; padding: 20px; }
        .card { background: #0d1117; padding: 25px; border-radius: 15px; border: 1px solid #30363d; width: 350px; text-align: center; }
        input, select { width: 100%; padding: 10px; margin: 5px 0; background: #010409; color: #58a6ff; border: 1px solid #30363d; box-sizing: border-box; }
        .btn { width: 100%; padding: 15px; border: none; border-radius: 8px; font-weight: bold; cursor: pointer; margin-top: 10px; }
        .start { background: #238636; color: white; }
        .stop { background: #da3633; color: white; }
        .stat-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 10px; margin-top: 20px; }
        .stat { background: #161b22; padding: 10px; border-radius: 8px; border: 1px solid #30363d; }
    </style>
</head>
<body>
    <div class="card">
        <h2 style="color:#58a6ff">KHOURY PRO V6</h2>
        <input type="text" id="u" placeholder="Session Name">
        <input type="password" id="t" placeholder="Deriv API Token">
        <select id="a">
            <option value="R_100">Volatility 100</option>
            <option value="R_75">Volatility 75</option>
            <option value="1HZ100V">Volatility 100 (1s)</option>
        </select>
        <div style="display:flex; gap:5px">
            <input type="number" id="s" value="1.00">
            <input type="number" id="tp" value="5.00">
        </div>
        <div id="status" style="margin:15px 0; color:#f1e05a; font-weight:bold;">READY</div>
        <button id="btn" class="btn start" onclick="action()">START BOT</button>
        <div class="stat-grid">
            <div class="stat">Wins: <b id="win">0</b></div>
            <div class="stat">Profit: <b id="prof">0.00</b></div>
        </div>
    </div>
    <script>
        async function action() {
            const user = document.getElementById('u').value;
            const token = document.getElementById('t').value;
            const btn = document.getElementById('btn');
            const type = btn.classList.contains('start') ? 'start' : 'stop';
            
            await fetch('/manage', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({
                    username: user, token: token, asset: document.getElementById('a').value,
                    stake: document.getElementById('s').value, tp: document.getElementById('tp').value,
                    action: type
                })
            });
            if(type === 'stop') location.reload();
        }

        async function sync() {
            const user = document.getElementById('u').value;
            if(!user) return;
            const r = await fetch('/data/' + user);
            const d = await r.json();
            const btn = document.getElementById('btn');
            if(d.found) {
                btn.innerText = "STOP & PURGE";
                btn.className = "btn stop";
                document.getElementById('status').innerText = d.status;
                document.getElementById('win').innerText = d.wins;
                document.getElementById('prof').innerText = d.total_profit.toFixed(2);
            } else {
                btn.innerText = "START BOT";
                btn.className = "btn start";
            }
        }
        setInterval(sync, 1000);
    </script>
</body>
</html>
"""

@app.route('/')
def home(): return render_template_string(HTML)

@app.route('/data/<username>')
def get_data(username):
    u = users_col.find_one({"username": username})
    if not u: return jsonify({"found": False})
    u['_id'] = str(u['_id']); u['found'] = True; return jsonify(u)

@app.route('/manage', methods=['POST'])
def manage():
    req = request.json
    if req['action'] == 'stop':
        users_col.delete_one({"username": req['username']})
        return jsonify({"s": "ok"})
    
    if not users_col.find_one({"username": req['username']}):
        uid = users_col.insert_one({
            "username": req['username'], "token": req['token'], "selected_asset": req['asset'],
            "base_stake": float(req['stake']), "current_stake": float(req['stake']),
            "take_profit": float(req['tp']), "wins": 0, "losses": 0, "total_profit": 0.0,
            "consecutive_losses": 0, "active_contract": None, "status": "ANALYZING"
        }).inserted_id
        Thread(target=user_loop, args=(str(uid),), daemon=True).start()
    return jsonify({"s": "ok"})

if __name__ == "__main__":
    app.run(host='0.0.0.0', port=5000)
