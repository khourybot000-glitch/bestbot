import json
import websocket
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
db = client["KHOURY_BOT"]
users = db["users"]

DERIV_WS = "wss://blue.derivws.com/websockets/v3?app_id=16929"

def log(uid, msg):
    t = datetime.now().strftime("%H:%M:%S")
    users.update_one({"_id": ObjectId(uid)}, {"$push": {"logs": {"$each": [f"[{t}] {msg}"], "$slice": -50}}})

# --- استراتيجية النمط (4 شموع متصلة - 17 تيك) ---
def strategy_pattern(ticks):
    if len(ticks) < 17: return "NONE", 0
    
    results = []
    # تحليل 4 شموع متصلة
    for i in range(0, 16, 4):
        open_p = ticks[i]
        close_p = ticks[i+4]
        if close_p > open_p: results.append("UP")
        elif close_p < open_p: results.append("DOWN")
        else: results.append("DOJI")

    # النمط المطلوب للصعود: صاعدة، هابطة، صاعدة، صاعدة
    if results == ["UP", "DOWN", "UP", "UP"]: return "CALL", -0.5
    # النمط المطلوب للهبوط: هابطة، صاعدة، هابطة، هابطة
    if results == ["DOWN", "UP", "DOWN", "DOWN"]: return "PUT", 0.5
    
    return "NONE", 0

# --- فحص نتائج الصفقة ---
def check_contract(uid):
    u = users.find_one({"_id": ObjectId(uid)})
    if not u or not u.get("contract"): return
    try:
        ws = websocket.create_connection(DERIV_WS)
        ws.send(json.dumps({"authorize": u["token"]}))
        ws.recv()
        ws.send(json.dumps({"proposal_open_contract": 1, "contract_id": u["contract"]}))
        r = json.loads(ws.recv())
        ws.close()
        c = r.get("proposal_open_contract")
        if not c or not c.get("is_sold"): return
        
        profit = round(float(c.get("profit", 0)), 2)
        total_p = round(u["profit"] + profit, 2)
        
        if profit > 0:
            log(uid, f"✅ WIN {profit}$")
            users.update_one({"_id": ObjectId(uid)}, {
                "$set": {"contract": None, "profit": total_p, "wins": u["wins"] + 1, "stake": round(u["base"], 2), "loss_seq": 0, "status": "analysing"}
            })
        else:
            loss_seq = u["loss_seq"] + 1
            new_stake = round(u["stake"] * 19, 2)
            log(uid, f"❌ LOSS. Next x19: {new_stake}$")
            users.update_one({"_id": ObjectId(uid)}, {
                "$set": {"contract": None, "stake": new_stake, "loss_seq": loss_seq, "status": "analysing"},
                "$inc": {"losses": 1, "profit": profit}
            })
            if loss_seq >= 2:
                users.update_one({"_id": ObjectId(uid)}, {"$set": {"status": "stopped", "reason": "Max losses reached"}})
        
        if total_p >= u["tp"]:
            users.update_one({"_id": ObjectId(uid)}, {"$set": {"status": "stopped", "reason": "TP Reached"}})
    except: pass

# --- حلقة البوت الرئيسية (نفس هيكلية كودك القديم الناجح) ---
def bot_worker(uid):
    while True:
        u = users.find_one({"_id": ObjectId(uid)})
        if not u or u.get("status") == "stopped": break
        
        sec = datetime.now().second
        # التحليل عند الثواني المطلوبة
        if sec in [0, 10, 20, 30, 40, 50]:
            if not u.get("contract"):
                try:
                    users.update_one({"_id": ObjectId(uid)}, {"$set": {"status": "analysing"}})
                    ws = websocket.create_connection(DERIV_WS)
                    ws.send(json.dumps({"ticks_history": u["symbol"], "count": 20, "end": "latest", "style": "ticks"}))
                    r = json.loads(ws.recv()); ws.close()
                    
                    if "history" in r:
                        sig, barrier = strategy_pattern(r["history"]["prices"][-17:])
                        if sig != "NONE":
                            ws = websocket.create_connection(DERIV_WS)
                            ws.send(json.dumps({"authorize": u["token"]}))
                            auth = json.loads(ws.recv())
                            ws.send(json.dumps({
                                "buy": 1, "price": round(u["stake"], 2),
                                "parameters": {
                                    "amount": round(u["stake"], 2), "basis": "stake",
                                    "contract_type": sig, "currency": auth["authorize"]["currency"],
                                    "duration": 6, "duration_unit": "t",
                                    "symbol": u["symbol"], "barrier": barrier
                                }
                            }))
                            trade = json.loads(ws.recv()); ws.close()
                            if "buy" in trade:
                                log(uid, f"🚀 ENTER {sig}")
                                users.update_one({"_id": ObjectId(uid)}, {"$set": {"contract": trade["buy"]["contract_id"], "status": "in trade"}})
                    time.sleep(1.5) # تجنب التكرار في نفس الثانية
                except: pass
        
        time.sleep(0.5)
        check_contract(uid)

# --- الواجهة الرسومية المحدثة ---
HTML = """
<body style='background:#0b1016;color:white;text-align:center;font-family:sans-serif;padding:30px'>
    <h2 style='color:#00f3ff'>KHOURY SNIPER V41</h2>
    <input id=email placeholder=Email style='padding:12px;border-radius:8px;border:none;width:250px'><br><br>
    <button onclick="login()" style='padding:10px 25px;background:#238636;color:white;border:none;border-radius:8px;cursor:pointer;font-weight:bold'>LOGIN</button>
    
    <div id=settings style='display:none;margin-top:25px'>
        <input id=token placeholder="API Token" type=password style='padding:10px;margin:5px;width:250px;border-radius:5px'><br>
        <input id=stake placeholder="Stake" type=number value=0.35 style='padding:10px;margin:5px;width:250px;border-radius:5px'><br>
        <input id=tp placeholder="Take Profit" type=number value=10 style='padding:10px;margin:5px;width:250px;border-radius:5px'><br>
        <button onclick="start()" style='padding:12px 30px;background:#238636;color:white;border:none;border-radius:8px;font-weight:bold'>START BOT</button>
    </div>

    <div id=stats style='display:none;margin-top:25px;background:#161b22;padding:25px;border-radius:15px;max-width:450px;margin-left:auto;margin-right:auto;border:1px solid #30363d'>
        <div id=status_badge style='font-size:22px;font-weight:bold;margin-bottom:15px;text-transform:uppercase'></div>
        <div style='display:flex;justify-content:space-around;margin-bottom:15px;background:#000;padding:12px;border-radius:12px'>
            <div style='color:#39ff14'>WINS: <span id=wins_count>0</span></div>
            <div style='color:#ff4757'>LOSSES: <span id=loss_count>0</span></div>
        </div>
        <div id=s style='font-size:18px;margin:10px 0'></div>
        <div id=l style='text-align:left;color:#39ff14;font-size:11px;margin-top:15px;height:180px;overflow:auto;background:#000;padding:10px;border-radius:8px'></div>
        <br>
        <button onclick="stop()" style='background:#da3633;color:white;border:none;padding:12px 25px;border-radius:8px;cursor:pointer;font-weight:bold'>STOP</button>
        <button onclick="news()" style='margin-left:10px;padding:12px 25px;border-radius:8px;background:#333;color:white;border:none'>RESET</button>
    </div>

    <script>
        let email="";
        async function login(){
            email=document.getElementById('email').value;
            let r=await fetch('/check/'+email); let d=await r.json();
            if(d.found && d.status !== 'stopped'){ 
                document.getElementById('stats').style.display='block'; 
                document.getElementById('settings').style.display='none';
            } else { 
                document.getElementById('settings').style.display='block'; 
                document.getElementById('stats').style.display='none';
            }
        }
        async function start(){
            let d={email:email,token:token.value,symbol:'R_100',stake:parseFloat(stake.value),tp:parseFloat(tp.value)};
            await fetch('/start',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(d)}); 
            login();
        }
        async function stop(){ await fetch('/stop',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({email:email})}); }
        async function news(){ await fetch('/reset',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({email:email})}); location.reload(); }
        
        setInterval(async()=>{ 
            if(!email) return; 
            let r=await fetch('/check/'+email); let d=await r.json(); 
            if(d.found){
                const st = document.getElementById('status_badge');
                st.innerText = d.status;
                st.style.color = d.status === 'analysing' ? '#00f3ff' : (d.status === 'in trade' ? '#ffcc00' : '#ff4757');
                document.getElementById('wins_count').innerText = d.wins;
                document.getElementById('loss_count').innerText = d.losses;
                document.getElementById('s').innerHTML=`Profit: <b>${d.profit.toFixed(2)}$</b> | Stake: <b>${d.stake.toFixed(2)}$</b>`;
                document.getElementById('l').innerHTML=d.logs.reverse().join('<br>'); 
            }
        }, 1000);
    </script>
</body>
"""

@app.route("/")
def home(): return render_template_string(HTML)

@app.route("/check/<email>")
def check_email(email):
    u = users.find_one({"email": email})
    if u: u["_id"]=str(u["_id"]); return jsonify({"found": True, **u})
    return jsonify({"found": False})

@app.route("/start", methods=["POST"])
def start():
    d = request.json; users.delete_one({"email": d["email"]})
    uid = users.insert_one({
        "email": d["email"], "token": d["token"], "symbol": d["symbol"], 
        "base": round(d["stake"], 2), "stake": round(d["stake"], 2), 
        "tp": round(d["tp"], 2), "profit": 0.0, "wins": 0, "losses": 0, 
        "loss_seq": 0, "contract": None, "logs": [], "status": "analysing", "reason": ""
    }).inserted_id
    Thread(target=bot_worker, args=(str(uid),), daemon=True).start(); return jsonify({"ok": True})

@app.route("/stop", methods=["POST"])
def stop(): 
    email = request.json["email"]; 
    users.update_one({"email": email}, {"$set": {"status": "stopped", "reason": "Manual Stop"}})
    return jsonify({"ok": True})

@app.route("/reset", methods=["POST"])
def reset(): 
    email = request.json["email"]; 
    users.delete_one({"email": email})
    return jsonify({"ok": True})

if __name__ == "__main__":
    for u in users.find({"status": {"$ne": "stopped"}}): 
        Thread(target=bot_worker, args=(str(u["_id"]),), daemon=True).start()
    app.run(host="0.0.0.0", port=5000)
