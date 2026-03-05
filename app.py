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
db = client['khoury_bot_v5_reversed']
users_col = db['users']

DERIV_WS_URL = "wss://blue.derivws.com/websockets/v3?app_id=16929"

# --- منطق التحليل الفني (2 تيك) ---
def compute_logic(df):
    if len(df) < 20: return "NONE"
    c = df['close']
    delta = c.diff()
    gain = (delta.where(delta > 0, 0)).rolling(window=14).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(window=14).mean()
    rsi = 100 - (100 / (1 + (gain / loss)))
    ema_fast = c.ewm(span=20, adjust=False).mean()
    
    r_curr, r_prev1, r_prev2 = rsi.iloc[-1], rsi.iloc[-2], rsi.iloc[-3]
    p_curr, e_curr = c.iloc[-1], ema_fast.iloc[-1]
    
    # الإشارات الأصلية
    if ((r_prev1 <= 50 and r_curr > 50) or (r_prev2 <= 50 and r_prev1 > 50)) and p_curr > e_curr:
        return "CALL"
    if ((r_prev1 >= 50 and r_curr < 50) or (r_prev2 >= 50 and r_prev1 < 50)) and p_curr < e_curr:
        return "PUT"
    return "NONE"

def user_bot_logic(user_id):
    while True:
        try:
            user = users_col.find_one({"_id": ObjectId(user_id)})
            if not user: break
            if not user.get("is_running"):
                time.sleep(1)
                continue
            if user.get("active_contract"):
                check_and_update_trade(user_id)
                time.sleep(1)
                continue

            scan_selected_asset(user_id, user.get("selected_asset"))
            time.sleep(0.5)
        except:
            time.sleep(2)

def scan_selected_asset(user_id, asset):
    try:
        ws = websocket.create_connection(DERIV_WS_URL)
        ws.send(json.dumps({"ticks_history": asset, "count": 200, "end": "latest", "style": "ticks"}))
        res = json.loads(ws.recv())
        ws.close()
        
        ticks = pd.DataFrame(res['history']['prices'], columns=['close'])
        signal = compute_logic(ticks.iloc[::2])
        
        if signal != "NONE":
            # --- منطق عكس الإشارة هنا ---
            reversed_signal = "PUT" if signal == "CALL" else "CALL"
            place_deriv_trade(user_id, asset, reversed_signal)
    except: pass

def place_deriv_trade(user_id, asset, side):
    user = users_col.find_one({"_id": ObjectId(user_id)})
    if not user: return
    try:
        ws = websocket.create_connection(DERIV_WS_URL)
        ws.send(json.dumps({"authorize": user['token']}))
        auth_res = json.loads(ws.recv())
        currency = auth_res['authorize'].get('currency', 'USD')
        stake = round(user['current_stake'], 2)
        
        trade_req = {
            "buy": 1, "price": stake,
            "parameters": {
                "amount": stake, "basis": "stake", "contract_type": side,
                "currency": currency, "duration": 10, "duration_unit": "t", "symbol": asset
            }
        }
        ws.send(json.dumps(trade_req))
        res = json.loads(ws.recv())
        ws.close()

        if "buy" in res:
            users_col.update_one({"_id": ObjectId(user_id)}, {"$set": {
                "active_contract": res["buy"]["contract_id"],
                "status": f"IN REVERSE TRADE ({asset})",
                "currency": currency
            }})
    except: pass

def check_and_update_trade(user_id):
    user = users_col.find_one({"_id": ObjectId(user_id)})
    if not user: return
    try:
        ws = websocket.create_connection(DERIV_WS_URL)
        ws.send(json.dumps({"authorize": user['token']}))
        ws.recv()
        ws.send(json.dumps({"proposal_open_contract": 1, "contract_id": user['active_contract']}))
        res = json.loads(ws.recv())
        ws.close()

        contract = res["proposal_open_contract"]
        if contract["is_sold"]:
            new_data = {"active_contract": None, "status": "WAITING"}
            if contract["status"] == "won":
                new_data["total_profit"] = user["total_profit"] + contract["profit"]
                new_data["wins"] = user["wins"] + 1
                new_data["current_stake"] = round(user["base_stake"], 2)
                new_data["consecutive_losses"] = 0
            else:
                new_data["total_profit"] = user["total_profit"] - user["current_stake"]
                new_data["losses"] = user["losses"] + 1
                new_data["consecutive_losses"] = user["consecutive_losses"] + 1
                new_data["current_stake"] = round(user["current_stake"] * 2.2, 2)
            
            if new_data["consecutive_losses"] >= 4 or new_data.get("total_profit", 0) >= user["take_profit"]:
                users_col.delete_one({"_id": ObjectId(user_id)})
                return

            users_col.update_one({"_id": ObjectId(user_id)}, {"$set": new_data})
    except: pass

# --- الواجهة (HTML) ---
HTML_TEMPLATE = """
<!DOCTYPE html>
<html>
<head>
    <title>KHOURY REVERSE V5</title>
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <style>
        :root { --bg: #05080a; --card: #0d1117; --blue: #ff9f43; --green: #238636; --red: #da3633; }
        body { background: var(--bg); color: white; font-family: sans-serif; display: flex; justify-content: center; padding: 20px; }
        .panel { background: var(--card); padding: 25px; border-radius: 20px; width: 100%; max-width: 400px; border: 1px solid #30363d; }
        input, select { width: 100%; padding: 12px; margin: 8px 0; background: #010409; color: #ff9f43; border: 1px solid #30363d; border-radius: 8px; box-sizing: border-box; }
        .status-box { background: #161b22; padding: 15px; border-radius: 10px; text-align: center; margin: 15px 0; border: 1px solid #30363d; color: #f1e05a; font-weight: bold; }
        .btn { width: 100%; padding: 15px; border: none; border-radius: 10px; font-weight: bold; cursor: pointer; margin-top: 10px; }
        .start { background: var(--green); color: white; }
        .stop { background: var(--red); color: white; }
        .stats { display: grid; grid-template-columns: 1fr 1fr; gap: 10px; margin-top: 20px; }
        .stat-card { background: #21262d; padding: 12px; border-radius: 10px; text-align: center; border: 1px solid #30363d; }
    </style>
</head>
<body>
    <div class="panel">
        <h2 style="text-align:center; color:var(--blue);">KHOURY REVERSE V5</h2>
        <input type="text" id="user" placeholder="Session Name">
        <input type="password" id="token" placeholder="Deriv API Token">
        
        <select id="asset">
            <option value="R_100">Volatility 100 Index</option>
            <option value="R_75">Volatility 75 Index</option>
            <option value="R_50">Volatility 50 Index</option>
            <option value="R_10">Volatility 10 Index</option>
            <option value="1HZ100V">Volatility 100 (1s) Index</option>
            <option value="1HZ10V">Volatility 10 (1s) Index</option>
        </select>

        <div style="display:flex; gap:10px;">
            <input type="number" id="stake" placeholder="Stake" value="1.00">
            <input type="number" id="tp" placeholder="Target" value="10.00">
        </div>
        <div class="status-box" id="ui_status">REVERSE MODE: ON</div>
        <button class="btn start" id="btnStart" onclick="handle('start')">START REVERSED BOT</button>
        <button class="btn stop" id="btnStop" onclick="handle('stop')" style="display:none;">STOP & PURGE</button>
        
        <div class="stats">
            <div class="stat-card">Wins: <b id="ui_wins">0</b></div>
            <div class="stat-card">Losses: <b id="ui_losses">0</b></div>
            <div class="stat-card" style="grid-column: span 2;">Profit: <b id="ui_profit" style="color:var(--blue); font-size:20px;">$ 0.00</b></div>
        </div>
    </div>
    <script>
        async function handle(type) {
            const payload = {
                username: document.getElementById('user').value,
                token: document.getElementById('token').value,
                asset: document.getElementById('asset').value,
                stake: document.getElementById('stake').value,
                tp: document.getElementById('tp').value,
                action: type
            };
            await fetch('/manage', { method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(payload) });
            if(type === 'stop') location.reload();
        }
        async function refresh() {
            const user = document.getElementById('user').value;
            if(!user) return;
            const res = await fetch('/data/' + user);
            const d = await res.json();
            if(d.found) {
                document.getElementById('ui_status').innerText = "STATUS: " + d.status;
                document.getElementById('ui_wins').innerText = d.wins;
                document.getElementById('ui_losses').innerText = d.losses;
                document.getElementById('ui_profit').innerText = (d.currency || "$") + " " + d.total_profit.toFixed(2);
                document.getElementById('btnStart').style.display = "none";
                document.getElementById('btnStop').style.display = "block";
            } else {
                document.getElementById('btnStart').style.display = "block";
                document.getElementById('btnStop').style.display = "none";
            }
        }
        setInterval(refresh, 1000);
    </script>
</body>
</html>
"""

@app.route('/')
def home(): return render_template_string(HTML_TEMPLATE)

@app.route('/data/<username>')
def get_user_data(username):
    u = users_col.find_one({"username": username})
    if not u: return jsonify({"found": False})
    u['_id'] = str(u['_id']); u['found'] = True
    return jsonify(u)

@app.route('/manage', methods=['POST'])
def manage_bot():
    req = request.json
    username = req['username']
    if req['action'] == 'stop':
        users_col.delete_one({"username": username})
        return jsonify({"status": "deleted"})
        
    user = users_col.find_one({"username": username})
    if not user:
        new_user = {
            "username": username, "token": req['token'], "selected_asset": req['asset'],
            "base_stake": float(req['stake']), "current_stake": float(req['stake']),
            "take_profit": float(req['tp']), "is_running": True, "status": "WAITING",
            "wins": 0, "losses": 0, "total_profit": 0.0, "consecutive_losses": 0,
            "active_contract": None, "currency": "$"
        }
        user_id = users_col.insert_one(new_user).inserted_id
        Thread(target=user_bot_logic, args=(str(user_id),), daemon=True).start()
    return jsonify({"status": "running"})

if __name__ == "__main__":
    app.run(host='0.0.0.0', port=5000)
