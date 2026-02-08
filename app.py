import websocket, json, time, os, threading, queue
import numpy as np
from flask import Flask, render_template_string, request, redirect
import telebot
from telebot import types
from pymongo import MongoClient
from datetime import datetime, timedelta

app = Flask(__name__)

# --- CONFIGURATION (UPDATED TOKEN) ---
BOT_TOKEN = "8433565422:AAHcnt2DyRqpwv_hZu027kQ5rtHpYphGr2Y"
MONGO_URI = "mongodb+srv://charbelnk111_db_user:Mano123mano@cluster0.2gzqkc8.mongodb.net/?appName=Cluster0"

bot = telebot.TeleBot(BOT_TOKEN, threaded=True, num_threads=100)
db_client = MongoClient(MONGO_URI)
db = db_client['Trading_System_V2']
users_col = db['Authorized_Users']
active_sessions_col = db['Active_Sessions']

msg_queue = queue.Queue()

def message_worker():
    while True:
        try:
            item = msg_queue.get()
            bot.send_message(item[0], item[1], parse_mode="Markdown", reply_markup=item[2] if len(item) > 2 else None)
            msg_queue.task_done()
            time.sleep(0.05) 
        except: pass

threading.Thread(target=message_worker, daemon=True).start()

def safe_send(chat_id, text, markup=None):
    msg_queue.put((chat_id, text, markup))

# --- FULL 15 INDICATORS LOGIC ---
def analyze15_mirror(ticks):
    if len(ticks) < 600: return {"signal": "NEUTRAL", "pct": 0}
    
    candles = []
    for i in range(0, len(ticks), 30):
        s = ticks[i : i+30]
        if len(s) < 2: continue
        candles.append({'open': s[0], 'close': s[-1], 'high': max(s), 'low': min(s)})
    
    c = candles[-30:]
    close = [x['close'] for x in c]
    high = [x['high'] for x in c]
    low = [x['low'] for x in c]
    s = []

    # 1. SMA | 2. EMA | 3. RSI
    sma = sum(close) / len(close); s.append(1 if close[-1] > sma else -1)
    ema = close[0]; k = 2 / (len(close) + 1)
    for p in close: ema = p * k + ema * (1 - k)
    s.append(1 if close[-1] > ema else -1)
    g, l = 0, 0
    for i in range(1, len(close)):
        d = close[i] - close[i-1]
        if d > 0: g += d
        else: l += abs(d)
    rsi = 100 - (100 / (1 + (g / (l if l != 0 else 1)))); s.append(1 if rsi > 50 else -1)
    
    # 4. MACD | 5. STOCHASTIC | 6. CCI
    ema12 = sum(close[-12:]) / 12; ema26 = sum(close[-26:]) / 26; s.append(1 if (ema12 - ema26) > 0 else -1)
    stoch = (close[-1] - min(low)) / ((max(high) - min(low)) if max(high) != min(low) else 1); s.append(1 if stoch > 0.5 else -1)
    tp = (sum(high)/len(high) + sum(low)/len(low) + sum(close)/len(close)) / 3
    md = sum([abs(x - tp) for x in close]) / len(close); cci = (close[-1] - tp) / (0.015 * md if md != 0 else 1); s.append(1 if cci > 0 else -1)
    
    # 7. Candle Color | 8. Long Trend | 9. Low Rebound
    s.append(1 if close[-1] - c[-1]['open'] > 0 else -1)
    s.append(1 if (close[-1] - c[0]['open'])/c[0]['open'] > 0 else -1)
    s.append(1 if close[-1] > min(low) else -1)
    
    # 10. OBV | 11. Momentum | 12. Williams %R
    obv = 0
    for i in range(1, len(close)): obv += 1 if close[i] > close[i-1] else -1
    s.append(1 if obv > 0 else -1)
    s.append(1 if close[-1] - close[-2] > 0 else -1)
    will = (max(high) - close[-1]) / ((max(high) - min(low)) if max(high) != min(low) else 1) * -100; s.append(1 if will > -50 else -1)
    
    # 13. ATR | 14. Price Delta | 15. Closing Momentum
    atr = sum([abs(close[i] - close[i-1]) for i in range(1, len(close))]) / len(close); s.append(1 if atr > 0 else -1)
    s.append(1 if abs(close[-1] - close[-2]) > 0 else -1)
    s.append(1 if close[-1] > close[-5] else -1)

    buy_c = s.count(1); sell_c = s.count(-1)
    buy_pct = (buy_c / 15) * 100; sell_pct = (sell_c / 15) * 100
    signal = "NEUTRAL"
    if buy_pct >= 70: signal = "CALL"
    elif sell_pct >= 70: signal = "PUT"
    return {"signal": signal, "pct": max(buy_pct, sell_pct)}

# --- TRADING ENGINE ---
def trade_engine(chat_id):
    while True:
        session = active_sessions_col.find_one({"chat_id": chat_id})
        if not session or not session.get("is_running"): break
        token = session['tokens'][0]
        prices_list = []
        waiting_for_rebound = False
        acc_currency = "USD"

        def on_message(ws, message):
            nonlocal prices_list, waiting_for_rebound, acc_currency
            data = json.loads(message)
            if "authorize" in data: acc_currency = data["authorize"].get("currency", "USD")
            if "tick" in data:
                prices_list.append(float(data["tick"]["quote"]))
                if len(prices_list) > 1000: prices_list.pop(0)
                if datetime.now().second == 54 and not waiting_for_rebound:
                    res = analyze15_mirror(prices_list)
                    if res["signal"] != "NEUTRAL":
                        waiting_for_rebound = True
                        last_p = prices_list[-1]
                        threading.Timer(60, lambda: execute_reverse(ws, chat_id, token, res["signal"], last_p, acc_currency)).start()
            if "buy" in data:
                cid = data["buy"]["contract_id"]
                safe_send(chat_id, "⚡ *Rebound Confirmed!* Trade entered.")
                threading.Timer(66, lambda: ws.send(json.dumps({"proposal_open_contract": 1, "contract_id": cid}))).start()
            if "proposal_open_contract" in data:
                if data["proposal_open_contract"].get("is_sold"): process_result(chat_id, token, data)

        def execute_reverse(ws, chat_id, token, signal, old_p, currency):
            nonlocal waiting_for_rebound
            trend = prices_list[-1] - old_p
            is_opp = (signal == "CALL" and trend < 0) or (signal == "PUT" and trend > 0)
            if is_opp:
                rev_type = "PUT" if signal == "CALL" else "CALL"
                acc = active_sessions_col.find_one({"chat_id": chat_id})["accounts_data"][token]
                ws.send(json.dumps({"buy": "1", "price": acc["current_stake"], "parameters": {"amount": acc["current_stake"], "basis": "stake", "contract_type": rev_type, "duration": 1, "duration_unit": "m", "symbol": "R_100", "currency": currency}}))
            waiting_for_rebound = False

        def on_open(ws):
            ws.send(json.dumps({"authorize": token}))
            ws.send(json.dumps({"ticks": "R_100", "subscribe": 1}))

        ws = websocket.WebSocketApp("wss://blue.derivws.com/websockets/v3?app_id=16929", on_open=on_open, on_message=on_message)
        ws.run_forever()

def process_result(chat_id, token, res):
    session = active_sessions_col.find_one({"chat_id": chat_id})
    if not session: return
    acc = session['accounts_data'].get(token)
    poc = res.get("proposal_open_contract", {})
    profit = float(poc.get("profit", 0))
    new_wins = acc.get("win_count", 0) + (1 if profit > 0 else 0)
    new_losses = acc.get("loss_count", 0) + (1 if profit <= 0 else 0)
    new_total = acc["total_profit"] + profit
    if profit > 0: new_stake, new_streak, status = session["initial_stake"], 0, "✅ *WIN*"
    else:
        new_stake = float("{:.2f}".format(acc["current_stake"] * 2.2))
        new_streak = acc.get("consecutive_losses", 0) + 1
        status = "❌ *LOSS*"
    active_sessions_col.update_one({"chat_id": chat_id}, {"$set": {f"accounts_data.{token}.current_stake": new_stake, f"accounts_data.{token}.consecutive_losses": new_streak, f"accounts_data.{token}.total_profit": new_total, f"accounts_data.{token}.win_count": new_wins, f"accounts_data.{token}.loss_count": new_losses}})
    msg = (f"{status}\n💰 Profit: `{profit:.2f}$`\n📊 Wins: `{new_wins}` | Losses: `{new_losses}`\n📈 Net Profit: `{new_total:.2f}$`\n⚠️ Streak: `{new_streak}/4` | Next: `{new_stake}$`")
    safe_send(chat_id, msg)
    if new_total >= session.get("target_profit", 10) or new_streak >= 4:
        active_sessions_col.delete_one({"chat_id": chat_id})
        safe_send(chat_id, "🛑 *Session Finished.*", types.ReplyKeyboardRemove())

# --- ADMIN PANEL HTML ---
HTML_ADMIN = """
<!DOCTYPE html><html><head><title>Admin Panel</title><style>
body{font-family:sans-serif; background:#0a0a0a; color:#eee; text-align:center; padding:40px;}
.box{max-width:700px; margin:auto; background:#151515; padding:30px; border-radius:12px; border:1px solid #222;}
input, select, button{padding:12px; margin:8px; border-radius:6px; border:1px solid #333; background:#111; color:#fff;}
button{background:#00ffa6; color:#000; font-weight:bold; border:none; cursor:pointer;}
table{width:100%; border-collapse:collapse; margin-top:20px;}
th, td{padding:12px; border-bottom:1px solid #222; text-align:left;}
a{color:#ff5555; text-decoration:none; font-size:14px;}
</style></head>
<body><div class="box">
    <h2>Access Management</h2>
    <form action="/add" method="POST"><input name="email" placeholder="Email Address" required><select name="days"><option value="1">1 Day</option><option value="30">30 Days</option><option value="36500">Life Time</option></select><button type="submit">Grant Access</button></form>
    <table><tr><th>User</th><th>Expiry</th><th>Action</th></tr>
    {% for u in users %}<tr><td>{{u.email}}</td><td>{{u.expiry}}</td><td><a href="/delete/{{u.email}}">Remove</a></td></tr>{% endfor %}
    </table>
</div></body></html>
"""

@app.route('/')
def index(): return render_template_string(HTML_ADMIN, users=list(users_col.find()))

@app.route('/add', methods=['POST'])
def add_user():
    exp = (datetime.now() + timedelta(days=int(request.form.get('days')))).strftime("%Y-%m-%d")
    users_col.update_one({"email": request.form.get('email').lower().strip()}, {"$set": {"expiry": exp}}, upsert=True)
    return redirect('/')

@app.route('/delete/<email>')
def delete_user(email): users_col.delete_one({"email": email}); return redirect('/')

# --- TELEGRAM HANDLERS ---
@bot.message_handler(commands=['start'])
def cmd_start(m):
    active_sessions_col.delete_one({"chat_id": m.chat.id})
    bot.send_message(m.chat.id, "📧 Enter registered Email:")
    bot.register_next_step_handler(m, auth)

def auth(m):
    u = users_col.find_one({"email": m.text.strip().lower()})
    if u and datetime.strptime(u['expiry'], "%Y-%m-%d") > datetime.now():
        bot.send_message(m.chat.id, "✅ Authorized. Enter Deriv Token:")
        bot.register_next_step_handler(m, save_token)
    else: bot.send_message(m.chat.id, "🚫 No active subscription.")

def save_token(m):
    active_sessions_col.update_one({"chat_id": m.chat.id}, {"$set": {"tokens": [m.text.strip()], "is_running": False}}, upsert=True)
    bot.send_message(m.chat.id, "Initial Stake:")
    bot.register_next_step_handler(m, save_stake)

def save_stake(m):
    active_sessions_col.update_one({"chat_id": m.chat.id}, {"$set": {"initial_stake": float(m.text)}})
    bot.send_message(m.chat.id, "Target Profit:")
    bot.register_next_step_handler(m, save_tp)

def save_tp(m):
    active_sessions_col.update_one({"chat_id": m.chat.id}, {"$set": {"target_profit": float(m.text)}})
    bot.send_message(m.chat.id, "Setup Finished!", reply_markup=types.ReplyKeyboardMarkup(resize_keyboard=True).add('START 🚀'))

@bot.message_handler(func=lambda m: m.text == 'START 🚀')
def run_bot(m):
    sess = active_sessions_col.find_one({"chat_id": m.chat.id})
    if sess:
        accs = {sess["tokens"][0]: {"current_stake": sess["initial_stake"], "total_profit": 0.0, "consecutive_losses": 0, "win_count": 0, "loss_count": 0}}
        active_sessions_col.update_one({"chat_id": m.chat.id}, {"$set": {"is_running": True, "accounts_data": accs}})
        bot.send_message(m.chat.id, "🚀 *Bot is active (Silent Mode)*", reply_markup=types.ReplyKeyboardMarkup(resize_keyboard=True).add('STOP 🛑'))
        threading.Thread(target=trade_engine, args=(m.chat.id,), daemon=True).start()

@bot.message_handler(func=lambda m: m.text == 'STOP 🛑')
def stop_bot(m):
    active_sessions_col.delete_one({"chat_id": m.chat.id})
    bot.send_message(m.chat.id, "🛑 Session reset.", reply_markup=types.ReplyKeyboardRemove())
    cmd_start(m)

if __name__ == '__main__':
    threading.Thread(target=lambda: app.run(host='0.0.0.0', port=10000), daemon=True).start()
    bot.infinity_polling()
