import json, websocket, time
from datetime import datetime
from threading import Thread
from flask import Flask, render_template_string, jsonify, request
from pymongo import MongoClient
from bson.objectid import ObjectId

app = Flask(__name__)
client = MongoClient("mongodb+srv://charbelnk111_db_user:Mano123mano@cluster0.2gzqkc8.mongodb.net/?appName=Cluster0")
db = client['KHOURY_V8_SCALPER']
users_col = db['users']
DERIV_WS_URL = "wss://blue.derivws.com/websockets/v3?app_id=16929"

def get_account_currency(token):
    try:
        ws = websocket.create_connection(DERIV_WS_URL)
        ws.send(json.dumps({"authorize": token}))
        ws.recv()
        ws.send(json.dumps({"balance": 1}))
        res = json.loads(ws.recv()); ws.close()
        return res.get("balance", {}).get("currency", "USD")
    except: return "USD"

def add_log(uid, msg):
    users_col.update_one({"_id": ObjectId(uid)}, {"$push": {"logs": {"$each": [f"[{datetime.now().strftime('%H:%M:%S')}] {msg}"], "$slice": -20}}})

def wait_for_contract(uid, contract_id, token):
    while True:
        try:
            ws = websocket.create_connection(DERIV_WS_URL)
            ws.send(json.dumps({"authorize": token}))
            ws.recv()
            ws.send(json.dumps({"proposal_open_contract": 1, "contract_id": contract_id}))
            res = json.loads(ws.recv()); ws.close()
            contract = res.get("proposal_open_contract", {})
            if contract.get("is_sold"): return float(contract.get("profit", 0))
            time.sleep(1)
        except: time.sleep(1)

def bot_loop(uid):
    while True:
        try:
            u = users_col.find_one({"_id": ObjectId(uid)})
            if not u: break
            now = datetime.now()
            
            # التحليل عند الثانية 58 من الدقيقة الرابعة
            if now.minute % 5 == 4 and now.second == 58:
                ws = websocket.create_connection(DERIV_WS_URL)
                ws.send(json.dumps({"ticks_history": u['symbol'], "count": 300, "end": "latest", "style": "ticks"}))
                res = json.loads(ws.recv()); ws.close()
                prices = res['history']['prices']
                trend_300 = "CALL" if prices[-1] > prices[-300] else "PUT"
                trend_60 = "CALL" if prices[-1] > prices[-60] else "PUT"
                
                if trend_300 == trend_60:
                    curr = get_account_currency(u['token'])
                    ws = websocket.create_connection(DERIV_WS_URL)
                    ws.send(json.dumps({"authorize": u['token']}))
                    ws.send(json.dumps({"buy": 1, "price": u['current_stake'], "parameters": {
                        "amount": u['current_stake'], "basis": "stake", "contract_type": trend_300, 
                        "currency": curr, "duration": 54, "duration_unit": "s", "symbol": u['symbol']
                    }}))
                    trade = json.loads(ws.recv()); ws.close()
                    
                    if "buy" in trade:
                        add_log(uid, f"Entered {trend_300} @ {u['current_stake']}$")
                        time.sleep(58) 
                        profit = wait_for_contract(uid, trade["buy"]["contract_id"], u['token'])
                        
                        if profit > 0:
                            new_total = u.get('total_profit', 0) + profit
                            if new_total >= u.get('tp', 9999):
                                add_log(uid, "TP Reached! STOPPED.")
                                users_col.delete_one({"_id": ObjectId(uid)})
                            else:
                                add_log(uid, f"WIN: +{profit}$")
                                users_col.update_one({"_id": ObjectId(uid)}, {"$inc": {"wins": 1, "total_profit": profit}, "$set": {"current_stake": u['base_stake'], "consecutive_losses": 0}})
                        else:
                            add_log(uid, f"LOSS: {profit}$")
                            losses = u.get('consecutive_losses', 0) + 1
                            if losses >= 5:
                                add_log(uid, "5 LOSSES! STOPPED.")
                                users_col.delete_one({"_id": ObjectId(uid)})
                            else:
                                users_col.update_one({"_id": ObjectId(uid)}, {"$inc": {"losses": 1, "total_profit": profit}, "$set": {"current_stake": round(u['current_stake']*2.2, 2), "consecutive_losses": losses}})
                time.sleep(2)
            time.sleep(0.5)
        except: time.sleep(1)

HTML = """
<!DOCTYPE html>
<html>
<head><style>
    body{background:#05080a;color:#fff;font-family:sans-serif;padding:20px;display:flex;justify-content:center}
    .card{background:#0d1117;padding:25px;border-radius:15px;width:350px;border:1px solid #30363d}
    input, select{width:100%;padding:10px;margin:5px 0;background:#010409;color:#58a6ff;border:1px solid #30363d}
    .btn{width:100%;padding:15px;border:none;border-radius:8px;font-weight:bold;cursor:pointer;margin-top:10px}
</style></head>
<body>
    <div class="card">
        <h2>KHOURY V8.8 PRO</h2>
        <input type="email" id="email" placeholder="Email Address">
        <div id="fields" style="display:none;">
            <input type="password" id="token" placeholder="API Token">
            <select id="symbol"><option value="R_100">Volatility 100</option><option value="R_75">Volatility 75</option></select>
            <input type="number" id="stake" value="1.00" placeholder="Initial Stake">
            <input type="number" id="tp" value="10.00" placeholder="Take Profit">
        </div>
        <button class="btn" id="main-btn" onclick="checkUser()">LOGIN</button>
        <div id="ui" style="display:none;">
            <div id="stats" style="margin:10px 0; font-size:13px; background:#161b22; padding:10px"></div>
            <div id="logs" style="font-size:11px;height:120px;overflow-y:auto;background:#161b22;padding:5px"></div>
            <button class="btn" style="background:#da3633" onclick="stop()">STOP BOT</button>
        </div>
    </div>
    <script>
        async function checkUser(){
            const email = document.getElementById('email').value;
            if(!email) return;
            const r = await fetch('/check/'+email); const d = await r.json();
            if(d.found){ 
                document.getElementById('ui').style.display='block'; 
                document.getElementById('fields').style.display='none';
                document.getElementById('main-btn').style.display='none'; 
            } else { 
                document.getElementById('fields').style.display='block'; 
                document.getElementById('main-btn').innerText='START BOT'; 
                document.getElementById('main-btn').onclick=start; 
            }
        }
        async function start(){
            const d = {email:document.getElementById('email').value, token:document.getElementById('token').value, symbol:document.getElementById('symbol').value, stake:document.getElementById('stake').value, tp:document.getElementById('tp').value};
            await fetch('/manage',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(d)}); location.reload();
        }
        async function stop(){ await fetch('/manage',{method:'DELETE',headers:{'Content-Type':'application/json'},body:JSON.stringify({email:document.getElementById('email').value})}); location.reload(); }
        
        setInterval(async()=>{
            const email = document.getElementById('email').value;
            if(!email || document.getElementById('ui').style.display === 'none') return;
            const r = await fetch('/check/'+email); const d = await r.json();
            if(d.found){ 
                document.getElementById('stats').innerHTML = `Wins: ${d.wins} | Losses: ${d.losses}<br>Profit: ${parseFloat(d.total_profit).toFixed(2)}$ | Next Stake: ${d.current_stake}$`; 
                if(d.logs) document.getElementById('logs').innerHTML = d.logs.slice().reverse().join('<br>'); 
            }
        }, 2000);
    </script>
</body>
</html>
"""

@app.route('/')
def home(): return render_template_string(HTML)

@app.route('/check/<email>')
def check(email):
    u = users_col.find_one({"email": email})
    return jsonify({"found": True if u else False, **(u if u else {})})

@app.route('/manage', methods=['POST', 'DELETE'])
def manage():
    if request.method == 'POST':
        req = request.json
        uid = users_col.insert_one({"email":req['email'],"token":req['token'],"symbol":req['symbol'],"current_stake":float(req['stake']),"base_stake":float(req['stake']),"tp":float(req['tp']),"wins":0,"losses":0,"total_profit":0.0,"consecutive_losses":0,"logs":[]}).inserted_id
        Thread(target=bot_loop, args=(str(uid),), daemon=True).start()
    else: users_col.delete_one({"email": request.json['email']})
    return jsonify({"status": "ok"})

if __name__ == "__main__": app.run(host='0.0.0.0', port=5000)
