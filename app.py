import json
import websocket
import pandas as pd
import numpy as np
import time
from datetime import datetime
from threading import Thread
from flask import Flask, render_template_string, jsonify, request
from pymongo import MongoClient
from bson.objectid import ObjectId

app = Flask(__name__)

# --- MongoDB & Configuration ---
MONGO_URI = "mongodb+srv://charbelnk111_db_user:Mano123mano@cluster0.2gzqkc8.mongodb.net/?appName=Cluster0"
client = MongoClient(MONGO_URI)
db = client['KHOURY_V7_FINAL']
users_col = db['users']
DERIV_WS_URL = "wss://blue.derivws.com/websockets/v3?app_id=16929"

def add_log(user_id, message):
    now = datetime.now().strftime("%H:%M:%S")
    users_col.update_one({"_id": ObjectId(user_id)}, {"$push": {"logs": {"$each": [f"[{now}] {message}"], "$slice": -15}}})

# --- Strategy Logic ---
def compute_logic(ticks_list):
    # تحويل 1500 تيك إلى شموع 60 تيك
    candles = []
    for i in range(0, len(ticks_list), 60):
        chunk = ticks_list[i:i+60]
        if len(chunk) == 60:
            candles.append({'open': chunk[0], 'close': chunk[-1]})
    
    df = pd.DataFrame(candles)
    if len(df) < 15: return "NONE"
    
    # حساب RSI (14)
    delta = df['close'].diff()
    gain = (delta.where(delta > 0, 0)).rolling(window=14).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(window=14).mean()
    rs = gain / loss
    df['rsi'] = 100 - (100 / (1 + rs))
    
    last_rsi = df['rsi'].iloc[-1]
    last_candle = df.iloc[-1]
    
    if last_rsi < 30 and last_candle['close'] > last_candle['open']: return "CALL"
    if last_rsi > 70 and last_candle['close'] < last_candle['open']: return "PUT"
    return "NONE"

# --- Trade Lifecycle ---
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
            if profit > 0:
                add_log(user_id, f"Win! +{profit}$")
                users_col.update_one({"_id": ObjectId(user_id)}, {"$set": {"active_contract": None, "total_profit": user.get('total_profit', 0) + profit, "wins": user.get('wins', 0) + 1, "current_stake": user['base_stake'], "consecutive_losses": 0}})
            else:
                losses = user.get('consecutive_losses', 0) + 1
                new_stake = round(user['current_stake'] * 2.2, 2)
                add_log(user_id, f"Loss! Martingale Active: {new_stake}$")
                if losses >= 4:
                    add_log(user_id, "4 Losses! STOP LOSS.")
                    users_col.delete_one({"_id": ObjectId(user_id)})
                    return
                users_col.update_one({"_id": ObjectId(user_id)}, {"$set": {"active_contract": None, "total_profit": user.get('total_profit', 0) + profit, "losses": user.get('losses', 0) + 1, "consecutive_losses": losses, "current_stake": new_stake}})
    except Exception as e: add_log(user_id, f"Error: {str(e)}")

# --- Bot Loop ---
def user_loop(user_id):
    while True:
        try:
            user = users_col.find_one({"_id": ObjectId(user_id)})
            if not user: break
            if user.get("active_contract"):
                time.sleep(310) # 5m + 10s
                check_trade_status(user_id)
                continue
            
            if time.localtime().tm_sec == 0:
                ws = websocket.create_connection(DERIV_WS_URL, timeout=15)
                ws.send(json.dumps({"ticks_history": user['selected_asset'], "count": 1500, "end": "latest", "style": "ticks"}))
                res = json.loads(ws.recv()); ws.close()
                if 'history' in res:
                    signal = compute_logic(res['history']['prices'])
                    if signal != "NONE":
                        ws = websocket.create_connection(DERIV_WS_URL)
                        ws.send(json.dumps({"authorize": user['token']}))
                        if "authorize" in json.loads(ws.recv()):
                            ws.send(json.dumps({"buy": 1, "price": user['current_stake'], "parameters": {"amount": user['current_stake'], "basis": "stake", "contract_type": signal, "currency": "USD", "duration": 5, "duration_unit": "m", "symbol": user['selected_asset']}}))
                            trade_res = json.loads(ws.recv())
                            if "buy" in trade_res:
                                add_log(user_id, f"Entered {signal} (RSI)")
                                users_col.update_one({"_id": ObjectId(user_id)}, {"$set": {"active_contract": trade_res["buy"]["contract_id"]}})
                        ws.close()
                time.sleep(1)
            time.sleep(0.5)
        except: time.sleep(5)

# --- Frontend (HTML) ---
HTML = """
<!DOCTYPE html>
<html>
<head><style>
    body{background:#05080a;color:white;font-family:sans-serif;padding:20px;display:flex;justify-content:center}
    .card{background:#0d1117;padding:25px;border-radius:15px;width:380px;text-align:center;border:1px solid #30363d}
    .stat-box{background:#161b22; padding:15px; border-radius:8px; margin-top:15px; text-align:left; border:1px solid #30363d; font-size:13px}
    .log-box{height:150px; overflow-y:auto; color:#c9d1d9; font-family:monospace; line-height:1.6; margin-top:10px}
    .btn{width:100%;padding:15px;border:none;border-radius:8px;font-weight:bold;cursor:pointer;margin-top:10px}
</style></head>
<body>
    <div class="card">
        <h2>KHOURY V7.0</h2>
        <input type="email" id="email" placeholder="Email"><br>
        <div id="fields" style="display:none;">
            <input type="password" id="token" placeholder="API Token"><br>
            <input type="number" id="stake" value="1.00">
            <button class="btn" style="background:#238636" onclick="start()">START BOT</button>
        </div>
        <div id="ui" style="display:none;">
            <div class="stat-box" id="live-stats"></div>
            <div class="log-box" id="live-logs"></div>
            <button class="btn" style="background:#da3633" onclick="stop()">STOP</button>
        </div>
    </div>
    <script>
        async function start() {
            const d = {email:document.getElementById('email').value, token:document.getElementById('token').value, stake:document.getElementById('stake').value};
            await fetch('/manage',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(d)});
            location.reload();
        }
        async function stop() {
            await fetch('/manage',{method:'DELETE',headers:{'Content-Type':'application/json'},body:JSON.stringify({email:document.getElementById('email').value})});
            location.reload();
        }
        setInterval(async()=>{
            const email = document.getElementById('email').value;
            const r = await fetch('/check/'+email); const d = await r.json();
            if(d.found){
                document.getElementById('ui').style.display='block';
                document.getElementById('live-stats').innerHTML = `Profit: ${d.total_profit.toFixed(2)}$<br>Stake: ${d.current_stake}$`;
                document.getElementById('live-logs').innerHTML = d.logs.reverse().join('<br>');
            }
        }, 1000);
    </script>
</body>
</html>
"""

@app.route('/')
def home(): return render_template_string(HTML)

@app.route('/check/<email>')
def check(email):
    u = users_col.find_one({"email": email})
    if u: u['_id']=str(u['_id']); return jsonify({"found": True, **u})
    return jsonify({"found": False})

@app.route('/manage', methods=['POST', 'DELETE'])
def manage():
    if request.method == 'POST':
        req = request.json
        uid = users_col.insert_one({"email":req['email'],"token":req['token'],"selected_asset":"R_100","base_stake":float(req['stake']),"current_stake":float(req['stake']),"wins":0,"losses":0,"total_profit":0.0,"consecutive_losses":0,"active_contract":None,"logs":[]}).inserted_id
        Thread(target=user_loop, args=(str(uid),), daemon=True).start()
    else: users_col.delete_one({"email": request.json['email']})
    return jsonify({"status": "ok"})

if __name__ == "__main__": app.run(host='0.0.0.0', port=5000)
