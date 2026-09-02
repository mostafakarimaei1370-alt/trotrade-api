import os
import re
import requests
from flask import Flask, jsonify, request

app = Flask(__name__)

BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
WEBHOOK_SECRET = os.getenv("TELEGRAM_WEBHOOK_SECRET")
RENDER_URL = os.getenv("RENDER_EXTERNAL_URL")

# Dry-run settings
ACCOUNT_BALANCE = float(os.getenv("ACCOUNT_BALANCE", "150"))
RISK_PERCENT = float(os.getenv("RISK_PERCENT", "3"))
LEVERAGE = int(os.getenv("LEVERAGE", "20"))


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


def validate_signal(signal):
    entry = signal.get("entry")
    sl = signal.get("stop_loss")
    side = signal.get("position")

    if not entry or not sl or not side:
        return False, "Entry / SL / Position ناقص است."

    if side == "BUY" and sl >= entry:
        return False, "برای BUY، استاپ باید پایین‌تر از Entry باشد."

    if side == "SELL" and sl <= entry:
        return False, "برای SELL، استاپ باید بالاتر از Entry باشد."

    return True, "OK"


def calculate_dry_run(signal):
    entry = signal["entry"]
    sl = signal["stop_loss"]

    risk_amount = ACCOUNT_BALANCE * (RISK_PERCENT / 100)

    stop_distance = abs(entry - sl)
    stop_percent = (stop_distance / entry) * 100

    if stop_distance <= 0:
        return None

    notional_size = risk_amount / (stop_distance / entry)
    estimated_margin = notional_size / LEVERAGE

    return {
        "balance": ACCOUNT_BALANCE,
        "risk_percent": RISK_PERCENT,
        "risk_amount": round(risk_amount, 2),
        "leverage": LEVERAGE,
        "stop_percent": round(stop_percent, 4),
        "notional_size": round(notional_size, 2),
        "estimated_margin": round(estimated_margin, 2)
    }


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
        "mode": "DRY_RUN",
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

    if not signal.get("pair") or not signal.get("position"):
        return jsonify({
            "status": "not_signal",
            "signal": signal
        })

    valid, reason = validate_signal(signal)

    if not valid:
        reply = (
            "⚠️ SIGNAL REJECTED\n\n"
            f"ID: {signal.get('signal_id', '-')}\n"
            f"Pair: {signal.get('pair', '-')}\n"
            f"Position: {signal.get('position', '-')}\n"
            f"Entry: {signal.get('entry', '-')}\n"
            f"SL: {signal.get('stop_loss', '-')}\n\n"
            f"❌ {reason}\n\n"
            "هیچ معامله‌ای انجام نشد."
        )

        if chat_id:
            send_telegram_message(chat_id, reply)

        return jsonify({
            "status": "rejected",
            "reason": reason,
            "signal": signal
        })

    dry_run = calculate_dry_run(signal)

    reply = (
        "🧪 DRY RUN — NO ORDER PLACED\n\n"
        f"ID: {signal.get('signal_id', '-')}\n"
        f"Pair: {signal.get('pair')}\n"
        f"Position: {signal.get('position')}\n"
        f"Entry: {signal.get('entry')}\n"
        f"SL: {signal.get('stop_loss')}\n"
        f"TPs: {signal.get('take_profits', [])}\n\n"
        f"Balance: {dry_run['balance']} USDT\n"
        f"Risk: {dry_run['risk_percent']}%\n"
        f"Max loss: {dry_run['risk_amount']} USDT\n"
        f"Leverage: {dry_run['leverage']}x\n"
        f"SL distance: {dry_run['stop_percent']}%\n"
        f"Position value: {dry_run['notional_size']} USDT\n"
        f"Estimated margin: {dry_run['estimated_margin']} USDT\n\n"
        "✅ فقط محاسبه شد؛ هیچ معامله‌ای باز نشده."
    )

    if chat_id:
        send_telegram_message(chat_id, reply)

    return jsonify({
        "status": "dry_run",
        "signal": signal,
        "calculation": dry_run
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
