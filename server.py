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
import psycopg2
from psycopg2.extras import RealDictCursor

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

TOKEN        = os.getenv("BOT_TOKEN")
API_KEY      = os.getenv("API_KEY")
ADMIN_ID     = int(os.getenv("ADMIN_ID", "574717871"))
WEBAPP_URL   = os.getenv("WEBAPP_URL", "https://lart-trade-ai-production.up.railway.app")
ADMIN_PASS   = os.getenv("ADMIN_PASS", "lart2024admin")
DATABASE_URL = os.getenv("DATABASE_URL")
ANTHROPIC_KEY = os.getenv("ANTHROPIC_API_KEY")
# ======================
# DATABASE
# ======================

def get_db():
    return psycopg2.connect(DATABASE_URL, cursor_factory=RealDictCursor)

def init_db():
    conn = get_db(); cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id SERIAL PRIMARY KEY,
            name TEXT,
            pocket_id TEXT UNIQUE,
            status TEXT DEFAULT 'pending',
            signals_used INTEGER DEFAULT 0,
            subscribed BOOLEAN DEFAULT FALSE,
            sub_expires_at TIMESTAMP DEFAULT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    # Добавляем колонки если их нет (для существующих таблиц)
    for col in [
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS signals_used INTEGER DEFAULT 0",
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS subscribed BOOLEAN DEFAULT FALSE",
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS sub_expires_at TIMESTAMP DEFAULT NULL",
    ]:
        try: cur.execute(col)
        except: pass
    cur.execute("""
        CREATE TABLE IF NOT EXISTS user_signals (
            id SERIAL PRIMARY KEY,
            pocket_id TEXT NOT NULL,
            pair TEXT NOT NULL,
            direction TEXT NOT NULL,
            timeframe TEXT NOT NULL,
            probability INTEGER,
            result TEXT DEFAULT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS payments (
            id SERIAL PRIMARY KEY,
            pocket_id TEXT NOT NULL,
            amount TEXT NOT NULL,
            tx_hash TEXT DEFAULT NULL,
            status TEXT DEFAULT 'pending',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.commit(); cur.close(); conn.close()
    logging.info("✅ База данных инициализирована")

init_db()

# ======================
# СПИСОК ПАР
# ======================

symbols = [
    "EUR/USD", "GBP/USD", "USD/JPY", "USD/CHF", "AUD/USD", "USD/CAD", "NZD/USD",
    "EUR/GBP", "EUR/JPY", "EUR/CHF", "EUR/AUD", "EUR/CAD",
    "GBP/JPY", "GBP/CHF", "GBP/AUD", "GBP/CAD",
    "AUD/JPY", "AUD/CHF", "AUD/CAD","CAD/JPY", "CAD/CHF",
    "NZD/JPY", "NZD/CAD","CHF/JPY", "EUR/NZD", "GBP/NZD", "AUD/NZD", "NZD/CHF"
]
# ======================
# ПОЛУЧЕНИЕ ДАННЫХ
# ======================

def get_data(symbol, interval="1min"):
    try:
        url = (
            f"https://api.twelvedata.com/time_series"
            f"?symbol={symbol}&interval={interval}&outputsize=100&apikey={API_KEY}"
        )
        r = requests.get(url, timeout=10).json()

        # Логируем ошибки API
        if "values" not in r:
            logging.warning(f"❌ {symbol}: нет данных — {r.get('message', r.get('code', 'unknown'))}")
            return None

        df = pd.DataFrame(r["values"])
        for col in ["close", "high", "low", "open"]:
            df[col] = df[col].astype(float)
        logging.info(f"✅ {symbol}: получено {len(df)} свечей")
        return df[::-1].reset_index(drop=True)
    except Exception as e:
        logging.error(f"get_data error {symbol}: {e}")
        return None


# ======================
# ПАТТЕРНЫ ЯПОНСКИХ СВЕЧЕЙ
# ======================

def candle_patterns(df):
    """
    Анализирует паттерны японских свечей.
    Возвращает список сигналов: +1 = вверх, -1 = вниз, 0 = нейтрально
    """
    signals = []
    o = df["open"]
    h = df["high"]
    l = df["low"]
    c = df["close"]

    if len(df) < 3:
        return signals

    # Последние 3 свечи
    o1, h1, l1, c1 = o.iloc[-3], h.iloc[-3], l.iloc[-3], c.iloc[-3]
    o2, h2, l2, c2 = o.iloc[-2], h.iloc[-2], l.iloc[-2], c.iloc[-2]
    o3, h3, l3, c3 = o.iloc[-1], h.iloc[-1], l.iloc[-1], c.iloc[-1]

    body3 = abs(c3 - o3)
    range3 = h3 - l3
    body2 = abs(c2 - o2)
    range2 = h2 - l2

    # Защита от деления на ноль
    if range3 == 0 or range2 == 0:
        return signals

    # --- МОЛОТ (Hammer) — бычий разворот ---
    # Тело маленькое вверху, длинная нижняя тень
    upper_shadow3 = h3 - max(o3, c3)
    lower_shadow3 = min(o3, c3) - l3
    if (body3 / range3 < 0.3 and
        lower_shadow3 > body3 * 2 and
        upper_shadow3 < body3 * 0.5):
        signals.append(1)
        logging.info("📍 Паттерн: Молот (бычий)")

    # --- ПОВЕШЕННЫЙ (Hanging Man) — медвежий разворот ---
    # Такой же как молот, но на вершине тренда
    if (body3 / range3 < 0.3 and
        lower_shadow3 > body3 * 2 and
        upper_shadow3 < body3 * 0.5 and
        c3 < o3):  # медвежья свеча
        signals.append(-1)

    # --- ДОДЖИ (Doji) — нейтрально, рынок не знает куда ---
    if body3 / range3 < 0.1:
        # Доджи не добавляем в сигналы, просто пропускаем
        pass

    # --- БЫЧЬЕ ПОГЛОЩЕНИЕ (Bullish Engulfing) ---
    # Медвежья свеча поглощается бычьей
    if (c2 < o2 and          # предыдущая медвежья
        c3 > o3 and          # текущая бычья
        o3 <= c2 and         # открылась ниже закрытия предыдущей
        c3 >= o2):           # закрылась выше открытия предыдущей
        signals.append(1)
        signals.append(1)    # двойной вес — сильный паттерн
        logging.info("📍 Паттерн: Бычье поглощение")

    # --- МЕДВЕЖЬЕ ПОГЛОЩЕНИЕ (Bearish Engulfing) ---
    if (c2 > o2 and          # предыдущая бычья
        c3 < o3 and          # текущая медвежья
        o3 >= c2 and         # открылась выше закрытия предыдущей
        c3 <= o2):           # закрылась ниже открытия предыдущей
        signals.append(-1)
        signals.append(-1)   # двойной вес
        logging.info("📍 Паттерн: Медвежье поглощение")

    # --- ТРИ БЕЛЫХ СОЛДАТА (Three White Soldiers) — сильный бычий тренд ---
    if (c1 > o1 and c2 > o2 and c3 > o3 and   # все три бычьи
        c3 > c2 > c1 and                        # каждая выше предыдущей
        o3 > o2 > o1):                          # открытия растут
        signals.append(1)
        signals.append(1)
        logging.info("📍 Паттерн: Три белых солдата")

    # --- ТРИ ЧЁРНЫХ ВОРОНЫ (Three Black Crows) — сильный медвежий тренд ---
    if (c1 < o1 and c2 < o2 and c3 < o3 and   # все три медвежьи
        c3 < c2 < c1 and                        # каждая ниже предыдущей
        o3 < o2 < o1):                          # открытия снижаются
        signals.append(-1)
        signals.append(-1)
        logging.info("📍 Паттерн: Три чёрных вороны")

    # --- УТРЕННЯЯ ЗВЕЗДА (Morning Star) — бычий разворот ---
    if (c1 < o1 and                    # первая медвежья
        body2 / range2 < 0.3 and       # вторая маленькая (звезда)
        c3 > o3 and                    # третья бычья
        c3 > (o1 + c1) / 2):          # закрылась выше середины первой
        signals.append(1)
        signals.append(1)
        logging.info("📍 Паттерн: Утренняя звезда")

    # --- ВЕЧЕРНЯЯ ЗВЕЗДА (Evening Star) — медвежий разворот ---
    if (c1 > o1 and                    # первая бычья
        body2 / range2 < 0.3 and       # вторая маленькая (звезда)
        c3 < o3 and                    # третья медвежья
        c3 < (o1 + c1) / 2):          # закрылась ниже середины первой
        signals.append(-1)
        signals.append(-1)
        logging.info("📍 Паттерн: Вечерняя звезда")

    # --- ПИНБАР (Pin Bar) — разворот ---
    # Длинная тень в одну сторону, маленькое тело
    upper_shadow = h3 - max(o3, c3)
    lower_shadow = min(o3, c3) - l3
    if upper_shadow > range3 * 0.6 and body3 < range3 * 0.2:
        signals.append(-1)  # длинная верхняя тень = давление продавцов
        logging.info("📍 Паттерн: Пинбар (медвежий)")
    if lower_shadow > range3 * 0.6 and body3 < range3 * 0.2:
        signals.append(1)   # длинная нижняя тень = давление покупателей
        logging.info("📍 Паттерн: Пинбар (бычий)")

    return signals


# ======================
# АНАЛИЗ СИГНАЛА
# ======================

def analyze(df):
    close = df["close"]
    signals = []

    # --- EMA 20/50 ---
    ema20 = ta.trend.ema_indicator(close, window=20)
    ema50 = ta.trend.ema_indicator(close, window=50)
    if not pd.isna(ema20.iloc[-1]):
        signals.append(1 if ema20.iloc[-1] > ema50.iloc[-1] else -1)

    # --- EMA 9/21 ---
    ema9  = ta.trend.ema_indicator(close, window=9)
    ema21 = ta.trend.ema_indicator(close, window=21)
    if not pd.isna(ema9.iloc[-1]):
        signals.append(1 if ema9.iloc[-1] > ema21.iloc[-1] else -1)

    # --- RSI ---
    rsi = ta.momentum.rsi(close, window=14)
    rsi_val = rsi.iloc[-1]
    if not pd.isna(rsi_val):
        if rsi_val < 40:   signals.append(1)
        elif rsi_val > 60: signals.append(-1)
        else:              signals.append(1 if rsi.iloc[-1] > rsi.iloc[-2] else -1)

    # --- MACD ---
    macd_diff = ta.trend.macd_diff(close)
    macd_line = ta.trend.macd(close)
    macd_sig  = ta.trend.macd_signal(close)
    if not pd.isna(macd_diff.iloc[-1]):
        signals.append(1 if macd_diff.iloc[-1] > 0 else -1)
        if macd_line.iloc[-2] < macd_sig.iloc[-2] and macd_line.iloc[-1] > macd_sig.iloc[-1]:
            signals.append(1)
        elif macd_line.iloc[-2] > macd_sig.iloc[-2] and macd_line.iloc[-1] < macd_sig.iloc[-1]:
            signals.append(-1)

    # --- Bollinger Bands ---
    bb = ta.volatility.BollingerBands(close, window=20, window_dev=2)
    bb_low  = bb.bollinger_lband().iloc[-1]
    bb_high = bb.bollinger_hband().iloc[-1]
    bb_mid  = bb.bollinger_mavg().iloc[-1]
    price   = close.iloc[-1]
    if not pd.isna(bb_low):
        if price < bb_low:    signals.append(1)
        elif price > bb_high: signals.append(-1)
        else:                 signals.append(1 if price > bb_mid else -1)

    # --- Stochastic ---
    stoch = ta.momentum.StochasticOscillator(df["high"], df["low"], close, window=14, smooth_window=3)
    sk = stoch.stoch().iloc[-1]
    sd = stoch.stoch_signal().iloc[-1]
    if not pd.isna(sk):
        if sk < 20:   signals.append(1)
        elif sk > 80: signals.append(-1)
        else:         signals.append(1 if sk > sd else -1)

    # --- Momentum ---
    if len(close) >= 10:
        signals.append(1 if close.iloc[-1] > close.iloc[-10] else -1)

    # --- Паттерны японских свечей ---
    candle_signals = candle_patterns(df)
    signals.extend(candle_signals)

    if not signals:
        return "ВВЕРХ", 60, 0

    up    = signals.count(1)
    down  = signals.count(-1)
    total = len(signals)

    if up >= down:
        direction, score = "ВВЕРХ", up
    else:
        direction, score = "ВНИЗ", down

    prob = int(55 + (score / total - 0.5) * 2 * 37)
    prob = max(55, min(92, prob))

    # --- ADX фильтр — только торгуем при сильном тренде ---
    try:
        adx = ta.trend.ADXIndicator(df["high"], df["low"], close, window=14)
        adx_val = adx.adx().iloc[-1]
        # Если ADX < 20 — рынок в флэте, снижаем вероятность
        if not pd.isna(adx_val) and adx_val < 20:
            prob = max(55, prob - 10)
            logging.info(f"⚠️ ADX={adx_val:.1f} — флэт, снижаем вероятность")
        elif not pd.isna(adx_val) and adx_val > 30:
            prob = min(92, prob + 5)
            logging.info(f"✅ ADX={adx_val:.1f} — сильный тренд, повышаем вероятность")
    except Exception as e:
        logging.warning(f"ADX error: {e}")

    return direction, prob, score


# ======================
# ПОЛУЧЕНИЕ СИГНАЛА
# ======================

def get_signal(timeframe=1):
    """
    Улучшенная логика выбора сигнала:
    1. Анализируем все 22 пары (не 15)
    2. Фильтруем: только пары где 75%+ согласие индикаторов
    3. Из отфильтрованных берём лучшую
    4. Если ни одна не прошла фильтр — берём лучшую из всех
    """
    # Маппинг таймфрейма → интервал TwelveData
    interval_map = {1: "1min", 3: "3min", 5: "5min", 30: "30min"}
    interval = interval_map.get(int(timeframe), "1min")
    logging.info(f"📐 Таймфрейм: {timeframe} мин → интервал свечей: {interval}")

    best    = {"symbol": None, "direction": "ВВЕРХ", "probability": 60, "score": 0}
    strong  = []  # пары с вероятностью 72%+

    # Grow план — 55 запросов/мин, анализируем все пары
    candidates = symbols.copy()
    random.shuffle(candidates)
    logging.info(f"🔍 Анализируем все {len(candidates)} пар")

    for symbol in candidates:
        df = get_data(symbol, interval)
        if df is None or len(df) < 60:
            continue
        try:
            direction, probability, score = analyze(df)

            # Обновляем лучший результат
            if score > best["score"]:
                best = {"symbol": symbol, "direction": direction, "probability": probability, "score": score}

            # Добавляем в список сильных сигналов
            if probability >= 72:
                strong.append({"symbol": symbol, "direction": direction, "probability": probability, "score": score})
                logging.info(f"⚡ Сильный кандидат: {symbol} {direction} {probability}%")

        except Exception as e:
            logging.error(f"analyze error {symbol}: {e}")

    # Если есть сильные сигналы — берём лучший из них
    if strong:
        winner = max(strong, key=lambda x: x["score"])
        logging.info(f"✅ Выбран сильный сигнал: {winner['symbol']} {winner['direction']} {winner['probability']}%")
        return winner["symbol"], winner["direction"], winner["probability"]

    # Иначе берём лучшее что нашли
    if best["symbol"] is None:
        # Если вообще нет данных — хотя бы рандомное направление
        fallback_symbol = random.choice(symbols)
        fallback_direction = random.choice(["ВВЕРХ", "ВНИЗ"])
        logging.error(f"⚠️ Нет данных ни по одной паре! API лимит? Возвращаем fallback: {fallback_symbol} {fallback_direction}")
        return fallback_symbol, fallback_direction, 60

    logging.info(f"📊 Лучший доступный: {best['symbol']} {best['direction']} {best['probability']}%")
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
        conn = get_db(); cur = conn.cursor()
        cur.execute("INSERT INTO users (name, pocket_id, status) VALUES (%s, %s, 'pending')", (name, pocket_id))
        conn.commit(); cur.close(); conn.close()
        return jsonify({"ok": True})
    except psycopg2.errors.UniqueViolation:
        conn.rollback(); cur.close(); conn.close()
        conn2 = get_db(); cur2 = conn2.cursor()
        cur2.execute("SELECT status FROM users WHERE pocket_id=%s", (pocket_id,))
        user = cur2.fetchone(); cur2.close(); conn2.close()
        if user:
            return jsonify({"ok": True, "status": user["status"]})
        return jsonify({"ok": False, "error": "ID уже зарегистрирован"}), 400
    except Exception as e:
        logging.error(f"register error: {e}")
        return jsonify({"ok": False, "error": "Ошибка сервера"}), 500


@app.route("/api/check", methods=["POST"])
def check_status():
    pocket_id = request.json.get("pocket_id", "").strip()
    conn = get_db(); cur = conn.cursor()
    cur.execute("SELECT status FROM users WHERE pocket_id=%s", (pocket_id,))
    user = cur.fetchone(); cur.close(); conn.close()
    if not user:
        return jsonify({"ok": False, "error": "Пользователь не найден"}), 404
    return jsonify({"ok": True, "status": user["status"]})


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
    conn = get_db(); cur = conn.cursor()
    cur.execute("SELECT id, name, pocket_id, status, created_at FROM users ORDER BY created_at DESC")
    rows = cur.fetchall(); cur.close(); conn.close()
    users = [{"id": r["id"], "name": r["name"], "pocket_id": r["pocket_id"], "status": r["status"], "created_at": str(r["created_at"])} for r in rows]
    return jsonify({"ok": True, "users": users})


@app.route("/api/admin/approve", methods=["POST"])
def admin_approve():
    if not session.get("admin"):
        return jsonify({"ok": False}), 403
    pocket_id = request.json.get("pocket_id")
    conn = get_db(); cur = conn.cursor()
    cur.execute("UPDATE users SET status='approved' WHERE pocket_id=%s", (pocket_id,))
    conn.commit(); cur.close(); conn.close()
    return jsonify({"ok": True})


@app.route("/api/admin/reject", methods=["POST"])
def admin_reject():
    if not session.get("admin"):
        return jsonify({"ok": False}), 403
    pocket_id = request.json.get("pocket_id")
    conn = get_db(); cur = conn.cursor()
    cur.execute("UPDATE users SET status='rejected' WHERE pocket_id=%s", (pocket_id,))
    conn.commit(); cur.close(); conn.close()
    return jsonify({"ok": True})


# ======================
# SIGNAL ROUTE
# ======================

FREE_SIGNALS_LIMIT = 20
USDT_WALLET = "TRRxtesKFFS4V6XVSSmUuwVDq8KxLJR9Ci"
SUBSCRIPTION_PRICE = 100

@app.route("/signal")
def signal():
    pocket_id = request.args.get("pocket_id", "")
    if pocket_id:
        conn = get_db(); cur = conn.cursor()
        cur.execute("SELECT status, signals_used, subscribed, sub_expires_at FROM users WHERE pocket_id=%s", (pocket_id,))
        user = cur.fetchone(); cur.close(); conn.close()
        if not user or user["status"] != "approved":
            return jsonify({"error": "access_denied"}), 403

        # Проверяем подписку
        from datetime import datetime
        is_subscribed = user["subscribed"]
        if user["sub_expires_at"] and user["sub_expires_at"] < datetime.now():
            is_subscribed = False

        # Проверяем лимит бесплатных сигналов
        if not is_subscribed and user["signals_used"] >= FREE_SIGNALS_LIMIT:
            return jsonify({
                "error": "limit_reached",
                "signals_used": user["signals_used"],
                "limit": FREE_SIGNALS_LIMIT,
                "wallet": USDT_WALLET,
                "price": SUBSCRIPTION_PRICE
            }), 402

    timeframe = request.args.get("timeframe", 1)
    symbol, direction, probability = get_signal(timeframe)

    # Сохраняем сигнал и увеличиваем счётчик
    if pocket_id:
        try:
            conn2 = get_db(); cur2 = conn2.cursor()
            cur2.execute(
                "INSERT INTO user_signals (pocket_id, pair, direction, timeframe, probability) VALUES (%s, %s, %s, %s, %s)",
                (pocket_id, symbol, direction, str(timeframe), int(probability))
            )
            cur2.execute(
                "UPDATE users SET signals_used = signals_used + 1 WHERE pocket_id=%s",
                (pocket_id,)
            )
            conn2.commit(); cur2.close(); conn2.close()
        except Exception as e:
            logging.error(f"save signal error: {e}")

    return jsonify({"symbol": symbol, "direction": direction, "probability": probability, "timeframe": timeframe})


# ======================
# SUBSCRIPTION ROUTES
# ======================

@app.route("/api/subscribe/info")
def subscribe_info():
    pocket_id = request.args.get("pocket_id", "")
    conn = get_db(); cur = conn.cursor()
    cur.execute("SELECT signals_used, subscribed, sub_expires_at FROM users WHERE pocket_id=%s", (pocket_id,))
    user = cur.fetchone(); cur.close(); conn.close()
    if not user:
        return jsonify({"ok": False}), 404
    return jsonify({
        "ok": True,
        "signals_used": user["signals_used"],
        "limit": FREE_SIGNALS_LIMIT,
        "subscribed": user["subscribed"],
        "wallet": USDT_WALLET,
        "price": SUBSCRIPTION_PRICE
    })


@app.route("/api/subscribe/submit", methods=["POST"])
def subscribe_submit():
    """Пользователь отправляет tx hash после оплаты"""
    data = request.json
    pocket_id = data.get("pocket_id", "")
    tx_hash   = data.get("tx_hash", "").strip()

    if not pocket_id or not tx_hash:
        return jsonify({"ok": False, "error": "Заполните все поля"}), 400

    try:
        conn = get_db(); cur = conn.cursor()
        cur.execute(
            "INSERT INTO payments (pocket_id, amount, tx_hash, status) VALUES (%s, %s, %s, 'pending')",
            (pocket_id, str(SUBSCRIPTION_PRICE), tx_hash)
        )
        conn.commit(); cur.close(); conn.close()
        return jsonify({"ok": True, "message": "Заявка принята! Подписка будет активирована после проверки."})
    except Exception as e:
        logging.error(f"subscribe submit error: {e}")
        return jsonify({"ok": False, "error": "Ошибка сервера"}), 500


@app.route("/api/admin/payments")
def admin_payments():
    if not session.get("admin"):
        return jsonify({"ok": False}), 403
    conn = get_db(); cur = conn.cursor()
    cur.execute("SELECT * FROM payments ORDER BY created_at DESC")
    rows = cur.fetchall(); cur.close(); conn.close()
    payments = [{"id": r["id"], "pocket_id": r["pocket_id"], "amount": r["amount"], "tx_hash": r["tx_hash"], "status": r["status"], "created_at": str(r["created_at"])} for r in rows]
    return jsonify({"ok": True, "payments": payments})


@app.route("/api/admin/activate", methods=["POST"])
def admin_activate():
    """Админ вручную активирует подписку после проверки оплаты"""
    if not session.get("admin"):
        return jsonify({"ok": False}), 403
    pocket_id  = request.json.get("pocket_id")
    payment_id = request.json.get("payment_id")
    from datetime import datetime, timedelta
    expires = datetime.now() + timedelta(days=30)
    conn = get_db(); cur = conn.cursor()
    cur.execute("UPDATE users SET subscribed=TRUE, sub_expires_at=%s WHERE pocket_id=%s", (expires, pocket_id))
    cur.execute("UPDATE payments SET status='confirmed' WHERE id=%s", (payment_id,))
    conn.commit(); cur.close(); conn.close()
    return jsonify({"ok": True})


# ======================
# PROFILE ROUTES
# ======================

@app.route("/api/profile/<pocket_id>")
def get_profile(pocket_id):
    try:
        conn = get_db(); cur = conn.cursor()

        # Получаем данные пользователя
        cur.execute("SELECT name, pocket_id, created_at FROM users WHERE pocket_id=%s", (pocket_id,))
        user = cur.fetchone()
        if not user:
            cur.close(); conn.close()
            return jsonify({"ok": False, "error": "Пользователь не найден"}), 404

        # Получаем историю сигналов
        cur.execute("""
            SELECT id, pair, direction, timeframe, probability, result, created_at
            FROM user_signals
            WHERE pocket_id=%s
            ORDER BY created_at DESC
            LIMIT 50
        """, (pocket_id,))
        signals = cur.fetchall()
        cur.close(); conn.close()

        # Считаем статистику
        rated = [s for s in signals if s["result"] is not None]
        wins  = len([s for s in rated if s["result"] == "win"])
        losses = len([s for s in rated if s["result"] == "loss"])
        winrate = round((wins / len(rated)) * 100) if rated else 0

        signals_list = [{
            "id": s["id"],
            "pair": s["pair"],
            "direction": s["direction"],
            "timeframe": s["timeframe"],
            "probability": s["probability"],
            "result": s["result"],
            "created_at": str(s["created_at"])
        } for s in signals]

        return jsonify({
            "ok": True,
            "user": {"name": user["name"], "pocket_id": user["pocket_id"], "created_at": str(user["created_at"])},
            "stats": {"total": len(signals), "wins": wins, "losses": losses, "rated": len(rated), "winrate": winrate},
            "signals": signals_list
        })

    except Exception as e:
        logging.error(f"profile error: {e}")
        return jsonify({"ok": False, "error": "Ошибка сервера"}), 500


@app.route("/api/signal/rate", methods=["PATCH"])
def rate_signal():
    data      = request.json
    signal_id = data.get("signal_id")
    pocket_id = data.get("pocket_id")
    result    = data.get("result")  # "win" или "loss"

    if result not in ("win", "loss"):
        return jsonify({"ok": False, "error": "Неверный результат"}), 400

    try:
        conn = get_db(); cur = conn.cursor()
        cur.execute(
            "UPDATE user_signals SET result=%s WHERE id=%s AND pocket_id=%s AND result IS NULL",
            (result, signal_id, pocket_id)
        )
        conn.commit()
        updated = cur.rowcount
        cur.close(); conn.close()

        if updated == 0:
            return jsonify({"ok": False, "error": "Сигнал не найден или уже оценён"}), 400

        return jsonify({"ok": True})

    except Exception as e:
        logging.error(f"rate signal error: {e}")
        return jsonify({"ok": False, "error": "Ошибка сервера"}), 500



# ======================
# OTC ANALYZE ROUTE
# ======================

@app.route("/api/analyze-otc", methods=["POST"])
def analyze_otc():
    pocket_id = request.form.get("pocket_id", "")
    if pocket_id:
        conn = get_db(); cur = conn.cursor()
        cur.execute("SELECT status FROM users WHERE pocket_id=%s", (pocket_id,))
        user = cur.fetchone(); cur.close(); conn.close()
        if not user or user["status"] != "approved":
            return jsonify({"ok": False, "error": "access_denied"}), 403

    pair = request.form.get("pair", "EUR/USD OTC")
    file = request.files.get("image")

    if not file:
        return jsonify({"ok": False, "error": "Файл не загружен"}), 400

    import base64, json, re
    image_data = base64.b64encode(file.read()).decode("utf-8")
    media_type = file.content_type or "image/jpeg"

    try:
        prompt = (
            f"Ты профессиональный трейдер. Анализируй скриншот графика OTC пары {pair} с PocketOption. "
            f"Смотри на свечи, тренд, уровни поддержки и сопротивления, паттерны. "
            f"Определи направление следующего движения и вероятность. "
            f'Ответь ТОЛЬКО в формате JSON без лишнего текста: {{"direction": "ВВЕРХ" или "ВНИЗ", "probability": число от 55 до 92, "explanation": "2-3 предложения на русском"}}'
        )

        response = requests.post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "x-api-key": ANTHROPIC_KEY,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json"
            },
            json={
                "model": "claude-sonnet-4-20250514",
                "max_tokens": 500,
                "messages": [{
                    "role": "user",
                    "content": [
                        {"type": "image", "source": {"type": "base64", "media_type": media_type, "data": image_data}},
                        {"type": "text", "text": prompt}
                    ]
                }]
            },
            timeout=30
        )

        data = response.json()
        if "error" in data:
            logging.error(f"Anthropic error: {data['error']}")
            return jsonify({"ok": False, "error": "Ошибка AI"}), 500

        text = data["content"][0]["text"].strip()
        clean = re.sub(r"```json|```", "", text).strip()
        parsed = json.loads(clean)

        return jsonify({
            "ok": True,
            "direction": parsed["direction"],
            "probability": int(parsed["probability"]),
            "explanation": parsed["explanation"]
        })

    except Exception as e:
        logging.error(f"OTC analyze error: {e}")
        return jsonify({"ok": False, "error": str(e)}), 500


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
        # Отключаем signal handlers — они не работают в не-главном потоке
        loop.run_until_complete(dp.start_polling(bot, handle_signals=False))

    bot_thread = threading.Thread(target=run_bot, daemon=True)
    bot_thread.start()
    logging.info("🤖 Бот запущен")

# ======================
# ТОЧКА ВХОДА
# ======================

if __name__ == "__main__":
    port = int(os.getenv("PORT", 8080))
    app.run(host="0.0.0.0", port=port, debug=False)
