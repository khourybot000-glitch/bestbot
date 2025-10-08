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

app = Flask(_name_)

# تأكد من استخدام معرف تطبيقك الخاص
DERIV_WSS = "wss://blue.derivws.com/websockets/v3?app_id=16929"
MAX_RETRIES = 3 # عدد محاولات إعادة الاتصال

# =======================================================
# دوال جلب البيانات ومعالجتها - الآن يجلب 50 شمعة 1 دقيقة
# =======================================================

def get_market_data(symbol, time_frame, count) -> pd.DataFrame:
    """
    جلب الشموع التاريخية مباشرةً من Deriv WSS.
    (50 شمعة، إطار زمني 1 دقيقة)
    """
    for attempt in range(MAX_RETRIES):
        ws = None
        try:
            ws = create_connection(DERIV_WSS, sslopt={"cert_reqs": ssl.CERT_NONE})
            # مهلة 60 ثانية ما زالت آمنة، رغم أن الطلب الآن خفيف جداً
            ws.settimeout(60) 

            # 👈🏻 التعديل الأول: Granularity = 60 ثانية (1 دقيقة)
            granularity = 60  
            # 👈🏻 التعديل الثاني: Candle Count = 50 شمعة
            candle_count = 50 

            request_data = json.dumps({
                "candles_history": symbol,
                "end": "latest",
                "count": candle_count, 
                "granularity": granularity
            })
            
            ws.send(request_data)
            response = ws.recv()
            data = json.loads(response)
            
            if 'candles' in data and data['candles']:
                df_candles = pd.DataFrame(data['candles'])
                df_candles['epoch'] = pd.to_numeric(df_candles['epoch'])
                df_candles.rename(columns={'open': 'open', 'high': 'high', 'low': 'low', 'close': 'close'}, inplace=True)
                
                for col in ['open', 'high', 'low', 'close']:
                    df_candles[col] = pd.to_numeric(df_candles[col], errors='coerce')
                
                df_candles.dropna(inplace=True)
                
                df_candles['datetime'] = pd.to_datetime(df_candles['epoch'], unit='s')
                df_candles.set_index('datetime', inplace=True)
                df_candles.sort_index(inplace=True) 
                
                return df_candles
            
            print(f"ATTEMPT {attempt + 1}: Received invalid candle data format from Deriv.")
        
        except WebSocketTimeoutException:
            print(f"ATTEMPT {attempt + 1}: WebSocket Timeout.")
        except Exception as e:
            print(f"ATTEMPT {attempt + 1}: General connection error: {e}")
        finally:
            if ws:
                try: ws.close() 
                except: pass 

        if attempt < MAX_RETRIES - 1:
            wait_time = 2 ** attempt
            print(f"Waiting {wait_time} seconds before retrying...")
            time.sleep(wait_time)

    print(f"FATAL: All {MAX_RETRIES} attempts to fetch data for {symbol} failed.")
    return pd.DataFrame()

# ⚠ ملاحظة: تم حذف دالة aggregate_ticks_to_candles حيث لم نعد بحاجة لها.

def generate_inverted_signal(df_candles: pd.DataFrame) -> dict:
    """توليد إشارة التداول بناءً على استراتيجية Inverted MA Crossover."""
    
    # 👈🏻 تم تعديل الشرط ليناسب 50 شمعة كحد أدنى
    if df_candles.empty or len(df_candles) < 50: 
        return {"signal": "⚠ ERROR", "color": "darkred", "reason": "بيانات غير كافية (يتطلب 50 شمعة 1 دقيقة لحساب المتوسطات)."}

    # يجب عليك التأكد من أن هذه المتوسطات تعمل بشكل جيد على إطار الـ 1 دقيقة.
    # حساب المتوسطات المتحركة (SMA)
    df_candles['SMA_20'] = ta.trend.sma_indicator(df_candles['close'], window=20)
    df_candles['SMA_50'] = ta.trend.sma_indicator(df_candles['close'], window=50)
    
    latest_close = df_candles['close'].iloc[-1]
    # قد تكون هذه القيمة NaN إذا لم يتوفر 50 شمعة، لذلك يجب التحقق
    sma_20 = df_candles['SMA_20'].iloc[-1] if not df_candles['SMA_20'].empty else None
    sma_50 = df_candles['SMA_50'].iloc[-1] if not df_candles['SMA_50'].empty else None

    if sma_20 is None or sma_50 is None:
         return {"signal": "⚠ ERROR", "color": "darkred", "reason": "خطأ في حساب المتوسطات المتحركة (قد تكون البيانات غير مكتملة)."}

    # منطق استراتيجية Inverted MA Crossover
    is_trend_up = (sma_20 > sma_50)
    
    if is_trend_up:
        # الاتجاه صاعد -> إشارتنا العكسية هي بيع
        signal = "SELL (INVERTED)"
        color = "red"
        reason = "SMA 20 (أسرع) فوق SMA 50 (أبطأ) - إشارة بيع عكسية."
    else: 
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
    
    # 👈🏻 تم تعديل توقيت الإغلاق ليعكس إطار الـ 1 دقيقة
    current_time = datetime.now()
    next_close_minute = current_time.minute + 1
    
    # إذا كانت الدقيقة القادمة ستتجاوز 60
    if next_close_minute >= 60:
        next_close_time = (current_time + timedelta(hours=1)).replace(minute=0, second=0, microsecond=0)
    else:
        next_close_time = current_time.replace(minute=next_close_minute, second=0, microsecond=0)

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
            /* (تصميم CSS) */
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
            <p>تحليل الحد الأقصى للقوة. الإشارة تظهر قبل 10 ثوانٍ من إغلاق شمعة الـ *1 دقيقة*.</p>

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
                
                // الكود المُصحح: يستخدم الجمع بدلاً من Template Literals
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
                    // 👈🏻 تم تعديل الإطار الزمني في الطلب إلى 1 دقيقة
                    body: JSON.stringify({{ symbol: 'frxEURUSD', time_frame: '1m' }}) 
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
                    if (data.signal === "⚠ ERROR") {{
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
        # 👈🏻 تم تعديل الإطار الزمني الافتراضي إلى 1 دقيقة
        time_frame = data.get('time_frame', '1m') 
    except:
        return jsonify({"signal": "⚠ ERROR", "color": "darkred", "price": "N/A", "reason": "خطأ في بيانات الطلب (JSON)."})

    # 1. جلب الشموع مباشرةً: نطلب 50 شمعة
    # عدد الشموع (50) يتم التعامل معه داخل دالة get_market_data الآن
    df_candles = get_market_data(symbol, time_frame, 50) 
    
    # التحقق من فشل جلب البيانات
    if df_candles.empty:
        return jsonify({
            "signal": "⚠ ERROR", 
            "color": "darkred", 
            "price": "N/A", 
            "reason": "فشل جلب الشموع التاريخية من Deriv. قد يكون بسبب مشكلة في الاتصال أو تجاوز حد الطلبات."
        }), 200

    # 2. توليد الإشارة
    signal_result = generate_inverted_signal(df_candles)

    return jsonify(signal_result), 200


if _name_ == '_main_':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=True)
