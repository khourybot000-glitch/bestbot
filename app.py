import time
import json
import websocket
import multiprocessing
import os
import sys
import fcntl
import ssl
from flask import Flask, request, render_template_string, redirect, url_for, session, flash

# ==========================================================
# BOT CONSTANT SETTINGS (UPDATED FOR T5 STRATEGY)
# ==========================================================
WSS_URL_UNIFIED = "wss://blue.derivws.com/websockets/v3?app_id=16929"
SYMBOL = "R_100"
DURATION = 1          
DURATION_UNIT = "t"
MARTINGALE_STEPS = 1          
MAX_CONSECUTIVE_LOSSES = 2    
RECONNECT_DELAY = 1
USER_IDS_FILE = "user_ids.txt"
ACTIVE_SESSIONS_FILE = "active_sessions.json"
TICK_HISTORY_SIZE = 5   
MARTINGALE_MULTIPLIER = 9.0 

# ==========================================================
# BOT RUNTIME STATE & PERSISTENCE
# ==========================================================
DEFAULT_SESSION_STATE = {
    "api_token": "",
    "base_stake": 0.35,
    "tp_target": 10.0,
    "current_profit": 0.0,
    "current_balance": 0.0,
    "initial_starting_balance": 0.0, 
    "before_trade_balance": 0.0,
    "current_stake": 0.35,
    "current_total_stake": 0.35,
    "current_step": 0,
    "consecutive_losses": 0,
    "total_wins": 0,
    "total_losses": 0,
    "is_running": False,
    "account_type": "demo",
    "currency": "USD",
    "last_entry_time": 0.0,
    "last_entry_d2": 'N/A', 
    "tick_history": [],
    "last_tick_data": {},
    "is_balance_received": False,
    "stop_reason": "Stopped",
    "display_t1_price": 0.0, 
    "display_t2_price": 0.0, 
    "last_trade_type": None
}

flask_local_processes = {}
active_ws = {}  
is_contract_open = None  

# --- Persistent Functions ---
def load_persistent_sessions():
    if not os.path.exists(ACTIVE_SESSIONS_FILE): return {}
    with open(ACTIVE_SESSIONS_FILE, 'r') as f:
        try: return json.load(f)
        except: return {}

def save_session_data(email, session_data):
    all_sessions = load_persistent_sessions()
    all_sessions[email] = session_data
    with open(ACTIVE_SESSIONS_FILE, 'w') as f:
        json.dump(all_sessions, f, indent=4)

def get_session_data(email):
    all_sessions = load_persistent_sessions()
    data = all_sessions.get(email, DEFAULT_SESSION_STATE.copy())
    for k, v in DEFAULT_SESSION_STATE.items():
        if k not in data: data[k] = v
    return data

def load_allowed_users():
    if not os.path.exists(USER_IDS_FILE):
        with open(USER_IDS_FILE, 'w') as f: f.write("test@example.com\n")
        return {"test@example.com"}
    with open(USER_IDS_FILE, 'r') as f:
        return {line.strip().lower() for line in f if line.strip()}

# ==========================================================
# FLASK INTERFACE (WITH ALL YOUR ORIGINAL STYLES & NOTES)
# ==========================================================
app = Flask(__name__)
app.config['SECRET_KEY'] = os.environ.get('SESSION_SECRET_KEY', 'VERY_STRONG_SECRET_KEY_RENDER_BOT')
app.config['SESSION_PERMANENT'] = False

LOGIN_FORM = """
<!doctype html>
<title>Login</title>
<meta name="viewport" content="width=device-width, initial-scale=1">
<style>
    body { font-family: Arial, sans-serif; padding: 20px; max-width: 400px; margin: auto; }
    h1 { color: #007bff; }
    input[type="email"], input[type="submit"] {
        width: 100%; padding: 10px; margin-top: 5px; margin-bottom: 10px;
        border: 1px solid #ccc; border-radius: 4px; box-sizing: border-box;
    }
    input[type="submit"] { background-color: #007bff; color: white; cursor: pointer; font-size: 1.1em; }
    .note { margin-top: 15px; padding: 10px; background-color: #f8f9fa; border-radius: 4px; }
</style>
<h1>Bot Login</h1>
{% with messages = get_flashed_messages(with_categories=true) %}
    {% if messages %}
        {% for category, message in messages %}
            <p style="color:{{ 'green' if category == 'success' else 'red' }};">{{ message }}</p>
        {% endfor %}
    {% endif %}
{% endwith %}
<form method="POST" action="{{ url_for('login_route') }}">
    <label for="email">Email Address:</label><br>
    <input type="email" id="email" name="email" required><br>
    <input type="submit" value="Login">
</form>
<div class="note">
    <p>💡 Note: This is a placeholder login. Only users listed in <code>user_ids.txt</code> can log in.</p>
</div>
"""

CONTROL_FORM = """
<!doctype html>
<title>Control Panel</title>
<meta name="viewport" content="width=device-width, initial-scale=1">
<style>
    body { font-family: Arial, sans-serif; padding: 10px; max-width: 600px; margin: auto; direction: ltr; text-align: left; }
    h1 { color: #007bff; font-size: 1.8em; border-bottom: 2px solid #eee; padding-bottom: 10px; }
    .status-running { color: green; font-weight: bold; font-size: 1.3em; }
    .status-stopped { color: red; font-weight: bold; font-size: 1.3em; }
    input[type="text"], input[type="number"], select { width: 98%; padding: 10px; margin-top: 5px; margin-bottom: 10px; border: 1px solid #ccc; border-radius: 4px; box-sizing: border-box; }
    form button { padding: 12px 20px; border: none; border-radius: 5px; cursor: pointer; font-size: 1.1em; margin-top: 15px; width: 100%; }
    .data-box { background-color: #f8f9fa; border: 1px solid #e9ecef; padding: 15px; border-radius: 5px; margin-bottom: 15px; }
    .tick-box { display: flex; justify-content: space-around; padding: 10px; background-color: #e9f7ff; border: 1px solid #007bff; border-radius: 4px; margin-bottom: 10px; font-weight: bold; }
    .current-digit { color: #ff5733; font-size: 1.2em; }
</style>

<h1>Bot Control Panel | User: {{ email }}</h1>
<hr>

{% with messages = get_flashed_messages(with_categories=true) %}
    {% if messages %}
        {% for category, message in messages %}
            <p style="color:{{ 'green' if category == 'success' else 'red' }};">{{ message }}</p>
        {% endfor %}
    {% endif %}
{% endwith %}

{% if session_data and session_data.is_running %}
    <p class="status-running">✅ Bot is Running! (Auto-refreshing)</p>
    
    <div class="tick-box">
        <div style="text-align: center;">
            <span style="font-size: 0.9em; color: #555;">T1 Price (Start):</span><br>
            <b>{{ "%.2f"|format(session_data.display_t1_price) if session_data.display_t1_price else 'N/A' }}</b>
        </div>
        <div style="text-align: center;">
            <span style="font-size: 0.9em; color: #555;">T5 Price (End):</span><br>
            <b>{{ "%.2f"|format(session_data.display_t2_price) if session_data.display_t2_price else 'N/A' }}</b>
        </div>
    </div>

    <div class="data-box">
        <p>Asset: <b>{{ SYMBOL }}</b> | Account: <b>{{ session_data.account_type.upper() }}</b></p>
        <p style="font-weight: bold; color: #17a2b8;">Current Balance: <b>{{ session_data.currency }} {{ "%.2f"|format(session_data.current_balance) }}</b></p>
        
        {% set net_profit = session_data.current_balance - session_data.initial_starting_balance %}
        <p style="font-weight: bold; color: {{ 'green' if net_profit >= 0 else 'red' }};">
            Net Profit: <b>{{ "%.2f"|format(net_profit) }}</b> (TP: {{ session_data.tp_target }})
        </p>

        <p>Status: <b>{{ 'Waiting for Result' if is_contract_open.get(email) else 'Searching Signal (T5-T1)' }}</b></p>
        <p>Current Step: <b>{{ session_data.current_step }}</b> | Losses: <b>{{ session_data.consecutive_losses }}</b></p>
        <p>Total Wins: <span style="color: green;">{{ session_data.total_wins }}</span> | Total Losses: <span style="color: red;">{{ session_data.total_losses }}</span></p>
    </div>

    <form method="POST" action="{{ url_for('stop_route') }}">
        <button type="submit" style="background-color: red; color: white;">🛑 Stop Bot</button>
    </form>
{% else %}
    {% if session_data.stop_reason and session_data.stop_reason != "Stopped" %}
        <p style="color:red; font-weight:bold;">Last Session Ended: {{ session_data.stop_reason }}</p>
    {% endif %}
    <p class="status-stopped">🛑 Bot is Stopped.</p>

    <form method="POST" action="{{ url_for('start_bot') }}">
        <label>Account Type:</label><br>
        <select name="account_type">
            <option value="demo" {% if session_data.account_type == 'demo' %}selected{% endif %}>Demo (USD)</option>
            <option value="live" {% if session_data.account_type == 'live' %}selected{% endif %}>Live (tUSDT)</option>
        </select><br>
        <label>Deriv API Token:</label><br>
        <input type="text" name="token" value="{{ session_data.api_token }}" required><br>
        <label>Base Stake:</label><br>
        <input type="number" name="stake" value="{{ session_data.base_stake or 0.35 }}" step="0.01" min="0.35" required><br>
        <label>TP Target:</label><br>
        <input type="number" name="tp" value="{{ session_data.tp_target or 10.0 }}" step="0.01" required><br>
        <button type="submit" style="background-color: green; color: white;">🚀 Start Bot</button>
    </form>
{% endif %}

<hr>
<a href="{{ url_for('logout') }}" style="display: block; text-align: center; margin-top: 15px;">Logout</a>

<script>
    function autoRefresh() {
        var isRunning = {{ 'true' if session_data and session_data.is_running else 'false' }};
        if (isRunning) {
            setTimeout(function() { window.location.reload(); }, 1000);
        }
    }
    autoRefresh();
</script>
"""

# ==========================================================
# TRADING LOGIC (CORE)
# ==========================================================

def stop_bot(email, stop_reason="Stopped Manually"):
    data = get_session_data(email)
    data["is_running"] = False
    data["stop_reason"] = stop_reason
    save_session_data(email, data)
    if email in active_ws:
        try: active_ws[email].close()
        except: pass
    if is_contract_open is not None: is_contract_open[email] = False

def bot_core_logic(email, token, stake, tp, account_type, currency, shared_contract_state):
    # جلب الرصيد الأولي
    try:
        ws_init = websocket.WebSocket()
        ws_init.connect(WSS_URL_UNIFIED, sslopt={"cert_reqs": ssl.CERT_NONE})
        ws_init.send(json.dumps({"authorize": token}))
        bal_res = json.loads(ws_init.recv())
        ws_init.send(json.dumps({"balance": 1}))
        bal_val = json.loads(ws_init.recv())['balance']['balance']
        ws_init.close()
    except: return

    data = get_session_data(email)
    data.update({"is_running": True, "initial_starting_balance": bal_val, "current_balance": bal_val, "stop_reason": "Running", "api_token": token, "base_stake": stake, "tp_target": tp, "currency": currency})
    save_session_data(email, data)

    def on_message(ws, message):
        msg = json.loads(message)
        if msg.get('msg_type') == 'tick':
            if shared_contract_state.get(email): return
            
            d = get_session_data(email)
            price = float(msg['tick']['quote'])
            d['tick_history'].append(price)
            if len(d['tick_history']) > 5: d['tick_history'].pop(0)
            
            if len(d['tick_history']) >= 1: d['display_t1_price'] = d['tick_history'][0]
            if len(d['tick_history']) >= 5: d['display_t2_price'] = d['tick_history'][4]

            if len(d['tick_history']) == 5:
                diff = round(d['tick_history'][4] - d['tick_history'][0], 4)
                # الاستراتيجية المطلوبة
                trade_type = None
                barrier = ""
                if diff >= 0.4: trade_type, barrier = "CALL", "+0.6"
                elif diff <= -0.4: trade_type, barrier = "PUT", "-0.6"

                if trade_type:
                    current_stake = round(d['base_stake'] * (MARTINGALE_MULTIPLIER ** d['current_step']), 2)
                    d['before_trade_balance'] = d['current_balance']
                    ws.send(json.dumps({
                        "buy": 1, "price": current_stake,
                        "parameters": {"amount": current_stake, "basis": "stake", "currency": currency, "duration": 1, "duration_unit": "t", "symbol": SYMBOL, "contract_type": trade_type, "barrier": barrier}
                    }))
                    shared_contract_state[email] = True
                    d['last_trade_type'] = trade_type
                    d['tick_history'] = []
                    # مراقبة النتيجة
                    threading.Thread(target=wait_for_result, args=(email, token, shared_contract_state)).start()
            save_session_data(email, d)

    def on_open(ws):
        ws.send(json.dumps({"authorize": token}))
        ws.send(json.dumps({"ticks": SYMBOL, "subscribe": 1}))

    ws_app = websocket.WebSocketApp(WSS_URL_UNIFIED, on_open=on_open, on_message=on_message)
    active_ws[email] = ws_app
    ws_app.run_forever(sslopt={"cert_reqs": ssl.CERT_NONE})

import threading
def wait_for_result(email, token, shared_contract_state):
    time.sleep(6)
    try:
        ws = websocket.WebSocket()
        ws.connect(WSS_URL_UNIFIED, sslopt={"cert_reqs": ssl.CERT_NONE})
        ws.send(json.dumps({"authorize": token}))
        ws.recv(); ws.send(json.dumps({"balance": 1}))
        new_bal = json.loads(ws.recv())['balance']['balance']
        ws.close()
        
        d = get_session_data(email)
        if new_bal > d['before_trade_balance']:
            d['total_wins'] += 1; d['current_step'] = 0; d['consecutive_losses'] = 0
            if (new_bal - d['initial_starting_balance']) >= d['tp_target']:
                stop_bot(email, "Target Profit Reached!")
        else:
            d['total_losses'] += 1; d['consecutive_losses'] += 1
            if d['consecutive_losses'] >= MAX_CONSECUTIVE_LOSSES:
                stop_bot(email, "Max Consecutive Losses Reached.")
            else: d['current_step'] += 1
        d['current_balance'] = new_bal
        save_session_data(email, d)
    except: pass
    shared_contract_state[email] = False

# ==========================================================
# ROUTES
# ==========================================================

@app.route('/')
def control_panel():
    if 'email' not in session: return redirect(url_for('login_route'))
    return render_template_string(CONTROL_FORM, email=session['email'], session_data=get_session_data(session['email']), SYMBOL=SYMBOL)

@app.route('/login', methods=['GET', 'POST'])
def login_route():
    if request.method == 'POST':
        email = request.form.get('email', '').strip().lower()
        if email in load_allowed_users():
            session['email'] = email
            return redirect(url_for('control_panel'))
        flash("Invalid email or unauthorized user.", 'error')
    return render_template_string(LOGIN_FORM)

@app.route('/start_bot', methods=['POST'])
def start_bot():
    email = session['email']
    token = request.form['token'].strip()
    stake = float(request.form['stake'])
    tp = float(request.form['tp'])
    acc_type = request.form['account_type']
    curr = "USD" if acc_type == 'demo' else "tUSDT"
    
    p = multiprocessing.Process(target=bot_core_logic, args=(email, token, stake, tp, acc_type, curr, is_contract_open))
    p.start()
    flask_local_processes[email] = p
    return redirect(url_for('control_panel'))

@app.route('/stop', methods=['POST'])
def stop_route():
    stop_bot(session['email'])
    return redirect(url_for('control_panel'))

@app.route('/logout')
def logout():
    session.pop('email', None)
    return redirect(url_for('login_route'))

if __name__ == '__main__':
    manager = multiprocessing.Manager()
    is_contract_open = manager.dict()
    if not os.path.exists(ACTIVE_SESSIONS_FILE):
        with open(ACTIVE_SESSIONS_FILE, 'w') as f: f.write('{}')
    app.run(host='0.0.0.0', port=5000)
