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
db = client['khoury_bot_v5']
users_col = db['users']

# --- الإعدادات الفنية ---
DERIV_WS_URL = "wss://blue.derivws.com/websockets/v3?app_id=16929"
ASSETS = ["R_100", "R_75", "R_50", "R_10"]

# --- منطق التحليل الفني ---
def compute_logic(df):
    if len(df) < 60: return "NONE"
    c = df['close']
    delta = c.diff()
    gain = (delta.where(delta > 0, 0)).rolling(window=14).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(window=14).mean()
    rsi = 100 - (100 / (1 + (gain / loss)))
    ema50 = c.ewm(span=50, adjust=False).mean()
    
    r_curr, r_prev1, r_prev2 = rsi.iloc[-1], rsi.iloc[-2], rsi.iloc[-3]
    p_curr, e_curr = c.iloc[-1], ema50.iloc[-1]
    
    if ((r_prev1 <= 50 and r_curr > 50) or (r_prev2 <= 50 and r_prev1 > 50)) and p_curr > e_curr:
        return "CALL"
    if ((r_prev1 >= 50 and r_curr < 50) or (r_prev2 >= 50 and r_prev1 < 50)) and p_curr < e_curr:
        return "PUT"
    return "NONE"

# --- وظائف التداول لكل مستخدم ---
def user_bot_logic(user_id):
    while True:
        user = users_col.find_one({"_id": ObjectId(user_id)})
        if not user or not user.get("is_running"):
            time.sleep(2)
            continue

        # 1. فحص النتيجة إذا كان هناك عقد مفتوح
        if user.get("active_contract") and time.time() >= user.get("check_time"):
            check_and_update_trade(user_id)
            continue

        # 2. المسح عند الثانية 00 إذا كان المستخدم WAITING
        if user.get("status") == "WAITING" and not user.get("active_contract"):
            if time.strftime("%S") == "00":
                scan_and_place(user_id)
                time.sleep(1)
        
        time.sleep(0.5)

def scan_and_place(user_id):
    user = users_col.find_one({"_id": ObjectId(user_id)})
    for asset in ASSETS:
        try:
            ws = websocket.create_connection(DERIV_WS_URL)
            ws.send(json.dumps({"ticks_history": asset, "count": 1000, "end": "latest", "style": "ticks"}))
            res = json.loads(ws.recv())
            ws.close()
            
            ticks = pd.DataFrame(res['history']['prices'], columns=['close'])
            signal = compute_logic(ticks.iloc[::30])
            
            if signal != "NONE":
                place_deriv_trade(user_id, asset, signal)
                break
        except: continue

def place_deriv_trade(user_id, asset, side):
    user = users_col.find_one({"_id": ObjectId(user_id)})
    try:
        ws = websocket.create_connection(DERIV_WS_URL)
        ws.send(json.dumps({"authorize": user['token']}))
        ws.recv()
        
        stake = round(user['current_stake'], 2)
        trade_req = {
            "buy": 1, "price": stake,
            "parameters": {
                "amount": stake, "basis": "stake", "contract_type": side,
                "currency": "USD", "duration": 5, "duration_unit": "m", "symbol": asset
            }
        }
        ws.send(json.dumps(trade_req))
        res = json.loads(ws.recv())
        ws.close()

        if "buy" in res:
            users_col.update_one({"_id": ObjectId(user_id)}, {"$set": {
                "active_contract": res["buy"]["contract_id"],
                "status": f"IN TRADE ({asset})",
                "check_time": time.time() + 306
            }})
    except: pass

def check_and_update_trade(user_id):
    user = users_col.find_one({"_id": ObjectId(user_id)})
    try:
        ws = websocket.create_connection(DERIV_WS_URL)
        ws.send(json.dumps({"authorize": user['token']}))
        ws.recv()
        ws.send(json.dumps({"proposal_open_contract": 1, "contract_id": user['active_contract']}))
        res = json.loads(ws.recv())
        ws.close()

        contract = res["proposal_open_contract"]
        if contract["is_sold"]:
            new_data = {
                "active_contract": None,
                "status": "WAITING"
            }
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
                new_data["is_running"] = False
                new_data["status"] = "STOPPED (LIMIT REACHED)"

            users_col.update_one({"_id": ObjectId(user_id)}, {"$set": new_data})
    except: pass

# --- واجهة المستخدم (HTML) ---
HTML_TEMPLATE = """
<!DOCTYPE html>
<html>
<head>
    <title>KHOURY MULTI-USER BOT</title>
    <style>
        body { background: #05080a; color: white; font-family: sans-serif; display: flex; justify-content: center; padding: 20px; }
        .card { background: #0d1117; padding: 20px; border-radius: 15px; width: 380px; border: 1px solid #30363d; }
        input { width: 100%; padding: 10px; margin: 10px 0; background: #000; color: #58a6ff; border: 1px solid #333; border-radius: 8px; box-sizing: border-box; }
        .btn { width: 100%; padding: 12px; border: none; border-radius: 8px; cursor: pointer; font-weight: bold; }
        .start { background: #238636; color: white; margin-bottom: 10px; }
        .stop { background: #da3633; color: white; }
        .stats { display: grid; grid-template-columns: 1fr 1fr; gap: 10px; margin-top: 15px; }
        .stat-item { background: #161b22; padding: 10px; border-radius: 8px; text-align: center; font-size: 14px; border: 1px solid #30363d; }
    </style>
</head>
<body>
    <div class="card">
        <h2 style="text-align:center; color:#58a6ff;">V5 MULTI-SNIPER</h2>
        <input type="text" id="username" placeholder="Enter Your Name">
        <input type="password" id="token" placeholder="Deriv API Token">
        <div style="display:flex; gap:10px;">
            <input type="number" id="stake" placeholder="Stake" value="1.00">
            <input type="number" id="tp" placeholder="T.Profit" value="10.00">
        </div>
        <button class="btn start" onclick="action('start')">START BOT</button>
        <button class="btn stop" onclick="action('stop')">STOP BOT</button>
        
        <div id="ui_status" style="text-align:center; margin:15px 0; color:#f1e05a; font-weight:bold;">Status: Offline</div>
        
        <div class="stats">
            <div class="stat-item">Wins: <b id="ui_wins" style="color:#3fb950;">0</b></div>
            <div class="stat-item">Losses: <b id="ui_losses" style="color:#f85149;">0</b></div>
            <div class="stat-item" style="grid-column: span 2;">Profit: <b id="ui_profit" style="color:#58a6ff;">$ 0.00</b></div>
        </div>
    </div>

    <script>
        async function action(type) {
            const data = {
                username: document.getElementById('username').value,
                token: document.getElementById('token').value,
                stake: document.getElementById('stake').value,
                tp: document.getElementById('tp').value,
                type: type
            };
            await fetch('/handle_bot', { method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(data) });
        }

        async function update() {
            const user = document.getElementById('username').value;
            if(!user) return;
            const res = await fetch('/status/' + user);
            const d = await res.json();
            if(d.exists) {
                document.getElementById('ui_status').innerText = "Status: " + d.status;
                document.getElementById('ui_wins').innerText = d.wins;
                document.getElementById('ui_losses').innerText = d.losses;
                document.getElementById('ui_profit').innerText = "$ " + d.total_profit.toFixed(2);
            }
        }
        setInterval(update, 2000);
    </script>
</body>
</html>
"""

@app.route('/')
def index(): return render_template_string(HTML_TEMPLATE)

@app.route('/status/<username>')
def get_status(username):
    user = users_col.find_one({"username": username})
    if not user: return jsonify({"exists": False})
    user['_id'] = str(user['_id'])
    user['exists'] = True
    return jsonify(user)

@app.route('/handle_bot', methods=['POST'])
def handle_bot():
    req = request.json
    username = req['username']
    
    user = users_col.find_one({"username": username})
    if not user:
        # إنشاء مستخدم جديد في قاعدة البيانات
        new_user = {
            "username": username, "token": req['token'], "base_stake": float(req['stake']),
            "current_stake": float(req['stake']), "take_profit": float(req['tp']),
            "is_running": False, "status": "WAITING", "wins": 0, "losses": 0,
            "total_profit": 0.0, "consecutive_losses": 0, "active_contract": None, "check_time": 0
        }
        user_id = users_col.insert_one(new_user).inserted_id
        # تشغيل خيط معالجة خاص بهذا المستخدم
        Thread(target=user_bot_logic, args=(str(user_id),), daemon=True).start()
    
    is_running = True if req['type'] == 'start' else False
    users_col.update_one({"username": username}, {"$set": {
        "is_running": is_running,
        "token": req['token'],
        "base_stake": float(req['stake']),
        "take_profit": float(req['tp'])
    }})
    return jsonify({"ok": True})

# عند تشغيل التطبيق، أعد تشغيل الخيوط لجميع المستخدمين الذين لديهم حسابات
def restart_all_threads():
    for user in users_col.find():
        Thread(target=user_bot_logic, args=(str(user['_id']),), daemon=True).start()

if __name__ == "__main__":
    restart_all_threads()
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 5000)))
