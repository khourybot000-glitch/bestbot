import json
import websocket
import pandas as pd
import time
from threading import Thread
from flask import Flask, render_template_string, jsonify, request
from pymongo import MongoClient
from bson.objectid import ObjectId
from datetime import datetime

app = Flask(__name__)

# --- إعدادات MongoDB ---
MONGO_URI = "mongodb+srv://charbelnk111_db_user:Mano123mano@cluster0.2gzqkc8.mongodb.net/?appName=Cluster0"
client = MongoClient(MONGO_URI)
db = client['KHOURY_PRECISION_V7']
users_col = db['users']
DERIV_WS_URL = "wss://blue.derivws.com/websockets/v3?app_id=16929"

def analyze_and_trade(user_id):
    try:
        user = users_col.find_one({"_id": ObjectId(user_id)})
        if not user or not user.get("is_running"): return
        
        ws = websocket.create_connection(DERIV_WS_URL)
        ws.send(json.dumps({"ticks_history": user['selected_asset'], "count": 60, "end": "latest", "style": "ticks"}))
        res = json.loads(ws.recv())
        ws.close()
        
        if 'history' not in res: return
        prices = res['history']['prices']
        if len(prices) < 60: return

        # المنطق: 60 تيك صاعدة، أول 5 من آخر 10 صاعدة، آخر 5 من آخر 10 هابطة
        is_60_bull = prices[-1] > prices[0]
        last_10 = prices[-10:]
        is_5_10_bull = last_10[4] > last_10[0]
        is_5_10_bear = last_10[-1] < last_10[5]

        if is_60_bull and is_5_10_bull and is_5_10_bear:
            execute_trade(user_id, user, "CALL")
    except: pass

def execute_trade(user_id, user, side):
    try:
        ws = websocket.create_connection(DERIV_WS_URL)
        ws.send(json.dumps({"authorize": user['token']}))
        auth = json.loads(ws.recv())
        
        ws.send(json.dumps({
            "buy": 1, "price": user['current_stake'],
            "parameters": {
                "amount": user['current_stake'], "basis": "stake", "contract_type": side,
                "currency": auth['authorize']['currency'], "duration": 5, "duration_unit": "t", "symbol": user['selected_asset']
            }
        }))
        res = json.loads(ws.recv())
        ws.close()
        
        if "buy" in res:
            contract_id = res["buy"]["contract_id"]
            users_col.update_one({"_id": ObjectId(user_id)}, {"$set": {"status": "IN TRADE"}})
            Thread(target=check_result_after_delay, args=(user_id, contract_id)).start()
    except: pass

def check_result_after_delay(user_id, contract_id):
    time.sleep(14)
    try:
        user = users_col.find_one({"_id": ObjectId(user_id)})
        ws = websocket.create_connection(DERIV_WS_URL)
        ws.send(json.dumps({"authorize": user['token']}))
        ws.recv()
        ws.send(json.dumps({"proposal_open_contract": 1, "contract_id": contract_id}))
        res = json.loads(ws.recv())
        ws.close()
        
        contract = res.get("proposal_open_contract")
        if contract and contract.get("is_sold"):
            profit = float(contract.get("profit", 0))
            if profit > 0:
                users_col.update_one({"_id": ObjectId(user_id)}, {"$inc": {"wins": 1, "total_profit": profit}, "$set": {"status": "WIN", "current_stake": user['base_stake']}})
            else:
                users_col.update_one({"_id": ObjectId(user_id)}, {"$inc": {"losses": 1, "total_profit": profit}, "$set": {"status": "LOSS", "current_stake": round(user['current_stake'] * 2.2, 2)}})
    except: pass

def scheduler():
    while True:
        if datetime.now().second == 0:
            for user in users_col.find({"is_running": True}):
                Thread(target=analyze_and_trade, args=(str(user['_id']),)).start()
            time.sleep(1)
        time.sleep(0.1)

Thread(target=scheduler, daemon=True).start()

# --- واجهة المستخدم (القديمة الاحترافية) ---
HTML = """
<!DOCTYPE html>
<html>
<head>
    <title>KHOURY PRECISION V7</title>
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <style>
        body { background: #05080a; color: white; font-family: sans-serif; display: flex; justify-content: center; padding: 20px; }
        .card { background: #0d1117; padding: 25px; border-radius: 15px; border: 1px solid #30363d; width: 350px; text-align: center; }
        input, select { width: 100%; padding: 10px; margin: 5px 0; background: #010409; color: #58a6ff; border: 1px solid #30363d; box-sizing: border-box; }
        .btn { width: 100%; padding: 15px; border: none; border-radius: 8px; font-weight: bold; cursor: pointer; margin-top: 10px; }
        .start { background: #238636; } .stop { background: #da3633; }
        .stat-grid { display: grid; grid-template-columns: 1fr 1fr 1fr; gap: 5px; margin-top: 20px; }
        .stat { background: #161b22; padding: 10px; border-radius: 8px; border: 1px solid #30363d; font-size: 13px; }
    </style>
</head>
<body>
    <div class="card">
        <h3>KHOURY V7 (SYNC 0s)</h3>
        <input type="text" id="u" placeholder="Session Name">
        <input type="password" id="t" placeholder="API Token">
        <div id="stat" style="margin:15px 0; color:#f1e05a;">READY</div>
        <button id="btn" class="btn start" onclick="toggle()">START</button>
        <div class="stat-grid">
            <div class="stat">W: <b id="win">0</b></div>
            <div class="stat">L: <b id="loss">0</b></div>
            <div class="stat">P: <b id="prof">0.00</b></div>
        </div>
    </div>
    <script>
        async function toggle() {
            const u = document.getElementById('u').value;
            const t = document.getElementById('t').value;
            await fetch('/manage', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({u:u, t:t})});
            location.reload();
        }
        setInterval(async () => {
            const u = document.getElementById('u').value;
            if(!u) return;
            const res = await fetch('/data/' + u);
            const d = await res.json();
            if(d.found) {
                document.getElementById('btn').innerText = "STOP & PURGE";
                document.getElementById('btn').className = "btn stop";
                document.getElementById('stat').innerText = d.status;
                document.getElementById('win').innerText = d.wins;
                document.getElementById('loss').innerText = d.losses;
                document.getElementById('prof').innerText = d.total_profit.toFixed(2);
            }
        }, 1000);
    </script>
</body>
</html>
"""


@app.route('/')
def home(): return render_template_string(HTML)

@app.route('/data/<u_name>')
def get_data(u_name):
    u = users_col.find_one({"username": u_name})
    if not u: return jsonify({"found": False})
    u['_id'] = str(u['_id']); u['found'] = True; return jsonify(u)

@app.route('/manage', methods=['POST'])
def manage():
    data = request.json
    if users_col.find_one({"username": data['u']}):
        users_col.delete_one({"username": data['u']})
    else:
        users_col.insert_one({"username": data['u'], "token": data['t'], "is_running": True, "selected_asset": "R_100", "base_stake": 1.0, "current_stake": 1.0, "total_profit": 0.0, "wins": 0, "losses": 0, "status": "WAITING"})
    return jsonify({"status": "ok"})

if __name__ == "__main__":
    app.run(host='0.0.0.0', port=5000)
