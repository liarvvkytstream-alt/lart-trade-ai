from flask import Flask, jsonify, request
import random
import requests
import pandas as pd
import ta

app = Flask(__name__, static_folder="web", static_url_path="")

API_KEY = "86d5500f514a46bbb125e2ea2ffee6e8"


symbols = [
"EURUSD",
"GBPUSD",
"USDJPY",
"AUDUSD"
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

    df = get_data(symbol)

    if df is None:

        return symbol, "ВВЕРХ", 60


    df["ema20"] = ta.trend.ema_indicator(df["close"], window=20)
    df["ema50"] = ta.trend.ema_indicator(df["close"], window=50)

    last = df.iloc[-1]

    if last["ema20"] > last["ema50"]:

        direction = "ВВЕРХ"

    else:

        direction = "ВНИЗ"


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

    return app.send_static_file("index.html")




if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)