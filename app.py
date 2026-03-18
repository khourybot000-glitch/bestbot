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

def bot_worker(uid):
    consecutive_losses = 0
    
    while True:
        u = users.find_one({"_id": ObjectId(uid)})
        if not u or u.get("status") == "STOPPED": break
        
        if consecutive_losses >= 5:
            set_status(uid, "STOPPED (MAX LOSS)")
            break

        now = datetime.now()
        # التحليل والتنفيذ الفوري عند الثانية 30
        if now.second == 30:
            try:
                set_status(uid, "ANALYZING & ENTERING...")
                ws = websocket.create_connection(DERIV_WS, timeout=15)
                ws.send(json.dumps({"authorize": u["token"]}))
                ws.recv() 
                
                # جلب البيانات للتحليل
                ws.send(json.dumps({"ticks_history": u["symbol"], "count": 270, "end": "latest", "style": "ticks"}))
                hist = json.loads(ws.recv())
                
                if "history" in hist:
                    prices = hist["history"]["prices"]
                    is_up = prices[-1] > prices[0]
                    is_down = prices[-1] < prices[0]
                    last_90 = prices[-90:]
                    corr_60 = last_90[:60]
                    imp_30 = last_90[-30:]

                    target = None
                    if is_up and (corr_60[-1] < corr_60[0]) and (imp_30[-1] > imp_30[0]):
                        target = "CALL"
                    elif is_down and (corr_60[-1] > corr_60[0]) and (imp_30[-1] < imp_30[0]):
                        target = "PUT"

                    if target:
                        print(f"[{now.strftime('%H:%M:%S')}] 🚀 Immediate Entry: {target}")
                        set_status(uid, f"IN TRADE ({target} 90s)")
                        
                        buy_data = {
                            "buy": 1, 
                            "price": round(float(u["stake"]), 2),
                            "parameters": {
                                "amount": round(float(u["stake"]), 2), 
                                "basis": "stake",
                                "contract_type": target, 
                                "currency": "USD",
                                "duration": 90, # مدة الصفقة 90 ثانية
                                "duration_unit": "s", 
                                "symbol": u["symbol"]
                            }
                        }
                        ws.send(json.dumps(buy_data))
                        res = json.loads(ws.recv())
                        
                        if "buy" in res:
                            cid = res["buy"]["contract_id"]
                            print(f"✅ Trade Placed! ID: {cid}")
                            
                            # ننتظر 95 ثانية (مدة الصفقة + 5 ثواني تأكيد)
                            time.sleep(95)
                            ws.send(json.dumps({"proposal_open_contract": 1, "contract_id": cid}))
                            r = json.loads(ws.recv())
                            poc = r.get("proposal_open_contract", {})
                            
                            if poc.get("is_sold"):
                                profit = float(poc.get("profit", 0))
                                if profit > 0:
                                    consecutive_losses = 0
                                    users.update_one({"_id": ObjectId(uid)}, {
                                        "$inc": {"wins": 1, "profit": profit},
                                        "$set": {"stake": u["base_stake"]}
                                    })
                                else:
                                    consecutive_losses += 1
                                    users.update_one({"_id": ObjectId(uid)}, {
                                        "$inc": {"losses": 1, "profit": -float(u["stake"])},
                                        "$set": {"stake": float(u["stake"]) * 2.2}
                                    })
                        else:
                            print(f"❌ Trade Failed: {res.get('error', {}).get('message')}")
                    else:
                        set_status(uid, "NO SIGNAL")
                
                ws.close()
                time.sleep(2) # تجنب التكرار في نفس الثانية
            except Exception as e:
                print(f"❌ Error: {e}")
                set_status(uid, "WAITING")
        
        time.sleep(0.5)

# --- Flask Routes ---
@app.route("/")
def home():
    return render_template_string("""
    <body style='background:#0d1117;color:white;text-align:center;font-family:sans-serif;padding:30px'>
        <h2 style='color:#00e676'>SILENT ALPHA - 90s ENGINE</h2>
        <div id=login_div><input id=ev placeholder="Email"><br><br><button onclick="login()">LOGIN</button></div>
        <div id=settings style='display:none'>
            <input id=tv placeholder="Deriv Token" style="width:250px"><br><br>
            <input id=sv value=1.0 placeholder="Stake"><br><br>
            <input id=tpv value=50 placeholder="TP"><br><br>
            <button onclick="start()">START BOT</button>
        </div>
        <div id=stats style='display:none'>
            <div style='font-size:30px; font-weight:bold; color:#00e676' id=st></div>
            <div id=info style='font-size:20px; margin:20px 0'></div>
            <button onclick="stop()">STOP</button>
            <button onclick="reset()" style='margin-left:10px'>RESET ALL</button>
        </div>
        <script>
            let email="";
            async function login(){ email=document.getElementById('ev').value; let r=await fetch('/check/'+email); let d=await r.json(); document.getElementById('login_div').style.display='none'; if(d.found){ document.getElementById('stats').style.display='block'; sync(); } else { document.getElementById('settings').style.display='block'; } }
            async function start(){ 
                let d={email:email, token:document.getElementById('tv').value, symbol:'R_100', stake:parseFloat(document.getElementById('sv').value), tp:parseFloat(document.getElementById('tpv').value)}; 
                await fetch('/start',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(d)}); 
                document.getElementById('settings').style.display='none'; document.getElementById('stats').style.display='block'; sync(); 
            }
            async function stop(){ fetch('/stop',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({email:email})}); location.reload(); }
            async function reset(){ if(confirm("Reset All Data?")){ await fetch('/reset',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({email:email})}); location.reload(); } }
            function sync(){ setInterval(async()=>{ let r=await fetch('/check/'+email); let d=await r.json(); if(d.found){ document.getElementById('st').innerText=d.status; document.getElementById('info').innerText=`Profit: ${d.profit.toFixed(2)}$ | W: ${d.wins} L: ${d.losses}`; } },1000); }
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
def stop(): email = request.json["email"]; users.update_one({"email": email}, {"$set": {"status": "STOPPED"}}); return jsonify({"ok": True})

@app.route("/reset", methods=["POST"])
def reset(): email = request.json["email"]; users.delete_one({"email": email}); return jsonify({"ok": True})

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
