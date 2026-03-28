import json, websocket, time
from datetime import datetime
from threading import Thread
from flask import Flask, render_template_string, jsonify, request
from pymongo import MongoClient
from telegram import Update, Bot
from telegram.ext import Updater, CommandHandler, MessageHandler, filters, CallbackContext
import requests

app = Flask(__name__)

# --- MongoDB ---
MONGO_URI = "mongodb+srv://charbelnk111_db_user:Mano123mano@cluster0.2gzqkc8.mongodb.net/?appName=Cluster0"
client = MongoClient(MONGO_URI)
db = client["KHOURY_BOT"]
users = db["users"]
activation = db["activation"]

# --- Telegram Bot ---
TG_TOKEN = "8264292822:AAGfnYDhdvakCZIGQ10Fqp-xUkw3F0Qc9lM"
bot = Bot(TG_TOKEN)
updater = Updater(TG_TOKEN)
dispatcher = updater.dispatcher

# --- Deriv WS ---
DERIV_WS = "wss://blue.derivws.com/websockets/v3?app_id=16929"

# --- Helpers ---
def get_currency(token):
    """Get currency from API token"""
    try:
        url = f"https://api.deriv.com/api/v1/balance?token={token}"
        r = requests.get(url, timeout=10).json()
        return r.get("currency", "USD")
    except:
        return "USD"

def update_db(email, profit=0, status="", next_stake=None, win=0, loss=0):
    data = {"$inc": {"profit": profit, "wins": win, "losses": loss}, "$set": {"status": status}}
    if next_stake is not None:
        data["$set"]["current_stake"] = float(next_stake)
    users.update_one({"email": email}, data)

# --- Bot Worker ---
def bot_worker(email, chat_id):
    consecutive_losses = 0
    while True:
        u = users.find_one({"email": email})
        if not u or "STOPPED" in u.get("status"):
            break

        # Run every 1 second to check tick
        if datetime.now().second % 1 == 0:
            u = users.find_one({"email": email})
            stake = float(u.get("current_stake", u["stake"]))
            currency = u.get("currency", "USD")
            try:
                ws = websocket.create_connection(DERIV_WS, timeout=20)
                ws.send(json.dumps({"authorize": u["token"]}))
                ws.recv()

                # 1 tick analysis
                ws.send(json.dumps({"ticks_history": "R_100", "count": 2, "end": "latest", "style": "ticks"}))
                prices = json.loads(ws.recv()).get("history", {}).get("prices", [])
                if len(prices) >= 1:
                    last = prices[-1]
                    # آخر رقم بعد الفاصلة
                    decimal = str(last).split(".")[1] if "." in str(last) else "00"
                    last_digit = int(decimal[1]) if len(decimal) > 1 else 0
                    update_db(email, status=f"ENTRY DIGIT {last_digit} (${stake})")

                    # Buy request
                    buy_req = {
                        "buy": 1,
                        "price": stake,
                        "parameters": {
                            "amount": stake,
                            "basis": "stake",
                            "contract_type": "DIGITDIFF",
                            "currency": currency,
                            "duration": 1,
                            "duration_unit": "t",
                            "symbol": "R_100",
                            "diff": last_digit
                        }
                    }
                    ws.send(json.dumps(buy_req))
                    res = json.loads(ws.recv())
                    if "buy" in res:
                        cid = res["buy"]["contract_id"]
                        time.sleep(6)
                        ws.send(json.dumps({"proposal_open_contract": 1, "contract_id": cid}))
                        poc_res = json.loads(ws.recv()).get("proposal_open_contract", {})
                        if poc_res.get("is_sold"):
                            buy_price = float(poc_res.get("buy_price", stake))
                            profit = float(poc_res.get("profit", 0))
                            if profit > 0:
                                consecutive_losses = 0
                                update_db(email, profit, "WIN! NEXT CYCLE", next_stake=u["stake"], win=1)
                                bot.send_message(
                                    chat_id=chat_id,
                                    text=f"WIN ✅\nProfit: {profit} {currency}\nWins: {u['wins']+1} | Losses: {u['losses']}"
                                )
                            else:
                                consecutive_losses += 1
                                if consecutive_losses >= 2:
                                    update_db(email, -buy_price, "STOPPED: 2 LOSSES", loss=1)
                                    bot.send_message(
                                        chat_id=chat_id,
                                        text=f"STOPPED ❌\nLoss: {buy_price} {currency}\nWins: {u['wins']} | Losses: {u['losses']+1}"
                                    )
                                    ws.close()
                                    return
                                else:
                                    update_db(email, -buy_price, f"LOSS! MARTINGALE x14", next_stake=stake*14, loss=1)
                                    bot.send_message(
                                        chat_id=chat_id,
                                        text=f"LOSS ❌\nLoss: {buy_price} {currency}\nWins: {u['wins']} | Losses: {u['losses']+1}"
                                    )
                ws.close()
            except Exception as e:
                print(f"Error: {e}")
            time.sleep(0.2)
        time.sleep(0.2)

# --- Telegram Handlers ---
def start(update: Update, context: CallbackContext):
    chat_id = update.message.chat_id
    update.message.reply_text("Send your email to start the bot:")
    context.user_data['chat_id'] = chat_id

def email_handler(update: Update, context: CallbackContext):
    chat_id = context.user_data['chat_id']
    email = update.message.text
    act = activation.find_one({"email": email})
    if not act:
        update.message.reply_text("Email not activated!")
        return
    context.user_data['email'] = email
    update.message.reply_text("Send Token:")

def token_handler(update: Update, context: CallbackContext):
    token = update.message.text
    context.user_data['token'] = token
    context.user_data['currency'] = get_currency(token)
    update.message.reply_text("Send Stake:")

def stake_handler(update: Update, context: CallbackContext):
    stake = update.message.text
    context.user_data['stake'] = stake
    update.message.reply_text("Send TP:")

def tp_handler(update: Update, context: CallbackContext):
    tp = update.message.text
    data = context.user_data
    users.delete_one({"email": data['email']})
    users.insert_one({
        "email": data['email'],
        "token": data['token'],
        "symbol": "R_100",
        "stake": float(data['stake']),
        "current_stake": float(data['stake']),
        "tp": float(tp),
        "currency": data['currency'],
        "profit": 0.0,
        "wins": 0,
        "losses": 0,
        "status": "READY"
    })
    Thread(target=bot_worker, args=(data['email'], data['chat_id']), daemon=True).start()
    update.message.reply_text("Bot started!")

dispatcher.add_handler(CommandHandler("start", start))
dispatcher.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), email_handler))

# --- Flask Activation Interface ---
@app.route("/")
def home():
    return render_template_string("""
    <body style='background:#0d1117;color:white;text-align:center;font-family:sans-serif;padding:30px'>
        <h2 style='color:#00e676'>Activation Page</h2>
        <input id=email placeholder="Email" style="padding:12px; border-radius:8px"><br><br>
        <select id=duration>
            <option value="1">1 day</option>
            <option value="7">7 days</option>
            <option value="30">30 days</option>
            <option value="36500">Lifetime</option>
        </select><br><br>
        <button onclick="activate()" style="padding:12px 25px; background:#238636; color:white; border:none; border-radius:8px">Activate</button>
        <script>
            async function activate(){
                let email = document.getElementById('email').value;
                let duration = document.getElementById('duration').value;
                await fetch('/activate', {
                    method:'POST',
                    headers:{'Content-Type':'application/json'},
                    body:JSON.stringify({email:email,duration:duration})
                });
                alert("Activated!");
            }
        </script>
    </body>
    """)

@app.route("/activate", methods=["POST"])
def activate():
    d = request.json
    activation.update_one(
        {"email": d["email"]},
        {"$set": {"email": d["email"], "duration": int(d["duration"]), "activated_at": datetime.now()}},
        upsert=True
    )
    return jsonify({"ok": True})

# --- Run ---
if __name__ == "__main__":
    Thread(target=updater.start_polling, daemon=True).start()
    app.run(host="0.0.0.0", port=5000)
