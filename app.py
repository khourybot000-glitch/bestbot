import json, websocket, time
from datetime import datetime
from threading import Thread
from flask import Flask, render_template_string, jsonify, request
from pymongo import MongoClient
from bson.objectid import ObjectId

app = Flask(__name__)
MONGO_URI = "mongodb+srv://charbelnk111_db_user:Mano123mano@cluster0.2gzqkc8.mongodb.net/?appName=Cluster0"
client = MongoClient(MONGO_URI); db = client["KHOURY_BOT"]; users = db["users"]
DERIV_WS = "wss://blue.derivws.com/websockets/v3?app_id=16929"

def log(uid, msg):
    t = datetime.now().strftime("%H:%M:%S")
    users.update_one({"_id": ObjectId(uid)}, {"$push": {"logs": {"$each": [f"[{t}] {msg}"], "$slice": -50}}})

# دالة التحليل: U-D-U-U للـ CALL و D-U-D-D للـ PUT
def strategy_check(ticks):
    if len(ticks) < 17: return "NONE"
    recent = ticks[-17:]; res = []
    for i in range(0, 16, 4):
        o, c = recent[i], recent[i+4]
        if c > o: res.append("U")
        elif c < o: res.append("D")
        else: res.append("S")
    
    if res == ["U", "D", "U", "U"]: return "CALL"
    if res == ["D", "U", "D", "D"]: return "PUT"
    return "NONE"

def bot_worker(uid):
    while True:
        u = users.find_one({"_id": ObjectId(uid)})
        if not u or u.get("status") == "stopped": break
        
        if not u.get("contract"):
            try:
                ws = websocket.create_connection(DERIV_WS, timeout=7)
                ws.send(json.dumps({"authorize": u["token"]}))
                auth = json.loads(ws.recv())
                
                if "authorize" in auth:
                    ws.send(json.dumps({"ticks_history": u["symbol"], "count": 25, "end": "latest", "style": "ticks"}))
                    hist = json.loads(ws.recv())
                    
                    if "history" in hist:
                        sig = strategy_check(hist["history"]["prices"])
                        
                        if sig != "NONE":
                            # تعديل الحواجز لتكون نصية مع إشارة + و - بدقة
                            barrier_val = "-0.5" if sig == "CALL" else "+0.5"
                            
                            buy_params = {
                                "buy": 1, "price": round(u["stake"], 2),
                                "parameters": {
                                    "amount": round(u["stake"], 2), "basis": "stake",
                                    "contract_type": sig, 
                                    "currency": auth["authorize"]["currency"],
                                    "duration": 6, "duration_unit": "t", 
                                    "symbol": u["symbol"], 
                                    "barrier": barrier_val # هنا الإرسال الدقيق
                                }
                            }
                            ws.send(json.dumps(buy_params))
                            res = json.loads(ws.recv())
                            
                            if "buy" in res:
                                log(uid, f"🚀 {sig} Entered | Barrier: {barrier_val}")
                                users.update_one({"_id": ObjectId(uid)}, {"$set": {"contract": res["buy"]["contract_id"], "status": "in trade"}})
                            else:
                                err = res.get("error", {}).get("message", "Unknown")
                                log(uid, f"⚠️ Trade Failed: {err}")
                ws.close()
            except Exception as e:
                pass
        
        check_result(uid)
        time.sleep(1)

def check_result(uid):
    u = users.find_one({"_id": ObjectId(uid)})
    if not u or not u.get("contract"): return
    try:
        ws = websocket.create_connection(DERIV_WS)
        ws.send(json.dumps({"authorize": u["token"]}))
        ws.recv()
        ws.send(json.dumps({"proposal_open_contract": 1, "contract_id": u["contract"]}))
        r = json.loads(ws.recv()); ws.close()
        c = r.get("proposal_open_contract")
        if c and c.get("is_sold"):
            profit = float(c.get("profit", 0))
            if profit > 0:
                log(uid, f"✅ WIN {profit}$")
                users.update_one({"_id": ObjectId(uid)}, {"$set": {"contract": None, "stake": u["base"], "status": "searching"}, "$inc": {"wins": 1, "profit": profit}})
            else:
                new_stake = round(u["stake"] * 19, 2)
                log(uid, f"❌ LOSS. Next Martingale: {new_stake}$")
                users.update_one({"_id": ObjectId(uid)}, {"$set": {"contract": None, "stake": new_stake, "status": "searching"}, "$inc": {"losses": 1, "profit": profit}})
    except: pass

@app.route("/")
def home():
    return render_template_string("""
    <body style='background:#0d1117;color:white;text-align:center;font-family:sans-serif;padding:30px'>
        <h2 style='color:#58a6ff'>KHOURY V45 - BARRIER FIX</h2>
        <div id=box style='background:#161b22;padding:20px;border-radius:10px;max-width:400px;margin:auto;border:1px solid #30363d'>
            <input id=email placeholder=Email style='padding:10px;width:80%'><br><br>
            <input id=token placeholder=Token type=password style='padding:10px;width:80%'><br><br>
            <input id=stake value=0.35 type=number style='padding:10px;width:80%'><br><br>
            <button onclick="start()" style='padding:10px 40px;background:#238636;color:white;border:none;border-radius:5px;font-weight:bold;cursor:pointer'>START SNIPING</button>
        </div>
        <div id=stats style='display:none;margin-top:20px'>
            <div id=s style='font-size:20px;margin-bottom:10px'></div>
            <div id=l style='text-align:left;background:black;padding:10px;height:250px;overflow:auto;max-width:400px;margin:auto;color:#39ff14;border:1px solid #333'></div>
        </div>
        <script>
            async function start(){
                let d={email:document.getElementById('email').value,token:document.getElementById('token').value,symbol:'R_100',stake:parseFloat(document.getElementById('stake').value),tp:100};
                fetch('/start',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(d)});
                document.getElementById('box').style.display='none';
                document.getElementById('stats').style.display='block';
                setInterval(async()=>{
                    let r=await fetch('/check/'+document.getElementById('email').value);
                    let d=await r.json();
                    if(d.found){
                        document.getElementById('s').innerText = `Profit: ${d.profit.toFixed(2)}$ | Wins: ${d.wins} Losses: ${d.losses}`;
                        document.getElementById('l').innerHTML = d.logs.reverse().join('<br>');
                    }
                },1000);
            }
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
    uid = users.insert_one({"email": d["email"], "token": d["token"], "symbol": d["symbol"], "base": d["stake"], "stake": d["stake"], "tp": d["tp"], "profit": 0.0, "wins": 0, "losses": 0, "contract": None, "logs": [], "status": "searching"}).inserted_id
    Thread(target=bot_worker, args=(str(uid),), daemon=True).start(); return jsonify({"ok": True})

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
