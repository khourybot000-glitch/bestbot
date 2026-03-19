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
        return res.get('authorize', {}).get('currency', 'USD')
    except: return "USD"

def bot_worker(uid):
    user_id = ObjectId(uid)
    consecutive_losses = 0
    current_stake = None

    while True:
        u = users.find_one({"_id": user_id})
        if not u or "STOPPED" in u.get("status") or "REACHED" in u.get("status"): break
        
        if current_stake is None: current_stake = float(u["stake"])

        # فحص الهدف الربحي
        if u.get("profit", 0) >= float(u.get("tp", 999)):
            set_status(uid, "TARGET REACHED! 🎉")
            break

        now = datetime.now()
        # التحليل فقط عند الثانية 30
        if now.second == 30:
            try:
                ws = websocket.create_connection(DERIV_WS, timeout=10)
                ws.send(json.dumps({"authorize": u["token"]}))
                ws.recv()

                # جلب 15 تيك
                ws.send(json.dumps({"ticks_history": u["symbol"], "count": 15, "end": "latest", "style": "ticks"}))
                res = json.loads(ws.recv())
                
                if "history" in res:
                    prices = res["history"]["prices"]
                    last_tick = prices[-1]
                    previous_14 = prices[:-1]
                    
                    high = max(previous_14)
                    low = min(previous_14)
                    
                    direction = None
                    barrier = ""

                    if last_tick > high:
                        direction = "CALL"
                        barrier = "-0.5"
                    elif last_tick < low:
                        direction = "PUT"
                        barrier = "+0.5"

                    if direction:
                        set_status(uid, f"BREAKOUT {direction} | STAKE: {current_stake}")
                        
                        buy_req = {
                            "buy": 1, "price": current_stake,
                            "parameters": {
                                "amount": current_stake, "basis": "stake",
                                "contract_type": direction, "currency": u["currency"],
                                "duration": 6, "duration_unit": "t",
                                "symbol": u["symbol"], "barrier": barrier
                            }
                        }
                        ws.send(json.dumps(buy_req))
                        buy_res = json.loads(ws.recv())
                        
                        if "buy" in buy_res:
                            cid = buy_res["buy"]["contract_id"]
                            ws.close()
                            time.sleep(12) # انتظار 6 تيكات
                            
                            # فحص النتيجة
                            ws_res = websocket.create_connection(DERIV_WS, timeout=10)
                            ws_res.send(json.dumps({"authorize": u["token"]}))
                            ws_res.recv()
                            ws_res.send(json.dumps({"proposal_open_contract": 1, "contract_id": cid}))
                            poc = json.loads(ws_res.recv()).get("proposal_open_contract", {})
                            
                            if poc.get("is_sold"):
                                profit = float(poc.get("profit", 0))
                                if profit > 0:
                                    users.update_one({"_id": user_id}, {"$inc": {"wins": 1, "profit": profit}})
                                    set_status(uid, "WIN! RESETTING STAKE")
                                    current_stake = float(u["stake"]) # إعادة المبلغ للأصلي
                                    consecutive_losses = 0
                                else:
                                    loss_amount = current_stake
                                    consecutive_losses += 1
                                    users.update_one({"_id": user_id}, {"$inc": {"losses": 1, "profit": -loss_amount}})
                                    
                                    if consecutive_losses >= 2:
                                        set_status(uid, "STOPPED: 2 LOSSES IN ROW")
                                        ws_res.close()
                                        break
                                    else:
                                        current_stake *= 19 # مضاعفة × 19
                                        set_status(uid, f"LOSS! MARTINGALE x19")
                            ws_res.close()
                        else: set_status(uid, "REFUSED BY BROKER")
                    else:
                        set_status(uid, "NO BREAKOUT AT SEC 30")
                
                if ws: ws.close()
            except: pass
            time.sleep(2) # منع التكرار في نفس الثانية

        time.sleep(0.5)

# --- الواجهة البرمجية ---
@app.route("/")
def home():
    return render_template_string("""
    <body style='background:#0d1117;color:white;text-align:center;font-family:sans-serif;padding:20px'>
        <h2 style='color:#00e676'>SEC-30 BREAKOUT V13</h2>
        <div id=login_div>
            <input id=ev placeholder="Email" style="padding:10px; border-radius:5px">
            <button onclick="login()" style="padding:10px 20px">ACCESS</button>
        </div>
        <div id=settings style='display:none'>
            <input id=tv placeholder="API Token" style="width:280px; padding:10px"><br><br>
            <input id=stk_v value="1.0" type="number" style="width:130px; padding:10px">
            <input id=tp_v value="10.0" type="number" style="width:130px; padding:10px"><br><br>
            <button onclick="start()" style="background:#238636; color:white; padding:15px; width:280px; border:none; border-radius:8px">START BOT</button>
        </div>
        <div id=stats style='display:none'>
            <div style="background:#161b22; padding:20px; border-radius:15px; border:1px solid #30363d; max-width:400px; margin:0 auto">
                <div id=st style='font-size:18px; color:#00e676'>WAITING FOR SEC 30...</div>
                <div id=profit_text style="font-size:45px; font-weight:bold; margin:10px 0">0.00</div>
                <div style="display:flex; justify-content:space-around; font-size:20px">
                    <div style="color:#7ee787">W: <span id=wins_text>0</span></div>
                    <div style="color:#ff7b72">L: <span id=loss_text>0</span></div>
                </div>
            </div><br>
            <button onclick="resetBot()" style="background:#30363d; color:white; border:none; padding:10px 20px; border-radius:8px">RESET & EXIT</button>
        </div>
        <script>
            let email = "";
            async function login(){
                email = document.getElementById('ev').value;
                let r = await fetch('/check/'+email);
                let d = await r.json();
                document.getElementById('login_div').style.display='none';
                if(d.found){ document.getElementById('stats').style.display='block'; sync(); }
                else { document.getElementById('settings').style.display='block'; }
            }
            async function start(){
                let d = {email:email, token:document.getElementById('tv').value, stake:document.getElementById('stk_v').value, tp:document.getElementById('tp_v').value, symbol:'R_100'};
                await fetch('/start', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(d)});
                document.getElementById('settings').style.display='none';
                document.getElementById('stats').style.display='block';
                sync();
            }
            async function resetBot(){
                await fetch('/reset', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({email:email})});
                location.reload();
            }
            function sync(){
                setInterval(async()=>{
                    let r = await fetch('/check/'+email);
                    let d = await r.json();
                    if(d.found){
                        document.getElementById('st').innerText = d.status;
                        document.getElementById('profit_text').innerText = d.profit.toFixed(2);
                        document.getElementById('wins_text').innerText = d.wins;
                        document.getElementById('loss_text').innerText = d.losses;
                    }
                }, 1000);
            }
        </script>
    </body>
    """)

@app.route("/check/<email>")
def check(email):
    u = users.find_one({"email": email})
    if u: u["_id"] = str(u["_id"]); return jsonify({"found": True, **u})
    return jsonify({"found": False})

@app.route("/start", methods=["POST"])
def start():
    d = request.json
    curr = get_account_currency(d["token"])
    users.delete_one({"email": d["email"]})
    uid = users.insert_one({"email": d["email"], "token": d["token"], "symbol": d["symbol"], "stake": float(d["stake"]), "tp": float(d["tp"]), "currency": curr, "profit": 0.0, "wins": 0, "losses": 0, "status": "WAITING SEC 30"}).inserted_id
    Thread(target=bot_worker, args=(str(uid),), daemon=True).start()
    return jsonify({"ok": True})

@app.route("/reset", methods=["POST"])
def reset():
    users.delete_one({"email": request.json["email"]})
    return jsonify({"ok": True})

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))
