import json, websocket, time, os
from datetime import datetime
from threading import Thread
from flask import Flask, render_template_string, jsonify, request
from pymongo import MongoClient

app = Flask(__name__)

# --- إعدادات قاعدة البيانات ---
MONGO_URI = "mongodb+srv://charbelnk111_db_user:Mano123mano@cluster0.2gzqkc8.mongodb.net/?appName=Cluster0"
client = MongoClient(MONGO_URI)
db = client["KHOURY_BOT"]
users = db["users"]
DERIV_WS = "wss://blue.derivws.com/websockets/v3?app_id=16929"

def update_bot_data(email, w, l, p, status):
    """تحديث قاعدة البيانات وإجبار الواجهة على القراءة"""
    users.update_one(
        {"email": email},
        {"$inc": {"wins": w, "losses": l, "profit": p}, "$set": {"status": status}}
    )

def get_currency(token):
    try:
        ws = websocket.create_connection(DERIV_WS, timeout=10)
        ws.send(json.dumps({"authorize": token}))
        res = json.loads(ws.recv())
        ws.close()
        return res.get('authorize', {}).get('currency', 'USD')
    except: return "USD"

def bot_worker(email):
    consecutive_losses = 0
    while True:
        u = users.find_one({"email": email})
        if not u or "STOPPED" in u.get("status") or "REACHED" in u.get("status"):
            break
        
        # فحص الهدف الربحي (Take Profit)
        if u.get("profit", 0) >= float(u.get("tp", 10)):
            update_bot_data(email, 0, 0, 0, "TARGET REACHED! 🎉")
            break

        # التحليل عند الثانية 30 فقط
        now = datetime.now()
        if now.second == 30:
            stake = float(u.get("current_stake", u["stake"]))
            try:
                ws = websocket.create_connection(DERIV_WS, timeout=15)
                ws.send(json.dumps({"authorize": u["token"]}))
                ws.recv()

                # استراتيجية الـ 15 تيك
                ws.send(json.dumps({"ticks_history": u["symbol"], "count": 15, "end": "latest", "style": "ticks"}))
                prices = json.loads(ws.recv()).get("history", {}).get("prices", [])
                
                if len(prices) >= 15:
                    last = prices[-1]
                    prev = prices[:-1]
                    high, low = max(prev), min(prev)
                    
                    side = "CALL" if last > high else "PUT" if last < low else None
                    barrier = "-0.5" if side == "CALL" else "+0.5"

                    if side:
                        update_bot_data(email, 0, 0, 0, f"ENTERING {side}...")
                        buy_data = {
                            "buy": 1, "price": stake,
                            "parameters": {
                                "amount": stake, "basis": "stake", "contract_type": side,
                                "currency": u["currency"], "duration": 6, "duration_unit": "t",
                                "symbol": u["symbol"], "barrier": barrier
                            }
                        }
                        ws.send(json.dumps(buy_data))
                        res = json.loads(ws.recv())
                        
                        if "buy" in res:
                            contract_id = res["buy"]["contract_id"]
                            time.sleep(12) # انتظار نتيجة الـ 6 تيكات
                            
                            # التحقق من النتيجة وتحديث الواجهة فوراً
                            ws_res = websocket.create_connection(DERIV_WS)
                            ws_res.send(json.dumps({"authorize": u["token"]}))
                            ws_res.recv()
                            ws_res.send(json.dumps({"proposal_open_contract": 1, "contract_id": contract_id}))
                            poc = json.loads(ws_res.recv()).get("proposal_open_contract", {})
                            
                            if poc.get("is_sold"):
                                profit = float(poc.get("profit", 0))
                                if profit > 0:
                                    # ربح: تصفير الخسارات المتتالية والعودة للمبلغ الأصلي
                                    update_bot_data(email, 1, 0, profit, "WIN! SCANNING...")
                                    users.update_one({"email": email}, {"$set": {"current_stake": float(u["stake"])}})
                                    consecutive_losses = 0
                                else:
                                    # خسارة: تفعيل الـ Martingale x19 أو الإيقاف
                                    consecutive_losses += 1
                                    update_bot_data(email, 0, 1, -stake, f"LOSS #{consecutive_losses}")
                                    if consecutive_losses >= 2:
                                        update_bot_data(email, 0, 0, 0, "STOPPED: 2 LOSSES")
                                        ws_res.close()
                                        break
                                    else:
                                        users.update_one({"email": email}, {"$set": {"current_stake": stake * 19}})
                            ws_res.close()
                        else: update_bot_data(email, 0, 0, 0, "REFUSED BY DERIV")
                    else: update_bot_data(email, 0, 0, 0, "NO BREAKOUT")
                ws.close()
            except Exception as e: print(f"Error: {e}")
            time.sleep(2) # تجنب تكرار الدخول في نفس الثانية
        time.sleep(0.5)

# --- واجهة Flask المحسنة ---
@app.route("/")
def home():
    return render_template_string("""
    <body style='background:#0d1117;color:white;text-align:center;font-family:sans-serif;padding:30px'>
        <h2 style='color:#00e676'>KHOURY BOT V16</h2>
        <div id=login_div>
            <input id=ev placeholder="Email Address" style="padding:12px; border-radius:8px; width:260px"><br><br>
            <button onclick="login()" style="padding:12px 30px; cursor:pointer; background:#238636; color:white; border:none; border-radius:8px; font-weight:bold">LOGIN & RUN</button>
        </div>
        
        <div id=stats style='display:none'>
            <div style="background:#161b22; padding:25px; border-radius:20px; border:1px solid #30363d; max-width:400px; margin:0 auto">
                <div id=st style='font-size:18px; color:#7ee787; font-weight:bold'>READY</div>
                <div id=profit_text style="font-size:55px; margin:15px 0; font-weight:bold; color:white">0.00</div>
                <div style="display:flex; justify-content:space-around; font-size:24px; background:#0d1117; padding:15px; border-radius:12px">
                    <div style="color:#7ee787">W: <span id=wins_text>0</span></div>
                    <div style="color:#ff7b72">L: <span id=loss_text>0</span></div>
                </div>
            </div>
            <br>
            <button onclick="reset()" style="background:#da3633; color:white; border:none; padding:12px 25px; border-radius:8px; font-weight:bold; cursor:pointer">RESET ALL DATA</button>
        </div>

        <script>
            let email = "";
            async function login(){
                email = document.getElementById('ev').value;
                if(!email) return alert("Email required!");
                let r = await fetch('/check/'+email);
                let d = await r.json();
                if(d.found){
                    startUI();
                } else {
                    let t = prompt("Enter Deriv API Token:"), s = prompt("Initial Stake:"), tp = prompt("Target Profit:");
                    if(!t || !s) return;
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
                if(confirm("Are you sure? This stops the bot and clears stats.")){
                    await fetch('/reset', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({email:email})});
                    location.reload();
                }
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
    curr = get_currency(d["token"])
    users.delete_one({"email": d["email"]})
    users.insert_one({
        "email": d["email"], "token": d["token"], "symbol": d["symbol"], 
        "stake": float(d["stake"]), "current_stake": float(d["stake"]),
        "tp": float(d["tp"]), "currency": curr, "profit": 0.0, "wins": 0, "losses": 0, "status": "WAITING SEC 30"
    })
    Thread(target=bot_worker, args=(d["email"],), daemon=True).start()
    return jsonify({"ok":True})

@app.route("/reset", methods=["POST"])
def reset_bot():
    users.delete_one({"email": request.json["email"]})
    return jsonify({"ok":True})

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
