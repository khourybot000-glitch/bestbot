import json, websocket, time, os
from datetime import datetime
from threading import Thread
from flask import Flask, render_template_string, jsonify, request
from pymongo import MongoClient
from bson.objectid import ObjectId

app = Flask(__name__)

# --- MongoDB ---
MONGO_URI = "mongodb+srv://charbelnk111_db_user:Mano123mano@cluster0.2gzqkc8.mongodb.net/?appName=Cluster0"
client = MongoClient(MONGO_URI)
db = client["KHOURY_BOT"]
users = db["users"]
DERIV_WS = "wss://blue.derivws.com/websockets/v3?app_id=16929"

def set_status(uid, status):
    users.update_one({"_id": ObjectId(uid)}, {"$set": {"status": status}})

def execute_contract(token, symbol, stake, c_type, currency):
    try:
        ws = websocket.create_connection(DERIV_WS, timeout=15)
        ws.send(json.dumps({"authorize": token}))
        ws.recv()
        ws.send(json.dumps({
            "buy": 1, 
            "price": round(float(stake), 2),
            "parameters": {
                "amount": round(float(stake), 2), 
                "basis": "stake",
                "contract_type": c_type, 
                "currency": currency,
                "duration": 1, 
                "duration_unit": "m", 
                "symbol": symbol
            }
        }))
        res = json.loads(ws.recv())
        ws.close()
        return res.get("buy", {}).get("contract_id")
    except: return None

def check_result(token, cid):
    try:
        ws = websocket.create_connection(DERIV_WS, timeout=10)
        ws.send(json.dumps({"authorize": token}))
        ws.recv()
        time.sleep(62) 
        ws.send(json.dumps({"proposal_open_contract": 1, "contract_id": cid}))
        r = json.loads(ws.recv())
        ws.close()
        poc = r.get("proposal_open_contract", {})
        if poc.get("is_sold"):
            return float(poc.get("profit", 0))
        return None
    except: return None

def bot_worker(uid):
    consecutive_losses = 0
    pending_signal = None 
    
    while True:
        u = users.find_one({"_id": ObjectId(uid)})
        if not u or u.get("status") == "STOPPED": break
        
        if consecutive_losses >= 5:
            set_status(uid, "STOPPED (MAX LOSS)")
            break

        now = datetime.now()

        # التحليل عند الثانية 30
        if now.second == 30:
            try:
                set_status(uid, "ANALYZING...")
                ws = websocket.create_connection(DERIV_WS, timeout=15)
                ws.send(json.dumps({"authorize": u["token"]}))
                ws.recv()
                ws.send(json.dumps({"ticks_history": u["symbol"], "count": 270, "end": "latest", "style": "ticks"}))
                hist = json.loads(ws.recv())
                ws.close()
                
                prices = hist["history"]["prices"]
                is_up = prices[-1] > prices[0]
                is_down = prices[-1] < prices[0]
                last_90 = prices[-90:]
                corr_60 = last_90[:60]
                imp_30 = last_90[-30:]

                if is_up and (corr_60[-1] < corr_60[0]) and (imp_30[-1] > imp_30[0]):
                    pending_signal = "CALL"
                    set_status(uid, "READY: CALL")
                elif is_down and (corr_60[-1] > corr_60[0]) and (imp_30[-1] < imp_30[0]):
                    pending_signal = "PUT"
                    set_status(uid, "READY: PUT")
                else:
                    pending_signal = None
                    set_status(uid, "NO SIGNAL")
                time.sleep(1)
            except: pass

        # الدخول عند الثانية 58
        if now.second == 58 and pending_signal:
            target = pending_signal
            pending_signal = None 
            set_status(uid, f"IN TRADE ({target})")
            current_stake = u.get("stake")
            cid = execute_contract(u["token"], u["symbol"], current_stake, target, "USD")
            
            if cid:
                profit = check_result(u["token"], cid)
                if profit is not None:
                    if profit > 0:
                        consecutive_losses = 0
                        users.update_one({"_id": ObjectId(uid)}, {
                            "$inc": {"wins": 1, "profit": profit},
                            "$set": {"stake": u["base_stake"]}
                        })
                    else:
                        consecutive_losses += 1
                        users.update_one({"_id": ObjectId(uid)}, {
                            "$inc": {"losses": 1, "profit": -current_stake},
                            "$set": {"stake": current_stake * 2.2}
                        })
            set_status(uid, "WAITING CYCLE")
        time.sleep(0.1)

@app.route("/")
def home():
    return render_template_string("""
    <body style='background:#0d1117;color:white;text-align:center;font-family:sans-serif;padding:30px'>
        <h2 style='color:#00e676'>SILENT ALPHA SNIPER - V2.2</h2>
        <div id=login_div><input id=ev placeholder="Email" style="padding:10px;border-radius:5px"><br><br><button onclick="login()" style="padding:10px 20px;background:#00e676;border:none;border-radius:5px;cursor:pointer">LOGIN</button></div>
        <div id=settings style='display:none'>
            <input id=tv placeholder="Deriv Token" style="width:250px;padding:10px"><br><br>
            <input id=sv value=1.0 placeholder="Stake" style="padding:10px"><br><br>
            <input id=tpv value=50 placeholder="Target Profit" style="padding:10px"><br><br>
            <button onclick="start()" style="padding:10px 20px;background:#2979ff;color:white;border:none;border-radius:5px">START BOT</button>
        </div>
        <div id=stats style='display:none'>
            <div style='font-size:35px; font-weight:bold; color:#00e676; margin-bottom:10px' id=st></div>
            <div id=info style='font-size:22px; margin:20px 0; background:#161b22; padding:20px; border-radius:10px'></div>
            <button onclick="stop()" style="background:#ff5252;color:white;border:none;padding:15px 30px;border-radius:5px;cursor:pointer;font-weight:bold">STOP</button>
            <button onclick="reset()" style='margin-left:15px;padding:15px 30px;border-radius:5px;background:#30363d;color:white;border:none;cursor:pointer'>RESET ALL</button>
        </div>
        <script>
            let email="";
            async function login(){ email=document.getElementById('ev').value; let r=await fetch('/check/'+email); let d=await r.json(); document.getElementById('login_div').style.display='none'; if(d.found){ document.getElementById('stats').style.display='block'; sync(); } else { document.getElementById('settings').style.display='block'; } }
            async function start(){ 
                let d={email:email, token:document.getElementById('tv').value, symbol:'frxEURUSD', stake:parseFloat(document.getElementById('sv').value), tp:parseFloat(document.getElementById('tpv').value)}; 
                await fetch('/start',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(d)}); 
                document.getElementById('settings').style.display='none'; document.getElementById('stats').style.display='block'; sync(); 
            }
            async function stop(){ await fetch('/stop',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({email:email})}); }
            async function reset(){ if(confirm("حذف جميع البيانات والبدء من جديد؟")){ await fetch('/reset',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({email:email})}); location.reload(); } }
            function sync(){ setInterval(async()=>{ let r=await fetch('/check/'+email); let d=await r.json(); if(d.found){ document.getElementById('st').innerText=d.status; document.getElementById('info').innerText=`Profit: ${d.profit.toFixed(2)}$ | Wins: ${d.wins} | Losses: ${d.losses}`; if(d.status.includes("STOPPED")) { document.getElementById('st').style.color='#ff5252'; } else { document.getElementById('st').style.color='#00e676'; } } },1000); }
        </script>
    </body>
    """)

@app.route("/check/<email>")
def check_email(email):
    u = users.find_one({"email": email})
    if u: u["_id"]=str(u["_id"]); return jsonify({"found": True, **u})
    return jsonify({"found": False})

@app.route("/start", methods=["POST"])
def start():
    d = request.json; users.delete_one({"email": d["email"]})
    uid = users.insert_one({"email": d["email"], "token": d["token"], "symbol": d["symbol"], "base_stake": d["stake"], "stake": d["stake"], "tp": d["tp"], "profit": 0.0, "wins": 0, "losses": 0, "status": "WAITING"}).inserted_id
    Thread(target=bot_worker, args=(str(uid),), daemon=True).start()
    return jsonify({"ok": True})

@app.route("/stop", methods=["POST"])
def stop(): 
    email = request.json["email"]
    users.update_one({"email": email}, {"$set": {"status": "STOPPED"}})
    return jsonify({"ok": True})

@app.route("/reset", methods=["POST"])
def reset(): 
    email = request.json["email"]
    users.delete_one({"email": email})
    return jsonify({"ok": True})

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
