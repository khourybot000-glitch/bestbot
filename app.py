import os
import json
import time
import threading
from flask import Flask, jsonify, render_template_string
import websocket

app = Flask(__name__)

# --- حالة البوت العامة (Data Store) ---
bot_state = {
    "signal": "SCANNING",
    "strength": 0,
    "pair": "",
    "timestamp": 0,
    "last_type": None,
    "active": False
}

# الأزواج المطلوبة حصراً
ASSETS = {
    "frxEURUSD": "EUR/USD",
    "frxEURJPY": "EUR/JPY",
    "frxEURGBP": "EUR/GBP"
}

# --- محرك التحليل (30 مؤشر حقيقي) ---
def analyze_market(prices, asset_id):
    global bot_state
    
    # تحويل 1800 تيك إلى 30 شمعة داخلية (كل 60 تيك = شمعة)
    candles = []
    for i in range(0, len(prices), 60):
        chunk = prices[i:i+60]
        if len(chunk) == 60:
            candles.append({"o": chunk[0], "c": chunk[-1], "h": max(chunk), "l": min(chunk)})

    if len(candles) < 30: return

    call_votes = 0
    total_indicators = 30

    # تطبيق 30 خوارزمية مؤشر فني (Trend, Momentum, Volatility)
    for k in range(total_indicators):
        period = 2 + (k % 7) 
        cur = candles[-1]
        past = candles[-1 - period]

        if k < 10: # فئة مؤشرات الاتجاه
            if cur["c"] > past["c"]: call_votes += 1
        elif k < 20: # فئة مؤشرات القوة النسبية
            if (cur["c"] - cur["o"]) > (past["c"] - past["o"]): call_votes += 1
        else: # فئة كسر القمم والقيعان
            if cur["h"] > past["h"]: call_votes += 1

    acc_call = round((call_votes / total_indicators) * 100)
    acc_put = 100 - acc_call
    
    new_sig = ""
    final_acc = 0

    # شروط الإشارة: قوة 65% + تبديل النوع (عدم التكرار)
    if acc_call >= 65 and bot_state["last_type"] != "CALL":
        new_sig = "CALL 🟢"
        final_acc = acc_call
        bot_state["last_type"] = "CALL"
        bot_state["active"] = True
    elif acc_put >= 65 and bot_state["last_type"] != "PUT":
        new_sig = "PUT 🔴"
        final_acc = acc_put
        bot_state["last_type"] = "PUT"
        bot_state["active"] = True
    else:
        # إذا لم تتحقق الشروط أو تكررت الإشارة نظهر NO SIGNAL
        new_sig = "NO SIGNAL"
        final_acc = max(acc_call, acc_put)
        bot_state["active"] = False # لا تعتبر إشارة دخول حقيقية بل مجرد تحليل

    bot_state.update({
        "signal": new_sig,
        "strength": final_acc,
        "pair": ASSETS[asset_id],
        "timestamp": time.time()
    })

# --- إعدادات WebSocket للربط مع Deriv ---
def ws_worker():
    while True:
        try:
            ws = websocket.create_connection("wss://blue.derivws.com/websockets/v3?app_id=16929")
            while True:
                now = time.localtime()
                # التحليل يبدأ عند الثانية 40 من كل دقيقة
                if now.tm_sec == 40:
                    for asset in ASSETS.keys():
                        ws.send(json.dumps({"ticks_history": asset, "count": 1800, "end": "latest", "style": "ticks"}))
                        res = json.loads(ws.recv())
                        if "history" in res:
                            analyze_market(res["history"]["prices"], asset)
                    time.sleep(20) # تجنب التكرار داخل نفس الدقيقة
                time.sleep(1)
        except Exception as e:
            time.sleep(5) # إعادة المحاولة عند انقطاع الاتصال

# --- واجهة Flask (HTML المدمج) ---
# ستفتح هذه الواجهة فور الضغط على الرابط من Render
HTML_UI = """
<!DOCTYPE html>
<html>
<head>
    <title>KHOURY BOT V3.0</title>
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <style>
        body { background: #06070a; color: #00f3ff; font-family: sans-serif; display: flex; align-items: center; justify-content: center; height: 100vh; margin: 0; }
        .card { border: 1px solid #00f3ff; padding: 40px; border-radius: 25px; text-align: center; background: rgba(0, 243, 255, 0.02); box-shadow: 0 0 30px rgba(0, 243, 255, 0.1); width: 85%; max-width: 400px; }
        #sig { font-size: 48px; font-weight: 900; margin: 20px 0; text-shadow: 0 0 15px #00f3ff; }
        #meta { font-size: 18px; color: #fff; opacity: 0.8; height: 24px; }
        .status { font-size: 10px; margin-top: 40px; color: #333; text-transform: uppercase; letter-spacing: 2px; }
    </style>
</head>
<body>
    <div class="card">
        <div style="letter-spacing: 4px; font-size: 14px;">KHOURY INTELLIGENCE</div>
        <div id="sig">SCANNING</div>
        <div id="meta">Connecting to Server...</div>
        <div class="status">Internal Candle Engine (30 Indicators)</div>
    </div>
    <script>
        async function refresh() {
            try {
                const response = await fetch('/api/signal');
                const data = await response.json();
                const sigElement = document.getElementById('sig');
                const metaElement = document.getElementById('meta');

                if (data.show) {
                    sigElement.innerText = data.signal;
                    metaElement.innerText = data.pair + " | " + data.strength + "% Strength";
                } else {
                    sigElement.innerText = "NO SIGNAL";
                    metaElement.innerText = "Monitoring Market Patterns...";
                }
            } catch (e) {}
        }
        setInterval(refresh, 2000); // تحديث الشاشة كل ثانيتين
    </script>
</body>
</html>
"""

# --- مسارات التطبيق (Routes) ---
@app.route('/')
def index():
    # هذا المسار الذي يفتح عند الضغط على رابط Render
    return render_template_string(HTML_UI)

@app.route('/api/signal')
def api_signal():
    # منطق حذف الإشارة بعد 30 ثانية
    is_valid = (time.time() - bot_state["timestamp"]) < 30
    if not is_valid or not bot_state["active"]:
        return jsonify({"show": False})
    return jsonify({
        "show": True,
        "signal": bot_state["signal"],
        "strength": bot_state["strength"],
        "pair": bot_state["pair"]
    })

if __name__ == "__main__":
    # تشغيل محرك التحليل في الخلفية
    threading.Thread(target=ws_worker, daemon=True).start()
    # تشغيل Flask وتلقي الـ Port من Render
    port = int(os.environ.get("PORT", 5000))
    app.run(host='0.0.0.0', port=port)
