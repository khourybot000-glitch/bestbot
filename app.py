import json, websocket, time, os
from datetime import datetime
from threading import Thread
from flask import Flask, render_template_string, jsonify, request
from pymongo import MongoClient
from bson.objectid import ObjectId

app = Flask(__name__)

# --- MongoDB Configuration ---
# ملاحظة: تأكد من أن هذا الرابط يعمل وصحيح في حسابك على MongoDB Atlas
MONGO_URI = "mongodb+srv://charbelnk111_db_user:Mano123mano@cluster0.2gzqkc8.mongodb.net/?appName=Cluster0"
client = MongoClient(MONGO_URI)
db = client["KHOURY_BOT"]
users = db["users"]
DERIV_WS = "wss://blue.derivws.com/websockets/v3?app_id=16929"

def set_status(uid, status):
    users.update_one({"_id": ObjectId(uid)}, {"$set": {"status": status}})

def get_ws_connection(token):
    try:
        ws = websocket.create_connection(DERIV_WS, timeout=10)
        ws.send(json.dumps({"authorize": token}))
        ws.recv()
        return ws
    except Exception as e:
        print(f"Connection Error: {e}")
        return None

def bot_worker(uid):
    last_price = None
    
    while True:
        u = users.find_one({"_id": ObjectId(uid)})
        if not u or "STOPPED" in u.get("status"):
            break

        # فتح اتصال سريع لمراقبة التيك
        ws = get_ws_connection(u["token"])
        if not ws:
            time.sleep(1)
            continue

        try:
            ws.send(json.dumps({"ticks": u["symbol"], "count": 1}))
            res = json.loads(ws.recv())
            
            if "tick" in res:
                current_price = float(res["tick"]["quote"])
                
                if last_price is not None:
                    diff = current_price - last_price
                    abs_diff = abs(diff)

                    # شرط الدخول: فرق 0.2 أو أكثر
                    if abs_diff >= 0.15:
                        direction = "CALL" if diff > 0 else "PUT"
                        barrier = "-0.7" if direction == "CALL" else "+0.7"
                        
                        set_status(uid, f"SPIKE {diff:.2f}! ENTERING {direction}")

                        # تنفيذ الصفقة (6 تيكات)
                        buy_req = {
                            "buy": 1, 
                            "price": float(u["stake"]),
                            "parameters": {
                                "amount": float(u["stake"]), 
                                "basis": "stake",
                                "contract_type": direction, 
                                "currency": "USD",
                                "duration": 6, 
                                "duration_unit": "t",
                                "symbol": u["symbol"], 
                                "barrier": barrier
                            }
                        }
                        ws.send(json.dumps(buy_req))
                        buy_res = json.loads(ws.recv())
                        
                        if "buy" in buy_res:
                            cid = buy_res["buy"]["contract_id"]
                            ws.close() # قطع الاتصال فوراً
                            
                            time.sleep(12) # انتظار انتهاء 6 تيكات
                            
                            # فتح اتصال جديد لفحص النتيجة
                            ws_res = get_ws_connection(u["token"])
                            if ws_res:
                                ws_res.send(json.dumps({"proposal_open_contract": 1, "contract_id": cid}))
                                r = json.loads(ws_res.recv())
                                poc = r.get("proposal_open_contract", {})
                                
                                if poc.get("is_sold"):
                                    profit = float(poc.get("profit", 0))
                                    if profit > 0:
                                        users.update_one({"_id": ObjectId(uid)}, {
                                            "$inc": {"wins": 1, "profit": profit}
                                        })
                                        set_status(uid, "WIN! SCANNING...")
                                    else:
                                        # التوقف بعد خسارة واحدة كما طلبت
                                        users.update_one({"_id": ObjectId(uid)}, {
                                            "$inc": {"losses": 1, "profit": -float(u["stake"])}
                                        })
                                        set_status(uid, "STOPPED (LOSS DETECTED)")
                                        ws_res.close()
                                        break 
                                ws_res.close()
                        else:
                            set_status(uid, f"ERROR: {buy_res.get('error', {}).get('message')}")
                
                last_price = current_price
            
            if ws: ws.close()
        except:
            if ws: ws.close()
        
        time.sleep(0.5)

# --- Flask Interface ---
@app.route("/")
def home():
    return render_template_string("""
    <!DOCTYPE html>
    <html>
    <head>
        <title>SPIKE SNIPER V4</title>
        <meta name="viewport" content="width=device-width, initial-scale=1">
        <style>
            body { background:#0d1117; color:white; text-align:center; font-family:sans-serif; padding:20px; }
            input { padding:12px; margin:10px; border-radius:5px; border:1px solid #30363d; background:#161b22; color:white; width:80%; max-width:300px; }
            button { padding:12px 25px; border-radius:5px; border:none; cursor:pointer; font-weight:bold; margin:10px; }
            .btn-start { background:#238636; color:white; }
            .btn-login { background:#21262d; color:#c9d1d9; border:1px solid #30363d; }
            .btn-stop { background:#da3633; color:white; }
            .btn-reset { background:#30363d; color:white; }
            #stats-box { background:#161b22; padding:20px; border-radius:10px; margin-top:20px; border:1px solid #30363d; }
        </style>
    </head>
    <body>
        <h2 style='color:#00e676'>SPIKE SNIPER V4</h2>
        
        <div id="login_div">
            <input type="email" id="email_input" placeholder="Enter Email to Login">
            <br>
            <button class="btn-login" onclick="login()">LOGIN / ACCESS</button>
        </div>

        <div id="settings_div" style="display:none">
            <h3>Bot Settings</h3>
            <input type="text" id="token_input" placeholder="Deriv API Token">
            <br>
            <input type="number" id="stake_input" value="1.0" step="0.5" placeholder="Stake Amount">
            <br>
            <button class="btn-start" onclick="startBot()">START SNIPING</button>
        </div>

        <div id="stats_div" style="display:none">
            <div id="stats-box">
                <div id="status_text" style="font-size:24px; margin-bottom:10px">Connecting...</div>
                <div id="info_text" style="font-size:18px; color:#8b949e">Profit: 0.00 | W: 0 L: 0</div>
            </div>
            <br>
            <button class="btn-stop" onclick="stopBot()">STOP</button>
            <button class="btn-reset" onclick="resetBot()">RESET ALL</button>
        </div>

        <script>
            let userEmail = "";

            async function login() {
                userEmail = document.getElementById('email_input').value;
                if(!userEmail) { alert("Please enter email!"); return; }
                
                let res = await fetch('/check/' + userEmail);
                let data = await res.json();
                
                document.getElementById('login_div').style.display = 'none';
                if(data.found) {
                    document.getElementById('stats_div').style.display = 'block';
                    startSync();
                } else {
                    document.getElementById('settings_div').style.display = 'block';
                }
            }

            async function startBot() {
                let token = document.getElementById('token_input').value;
                let stake = document.getElementById('stake_input').value;
                if(!token) { alert("Token required!"); return; }

                await fetch('/start', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({email: userEmail, token: token, stake: stake, symbol: 'R_100'})
                });

                document.getElementById('settings_div').style.display = 'none';
                document.getElementById('stats_div').style.display = 'block';
                startSync();
            }

            async function stopBot() {
                await fetch('/stop', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({email: userEmail})
                });
                location.reload();
            }

            async function resetBot() {
                if(confirm("Delete all data and reset?")) {
                    await fetch('/reset', {
                        method: 'POST',
                        headers: {'Content-Type': 'application/json'},
                        body: JSON.stringify({email: userEmail})
                    });
                    location.reload();
                }
            }

            function startSync() {
                setInterval(async () => {
                    let res = await fetch('/check/' + userEmail);
                    let data = await res.json();
                    if(data.found) {
                        let st = document.getElementById('status_text');
                        st.innerText = data.status;
                        st.style.color = data.status.includes("STOPPED") ? "#ff7b72" : "#7ee787";
                        document.getElementById('info_text').innerText = `Profit: ${data.profit.toFixed(2)}$ | W: ${data.wins} L: ${data.losses}`;
                    }
                }, 1000);
            }
        </script>
    </body>
    </html>
    """)

@app.route("/check/<email>")
def check_user(email):
    u = users.find_one({"email": email})
    if u:
        u["_id"] = str(u["_id"])
        return jsonify({"found": True, **u})
    return jsonify({"found": False})

@app.route("/start", methods=["POST"])
def start():
    d = request.json
    users.delete_one({"email": d["email"]})
    new_user = {
        "email": d["email"], "token": d["token"], "symbol": d["symbol"],
        "stake": float(d["stake"]), "base_stake": float(d["stake"]),
        "profit": 0.0, "wins": 0, "losses": 0, "status": "SCANNING..."
    }
    uid = users.insert_one(new_user).inserted_id
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
    # هذا السطر مهم جداً ليعمل على Render
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
