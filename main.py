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

def update_db(email, wins, losses, profit, status, next_stake=None):
    """تحديث قاعدة البيانات لضمان المزامنة مع الواجهة"""
    data = {"$inc": {"wins": wins, "losses": losses, "profit": profit}, "$set": {"status": status}}
    if next_stake is not None:
        data["$set"]["current_stake"] = float(next_stake)
    users.update_one({"email": email}, data)

def get_ws_connection(token):
    """دالة لإنشاء اتصال جديد والترخيص فوراً"""
    try:
        ws = websocket.create_connection(DERIV_WS, timeout=15)
        ws.send(json.dumps({"authorize": token}))
        res = json.loads(ws.recv())
        if "error" in res:
            return None, res["error"]["message"]
        return ws, None
    except Exception as e:
        return None, str(e)

def bot_worker(email):
    consecutive_losses = 0
    while True:
        u = users.find_one({"email": email})
        if not u or "STOPPED" in str(u.get("status")): break

        # التحليل عند رأس الدقيقة (الثانية 00)
        if datetime.now().second == 0:
            u = users.find_one({"email": email})
            if not u: break
            stake = float(u.get("current_stake", u["stake"]))
            
            # 1. فتح الاتصال للتحليل والشراء
            ws, err = get_ws_connection(u["token"])
            if err:
                update_db(email, 0, 0, 0, f"CONN ERR: {err[:15]}")
                time.sleep(2)
                continue

            try:
                # جلب 90 تيك
                ws.send(json.dumps({"ticks_history": u["symbol"], "count": 90, "end": "latest", "style": "ticks"}))
                prices = json.loads(ws.recv()).get("history", {}).get("prices", [])
                
                side, barrier = None, ""
                if len(prices) >= 90:
                    p1, p2, p3 = prices[0:30], prices[30:60], prices[60:90]
                    c1 = "G" if p1[-1] > p1[0] else "R"
                    c2 = "G" if p2[-1] > p2[0] else "R"
                    c3 = "G" if p3[-1] > p3[0] else "R"
                    
                    if c1=="R" and c2=="G" and c3=="G": side, barrier = "CALL", "-0.4"
                    elif c1=="G" and c2=="R" and c3=="R": side, barrier = "PUT", "+0.4"

                    if side:
                        # تنفيذ الصفقة
                        buy_req = {"buy": 1, "price": stake, "parameters": {"amount": stake, "basis": "stake", "contract_type": side, "currency": u["currency"], "duration": 10, "duration_unit": "t", "symbol": u["symbol"], "barrier": barrier}}
                        ws.send(json.dumps(buy_req))
                        buy_res = json.loads(ws.recv())
                        
                        if "buy" in buy_res:
                            contract_id = buy_res["buy"]["contract_id"]
                            update_db(email, 0, 0, 0, f"BOUGHT {side}: WAIT 30S")
                            
                            # 2. قطع الاتصال فوراً بعد الشراء
                            ws.close()
                            
                            # 3. الانتظار لمدة 30 ثانية كما طلبت
                            time.sleep(30)
                            
                            # 4. فتح اتصال جديد تماماً لجلب النتيجة باستخدام الـ ID المخزن
                            ws_result, err2 = get_ws_connection(u["token"])
                            if not err2:
                                ws_result.send(json.dumps({"proposal_open_contract": 1, "contract_id": contract_id}))
                                poc = json.loads(ws_result.recv()).get("proposal_open_contract", {})
                                
                                if poc.get("is_sold"):
                                    profit = float(poc.get("profit", 0))
                                    buy_price = float(poc.get("buy_price", stake))

                                    if profit > 0:
                                        consecutive_losses = 0
                                        update_db(email, 1, 0, profit, "WIN! RESETTING", next_stake=u["stake"])
                                    else:
                                        consecutive_losses += 1
                                        if consecutive_losses >= 2:
                                            update_db(email, 0, 1, -buy_price, "STOPPED: L-L")
                                            ws_result.close()
                                            return
                                        else:
                                            update_db(email, 0, 1, -buy_price, "LOSS! MARTI x9", next_stake=stake*9)
                                ws_result.close()
                            else:
                                update_db(email, 0, 0, 0, "RESULT CONN ERR")
                        else:
                            update_db(email, 0, 0, 0, "REFUSED BY DERIV")
                            ws.close()
                    else:
                        update_db(email, 0, 0, 0, f"SCAN: {c1}{c2}{c3}")
                        ws.close()
                else:
                    ws.close()
            except Exception as e:
                print(f"Error: {e}")
                if 'ws' in locals(): ws.close()
            
            time.sleep(2)
        time.sleep(0.5)

# --- واجهة الويب (نفس التصميم الاحترافي) ---
@app.route("/")
def home():
    return render_template_string("""
    <body style='background:#0d1117;color:white;text-align:center;font-family:sans-serif;padding:30px'>
        <h2 style='color:#00e676'>V21 - ON-DEMAND CONNECTION</h2>
        <div id=login_div>
            <input id=ev placeholder="Email" style="padding:12px; border-radius:8px; border:1px solid #30363d; background:#161b22; color:white"><br><br>
            <button onclick="login()" style="padding:12px 25px; background:#238636; color:white; border:none; border-radius:8px; cursor:pointer">START</button>
        </div>
        <div id=stats style='display:none'>
            <div style="background:#161b22; padding:25px; border-radius:20px; border:1px solid #30363d; max-width:400px; margin:0 auto">
                <div id=st style='font-size:18px; color:#7ee787'>WAITING...</div>
                <div id=profit_text style="font-size:55px; margin:15px 0; font-weight:bold">0.00</div>
                <div style="display:flex; justify-content:space-around; font-size:24px">
                    <div style="color:#7ee787">W: <span id=wins_text>0</span></div>
                    <div style="color:#ff7b72">L: <span id=loss_text>0</span></div>
                </div>
            </div><br>
            <button onclick="reset()" style="background:#da3633; color:white; border:none; padding:12px 25px; border-radius:8px; cursor:pointer">STOP & DELETE DATA</button>
        </div>
        <script>
            let email = "";
            async function login(){
                email = document.getElementById('ev').value;
                if(!email) return alert("Email Required");
                let r = await fetch('/check/'+email);
                let d = await r.json();
                if(d.found) startUI();
                else {
                    let t = prompt("Token:"), s = prompt("Stake:"), tp = prompt("Target:");
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
                    } else { location.reload(); }
                }, 1000);
            }
            async function reset(){
                if(!confirm("Reset and Stop?")) return;
                await fetch('/reset', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({email:email})});
                location.reload();
            }
        </script>
    </body>
    """)

@app.route("/check/<email>")
def check(email):
    u = users.find_one({"email": email})
    if u: return jsonify({"found":True, "status":u.get("status",""), "profit":u.get("profit",0), "wins":u.get("wins",0), "losses":u.get("losses",0)})
    return jsonify({"found":False})

@app.route("/start", methods=["POST"])
def start():
    d = request.json
    users.delete_one({"email": d["email"]})
    users.insert_one({"email": d["email"], "token": d["token"], "symbol": d["symbol"], "stake": float(d["stake"]), "current_stake": float(d["stake"]), "tp": float(d["tp"]), "currency": "USD", "profit": 0.0, "wins": 0, "losses": 0, "status": "WAITING SEC 00"})
    Thread(target=bot_worker, args=(d["email"],), daemon=True).start()
    return jsonify({"ok":True})

@app.route("/reset", methods=["POST"])
def reset_bot():
    users.delete_one({"email": request.json.get("email")})
    return jsonify({"ok":True})

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
