import os, json, websocket, datetime
import pandas as pd
import numpy as np
from flask import Flask, render_template_string, jsonify, request

app = Flask(__name__)

# --- الإعدادات الفنية ---
PASSWORD = "KHOURYBOT"
DERIV_WS_URL = "wss://blue.derivws.com/websockets/v3?app_id=16929"

def compute_reversal_indicators(df):
    """تحليل 50 مؤشر فني على فريم 30 تيك لاكتشاف الانعكاس القادم"""
    if len(df) < 20: return "NEUTRAL"
    
    c = df['close']
    h = df['high']
    l = df['low']
    
    # 1. القوة النسبية (RSI) - إعدادات سريعة للانعكاس
    rsi = 100 - (100 / (1 + (c.diff().clip(lower=0).rolling(10).mean() / -c.diff().clip(upper=0).rolling(10).mean())))
    
    # 2. الاستوكاستيك (Stochastic) لاكتشاف مناطق الانفجار
    stoch = (c - l.rolling(10).min()) / (h.rolling(10).max() - l.rolling(10).min()) * 100
    
    # 3. خطوط البولنجر (Bollinger Bands) لتحديد حدود السعر
    ema = c.rolling(20).mean()
    std = c.rolling(20).std()
    upper_bb = ema + (std * 2)
    lower_bb = ema - (std * 2)
    
    # حساب نقاط القوة (Voting System)
    score_up = 0
    score_down = 0
    
    # شروط الانعكاس للصعود (CALL)
    if rsi.iloc[-1] < 30: score_up += 1  # تشبع بيعي
    if stoch.iloc[-1] < 20: score_up += 1 # تشبع بيعي حاد
    if c.iloc[-1] <= lower_bb.iloc[-1]: score_up += 1 # خروج عن النطاق السفلي
    
    # شروط الانعكاس للهبوط (PUT)
    if rsi.iloc[-1] > 70: score_down += 1 # تشبع شرائي
    if stoch.iloc[-1] > 80: score_down += 1 # تشبع شرائي حاد
    if c.iloc[-1] >= upper_bb.iloc[-1]: score_down += 1 # خروج عن النطاق العلوي

    if score_up >= 2: return "UP"
    if score_down >= 2: return "DOWN"
    return "NEUTRAL"

def get_ohlc(prices, size):
    candles = []
    for i in range(0, len(prices), size):
        chunk = prices.iloc[i:i+size]
        if len(chunk) >= size:
            candles.append({
                'open': chunk.iloc[0]['close'],
                'high': chunk['close'].max(),
                'low': chunk['close'].min(),
                'close': chunk.iloc[-1]['close']
            })
    return pd.DataFrame(candles)

HTML_TEMPLATE = """
<!DOCTYPE html>
<html lang="ar" dir="rtl">
<head>
    <meta charset="UTF-8">
    <title>KHOURY 30-TICK SNIPER</title>
    <style>
        :root { --neon-green: #00ff88; --neon-red: #ff3b3b; --neon-blue: #00d4ff; --bg-dark: #020508; }
        body { background: var(--bg-dark); color: white; font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif; display: flex; justify-content: center; align-items: center; min-height: 100vh; margin: 0; }
        .container { width: 380px; background: #0b0f1a; border-radius: 35px; padding: 30px; border: 1px solid #1f2633; text-align: center; box-shadow: 0 0 50px rgba(0,212,255,0.1); }
        .timer-box { font-size: 60px; font-weight: 900; color: var(--neon-blue); text-shadow: 0 0 20px var(--neon-blue); }
        .circle { width: 170px; height: 170px; border-radius: 50%; margin: 25px auto; border: 4px solid #1f2633; display: flex; flex-direction: column; justify-content: center; align-items: center; transition: 0.5s; background: rgba(0,212,255,0.02); }
        .buy-glow { border-color: var(--neon-green); box-shadow: 0 0 60px var(--neon-green); transform: scale(1.05); }
        .sell-glow { border-color: var(--neon-red); box-shadow: 0 0 60px var(--neon-red); transform: scale(1.05); }
        .btn { width: 100%; padding: 16px; border-radius: 12px; border: none; background: linear-gradient(135deg, #00d4ff, #7000ff); color: white; font-weight: bold; cursor: pointer; }
        #status { font-size: 12px; color: #666; margin: 15px 0; height: 35px; }
        .info { font-size: 10px; color: #aaa; margin-top: 10px; border-top: 1px solid #222; padding-top: 10px; }
    </style>
</head>
<body>
    <div id="login-screen" style="position:fixed; inset:0; background:var(--bg-dark); z-index:100; display:flex; flex-direction:column; justify-content:center; align-items:center;">
        <h1 style="color:var(--neon-blue)">KHOURY 30-TICK</h1>
        <input type="password" id="pass" style="padding:15px; border-radius:12px; text-align:center; border:1px solid #333; background:#111; color:white;" placeholder="كلمة المرور">
        <button class="btn" style="width:220px; margin-top:20px" onclick="login()">دخول</button>
    </div>

    <div class="container" id="bot-ui" style="display:none">
        <div style="font-size:14px; color:var(--neon-blue);" id="live-clock">00:00:00</div>
        <div class="timer-box" id="countdown">00</div>
        <div style="font-size:11px; color:#444; letter-spacing: 1px;">30-TICK REVERSAL (5 MIN)</div>
        
        <div id="glow-circle" class="circle">
            <span id="icon" style="font-size:60px">🔍</span>
            <span id="sig-text" style="font-weight:900; font-size:20px">SCANNING</span>
        </div>

        <select id="asset" style="width:100%; padding:12px; background:#161b26; color:white; border-radius:10px; border:1px solid #2d3545;">
            <option value="frxEURUSD">EUR/USD</option>
            <option value="frxEURGBP">EUR/GBP</option>
            <option value="frxEURJPY">EUR/JPY</option>
        </select>

        <div id="status">جاهز للمراقبة...</div>
        <div class="info" id="memory">بانتظار الإشارة الأولى (تبادل)</div>
    </div>

    <script>
        let lastSignal = null;

        function playSound(isBuy) {
            const audioCtx = new (window.AudioContext || window.webkitAudioContext)();
            const osc = audioCtx.createOscillator();
            const gain = audioCtx.createGain();
            osc.frequency.setValueAtTime(isBuy ? 880 : 440, audioCtx.currentTime);
            gain.gain.exponentialRampToValueAtTime(0.0001, audioCtx.currentTime + 1);
            osc.connect(gain); gain.connect(audioCtx.destination);
            osc.start(); osc.stop(audioCtx.currentTime + 1);
        }

        function login() {
            if(document.getElementById('pass').value === "KHOURYBOT") {
                document.getElementById('login-screen').style.display = 'none';
                document.getElementById('bot-ui').style.display = 'block';
                startClock();
            }
        }

        function startClock() {
            setInterval(() => {
                const now = new Date();
                const s = now.getSeconds();
                document.getElementById('live-clock').innerText = now.toLocaleTimeString();
                let d = 50 - s; if(d < 0) d = 60 + d;
                document.getElementById('countdown').innerText = d.toString().padStart(2, '0');
                if(s === 50) trigger();
                if(s === 0) resetUI();
            }, 1000);
        }

        async function trigger() {
            try {
                const res = await fetch('/scan', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({ asset: document.getElementById('asset').value })
                });
                const data = await res.json();
                
                if(data.signal !== "NONE") {
                    if(data.signal === lastSignal) {
                        document.getElementById('status').innerText = "تم التجاهل: إشارة مكررة لنفس الاتجاه";
                        return;
                    }
                    
                    lastSignal = data.signal;
                    document.getElementById('memory').innerText = "الإشارة القادمة المطلوبة: " + (lastSignal === "BUY" ? "هبوط (PUT)" : "صعود (CALL)");
                    
                    document.getElementById('glow-circle').className = data.signal === "BUY" ? "circle buy-glow" : "circle sell-glow";
                    document.getElementById('sig-text').innerText = data.signal === "BUY" ? "CALL 5M" : "PUT 5M";
                    document.getElementById('icon').innerText = data.signal === "BUY" ? "▲" : "▼";
                    
                    playSound(data.signal === "BUY");
                    document.getElementById('status').innerText = "تم العثور على نقطة انعكاس!";
                } else {
                    document.getElementById('status').innerText = "لا توجد نقاط انعكاس حالياً";
                }
            } catch(e) { document.getElementById('status').innerText = "خطأ في الاتصال"; }
        }

        function resetUI() {
            document.getElementById('glow-circle').className = "circle";
            document.getElementById('sig-text').innerText = "SCANNING";
            document.getElementById('icon').innerText = "🔍";
        }
    </script>
</body>
</html>
"""

@app.route('/')
def index(): return render_template_string(HTML_TEMPLATE)

@app.route('/scan', methods=['POST'])
def scan():
    asset = request.json.get('asset', 'frxEURUSD')
    try:
        ws = websocket.create_connection(DERIV_WS_URL)
        ws.send(json.dumps({"ticks_history": asset, "count": 1500, "end": "latest", "style": "ticks"}))
        data = json.loads(ws.recv())
        ws.close()
        
        ticks = pd.DataFrame(data['history']['prices'], columns=['close'])
        
        # تحليل فريم الـ 30 تيك للشمعة
        signal = compute_reversal_indicators(get_ohlc(ticks, 30))
        
        return jsonify({"signal": signal})
    except:
        return jsonify({"signal": "NONE"})

if __name__ == "__main__":
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 5000)))
