import json, websocket, time, os
from datetime import datetime
from threading import Thread
from flask import Flask, render_template_string, jsonify, request
from pymongo import MongoClient

app = Flask(__name__)

# --- MongoDB ---
MONGO_URI = "mongodb+srv://charbelnk111_db_user:Mano123mano@cluster0.2gzqkc8.mongodb.net/?appName=Cluster0"
client = MongoClient(MONGO_URI)
db = client["KHOURY_BOT"]
users = db["users"]
DERIV_WS = "wss://blue.derivws.com/websockets/v3?app_id=16929"

def update_db(email, wins, losses, profit, status, next_stake=None):
    """تحديث قاعدة البيانات لضمان ظهور النتائج في الواجهة"""
    data = {"$inc": {"wins": wins, "losses": losses, "profit": profit}, "$set": {"status": status}}
    if next_stake is not None:
        data["$set"]["current_stake"] = float(next_stake)
    users.update_one({"email": email}, data)

def bot_worker(email):
    consecutive_losses = 0
    while True:
        u = users.find_one({"email": email})
        if not u or "STOPPED" in u.get("status"): break

        if datetime.now().second == 30:
            u = users.find_one({"email": email}) 
            stake = float(u.get("current_stake", u["stake"]))
            
            try:
                ws = websocket.create_connection(DERIV_WS, timeout=20)
                ws.send(json.dumps({"authorize": u["token"]}))
                ws.recv()

                # تحليل 15 تيك
                ws.send(json.dumps({"ticks_history": u["symbol"], "count": 15, "end": "latest", "style": "ticks"}))
                prices = json.loads(ws.recv()).get("history", {}).get("prices", [])
                
                if len(prices) >= 15:
                    last, prev = prices[-1], prices[:-1]
                    h, l = max(prev), min(prev)
                    
                    side = "CALL" if last > h else "PUT" if last < l else None
                    if side:
                        barrier = "-0.5" if side == "CALL" else "+0.5"
                        update_db(email, 0, 0, 0, f"ENTRY: {side} (${stake})")
                        
                        buy_req = {"buy": 1, "price": stake, "parameters": {"amount": stake, "basis": "stake", "contract_type": side, "currency": u["currency"], "duration": 6, "duration_unit": "t", "symbol": u["symbol"], "barrier": barrier}}
                        ws.send(json.dumps(buy_req))
                        res = json.loads(ws.recv())
                        
                        if "buy" in res:
                            cid = res["buy"]["contract_id"]
                            time.sleep(15) # انتظار انتهاء التيكات
                            
                            # --- فحص النتيجة بدقة (عن طريق عقد العقد المفتوح) ---
                            ws.send(json.dumps({"proposal_open_contract": 1, "contract_id": cid}))
                            poc_res = json.loads(ws.recv())
                            poc = poc_res.get("proposal_open_contract", {})
                            
                            if poc.get("is_sold"):
                                sell_price = float(poc.get("sell_price", 0))
                                buy_price = float(poc.get("buy_price", stake))
                                profit = float(poc.get("profit", 0))

                                # ✅ القاعدة الذهبية: إذا كان سعر البيع (0) أو أقل من الشراء فهي خسارة
                                if profit > 0:
                                    consecutive_losses = 0
                                    update_db(email, 1, 0, profit, "WIN! NEXT CYCLE", next_stake=u["stake"])
                                else:
                                    # خسارة مؤكدة
                                    consecutive_losses += 1
                                    if consecutive_losses >= 2:
                                        update_db(email, 0, 1, -buy_price, "STOPPED: 2 LOSSES")
                                        ws.close()
                                        return
                                    else:
                                        update_db(email, 0, 1, -buy_price, "LOSS! MARTINGALE x19", next_stake=stake*19)
                            else:
                                update_db(email, 0, 0, 0, "RESULT DELAYED...")
                        else:
                            update_db(email, 0, 0, 0, "REFUSED BY DERIV")
                ws.close()
            except Exception as e:
                print(f"Error: {e}")
            time.sleep(2)
        time.sleep(0.5)

# --- الواجهة ---
@app.route("/")
def home():
    return render_template_string("""
    <body style='background:#0d1117;color:white;text-align:center;font-family:sans-serif;padding:30px'>
        <h2 style='color:#00e676'>V19 - LOSS DETECTION FIXED</h2>
        <div id=login_div>
            <input id=ev placeholder="Email" style="padding:12px; border-radius:8px"><br><br>
            <button onclick="login()" style="padding:12px 25px; background:#238636; color:white; border:none; border-radius:8px">START</button>
        </div>
        <div id=stats style='display:none'>
            <div style="background:#161b22; padding:25px; border-radius:20px; border:1px solid #30363d; max-width:400px; margin:0 auto">
                <div id=st style='font-size:18px; color:#7ee787'>WAITING SEC 30...</div>
                <div id=profit_text style="font-size:55px; margin:15px 0; font-weight:bold">0.00</div>
                <div style="display:flex; justify-content:space-around; font-size:24px">
                    <div style="color:#7ee787">W: <span id=wins_text>0</span></div>
                    <div style="color:#ff7b72">L: <span id=loss_text>0</span></div>
                </div>
            </div><br>
            <button onclick="reset()" style="background:#30363d; color:white; border:none; padding:10px 20px; border-radius:8px">RESET</button>
        </div>
        <script>
            let email = "";
            async function login(){
                email = document.getElementById('ev').value;
                let r = await fetch('/check/'+email);
                let d = await r.json();
                if(d.found) startUI();
                else {
                    let t = prompt("Token:"), s = prompt("Stake:"), tp = prompt("Target:");
                    await fetch('/start', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({email:email, token:t, stake:s, tp:tp, symbol:'R_100'})});
                    startUI();
                }
            }
            function startUI(){
                document.getElementById('login_div').style.display='none';
                document.getElementById('stats').style.display='block';
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
            async function reset(){
                await fetch('/reset', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({email:email})});
                location.reload();
            }
        </script>
    </body>
    """)

@app.route("/check/<email>")
def check(email):
    u = users.find_one({"email": email})
    if u: return jsonify({"found":True, "status":u["status"], "profit":u["profit"], "wins":u["wins"], "losses":u["losses"]})
    return jsonify({"found":False})

@app.route("/start", methods=["POST"])
def start():
    d = request.json
    users.delete_one({"email": d["email"]})
    users.insert_one({"email": d["email"], "token": d["token"], "symbol": d["symbol"], "stake": float(d["stake"]), "current_stake": float(d["stake"]), "tp": float(d["tp"]), "currency": "USD", "profit": 0.0, "wins": 0, "losses": 0, "status": "READY"})
    Thread(target=bot_worker, args=(d["email"],), daemon=True).start()
    return jsonify({"ok":True})

@app.route("/reset", methods=["POST"])
def reset_bot():
    users.delete_one({"email": request.json["email"]})
    return jsonify({"ok":True})

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
