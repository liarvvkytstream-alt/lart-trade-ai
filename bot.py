import asyncio
import requests
import pandas as pd
import ta
import random
import os
import sqlite3

from aiogram import Bot, Dispatcher, types
from aiogram.types import ReplyKeyboardMarkup,KeyboardButton, WebAppInfo
from aiogram.filters import CommandStart




# ======================pip3 install requests
# НАСТРОЙКИ
# ======================

TOKEN = "8539580314:AAFGLA8yLEQuO62P7n4qfOpB54GFowEb6DU"
API_KEY = "86d5500f514a46bbb125e2ea2ffee6e8"

ADMIN_ID = 574717871


bot = Bot(token=TOKEN)
dp = Dispatcher()


# ======================
# DATABASE USERS
# ======================

conn = sqlite3.connect("users.db")
cursor = conn.cursor()

cursor.execute("""
CREATE TABLE IF NOT EXISTS users(
telegram_id INTEGER,
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
"GBPUSD","USDJPY","USDCHF","AUDUSD","USDCAD","NZDUSD",
"EURGBP","EURJPY","EURCHF","EURAUD","EURCAD",
"GBPJPY","GBPCHF","GBPAUD","GBPCAD",
"AUDJPY","AUDCHF","AUDCAD",
"CADJPY","CADCHF",
"NZDJPY","NZDCAD"
]


# ======================
# SIGNAL LOGIC
# ======================

def get_data(symbol):

    try:

        url = f"https://api.twelvedata.com/time_series?symbol={symbol}&interval=1min&outputsize=50&apikey={API_KEY}"

        r = requests.get(url).json()

        if "values" not in r:
            return None

        df = pd.DataFrame(r["values"])

        df["close"] = df["close"].astype(float)

        df = df[::-1]

        return df

    except:
        return None


def get_signal(symbol):

    df = get_data(symbol)

    if df is None or len(df) < 60:

        return random.choice(["🟢 ВВЕРХ","🔴 ВНИЗ"]), 55


    df["ema20"] = ta.trend.ema_indicator(df["close"], window=20)
    df["ema50"] = ta.trend.ema_indicator(df["close"], window=50)

    last = df.iloc[-1]


    if pd.isna(last["ema20"]):

        return random.choice(["🟢 ВВЕРХ","🔴 ВНИЗ"]), 55


    if last["ema20"] > last["ema50"]:

        direction = "🟢 ВВЕРХ"

    else:

        direction = "🔴 ВНИЗ"


    probability = random.randint(70, 90)

    return direction, probability


# ======================
# START
# ======================

@dp.message(CommandStart())
async def start(msg: types.Message):

    user_id = msg.from_user.id

    cursor.execute(
        "SELECT status FROM users WHERE telegram_id=?",
        (user_id,)
    )

    user = cursor.fetchone()


    if not user:

        keyboard = ReplyKeyboardMarkup(
            keyboard=[
                [KeyboardButton(text="🚀 Регистрация")]
            ],
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

        await msg.answer(
            "⏳ Ваша заявка проверяется администрацией."
        )

        return


    if status == "approved":

        webapp_keyboard = ReplyKeyboardMarkup(
            keyboard=[
                [
                    KeyboardButton(
                        text="🚀 Запустить AI",
                        web_app=types.WebAppInfo(
    url="https://untimed-reapply-snitch.ngrok-free.dev?ngrok-skip-browser-warning=1"
)
                    )
                ]
            ],
            resize_keyboard=True
        )

        await msg.answer(
            "Доступ открыт ✅\n\nЗапусти Trade AI:",
            reply_markup=webapp_keyboard
        )


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

@dp.message(lambda message: message.text.startswith("approve"))
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

        await msg.answer(f"Ошибка команды: {e}")


# ======================
# SAVE POCKETOPTION ID
# ======================

@dp.message()
async def save_id(msg: types.Message):

    user_id = msg.from_user.id
    text = msg.text


    cursor.execute(
        "SELECT status FROM users WHERE telegram_id=?",
        (user_id,)
    )

    user = cursor.fetchone()


    if text.isdigit() and not user:

        cursor.execute(
            "INSERT INTO users VALUES (?, ?, ?, ?)",
            (
                user_id,
                msg.from_user.username,
                text,
                "pending"
            )
        )

        conn.commit()


        await msg.answer(
            "Ваш ID отправлен на проверку администрации ✅"
        )


        await bot.send_message(
            ADMIN_ID,
            f"""
Новая регистрация:

Username: @{msg.from_user.username}
PocketOption ID: {text}
Telegram ID: {user_id}

approve {user_id}
"""
        )


# ======================
# MAIN
# ======================

async def main():

    print("🚀 Бот запущен")

    await dp.start_polling(bot)


asyncio.run(main())