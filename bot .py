import asyncio
import requests
import pandas as pd
import ta
import random
import os
import sqlite3
import logging

from aiogram import Bot, Dispatcher, types
from aiogram.types import ReplyKeyboardMarkup, KeyboardButton
from aiogram.filters import CommandStart

logging.basicConfig(level=logging.INFO)

# ======================
# НАСТРОЙКИ — берём из переменных окружения Railway
# ======================

TOKEN = os.getenv("BOT_TOKEN")
API_KEY = os.getenv("API_KEY")
ADMIN_ID = int(os.getenv("ADMIN_ID", "574717871"))

# URL твоего приложения на Railway (замени на свой)
WEBAPP_URL = os.getenv("WEBAPP_URL", "https://lart-trade-ai-production.up.railway.app"  )

bot = Bot(token=TOKEN)
dp = Dispatcher()


# ======================
# DATABASE
# ======================

# ✅ FIX: check_same_thread=False для async-окружения
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
        # ✅ FIX: outputsize=60 чтобы хватало для EMA50 + проверки
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
        logging.error(f"get_data error for {symbol}: {e}")
        return None


def get_signal(symbol):
    df = get_data(symbol)

    # ✅ FIX: порог 50 совпадает с окном EMA
    if df is None or len(df) < 50:
        return random.choice(["🟢 ВВЕРХ", "🔴 ВНИЗ"]), 55

    df["ema20"] = ta.trend.ema_indicator(df["close"], window=20)
    df["ema50"] = ta.trend.ema_indicator(df["close"], window=50)

    last = df.iloc[-1]

    if pd.isna(last["ema20"]) or pd.isna(last["ema50"]):
        return random.choice(["🟢 ВВЕРХ", "🔴 ВНИЗ"]), 55

    direction = "🟢 ВВЕРХ" if last["ema20"] > last["ema50"] else "🔴 ВНИЗ"
    probability = random.randint(70, 90)

    return direction, probability


# ======================
# START
# ======================

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
                    web_app=types.WebAppInfo(url=WEBAPP_URL)  # ✅ FIX: URL из env
                )
            ]],
            resize_keyboard=True
        )
        await msg.answer("Доступ открыт ✅\n\nЗапусти Trade AI:", reply_markup=webapp_keyboard)


# ======================
# REGISTER BUTTON
# ======================

@dp.message(lambda message: message.text == "🚀 Регистрация")
async def register(msg: types.Message):
    await msg.answer(
        "Для завершения регистрации:\n\n"
        "1️⃣ Зарегистрируйтесь по нашей ссылке\n"
        "https://your-link.com\n\n"
        "2️⃣ Пополните баланс от 1000€\n\n"
        "3️⃣ Отправьте ваш PocketOption ID"
    )


# ======================
# APPROVE COMMAND
# ======================

@dp.message(lambda message: message.text and message.text.startswith("approve"))
async def approve_user(msg: types.Message):
    if msg.from_user.id != ADMIN_ID:
        return

    try:
        user_id = int(msg.text.split()[1])

        cursor.execute(
            "UPDATE users SET status=? WHERE telegram_id=?",
            ("approved", user_id)
        )
        conn.commit()

        await bot.send_message(
            user_id,
            "🎉 Ваша регистрация подтверждена!\nТеперь доступ открыт.\nНажмите /start"
        )
        await msg.answer("Пользователь одобрен ✅")

    except Exception as e:
        logging.error(f"approve error: {e}")
        await msg.answer(f"Ошибка команды: {e}")


# ======================
# SAVE POCKETOPTION ID
# ======================

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
            f"Новая регистрация:\n\n"
            f"Username: @{msg.from_user.username}\n"
            f"PocketOption ID: {text}\n"
            f"Telegram ID: {user_id}\n\n"
            f"approve {user_id}"
        )


# ======================
# MAIN
# ======================

async def main():
    logging.info("🚀 Бот запущен")
    await dp.start_polling(bot)


asyncio.run(main())
