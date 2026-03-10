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

DERIV_WS = "wss://ws.binaryws.com/websockets/v3?app_id=1089"

def log(uid, msg):
    t = datetime.now().strftime("%H:%M:%S")
    users.update_one({"_id": ObjectId(uid)}, {"$push": {"logs": {"$each": [f"[{t}] {msg}"], "$slice": -50}}})

def strategy_7_6(ticks):
    if len(ticks) < 65: return "NONE", 0
    up, down = 0, 0
    for i in range(0, 65, 5):
        seg = ticks[i:i+5]
        if len(seg) < 2: continue
        if seg[-1] > seg[0]: up += 1
        elif seg[-1] < seg[0]: down += 1
    if up == 7 and down == 6: return "CALL", -0.5
    if down == 7 and up == 6: return "PUT", 0.5
    return "NONE", 0

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
            users.update_one({"_id": ObjectId(uid)}, {"$set": {"contract": None, "profit": total_p, "wins": u["wins"] + 1, "stake": round(u["base"], 2), "loss_seq": 0}})
        else:
            loss_seq = u["loss_seq"] + 1
            new_stake = round(u["stake"] * 19, 2) # مضاعفة x19
            log(uid, f"❌ LOSS. Next stake (x19): {new_stake}$")
            users.update_one({"_id": ObjectId(uid)}, {"$set": {"contract": None, "stake": new_stake, "loss_seq": loss_seq}, "$inc": {"losses": 1, "profit": profit}})
            if loss_seq >= 2:
                users.update_one({"_id": ObjectId(uid)}, {"$set": {"status": "stopped", "reason": "2 consecutive losses"}})
        
        if total_p >= u["tp"]:
            users.update_one({"_id": ObjectId(uid)}, {"$set": {"status": "stopped", "reason": "Target TP reached"}})
    except: pass

def bot_worker(uid):
    while True:
        u = users.find_one({"_id": ObjectId(uid)})
        if not u or u.get("status") != "running": break
        
        # تحليل عند الثانية 55
        if datetime.now().second == 55:
            try:
                ws = websocket.create_connection(DERIV_WS)
                ws.send(json.dumps({"ticks_history": u["symbol"], "count": 65, "style": "ticks"}))
                r = json.loads(ws.recv()); ws.close()
                if "history" in r:
                    sig, bar = strategy_7_6(r["history"]["prices"])
                    if sig != "NONE" and not u.get("contract"):
                        ws = websocket.create_connection(DERIV_WS)
                        ws.send(json.dumps({"authorize": u["token"]}))
                        auth = json.loads(ws.recv())
                        ws.send(json.dumps({
                            "buy": 1, 
                            "price": round(u["stake"], 2), 
                            "parameters": {
                                "amount": round(u["stake"], 2), 
                                "basis": "stake", 
                                "contract_type": sig, 
                                "currency": auth["authorize"]["currency"], 
                                "duration": 5,           # تم التعديل إلى 5
                                "duration_unit": "t",    # تم التعديل إلى تيكات (ticks)
                                "symbol": u["symbol"], 
                                "barrier": bar
                            }
                        }))
                        trade = json.loads(ws.recv()); ws.close()
                        if "buy" in trade:
                            log(uid, f"🚀 ENTER {sig} (5 Ticks) @ {u['stake']}$")
                            users.update_one({"_id": ObjectId(uid)}, {"$set": {"contract": trade["buy"]["contract_id"]}})
                time.sleep(5) # منع التكرار في نفس الدقيقة
            except: pass
        time.sleep(0.5); check_contract(uid)

# --- الواجهة وباقي الراوتات تبقى كما هي ---
@app.route("/")
def home():
    return render_template_string("""
    <body style='background:#0b1016;color:white;text-align:center;font-family:sans-serif;padding:50px'>
        <h2>KHOURY 5-TICK SNIPER (x19)</h2>
        <input id=email placeholder=Email style='padding:10px;border-radius:5px;border:none'><br><br>
        <button onclick="login()" style='padding:10px 20px;background:#238636;color:white;border:none;border-radius:5px;cursor:pointer'>LOGIN</button>
        <div id=settings style='display:none;margin-top:20px'>
            <input id=token placeholder="API Token" type=password style='padding:10px;margin:5px'><br>
            <input id=stake placeholder="Stake" type=number value=0.35 style='padding:10px;margin:5px'><br>
            <input id=tp placeholder="TP" type=number value=10 style='padding:10px;margin:5px'><br>
            <button onclick="start()" style='padding:10px 20px;background:#238636;color:white;border:none;border-radius:5px'>START BOT</button>
        </div>
        <div id=stats style='display:none;margin-top:20px;background:#000;padding:20px;border-radius:10px'>
            <div id=res style='color:red;font-weight:bold'></div><div id=s></div><div id=l style='text-align:left;color:#39ff14;font-size:12px;margin-top:10px'></div>
            <br><button onclick="stop()" style='background:#da3633;color:white;border:none;padding:10px'>STOP</button>
            <button onclick="news()" style='margin-left:10px'>NEW SESSION</button>
        </div>
        <script>
            let email="";
            async function login(){
                email=document.getElementById('email').value;
                let r=await fetch('/check/'+email); let d=await r.json();
                if(d.found && d.status==='running'){ document.getElementById('stats').style.display='block'; }
                else { document.getElementById('settings').style.display='block'; }
            }
            async function start(){
                let d={email:email,token:token.value,symbol:'R_100',stake:parseFloat(stake.value),tp:parseFloat(tp.value)};
                await fetch('/start',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(d)}); location.reload();
            }
            async function stop(){ await fetch('/stop',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({email:email})}); }
            async function news(){ await fetch('/reset',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({email:email})}); location.reload(); }
            setInterval(async()=>{ if(!email)return; let r=await fetch('/check/'+email); let d=await r.json(); if(d.found){
                document.getElementById('s').innerHTML=`Profit: ${d.profit.toFixed(2)}$ | Next Stake: ${d.stake}$`;
                document.getElementById('l').innerHTML=d.logs.reverse().join('<br>'); document.getElementById('res').innerText=d.reason;
            }},1000);
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
    uid = users.insert_one({"email": d["email"], "token": d["token"], "symbol": d["symbol"], "base": round(d["stake"], 2), "stake": round(d["stake"], 2), "tp": round(d["tp"], 2), "profit": 0.0, "wins": 0, "losses": 0, "loss_seq": 0, "contract": None, "logs": [], "status": "running", "reason": ""}).inserted_id
    Thread(target=bot_worker, args=(str(uid),), daemon=True).start(); return jsonify({"ok": True})

@app.route("/stop", methods=["POST"])
def stop(): email = request.json["email"]; users.update_one({"email": email}, {"$set": {"status": "stopped", "reason": "Manual Stop"}}); return jsonify({"ok": True})

@app.route("/reset", methods=["POST"])
def reset(): email = request.json["email"]; users.delete_one({"email": email}); return jsonify({"ok": True})

if __name__ == "__main__":
    for u in users.find({"status": "running"}): Thread(target=bot_worker, args=(str(u["_id"]),), daemon=True).start()
    app.run(host="0.0.0.0", port=5000)
