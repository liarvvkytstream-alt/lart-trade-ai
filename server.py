
from flask import Flask, jsonify, request, send_from_directory
import random
import requests
# import pandas as pd
# import ta
import os

# правильный путь к папке web
BASE_DIR = os.path.dirname(os.path.abspath(__file__))

app = Flask(
    __name__,
    static_folder=os.path.join(BASE_DIR, "web"),
    static_url_path=""
)

# API ключ из Railway Variables
API_KEY = os.getenv("86d5500f514a46bbb125e2ea2ffee6e8")

symbols = [ "GBPUSD","USDJPY","USDCHF","AUDUSD","USDCAD","NZDUSD",
"EURGBP","EURJPY","EURCHF","EURAUD","EURCAD",
"GBPJPY","GBPCHF","GBPAUD","GBPCAD",
"AUDJPY","AUDCHF","AUDCAD",
"CADJPY","CADCHF",
"NZDJPY","NZDCAD"
]


def get_data(symbol):
    url = f"https://api.twelvedata.com/time_series?symbol={symbol}&interval=1min&outputsize=60&apikey={API_KEY}"

    r = requests.get(url).json()

    if "values" not in r:
        return None

    df = pd.DataFrame(r["values"])
    df["close"] = df["close"].astype(float)
    df = df[::-1]

    return df



def get_signal():
    symbol = random.choice(symbols)

    direction = random.choice(["ВВЕРХ", "ВНИЗ"])
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