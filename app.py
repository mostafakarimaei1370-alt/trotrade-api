import os
import re
import requests
from flask import Flask, jsonify, request

app = Flask(__name__)

BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
WEBHOOK_SECRET = os.getenv("TELEGRAM_WEBHOOK_SECRET")
RENDER_URL = os.getenv("RENDER_EXTERNAL_URL")


def parse_signal(text):
    signal = {}

    pair = re.search(r"Pair:\s*([A-Z0-9]+)", text, re.I)
    position = re.search(r"Position:\s*(BUY|SELL)", text, re.I)
    entry = re.search(r"Entry\s+(?:Market|Limit):\s*([\d.]+)", text, re.I)
    stop_loss = re.search(r"SL:\s*([\d.]+)", text, re.I)
    signal_id = re.search(r"#S(\d+)", text, re.I)

    tps = re.findall(r"TP\d+:\s*([\d.]+)", text, re.I)

    if signal_id:
        signal["signal_id"] = "S" + signal_id.group(1)

    if pair:
        signal["pair"] = pair.group(1).upper()

    if position:
        signal["position"] = position.group(1).upper()

    if entry:
        signal["entry"] = float(entry.group(1))

    if stop_loss:
        signal["stop_loss"] = float(stop_loss.group(1))

    if tps:
        signal["take_profits"] = [float(tp) for tp in tps]

    return signal


def send_telegram_message(chat_id, text):
    if not BOT_TOKEN:
        return

    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"

    requests.post(
        url,
        json={
            "chat_id": chat_id,
            "text": text
        },
        timeout=10
    )


@app.route("/")
def home():
    return jsonify({
        "status": "ok",
        "message": "TroTrade Signal API is running"
    })


@app.route("/health")
def health():
    return jsonify({"status": "healthy"})


@app.route("/telegram", methods=["POST"])
def telegram_webhook():

    if WEBHOOK_SECRET:
        received_secret = request.headers.get(
            "X-Telegram-Bot-Api-Secret-Token"
        )

        if received_secret != WEBHOOK_SECRET:
            return jsonify({"error": "unauthorized"}), 403

    update = request.get_json(silent=True) or {}

    message = update.get("message") or update.get("channel_post")

    if not message:
        return jsonify({"status": "ignored"})

    text = message.get("text") or message.get("caption")

    if not text:
        return jsonify({"status": "no text"})

    signal = parse_signal(text)

    chat_id = message.get("chat", {}).get("id")

    if signal.get("pair") and signal.get("position"):

        reply = (
            f"✅ Signal detected\n\n"
            f"ID: {signal.get('signal_id', '-')}\n"
            f"Pair: {signal.get('pair')}\n"
            f"Position: {signal.get('position')}\n"
            f"Entry: {signal.get('entry', '-')}\n"
            f"SL: {signal.get('stop_loss', '-')}\n"
            f"TPs: {signal.get('take_profits', [])}"
        )

        if chat_id:
            send_telegram_message(chat_id, reply)

    return jsonify({
        "status": "received",
        "signal": signal
    })


def setup_webhook():

    if not BOT_TOKEN or not RENDER_URL or not WEBHOOK_SECRET:
        return

    webhook_url = f"{RENDER_URL}/telegram"

    telegram_url = (
        f"https://api.telegram.org/bot{BOT_TOKEN}/setWebhook"
    )

    try:
        requests.post(
            telegram_url,
            json={
                "url": webhook_url,
                "secret_token": WEBHOOK_SECRET
            },
            timeout=10
        )
    except Exception:
        pass


setup_webhook()


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=10000)
