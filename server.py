from flask import Flask, jsonify, request, send_from_directory
from flask_cors import CORS
import random
import requests
import pandas as pd
import ta
import os
import asyncio
import threading
import logging
import sqlite3

from aiogram import Bot, Dispatcher, types
from aiogram.types import ReplyKeyboardMarkup, KeyboardButton
from aiogram.filters import CommandStart

logging.basicConfig(level=logging.INFO)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# ======================
# FLASK APP
# ======================

app = Flask(
    __name__,
    static_folder=os.path.join(BASE_DIR, "web"),
    static_url_path=""
)
CORS(app, resources={r"/*": {"origins": "*"}})

# ======================
# НАСТРОЙКИ
# ======================

TOKEN = os.getenv("BOT_TOKEN")
API_KEY = os.getenv("API_KEY")
ADMIN_ID = int(os.getenv("ADMIN_ID", "574717871"))
WEBAPP_URL = os.getenv("WEBAPP_URL", "https://lart-trade-ai-production.up.railway.app")

# ======================
# DATABASE
# ======================

conn = sqlite3.connect("users.db", check_same_thread=False)
cursor = conn.cursor()

cursor.execute("""
CREATE TABLE IF NOT EXISTS users(
    telegram_id INTEGER PRIMARY KEY,
    username TEXT,
    pocket_id TEXT,
    status TEXT
)
""")
conn.commit()

# ======================
# СПИСОК ПАР
# ======================

symbols = [
    "GBPUSD", "USDJPY", "USDCHF", "AUDUSD", "USDCAD", "NZDUSD",
    "EURGBP", "EURJPY", "EURCHF", "EURAUD", "EURCAD",
    "GBPJPY", "GBPCHF", "GBPAUD", "GBPCAD",
    "AUDJPY", "AUDCHF", "AUDCAD",
    "CADJPY", "CADCHF",
    "NZDJPY", "NZDCAD"
]

# ======================
# SIGNAL LOGIC
# ======================

def get_data(symbol):
    try:
        url = (
            f"https://api.twelvedata.com/time_series"
            f"?symbol={symbol}&interval=1min&outputsize=60&apikey={API_KEY}"
        )
        r = requests.get(url, timeout=10).json()
        if "values" not in r:
            logging.warning(f"No values for {symbol}: {r}")
            return None
        df = pd.DataFrame(r["values"])
        df["close"] = df["close"].astype(float)
        df = df[::-1].reset_index(drop=True)
        return df
    except Exception as e:
        logging.error(f"get_data error: {e}")
        return None


def get_signal():
    symbol = random.choice(symbols)
    df = get_data(symbol)

    if df is None or len(df) < 50:
        return symbol, "ВВЕРХ", random.randint(82, 90)

    df["ema20"] = ta.trend.ema_indicator(df["close"], window=20)
    df["ema50"] = ta.trend.ema_indicator(df["close"], window=50)
    last = df.iloc[-1]

    if pd.isna(last["ema20"]) or pd.isna(last["ema50"]):
        return symbol, "ВВЕРХ", random.randint(82, 90)

    direction = "ВВЕРХ" if last["ema20"] > last["ema50"] else "ВНИЗ"
    probability = random.randint(82, 90)
    return symbol, direction, probability

# ======================
# FLASK ROUTES
# ======================

@app.route("/signal")
def signal():
    timeframe = request.args.get("timeframe")
    symbol, direction, probability = get_signal()
    return jsonify({
        "symbol": symbol,
        "direction": direction,
        "probability": probability,
        "timeframe": timeframe
    })

@app.route("/")
def index():
    return send_from_directory(app.static_folder, "index.html")

# ======================
# TELEGRAM BOT
# ======================

bot = Bot(token=TOKEN)
dp = Dispatcher()


@dp.message(CommandStart())
async def start(msg: types.Message):
    user_id = msg.from_user.id
    cursor.execute("SELECT status FROM users WHERE telegram_id=?", (user_id,))
    user = cursor.fetchone()

    if not user:
        keyboard = ReplyKeyboardMarkup(
            keyboard=[[KeyboardButton(text="🚀 Регистрация")]],
            resize_keyboard=True
        )
        await msg.answer(
            "Добро пожаловать в Trade AI 🚀\n\n"
            "Чтобы получить доступ к сигналам,\n"
            "необходимо пройти регистрацию.",
            reply_markup=keyboard
        )
        return

    status = user[0]

    if status == "pending":
        await msg.answer("⏳ Ваша заявка проверяется администрацией.")
        return

    if status == "approved":
        webapp_keyboard = ReplyKeyboardMarkup(
            keyboard=[[
                KeyboardButton(
                    text="🚀 Запустить AI",
                    web_app=types.WebAppInfo(url=WEBAPP_URL)
                )
            ]],
            resize_keyboard=True
        )
        await msg.answer("Доступ открыт ✅\n\nЗапусти Trade AI:", reply_markup=webapp_keyboard)


@dp.message(lambda message: message.text == "🚀 Регистрация")
async def register(msg: types.Message):
    await msg.answer(
        "Для завершения регистрации:\n\n"
        "1️⃣ Зарегистрируйтесь по нашей ссылке\n"
        "https://your-link.com\n\n"
        "2️⃣ Пополните баланс от 1000€\n\n"
        "3️⃣ Отправьте ваш PocketOption ID"
    )


@dp.message(lambda message: message.text and message.text.startswith("approve"))
async def approve_user(msg: types.Message):
    if msg.from_user.id != ADMIN_ID:
        return
    try:
        user_id = int(msg.text.split()[1])
        cursor.execute("UPDATE users SET status=? WHERE telegram_id=?", ("approved", user_id))
        conn.commit()
        await bot.send_message(user_id, "🎉 Ваша регистрация подтверждена!\nНажмите /start")
        await msg.answer("Пользователь одобрен ✅")
    except Exception as e:
        await msg.answer(f"Ошибка: {e}")


@dp.message()
async def save_id(msg: types.Message):
    if not msg.text:
        return
    user_id = msg.from_user.id
    text = msg.text
    cursor.execute("SELECT status FROM users WHERE telegram_id=?", (user_id,))
    user = cursor.fetchone()

    if text.isdigit() and not user:
        cursor.execute(
            "INSERT INTO users VALUES (?, ?, ?, ?)",
            (user_id, msg.from_user.username, text, "pending")
        )
        conn.commit()
        await msg.answer("Ваш ID отправлен на проверку администрации ✅")
        await bot.send_message(
            ADMIN_ID,
            f"Новая регистрация:\n\nUsername: @{msg.from_user.username}\n"
            f"PocketOption ID: {text}\nTelegram ID: {user_id}\n\napprove {user_id}"
        )


# ======================
# ЗАПУСК БОТА В ПОТОКЕ
# ======================

def run_bot():
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    loop.run_until_complete(dp.start_polling(bot))


bot_thread = threading.Thread(target=run_bot, daemon=True)
bot_thread.start()
logging.info("🤖 Бот запущен в фоне")

# ======================
# ТОЧКА ВХОДА
# ======================

if __name__ == "__main__":
    port = int(os.getenv("PORT", 8080))
    app.run(host="0.0.0.0", port=port, debug=False)
