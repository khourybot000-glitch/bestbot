import os
import json
import time
import pandas as pd
from datetime import datetime, timedelta
import ssl
from websocket import create_connection, WebSocketTimeoutException
from flask import Flask, request, jsonify, render_template_string
import ta  # مكتبة التحليل الفني

# =======================================================
# الإعدادات والثوابت
# =======================================================

app = Flask(__name__)

# تأكد من استخدام معرف تطبيقك الخاص
DERIV_WSS = "wss://blue.derivws.com/websockets/v3?app_id=16929"
# عدد النقاط المطلوب، تم تخفيضه إلى 5000 لزيادة الاستقرار
TICK_COUNT = 5000
MAX_RETRIES = 3 # عدد محاولات إعادة الاتصال

# =======================================================
# دوال جلب البيانات ومعالجتها
# =======================================================

def get_market_data(symbol, time_frame, count) -> pd.DataFrame:
    """
    جلب النقرات التاريخية من Deriv WSS مع منطق إعادة المحاولة.
    تم إضافة إعادة محاولة (Retry) وزيادة المهلة (Timeout) لزيادة الاستقرار.
    """
    for attempt in range(MAX_RETRIES):
        ws = None  # تهيئة ws خارج try
        try:
            # محاولة إنشاء الاتصال
            ws = create_connection(DERIV_WSS, sslopt={"cert_reqs": ssl.CERT_NONE})
            # زيادة المهلة لـ 60 ثانية لضمان استلام الـ 5000 نقطة
            ws.settimeout(60) 

            request_data = json.dumps({
                "ticks_history": symbol, "end": "latest", "start": 1, 
                "style": "ticks", "count": count, "granularity": 0
            })
            
            ws.send(request_data)
            response = ws.recv()
            data = json.loads(response)
            
            # تحقق من وجود بيانات صالحة
            if 'history' in data and 'prices' in data['history']:
                df_ticks = pd.DataFrame({'epoch': data['history']['times'], 'quote': data['history']['prices']})
                df_ticks['quote'] = pd.to_numeric(df_ticks['quote'], errors='coerce')
                df_ticks.dropna(inplace=True)
                return df_ticks
            
            print(f"ATTEMPT {attempt + 1}: Received invalid data format from Deriv.")
        
        except WebSocketTimeoutException:
            print(f"ATTEMPT {attempt + 1}: WebSocket Timeout.")
        except Exception as e:
            # يشمل أخطاء الاتصال، Rate Limit، وما إلى ذلك
            print(f"ATTEMPT {attempt + 1}: General connection error: {e}")
        finally:
            # نضمن إغلاق الاتصال
            if ws:
                try: ws.close() 
                except: pass 

        # الانتظار قبل المحاولة التالية (تأخير تصاعدي)
        if attempt < MAX_RETRIES - 1:
            wait_time = 2 ** attempt
            print(f"Waiting {wait_time} seconds before retrying...")
            time.sleep(wait_time)

    # إذا فشلت جميع المحاولات
    print(f"FATAL: All {MAX_RETRIES} attempts to fetch data for {symbol} failed.")
    return pd.DataFrame()


def aggregate_ticks_to_candles(df_ticks: pd.DataFrame, time_frame: str) -> pd.DataFrame:
    """تجميع بيانات النقاط إلى شموع (Candles) حسب الإطار الزمني."""
    if df_ticks.empty:
        return pd.DataFrame()

    df_ticks['datetime'] = pd.to_datetime(df_ticks['epoch'], unit='s')
    df_ticks.set_index('datetime', inplace=True)
    
    # تحويل الإطار الزمني مثل '5m' إلى offset
    freq = time_frame.lower() 

    # تجميع الشموع
    df_candles = df_ticks['quote'].resample(freq).ohlc()
    df_candles.dropna(inplace=True)
    return df_candles

def generate_inverted_signal(df_candles: pd.DataFrame) -> dict:
    """توليد إشارة التداول بناءً على استراتيجية Inverted MA Crossover."""
    
    if df_candles.empty or len(df_candles) < 250: # نحتاج 250 شمعة
        return {"signal": "⚠️ ERROR", "color": "darkred", "reason": "بيانات غير كافية (يتطلب 250 شمعة)."}

    # حساب المتوسطات المتحركة (SMA)
    # نستخدم Close Price
    df_candles['SMA_20'] = ta.trend.sma_indicator(df_candles['close'], window=20)
    df_candles['SMA_50'] = ta.trend.sma_indicator(df_candles['close'], window=50)
    
    # تحديد أحدث القيم
    latest_close = df_candles['close'].iloc[-1]
    sma_20 = df_candles['SMA_20'].iloc[-1]
    sma_50 = df_candles['SMA_50'].iloc[-1]
    
    # منطق استراتيجية Inverted MA Crossover
    # الإشارة هي دائماً عكس الاتجاه التقليدي
    
    # حالة صعودية تقليدية (Trend UP):
    # SMA 20 (أسرع) فوق SMA 50 (أبطأ)
    is_trend_up = (sma_20 > sma_50)
    
    # إشارة البيع (SELL) - عكس الاتجاه الصعودي
    if is_trend_up:
        # الاتجاه صاعد -> إشارتنا العكسية هي بيع
        signal = "SELL (INVERTED)"
        color = "red"
        reason = "SMA 20 (أسرع) فوق SMA 50 (أبطأ) - إشارة بيع عكسية."
    
    # إشارة الشراء (BUY) - عكس الاتجاه الهبوطي
    else: # is_trend_down: (SMA 20 تحت SMA 50)
        # الاتجاه هابط -> إشارتنا العكسية هي شراء
        signal = "BUY (INVERTED)"
        color = "green"
        reason = "SMA 20 (أسرع) تحت SMA 50 (أبطأ) - إشارة شراء عكسية."
        
    return {
        "signal": signal,
        "color": color,
        "price": f"{latest_close:.5f}",
        "reason": reason
    }

# =======================================================
# دوال الـ Flask (المسارات)
# =======================================================

@app.route('/')
def index():
    """عرض الواجهة الرئيسية وتحديث مؤقت العد التنازلي."""
    
    # توقيت إغلاق الشمعة القادمة (على 5 دقائق)
    current_time = datetime.now()
    # الشمعة تغلق في الدقيقة 5 و 10 و 15 و ...
    next_close_minute = ((current_time.minute // 5) * 5) + 5
    
    # إذا كانت الدقيقة القادمة ستتجاوز 60، نعدل للساعة القادمة
    if next_close_minute >= 60:
        next_close_time = (current_time + timedelta(hours=1)).replace(minute=0, second=0, microsecond=0)
    else:
        next_close_time = current_time.replace(minute=next_close_minute, second=0, microsecond=0)

    # حساب الدقائق والثواني المتبقية (بالتوقيت المحلي)
    time_remaining = next_close_time - current_time
    total_seconds = int(time_remaining.total_seconds())

    # عرض التوقيت بالتنسيق: HH:MM
    display_close_time = next_close_time.strftime("%H:%M")

    # القالب النصي HTML مع كود JavaScript المُصحح
    html_template = f"""
    <!DOCTYPE html>
    <html lang="ar" dir="rtl">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>KhouryBot محور 21</title>
        <style>
            body {{
                font-family: 'Arial', sans-serif;
                background-color: #2c3e50;
                color: #ecf0f1;
                text-align: center;
                padding: 20px;
            }}
            .container {{
                max-width: 600px;
                margin: 0 auto;
                background-color: #34495e;
                padding: 30px;
                border-radius: 10px;
                box-shadow: 0 4px 12px rgba(0, 0, 0, 0.5);
            }}
            h1 {{ color: #f1c40f; margin-bottom: 5px; }}
            p {{ font-size: 1.1em; color: #bdc3c7; }}
            .data-box {{
                background-color: #2c3e50;
                padding: 15px;
                margin: 20px 0;
                border-radius: 8px;
                text-align: right;
            }}
            .signal-display {{
                min-height: 100px;
                display: flex;
                flex-direction: column;
                justify-content: center;
                align-items: center;
                font-size: 1.8em;
                font-weight: bold;
                border-radius: 8px;
                transition: background-color 0.5s;
                color: #ecf0f1;
            }}
            .time-display {{ font-size: 1.5em; margin-top: 10px; color: #9b59b6; }}
            .loader {{ 
                font-size: 1.5em; 
                color: #3498db; 
                margin-top: 15px; 
            }}
            .loader span {{ animation: pulse 1s infinite alternate; }}
            @keyframes pulse {{
                0% {{ opacity: 0.5; }}
                100% {{ opacity: 1; }}
            }}
            .error-message {{ color: darkred; font-weight: bold; margin-top: 10px; }}
        </style>
    </head>
    <body>
        <div class="container">
            <h1>محور 21 KhouryBot - فوركس فقط</h1>
            <p>تحليل الحد الأقصى للقوة. الإشارة تظهر قبل 10 ثوانٍ من إغلاق شمعة الـ 5 دقائق.</p>

            <div class="data-box">
                <p><strong>الوقت المتبقي لظهور الإشارة:</strong></p>
                <div id="countdown-timer" class="time-display">جاري التحميل...</div>
                <p>إغلاق الشمعة: <span id="close-time">{display_close_time}</span> (بتوقيتك المحلي)</p>
            </div>

            <div id="signal-area" class="signal-display">
                <p class="loader" id="loader-text">KhouryBot... يحلل القوة القصوى...</p>
                <p class="time-display" id="error-display" style="display: none;"></p>
            </div>

            <div class="data-box">
                <p><strong>زوج العملات:</strong> EUR/USD (fixEURUSD)</p>
                <p><strong>آخر سعر إغلاق تم تحليله:</strong> <span id="last-price">N/A</span></p>
                <p class="error-message" id="error-reason"></p>
            </div>
            
            <p style="font-size: 0.9em; color: #7f8c8d;">تم التطوير ليكون مخصصًا لـ Deriv API - قد لا يعمل على منصات أخرى.</p>
        </div>

        <script>
            let totalSeconds = {total_seconds};
            const countdownTimer = document.getElementById('countdown-timer');
            const signalArea = document.getElementById('signal-area');
            const loaderText = document.getElementById('loader-text');
            const lastPrice = document.getElementById('last-price');
            const errorReason = document.getElementById('error-reason');
            const errorDisplay = document.getElementById('error-display');
            const closeTimeDisplay = document.getElementById('close-time');

            function updateTimer() {{
                if (totalSeconds <= 0) {{
                    // إعادة تحميل الصفحة أو إعادة حساب التوقيت
                    location.reload(); 
                    return;
                }}

                totalSeconds--;

                const displayMinutes = Math.floor(totalSeconds / 60);
                const displaySeconds = totalSeconds % 60;
                
                // 👈🏻 الكود المُصحح: يستخدم الجمع بدلاً من Template Literals لتجنب تضارب Python
                countdownTimer.textContent = displayMinutes.toString().padStart(2, '0') + ':' + displaySeconds.toString().padStart(2, '0');

                // إذا تبقى 10 ثوانٍ، نبدأ بجلب الإشارة
                if (totalSeconds === 10) {{
                    fetchSignal();
                }}
            }}

            function fetchSignal() {{
                signalArea.style.backgroundColor = '#34495e'; // لون الخلفية العادي
                loaderText.style.display = 'block';
                errorReason.textContent = '';
                errorDisplay.style.display = 'none';

                fetch('/get-inverted-signal', {{
                    method: 'POST',
                    headers: {{ 'Content-Type': 'application/json' }},
                    body: JSON.stringify({{ symbol: 'frxEURUSD', time_frame: '5m' }})
                }})
                .then(response => response.json())
                .then(data => {{
                    loaderText.style.display = 'none';
                    lastPrice.textContent = data.price || 'N/A';
                    errorReason.textContent = '';
                    errorDisplay.style.display = 'none';

                    // تحديث الإشارة واللون
                    signalArea.textContent = data.signal;
                    signalArea.style.backgroundColor = data.color === 'red' ? '#c0392b' : data.color === 'green' ? '#27ae60' : '#34495e';
                    
                    // إذا كان هناك خطأ في البيانات/الجلب، نعرض السبب
                    if (data.signal === "⚠️ ERROR") {{
                         errorReason.textContent = data.reason || 'حدث خطأ غير معروف في التحليل.';
                         signalArea.style.backgroundColor = 'darkred';
                    }}
                }})
                .catch(error => {{
                    // خطأ شبكة أو خطأ في الاتصال بالخادم
                    loaderText.style.display = 'none';
                    errorReason.textContent = 'ERROR: فشل في الاتصال بالخادم. حاول تحديث الصفحة.';
                    signalArea.style.backgroundColor = 'darkred';
                    signalArea.textContent = "ERROR";
                }});
            }}

            // بدء المؤقت
            updateTimer();
            setInterval(updateTimer, 1000);
        </script>
    </body>
    </html>
    """
    return render_template_string(html_template)


@app.route('/get-inverted-signal', methods=['POST'])
def get_signal_api():
    """نقطة نهاية (API Endpoint) لجلب وتحليل الإشارة."""
    try:
        data = request.json
        symbol = data.get('symbol', 'frxEURUSD')
        time_frame = data.get('time_frame', '5m')
    except:
        return jsonify({"signal": "⚠️ ERROR", "color": "darkred", "price": "N/A", "reason": "خطأ في بيانات الطلب (JSON)."})

    # 1. جلب البيانات (يستخدم الآن 5000 نقطة)
    df_ticks = get_market_data(symbol, time_frame, TICK_COUNT)

    # 2. تجميع النقاط إلى شموع
    df_local = aggregate_ticks_to_candles(df_ticks, time_frame)
    
    # التحقق من فشل جلب البيانات
    if df_local.empty:
        # رسالة خطأ واضحة تظهر للواجهة الأمامية
        return jsonify({
            "signal": "⚠️ ERROR", 
            "color": "darkred", 
            "price": "N/A", 
            "reason": "فشل جلب النقرات أو عدم كفاية البيانات لتكوين الشموع المحلية (قد يكون بسبب تجاوز حد الطلبات أو انتهاء مهلة الاتصال)."
        }), 200 # نستخدم 200 لتمرير رسالة الخطأ للواجهة الأمامية

    # 3. توليد الإشارة
    signal_result = generate_inverted_signal(df_local)

    return jsonify(signal_result), 200


if __name__ == '__main__':
    # يتم استخدام Gunicorn/Render عادةً لتشغيل التطبيق، ولكن هذا للتشغيل المحلي
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=True)
