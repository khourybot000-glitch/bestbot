import json
import websocket
import pandas as pd
import time
from datetime import datetime
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

def add_log(user_id, message):
    now = datetime.now().strftime("%H:%M:%S")
    log_entry = f"[{now}] {message}"
    users_col.update_one({"_id": ObjectId(user_id)}, {"$push": {"logs": {"$each": [log_entry], "$slice": -15}}})

# --- Strategy: 30-Tick Trend + 5-Tick Confirmation ---
def compute_logic(ticks_list):
    df = pd.DataFrame(ticks_list, columns=['price'])
    if len(df) < 35: return "NONE"
    
    # 1. تحليل الاتجاه العام (آخر 30 تيك)
    last_30 = df['price'].iloc[-30:].values
    trend_bullish = last_30[-1] > last_30[0]
    trend_bearish = last_30[-1] < last_30[0]

    # 2. تحليل التأكيد (آخر 5 تيك)
    last_5 = df['price'].iloc[-5:].values
    confirm_bullish = last_5[-1] > last_5[0]
    confirm_bearish = last_5[-1] < last_5[0]

    # الشرط: يجب أن يتفق الاتجاه العام مع تأكيد آخر 5 تيك
    if trend_bullish and confirm_bullish: return "CALL"
    if trend_bearish and confirm_bearish: return "PUT"
    
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
            new_total = user.get('total_profit', 0) + profit
            
            if profit > 0:
                add_log(user_id, f"Win! Profit: +{profit}$")
                users_col.update_one({"_id": ObjectId(user_id)}, {"$set": {"active_contract": None, "total_profit": new_total, "wins": user.get('wins', 0) + 1, "current_stake": user['base_stake'], "consecutive_losses": 0}})
            else:
                losses = user.get('consecutive_losses', 0) + 1
                add_log(user_id, f"Loss! Martingale x19 Active")
                if losses >= 4:
                    add_log(user_id, "Stop Loss (2 Losses). Session Ended.")
                    users_col.delete_one({"_id": ObjectId(user_id)})
                    return
                users_col.update_one({"_id": ObjectId(user_id)}, {"$set": {"active_contract": None, "total_profit": new_total, "losses": user.get('losses', 0) + 1, "consecutive_losses": losses, "current_stake": round(user['current_stake'] * 2.2, 2)}})
            
            if new_total >= user.get('take_profit', 9999):
                add_log(user_id, "Target TP Reached!")
                users_col.delete_one({"_id": ObjectId(user_id)})
    except Exception as e: add_log(user_id, f"System Error: {str(e)}")

# --- Main Bot Loop ---
def user_loop(user_id):
    while True:
        try:
            user = users_col.find_one({"_id": ObjectId(user_id)})
            if not user: break
            if user.get("active_contract"):
                time.sleep(25); check_trade_status(user_id); continue
            
            if time.localtime().tm_sec == 30:
                ws = websocket.create_connection(DERIV_WS_URL, timeout=10)
                ws.send(json.dumps({"ticks_history": user['selected_asset'], "count": 50, "end": "latest", "style": "ticks"}))
                res = json.loads(ws.recv()); ws.close()
                
                if 'history' in res:
                    signal = compute_logic(res['history']['prices'])
                    if signal != "NONE":
                        barrier = "-0.01" if signal == "CALL" else "+0.01"
                        ws = websocket.create_connection(DERIV_WS_URL)
                        ws.send(json.dumps({"authorize": user['token']}))
                        if "authorize" in json.loads(ws.recv()):
                            ws.send(json.dumps({"buy": 1, "price": user['current_stake'], "parameters": {"amount": user['current_stake'], "basis": "stake", "contract_type": signal, "currency": "USD", "duration": 20, "duration_unit": "s", "symbol": user['selected_asset'], "barrier": barrier}}))
                            trade_res = json.loads(ws.recv())
                            if "buy" in trade_res:
                                add_log(user_id, f"ENTERED {signal} (Trend & 5-Tick Match)")
                                users_col.update_one({"_id": ObjectId(user_id)}, {"$set": {"active_contract": trade_res["buy"]["contract_id"]}})
                        ws.close()
                    else:
                        add_log(user_id, "No Entry: Trend and 5-Tick NOT aligned.")
                time.sleep(1)
            time.sleep(0.5)
        except: time.sleep(5)

# --- Frontend with Logs ---
HTML = """
<!DOCTYPE html>
<html>
<head><style>
    body{background:#05080a;color:white;font-family:sans-serif;padding:20px;display:flex;justify-content:center}
    .card{background:#0d1117;padding:25px;border-radius:15px;width:400px;text-align:center;border:1px solid #30363d}
    input,select{width:100%;padding:10px;margin:8px 0;background:#010409;color:#58a6ff;border:1px solid #30363d}
    .btn{width:100%;padding:15px;border:none;border-radius:8px;font-weight:bold;cursor:pointer;margin-top:10px}
    .start{background:#238636;color:white} .stop{background:#da3633;color:white}
    .stat-box, .log-box{background:#161b22; padding:15px; border-radius:8px; margin-top:15px; text-align:left; border:1px solid #30363d; font-size:13px}
    .log-box{height:150px; overflow-y:auto; color:#c9d1d9; font-family:monospace; line-height:1.6}
</style></head>
<body>
    <div class="card">
        <h2 id="title">KHOURY PRO V6.2.3</h2>
        <input type="email" id="email" placeholder="Email Address">
        <div id="fields" style="display:none;">
            <input type="password" id="token" placeholder="API Token">
            <select id="asset"><option value="R_100">Volatility 100</option><option value="R_75">Volatility 75</option></select>
            <input type="number" id="stake" placeholder="Initial Stake" value="1.00">
            <input type="number" id="tp" placeholder="Take Profit" value="5.00">
        </div>
        <button class="btn start" id="btn" onclick="process()">LOGIN</button>
        <div id="stats-ui" style="display:none;">
            <div class="stat-box" id="live-stats">Loading...</div>
            <div class="log-box" id="live-logs">Waiting for market data...</div>
        </div>
    </div>
    <script>
        let timer = null;
        async function update(email) {
            const r = await fetch('/check/'+email); const d = await r.json();
            if(d.found) {
                document.getElementById('live-stats').innerHTML = `<b>Profit:</b> <span style="color:${d.total_profit>=0?'#238636':'#da3633'}">${d.total_profit.toFixed(2)} USD</span><br><b>Wins/Losses:</b> ${d.wins} / ${d.losses}<br><b>Current Stake:</b> ${d.current_stake}$`;
                document.getElementById('live-logs').innerHTML = d.logs ? d.logs.reverse().join('<br>') : 'Scanning...';
            } else { clearInterval(timer); location.reload(); }
        }
        async function process() {
            const email = document.getElementById('email').value; const r = await fetch('/check/'+email); const d = await r.json();
            if(d.found) {
                document.getElementById('title').innerText="DASHBOARD"; document.getElementById('stats-ui').style.display='block';
                document.getElementById('btn').innerText="STOP SESSION"; document.getElementById('btn').className="btn stop";
                document.getElementById('btn').onclick=async()=>{await fetch('/manage',{method:'DELETE',headers:{'Content-Type':'application/json'},body:JSON.stringify({email:email})}); location.reload();};
                timer=setInterval(()=>update(email),1000);
            } else { document.getElementById('fields').style.display='block'; document.getElementById('btn').innerText="START BOT"; document.getElementById('btn').onclick=start; }
        }
        async function start() {
            const data = {email:document.getElementById('email').value, token:document.getElementById('token').value, asset:document.getElementById('asset').value, stake:document.getElementById('stake').value, tp:document.getElementById('tp').value};
            await fetch('/manage',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(data)}); process();
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
    if u: u['_id']=str(u['_id']); return jsonify({"found": True, **u})
    return jsonify({"found": False})

@app.route('/manage', methods=['POST', 'DELETE'])
def manage():
    if request.method == 'POST':
        req = request.json
        uid = users_col.insert_one({"email":req['email'],"token":req['token'],"selected_asset":req['asset'],"base_stake":float(req['stake']),"current_stake":float(req['stake']),"take_profit":float(req['tp']),"wins":0,"losses":0,"total_profit":0.0,"consecutive_losses":0,"active_contract":None,"logs":[]}).inserted_id
        Thread(target=user_loop, args=(str(uid),), daemon=True).start()
    else: users_col.delete_one({"email": request.json['email']})
    return jsonify({"status": "ok"})

if __name__ == "__main__": app.run(host='0.0.0.0', port=5000)
