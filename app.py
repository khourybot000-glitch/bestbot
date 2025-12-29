from flask import Flask, jsonify, request
from flask_cors import CORS
import ccxt
import datetime

app = Flask(__name__)
CORS(app)

# إعداد الربط مع Binance جلب البيانات
exchange = ccxt.binance()
market_data = {}

@app.route('/')
def home():
    return "KHOURY BOT - Universal Forex Analyzer is Live", 200

@app.route('/signal')
def get_signal():
    pair_raw = request.args.get('pair', 'EURUSD')
    # تحويل التنسيق ليتوافق مع أزواج بينانس (مثلاً EURUSD تصبح EUR/USDT)
    pair_binance = f"{pair_raw[:3]}/{pair_raw[3:]}T"
    
    now = datetime.datetime.now()
    m = now.minute
    s = now.second

    try:
        ticker = exchange.fetch_ticker(pair_binance)
        current_price = ticker['last']

        # إنشاء سجل للزوج إذا لم يكن موجوداً
        if pair_raw not in market_data:
            market_data[pair_raw] = {'open10': None, 'open1': None}

        # 1. تسجيل سعر فتح الـ 10 دقائق (عند دقيقة 00)
        if m % 10 == 0 and s == 0:
            market_data[pair_raw]['open10'] = current_price

        # 2. تسجيل سعر فتح الدقيقة الأخيرة (عند دقيقة 09)
        if m % 10 == 9 and s == 0:
            market_data[pair_raw]['open1'] = current_price

        # 3. تحليل الإشارة عند الثانية 58 من الدقيقة 9
        if m % 10 == 9 and s >= 58:
            data = market_data[pair_raw]
            if data['open10'] and data['open1']:
                is_10m_up = current_price > data['open10']
                is_1m_up = current_price > data['open1']
                
                # تنفيذ الاستراتيجية: الدخول عكس الاتجاه فقط عند تطابق الاتجاهين
                if is_10m_up and is_1m_up:
                    # تنظيف البيانات للدورة القادمة
                    market_data[pair_raw] = {'open10': None, 'open1': None}
                    return jsonify({"status": "trade", "action": "put", "msg": "Confirm: 10m & 1m UP -> SELL"})
                elif not is_10m_up and not is_1m_up:
                    market_data[pair_raw] = {'open10': None, 'open1': None}
                    return jsonify({"status": "trade", "action": "call", "msg": "Confirm: 10m & 1m DOWN -> BUY"})
                else:
                    return jsonify({"status": "wait", "msg": "Directions mismatch - No Trade"})

        return jsonify({
            "status": "scanning", 
            "pair": pair_raw, 
            "price": current_price,
            "has_open10": market_data[pair_raw]['open10'] is not None,
            "has_open1": market_data[pair_raw]['open1'] is not None
        })
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)})

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=10000)
