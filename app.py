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

def get_account_currency(token):
    try:
        ws = websocket.create_connection(DERIV_WS, timeout=10)
        ws.send(json.dumps({"authorize": token}))
        res = json.loads(ws.recv())
        currency = res.get('authorize', {}).get('currency', 'USD')
        ws.close()
        return currency
    except: return "USD"

def bot_worker(uid):
    pending_direction = None
    
    while True:
        u = users.find_one({"_id": ObjectId(uid)})
        if not u or "STOPPED" in u.get("status") or "REACHED" in u.get("status"):
            break

        # فحص الهدف (Target Profit)
        if u.get("profit", 0) >= float(u.get("tp", 999999)):
            set_status(uid, "TARGET REACHED! 🎉")
            break

        now = datetime.now()
        if now.second % 10 == 0:
            try:
                ws = websocket.create_connection(DERIV_WS, timeout=10)
                ws.send(json.dumps({"authorize": u["token"]}))
                ws.recv()

                ws.send(json.dumps({"ticks_history": u["symbol"], "count": 5, "end": "latest", "style": "ticks"}))
                res = json.loads(ws.recv())
                
                if "history" in res:
                    prices = res["history"]["prices"]
                    diff = round(prices[-1] - prices[0], 3)
                    current_dir = "CALL" if diff > 0 else "PUT"

                    if not pending_direction:
                        if abs(diff) >= 0.5:
                            pending_direction = current_dir
                            set_status(uid, f"SIGNAL: {current_dir} (WAIT)")
                        else:
                            set_status(uid, "SCANNING (IDLE)...")
                    else:
                        if current_dir == pending_direction:
                            set_status(uid, f"CONFIRMED! BUYING {current_dir}")
                            barrier = "-0.7" if current_dir == "CALL" else "+0.7"
                            
                            buy_req = {
                                "buy": 1, "price": float(u["stake"]),
                                "parameters": {
                                    "amount": float(u["stake"]), "basis": "stake",
                                    "contract_type": current_dir, "currency": u["currency"],
                                    "duration": 6, "duration_unit": "t",
                                    "symbol": u["symbol"], "barrier": barrier
                                }
                            }
                            ws.send(json.dumps(buy_req))
                            buy_res = json.loads(ws.recv())
                            
                            if "buy" in buy_res:
                                cid = buy_res["buy"]["contract_id"]
                                ws.close()
                                time.sleep(12) 
                                
                                ws_check = websocket.create_connection(DERIV_WS, timeout=10)
                                ws_check.send(json.dumps({"authorize": u["token"]}))
                                ws_check.recv()
                                ws_check.send(json.dumps({"proposal_open_contract": 1, "contract_id": cid}))
                                poc = json.loads(ws_check.recv()).get("proposal_open_contract", {})
                                
                                if poc.get("is_sold"):
                                    profit = float(poc.get("profit", 0))
                                    if profit > 0:
                                        users.update_one({"_id": ObjectId(uid)}, {"$inc": {"wins": 1, "profit": profit}})
                                        set_status(uid, "WIN! SCANNING...")
                                    else:
                                        users.update_one({"_id": ObjectId(uid)}, {"$inc": {"losses": 1, "profit": -float(u["stake"])}})
                                        set_status(uid, "STOPPED (LOSS)")
                                        ws_check.close()
                                        break
                                ws_check.close()
                            pending_direction = None
                        else:
                            pending_direction = current_dir if abs(diff) >= 0.7 else None
                            set_status(uid, "RESET (DIRECTION CHANGE)")
                
                if ws: ws.close()
            except: pass
            time.sleep(1.5)
        time.sleep(0.1)

# --- واجهة المستخدم ---
@app.route("/")
def home():
    return render_template_string("""
    <body style='background:#0d1117;color:white;text-align:center;font-family:sans-serif;padding:20px'>
        <h2 style='color:#00e676'>SMART SNIPER V11</h2>
        
        <div id=login_div>
            <input id=ev placeholder="Email" style="padding:12px; border-radius:8px; border:none; width:250px"><br><br>
            <button onclick="login()" style="padding:12px 25px; cursor:pointer; background:#21262d; color:white; border:1px solid #30363d; border-radius:8px">ACCESS BOT</button>
        </div>
        
        <div id=settings style='display:none'>
            <input id=tv placeholder="Deriv API Token" style="width:280px; padding:12px; border-radius:8px; border:none"><br><br>
            <div style="display:flex; justify-content:center; gap:10px">
                <input id=stk_v value="1.0" type="number" placeholder="Stake" style="width:130px; padding:12px; border-radius:8px">
                <input id=tp_v value="10.0" type="number" placeholder="Target" style="width:130px; padding:12px; border-radius:8px">
            </div><br>
            <button onclick="start()" style="background:#238636; padding:12px; border:none; border-radius:8px; width:280px; color:white; font-weight:bold">START MONITORING</button>
        </div>

        <div id=stats style='display:none'>
            <div style="background:#161b22; padding:20px; border-radius:15px; border:1px solid #30363d; max-width:400px; margin:0 auto">
                <div id=st style='font-size:22px; font-weight:bold; color:#00e676'>WAITING FOR CYCLE...</div>
                <hr style="border:0.5px solid #30363d; margin:15px 0">
                <div style="font-size:45px; margin:10px 0; font-weight:bold" id=profit_text>0.00 $</div>
                <div style="color:#8b949e; margin-bottom:15px">Goal: <span id=target_text>0</span> | Acc: <span id=curr_text>---</span></div>
                <div style="display:flex; justify-content:space-around; font-size:22px; background:#0d1117; padding:10px; border-radius:10px">
                    <div style="color:#7ee787">W: <span id=wins_text>0</span></div>
                    <div style="color:#ff7b72">L: <span id=loss_text>0</span></div>
                </div>
            </div>
            <br>
            <button onclick="stopBot()" style="background:#da3633; color:white; border:none; padding:12px 25px; border-radius:8px; font-weight:bold">STOP</button>
            <button onclick="resetBot()" style="background:#30363d; color:white; border:none; padding:12px 25px; border-radius:8px; font-weight:bold; margin-left:10px">RESET DATA</button>
        </div>

        <script>
            let email = "";
            let syncInterval;

            async function login(){
                email = document.getElementById('ev').value;
                if(!email) return alert("Please enter email");
                let r = await fetch('/check/'+email);
                let d = await r.json();
                document.getElementById('login_div').style.display = 'none';
                if(d.found){
                    document.getElementById('stats').style.display = 'block';
                    startSync();
                } else {
                    document.getElementById('settings').style.display = 'block';
                }
            }

            async function start(){
                let t = document.getElementById('tv').value;
                let s = document.getElementById('stk_v').value;
                let tp = document.getElementById('tp_v').value;
                if(!t) return alert("Token Required");
                await fetch('/start', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({email: email, token: t, stake: s, tp: tp, symbol: 'R_100'})
                });
                document.getElementById('settings').style.display = 'none';
                document.getElementById('stats').style.display = 'block';
                startSync();
            }

            async function stopBot(){
                await fetch('/stop', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({email: email})});
            }

            async function resetBot(){
                if(confirm("This will delete all trade history and settings. Continue?")){
                    await fetch('/reset', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({email: email})});
                    location.reload();
                }
            }

            function startSync(){
                if(syncInterval) clearInterval(syncInterval);
                syncInterval = setInterval(async()=>{
                    try {
                        let r = await fetch('/check/'+email);
                        let d = await r.json();
                        if(d.found){
                            document.getElementById('st').innerText = d.status;
                            document.getElementById('profit_text').innerText = d.profit.toFixed(2) + " " + (d.currency || "$");
                            document.getElementById('target_text').innerText = d.tp.toFixed(2);
                            document.getElementById('curr_text').innerText = d.currency;
                            document.getElementById('wins_text').innerText = d.wins;
                            document.getElementById('loss_text').innerText = d.losses;
                            
                            if(d.status.includes("STOPPED") || d.status.includes("REACHED")) {
                                document.getElementById('st').style.color = "#ff7b72";
                            } else {
                                document.getElementById('st').style.color = "#7ee787";
                            }
                        }
                    } catch(e) {}
                }, 1000);
            }
        </script>
    </body>
    """)

@app.route("/check/<email>")
def check(email):
    u = users.find_one({"email": email})
    if u: u["_id"]=str(u["_id"]); return jsonify({"found": True, **u})
    return jsonify({"found": False})

@app.route("/start", methods=["POST"])
def start():
    d = request.json
    currency = get_account_currency(d["token"])
    users.delete_one({"email": d["email"]})
    uid = users.insert_one({
        "email": d["email"], "token": d["token"], "symbol": d["symbol"], 
        "stake": float(d["stake"]), "tp": float(d["tp"]), "currency": currency,
        "profit": 0.0, "wins": 0, "losses": 0, "status": "INITIALIZING..."
    }).inserted_id
    Thread(target=bot_worker, args=(str(uid),), daemon=True).start()
    return jsonify({"ok": True})

@app.route("/stop", methods=["POST"])
def stop():
    email = request.json["email"]
    users.update_one({"email": email}, {"$set": {"status": "STOPPED MANUALLY"}})
    return jsonify({"ok": True})

@app.route("/reset", methods=["POST"])
def reset():
    email = request.json["email"]
    users.delete_one({"email": email})
    return jsonify({"ok": True})

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))
