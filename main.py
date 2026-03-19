import os
import time
import threading
from datetime import datetime, timedelta
from flask import Flask, render_template_string, request, redirect, url_for
from pymongo import MongoClient
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.common.keys import Keys
import requests

app = Flask(__name__)

# --- CONFIGURATION ---
TELEGRAM_TOKEN = "8431265794:AAGo7BsACdAQlz3-redE7gLHg45XmJAqKKE" 
MONGO_URI = "mongodb+srv://charbelnk111_db_user:Mano123mano@cluster0.2gzqkc8.mongodb.net/?appName=Cluster0"
ASSET_TO_SELECT = "MSFT"

client = MongoClient(MONGO_URI)
db = client['trading_bot_db']
users_collection = db['users']

# --- ADMIN PANEL UI ---
HTML_TEMPLATE = '''
<!DOCTYPE html>
<html>
<head>
    <title>KhouryBot Admin Panel</title>
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <style>
        body { font-family: sans-serif; background: #1a1a2e; color: white; text-align: center; padding: 20px; }
        .container { max-width: 800px; margin: auto; background: #16213e; padding: 30px; border-radius: 15px; }
        h1 { color: #e94560; }
        input, select, button { padding: 12px; margin: 5px; border-radius: 5px; border: none; }
        button { background: #e94560; color: white; font-weight: bold; cursor: pointer; }
        table { width: 100%; border-collapse: collapse; margin-top: 20px; }
        th, td { padding: 15px; border-bottom: 1px solid #4e4e6a; }
        .active { color: #4ee44e; } .expired { color: #ff4d4d; }
    </style>
</head>
<body>
    <div class="container">
        <h1>🚀 KhouryBot Admin</h1>
        <p>Trigger: Second 40 | Entry: Next Minute Candle</p>
        <form action="/add_user" method="post">
            <input type="text" name="chat_id" placeholder="Telegram ID" required>
            <select name="duration">
                <option value="1">1 Day</option>
                <option value="7">7 Days</option>
                <option value="30">30 Days</option>
                <option value="36500">Lifetime</option>
            </select>
            <button type="submit">Add User</button>
        </form>
        <table>
            <tr><th>Telegram ID</th><th>Expiry</th><th>Status</th></tr>
            {% for user in users %}
            <tr>
                <td>{{ user.chat_id }}</td>
                <td>{{ user.expiry.strftime('%Y-%m-%d %H:%M') }}</td>
                <td class="{{ 'active' if user.expiry > now else 'expired' }}">
                    {{ 'ACTIVE ✅' if user.expiry > now else 'EXPIRED ❌' }}
                </td>
            </tr>
            {% endfor %}
        </table>
    </div>
</body>
</html>
'''

@app.route('/')
def index():
    users = list(users_collection.find())
    return render_template_string(HTML_TEMPLATE, users=users, now=datetime.now())

@app.route('/add_user', methods=['POST'])
def add_user():
    chat_id = request.form.get('chat_id')
    days = int(request.form.get('duration'))
    expiry_date = datetime.now() + timedelta(days=days)
    users_collection.update_one({"chat_id": chat_id}, {"$set": {"expiry": expiry_date}}, upsert=True)
    return redirect(url_for('index'))

# --- AUTOMATION ENGINE ---
def signal_engine():
    chrome_options = Options()
    chrome_options.add_argument("--headless")
    chrome_options.add_argument("--no-sandbox")
    chrome_options.add_argument("--disable-dev-shm-usage")
    driver = webdriver.Chrome(options=chrome_options)
    wait = WebDriverWait(driver, 25)

    while True:
        try:
            now = datetime.now()
            # Logic: Request at :40 to enter at :00 of next minute
            if now.second == 40:
                active_users = list(users_collection.find({"expiry": {"$gt": datetime.now()}}))
                if not active_users:
                    time.sleep(1)
                    continue

                driver.get("https://pickachu-bot.netlify.app")
                
                # Popup Crusher
                try:
                    close_btn = driver.find_element(By.XPATH, "//*[contains(text(), 'No thanks')]")
                    close_btn.click()
                except:
                    pass

                # Select Asset
                dropdown = wait.until(EC.element_to_be_clickable((By.XPATH, "//div[contains(@class, 'select')]")))
                dropdown.click()
                time.sleep(1)
                search_input = driver.switch_to.active_element
                search_input.send_keys(ASSET_TO_SELECT)
                time.sleep(1)
                search_input.send_keys(Keys.ENTER)
                
                # Generate
                btn = wait.until(EC.element_to_be_clickable((By.XPATH, "//button[contains(., 'GENERATE SIGNAL')]")))
                btn.click()
                time.sleep(10) # Wait for "Analyzing" state to finish

                # Get Signal Data
                signal = driver.find_element(By.XPATH, "//*[contains(text(), 'SELL') or contains(text(), 'BUY')]").text
                conf = driver.find_element(By.XPATH, "//*[contains(text(), '%')]").text
                
                entry_time = (now + timedelta(minutes=1)).replace(second=0).strftime('%H:%M')
                emoji = "🔴" if "SELL" in signal.upper() else "🟢"
                
                msg = (f"{emoji} *KhouryBot Signal*\n\n"
                       f"📊 Asset: `{ASSET_TO_SELECT}`\n"
                       f"🚦 Action: *{signal}*\n"
                       f"🎯 Confidence: `{conf}`\n"
                       f"⏱ **Entry Time: {entry_time}**")

                for user in active_users:
                    requests.post(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage", 
                                 json={"chat_id": user['chat_id'], "text": msg, "parse_mode": "Markdown"})
                
                time.sleep(150) # Cooldown for 3 minutes
            else:
                time.sleep(0.5)
        except Exception as e:
            print(f"Error: {e}")
            time.sleep(2)

threading.Thread(target=signal_engine, daemon=True).start()

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    app.run(host='0.0.0.0', port=port)
