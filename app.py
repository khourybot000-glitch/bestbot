import os
import time
import datetime
import sys
from iqoptionapi.stable_api import IQ_Option
from iqoptionapi.api.iqoptionapi import errors
import requests # مكتبة بسيطة لاستخدامها لاحقاً في الإعداد (إن لم يتم استخدامها الآن)

# =========================================================================
# 1. متغيرات البيئة (Environment Variables)
# =========================================================================

# يتم جلب الإعدادات من Render Environment Variables
EMAIL = os.environ.get("IQ_EMAIL")
PASSWORD = os.environ.get("IQ_PASSWORD")
ACCOUNT_TYPE = os.environ.get("ACCOUNT_TYPE", "PRACTICE") # الافتراضي هو التجريبي
ASSET = os.environ.get("SELECTED_ASSET")
INITIAL_AMOUNT = float(os.environ.get("INITIAL_AMOUNT", 1.0))
MARTINGALE_STEPS = int(os.environ.get("MARTINGALE_STEPS", 1))
MARTINGALE_MULTIPLIER = float(os.environ.get("MARTINGALE_MULTIPLIER", 2.2))
DURATION = 1 # ثابتة على 1 دقيقة

# =========================================================================
# 2. الدوال المساعدة
# =========================================================================

def calculate_martingale_amounts(initial, steps, multiplier):
    """حساب مبالغ المضاعفات."""
    amounts = [initial]
    for i in range(1, steps):
        amounts.append(round(amounts[-1] * multiplier, 2))
    return amounts

def wait_for_minute_start():
    """المزامنة مع بداية الدقيقة الجديدة تماماً."""
    current_time = datetime.datetime.now()
    seconds_to_wait = 60 - current_time.second
    
    if seconds_to_wait > 0 and seconds_to_wait <= 60:
        print(f"[{current_time.strftime('%H:%M:%S')}] جاري المزامنة. انتظار {seconds_to_wait} ثوانٍ...")
        time.sleep(seconds_to_wait)
        time.sleep(1) # ثانية إضافية لضمان تحديث بيانات الشمعة

def get_trade_signal(iq_api, asset):
    """استراتيجية: تحليل الشمعة الأخيرة (1 دقيقة) واتباع اتجاهها."""
    print(f"[{datetime.datetime.now().strftime('%H:%M:%S')}] جاري تحليل آخر شمعة مكتملة...")
    
    candles = iq_api.get_candles(asset, 60, 1, time.time())
    
    if not candles:
        print("⚠ لم يتم العثور على بيانات شموع.")
        return None
    
    last_candle = candles[0]
    open_price = last_candle['open']
    close_price = last_candle['close']
    
    # تحديد الاتجاه
    if close_price > open_price:
        print("   -> إشارة: صعود (Call) 🟢")
        return 'call'
    elif close_price < open_price:
        print("   -> إشارة: هبوط (Put) 🔴")
        return 'put'
    else:
        print("   -> إشارة: محايد. لا تداول. ⚪")
        return None

# =========================================================================
# 3. وظيفة التشغيل الرئيسية
# =========================================================================

def run_bot():
    print("--- بدء تشغيل بوت التداول المتزامن (Render) ---")

    # التحقق من الإعدادات الضرورية
    if not all([EMAIL, PASSWORD, ASSET]):
        print("❌ خطأ: لم يتم تعيين متغيرات البيئة (EMAIL, PASSWORD, ASSET). يرجى التحقق من إعدادات Render.")
        return

    # 1. الاتصال
    Iq = IQ_Option(EMAIL, PASSWORD) 
    print("جارٍ محاولة الاتصال...")
    check, reason = Iq.connect()

    if not check:
        print(f"❌ فشل الاتصال. السبب: {reason}")
        sys.exit() 
        
    # 2. إعداد الحساب والمضاعفات
    Iq.change_balance(ACCOUNT_TYPE)
    martingale_amounts = calculate_martingale_amounts(INITIAL_AMOUNT, MARTINGALE_STEPS, MARTINGALE_MULTIPLIER)
    current_martingale_step = 0
    MARTINGALE_STEPS_TOTAL = len(martingale_amounts)
    
    print(f"✅ تم تسجيل الدخول بنجاح على حساب: {ACCOUNT_TYPE}")
    print(f"✅ الأصل: {ASSET} | مبالغ المضاعفات: {martingale_amounts}")
    
    # 3. حلقة التداول الرئيسية
    while True:
        try:
            # 1. المزامنة: الانتظار حتى بداية الدقيقة الجديدة
            wait_for_minute_start()
            
            # 2. تحديد مبلغ الصفقة
            if current_martingale_step >= MARTINGALE_STEPS_TOTAL:
                print("🛑 تجاوز الحد الأقصى للمضاعفات. إعادة تعيين.")
                current_martingale_step = 0
            
            trade_amount = martingale_amounts[current_martingale_step]
            
            print(f"\n--- الجولة الحالية: {trade_amount:.2f}$ (الخطوة {current_martingale_step + 1}/{MARTINGALE_STEPS_TOTAL}) ---")

            # 3. الحصول على الإشارة
            signal = get_trade_signal(Iq, ASSET)

            if signal in ['call', 'put']:
                # 4. تنفيذ الصفقة
                print(f"💰 دخول صفقة {signal.upper()}...")
                
                # تنفيذ الشراء
                (check_buy, order_id) = Iq.buy(trade_amount, ASSET, signal, DURATION)

                if check_buy:
                    print(f"✅ تم فتح الصفقة. انتظار النتيجة...")
                    
                    # الانتظار حتى انتهاء الصفقة
                    time.sleep(DURATION * 60 + 5) 
                    
                    # 5. التحقق من النتيجة
                    result = Iq.check_win_v4(order_id)
                    
                    if result == 'win':
                        print("🎉 ربح! إعادة تعيين المبلغ الأولي.")
                        current_martingale_step = 0 
                    elif result == 'lose':
                        print("💔 خسارة! الانتقال إلى خطوة المضاعفة التالية.")
                        current_martingale_step += 1 
                    else:
                        print("❓ تعادل/نتيجة غير واضحة. الحفاظ على الخطوة الحالية.")

                else:
                    print("❌ فشل فتح الصفقة. قد تكون مشكلة في المنصة.")
            else:
                print("⚪ لا تداول: الشمعة محايدة. انتظار للدقيقة التالية.")

        except errors.NoOpenTimeError:
             print(f"🛑 خطأ: الأصل {ASSET} مغلق حالياً أو غير متوفر. سأنتظر 60 ثانية للمحاولة مجدداً.")
             time.sleep(60)
        except Exception as e:
            print(f"⚠ حدث خطأ غير متوقع: {e}. سأحاول مجدداً بعد 10 ثوانٍ.")
            time.sleep(10) 

# =========================================================================
# 4. نقطة الدخول (Entry Point) ودمجها مع متطلبات Render
# =========================================================================

# سنستخدم مكتبة بسيطة مثل Flask أو FastAPI لجعل الكود يعمل كخدمة ويب يمكن لـ UptimeRobot الوصول إليها. 
# ولكن، لتجنب التعقيد، سنكتفي بجعل الكود يعمل كخدمة عاملة (Worker) تستمر في التشغيل. 
# الطريقة الأسهل للـ Free Tier هي تهيئة مسار بسيط لـ Health Check.

if _name_ == "_main_":
    # هذا الجزء من الكود يجعل البوت يعمل كخادم بسيط يستجيب لطلبات UptimeRobot
    # Render عادةً ما يتوقع تشغيل "خادم ويب" في الخطة المجانية.
    
    # يمكنك استخدام مكتبة خفيفة مثل 'http.server' أو 'waitress' لكن سنقوم بتبسيط الأمر.
    
    # في الواقع، Render يسمح بتشغيل أمر 'start' مخصص. سنقوم بتشغيل البوت كـ Worker Service.
    
    # للتوافق مع شروط Free Tier، يجب أن تعمل الخدمة كـ Web Service، وهذا يتطلب مكتبة مثل Flask.
    # بما أن الهدف هو التشغيل المستمر، فسنركز على الأمر الذي يشغل run_bot().
    
    # يرجى ملاحظة: نظرًا لأنك لا تستطيع تشغيل 'Worker' في الخطة المجانية، فإننا سنتجاوز هذه النقطة
    # ونطلب من Render تشغيل الأمر التالي مباشرةً، على أمل أن يبقى مستيقظًا بسبب UptimeRobot.
    
    print("بدء تنفيذ حلقة البوت الرئيسية...")
    run_bot()
