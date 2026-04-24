from flask import Flask, jsonify, request, send_from_directory, session
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

app = Flask(
    __name__,
    static_folder=os.path.join(BASE_DIR, "web"),
    static_url_path=""
)
app.secret_key = os.getenv("SECRET_KEY", "lart-secret-2024")
CORS(app, resources={r"/*": {"origins": "*"}})

# ======================
# НАСТРОЙКИ
# ======================

TOKEN      = os.getenv("BOT_TOKEN")
API_KEY    = os.getenv("API_KEY")
ADMIN_ID   = int(os.getenv("ADMIN_ID", "574717871"))
WEBAPP_URL = os.getenv("WEBAPP_URL", "https://lart-trade-ai-production.up.railway.app")
ADMIN_PASS = os.getenv("ADMIN_PASS", "lart2024admin")  # пароль для /admin

# ======================
# DATABASE
# ======================

conn = sqlite3.connect("users.db", check_same_thread=False)
cursor = conn.cursor()

cursor.execute
cursor.execute("DROP TABLE IF EXISTS users")
cursor.execute("""
CREATE TABLE IF NOT EXISTS users(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT,
    pocket_id TEXT UNIQUE,
    status TEXT DEFAULT 'pending',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
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
    "CADJPY", "CADCHF", "NZDJPY", "NZDCAD"
]

# ======================
# SIGNAL LOGIC
# ======================

def get_data(symbol):
    try:
        url = (
            f"https://api.twelvedata.com/time_series"
            f"?symbol={symbol}&interval=1min&outputsize=100&apikey={API_KEY}"
        )
        r = requests.get(url, timeout=10).json()
        if "values" not in r:
            return None
        df = pd.DataFrame(r["values"])
        for col in ["close", "high", "low", "open"]:
            df[col] = df[col].astype(float)
        return df[::-1].reset_index(drop=True)
    except Exception as e:
        logging.error(f"get_data error: {e}")
        return None


def analyze(df):
    close = df["close"]
    signals = []

    ema20 = ta.trend.ema_indicator(close, window=20)
    ema50 = ta.trend.ema_indicator(close, window=50)
    if not pd.isna(ema20.iloc[-1]):
        signals.append(1 if ema20.iloc[-1] > ema50.iloc[-1] else -1)

    ema9  = ta.trend.ema_indicator(close, window=9)
    ema21 = ta.trend.ema_indicator(close, window=21)
    if not pd.isna(ema9.iloc[-1]):
        signals.append(1 if ema9.iloc[-1] > ema21.iloc[-1] else -1)

    rsi = ta.momentum.rsi(close, window=14)
    rsi_val = rsi.iloc[-1]
    if not pd.isna(rsi_val):
        if rsi_val < 40:   signals.append(1)
        elif rsi_val > 60: signals.append(-1)
        else:              signals.append(1 if rsi.iloc[-1] > rsi.iloc[-2] else -1)

    macd_diff = ta.trend.macd_diff(close)
    macd_line = ta.trend.macd(close)
    macd_sig  = ta.trend.macd_signal(close)
    if not pd.isna(macd_diff.iloc[-1]):
        signals.append(1 if macd_diff.iloc[-1] > 0 else -1)
        if macd_line.iloc[-2] < macd_sig.iloc[-2] and macd_line.iloc[-1] > macd_sig.iloc[-1]:
            signals.append(1)
        elif macd_line.iloc[-2] > macd_sig.iloc[-2] and macd_line.iloc[-1] < macd_sig.iloc[-1]:
            signals.append(-1)

    bb = ta.volatility.BollingerBands(close, window=20, window_dev=2)
    bb_low  = bb.bollinger_lband().iloc[-1]
    bb_high = bb.bollinger_hband().iloc[-1]
    bb_mid  = bb.bollinger_mavg().iloc[-1]
    price   = close.iloc[-1]
    if not pd.isna(bb_low):
        if price < bb_low:    signals.append(1)
        elif price > bb_high: signals.append(-1)
        else:                 signals.append(1 if price > bb_mid else -1)

    stoch = ta.momentum.StochasticOscillator(df["high"], df["low"], close, window=14, smooth_window=3)
    sk = stoch.stoch().iloc[-1]
    sd = stoch.stoch_signal().iloc[-1]
    if not pd.isna(sk):
        if sk < 20:   signals.append(1)
        elif sk > 80: signals.append(-1)
        else:         signals.append(1 if sk > sd else -1)

    if len(close) >= 10:
        signals.append(1 if close.iloc[-1] > close.iloc[-10] else -1)

    if not signals:
        return "ВВЕРХ", 60, 0

    up   = signals.count(1)
    down = signals.count(-1)
    total = len(signals)

    if up >= down:
        direction, score = "ВВЕРХ", up
    else:
        direction, score = "ВНИЗ", down

    prob = int(55 + (score / total - 0.5) * 2 * 37)
    prob = max(55, min(92, prob))
    return direction, prob, score


def get_signal():
    best = {"symbol": None, "direction": "ВВЕРХ", "probability": 60, "score": 0}
    candidates = random.sample(symbols, min(15, len(symbols)))
    for symbol in candidates:
        df = get_data(symbol)
        if df is None or len(df) < 60:
            continue
        try:
            direction, probability, score = analyze(df)
            if score > best["score"]:
                best = {"symbol": symbol, "direction": direction, "probability": probability, "score": score}
        except Exception as e:
            logging.error(f"analyze error {symbol}: {e}")
    if best["symbol"] is None:
        best["symbol"] = random.choice(symbols)
    return best["symbol"], best["direction"], best["probability"]


# ======================
# AUTH ROUTES
# ======================

@app.route("/api/register", methods=["POST"])
def register():
    data      = request.json
    name      = data.get("name", "").strip()
    pocket_id = data.get("pocket_id", "").strip()
    if not name or not pocket_id:
        return jsonify({"ok": False, "error": "Заполните все поля"}), 400
    try:
        cursor.execute("INSERT INTO users (name, pocket_id, status) VALUES (?, ?, 'pending')", (name, pocket_id))
        conn.commit()
        return jsonify({"ok": True})
    except sqlite3.IntegrityError:
        # Пользователь уже существует — проверим статус
        cursor.execute("SELECT status FROM users WHERE pocket_id=?", (pocket_id,))
        user = cursor.fetchone()
        if user:
            return jsonify({"ok": True, "status": user[0]})
        return jsonify({"ok": False, "error": "ID уже зарегистрирован"}), 400


@app.route("/api/check", methods=["POST"])
def check_status():
    pocket_id = request.json.get("pocket_id", "").strip()
    cursor.execute("SELECT status FROM users WHERE pocket_id=?", (pocket_id,))
    user = cursor.fetchone()
    if not user:
        return jsonify({"ok": False, "error": "Пользователь не найден"}), 404
    return jsonify({"ok": True, "status": user[0]})


# ======================
# ADMIN ROUTES
# ======================

@app.route("/api/admin/login", methods=["POST"])
def admin_login():
    password = request.json.get("password", "")
    if password == ADMIN_PASS:
        session["admin"] = True
        return jsonify({"ok": True})
    return jsonify({"ok": False, "error": "Неверный пароль"}), 403


@app.route("/api/admin/users")
def admin_users():
    if not session.get("admin"):
        return jsonify({"ok": False}), 403
    cursor.execute("SELECT id, name, pocket_id, status, created_at FROM users ORDER BY created_at DESC")
    rows = cursor.fetchall()
    users = [{"id": r[0], "name": r[1], "pocket_id": r[2], "status": r[3], "created_at": r[4]} for r in rows]
    return jsonify({"ok": True, "users": users})


@app.route("/api/admin/approve", methods=["POST"])
def admin_approve():
    if not session.get("admin"):
        return jsonify({"ok": False}), 403
    pocket_id = request.json.get("pocket_id")
    cursor.execute("UPDATE users SET status='approved' WHERE pocket_id=?", (pocket_id,))
    conn.commit()
    return jsonify({"ok": True})


@app.route("/api/admin/reject", methods=["POST"])
def admin_reject():
    if not session.get("admin"):
        return jsonify({"ok": False}), 403
    pocket_id = request.json.get("pocket_id")
    cursor.execute("UPDATE users SET status='rejected' WHERE pocket_id=?", (pocket_id,))
    conn.commit()
    return jsonify({"ok": True})


# ======================
# SIGNAL ROUTE
# ======================

@app.route("/signal")
def signal():
    pocket_id = request.args.get("pocket_id", "")
    # Проверяем доступ
    if pocket_id:
        cursor.execute("SELECT status FROM users WHERE pocket_id=?", (pocket_id,))
        user = cursor.fetchone()
        if not user or user[0] != "approved":
            return jsonify({"error": "access_denied"}), 403

    timeframe = request.args.get("timeframe", 1)
    symbol, direction, probability = get_signal()
    return jsonify({
        "symbol": symbol,
        "direction": direction,
        "probability": probability,
        "timeframe": timeframe
    })


# ======================
# STATIC ROUTES
# ======================

@app.route("/")
def index():
    return send_from_directory(app.static_folder, "index.html")

@app.route("/<path:filename>")
def static_files(filename):
    return send_from_directory(app.static_folder, filename)


# ======================
# TELEGRAM BOT
# ======================

if TOKEN:
    bot = Bot(token=TOKEN)
    dp  = Dispatcher()

    @dp.message(CommandStart())
    async def start(msg: types.Message):
        await msg.answer(
            "Добро пожаловать в LART Trade AI 🚀\n\n"
            f"Для доступа к сигналам зарегистрируйтесь на сайте:\n{WEBAPP_URL}"
        )

    def run_bot():
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        loop.run_until_complete(dp.start_polling(bot))

    bot_thread = threading.Thread(target=run_bot, daemon=True)
    bot_thread.start()
    logging.info("🤖 Бот запущен")

# ======================
# ТОЧКА ВХОДА
# ======================

if __name__ == "__main__":
    port = int(os.getenv("PORT", 8080))
    app.run(host="0.0.0.0", port=port, debug=False)
