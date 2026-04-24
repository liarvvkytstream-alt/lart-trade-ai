from flask import Flask, jsonify, request, send_from_directory
from flask_cors import CORS
import random
import requests
import pandas as pd
import ta
import os

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

app = Flask(
    __name__,
    static_folder=os.path.join(BASE_DIR, "web"),
    static_url_path=""
)

# ✅ FIX 3: CORS — разрешаем запросы от Telegram и любых других доменов
CORS(app, resources={r"/*": {"origins": "*"}})

# API ключ из Railway Variables
API_KEY = os.getenv("API_KEY")

symbols = [
    "GBPUSD", "USDJPY", "USDCHF", "AUDUSD", "USDCAD", "NZDUSD",
    "EURGBP", "EURJPY", "EURCHF", "EURAUD", "EURCAD",
    "GBPJPY", "GBPCHF", "GBPAUD", "GBPCAD",
    "AUDJPY", "AUDCHF", "AUDCAD",
    "CADJPY", "CADCHF",
    "NZDJPY", "NZDCAD"
]


def get_data(symbol):
    url = (
        f"https://api.twelvedata.com/time_series"
        f"?symbol={symbol}&interval=1min&outputsize=60&apikey={API_KEY}"
    )
    r = requests.get(url).json()

    if "values" not in r:
        return None

    df = pd.DataFrame(r["values"])
    df["close"] = df["close"].astype(float)
    df = df[::-1].reset_index(drop=True)

    return df


def get_signal():
    symbol = random.choice(symbols)
    df = get_data(symbol)

    if df is None:
        return symbol, "ВВЕРХ", 60

    df["ema20"] = ta.trend.ema_indicator(df["close"], window=20)
    df["ema50"] = ta.trend.ema_indicator(df["close"], window=50)

    last = df.iloc[-1]

    direction = "ВВЕРХ" if last["ema20"] > last["ema50"] else "ВНИЗ"
    probability = random.randint(82, 90)

    return symbol, direction, probability


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


# ✅ FIX 1 & 2: Правильный host и port для Railway
if __name__ == "__main__":
    port = int(os.getenv("PORT", 8080))   # Railway сам задаёт PORT
    app.run(host="0.0.0.0", port=port, debug=False)