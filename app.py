import os
import re
import requests
from flask import Flask, jsonify, request

app = Flask(__name__)

BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
WEBHOOK_SECRET = os.getenv("TELEGRAM_WEBHOOK_SECRET")
RENDER_URL = os.getenv("RENDER_EXTERNAL_URL")

DEFAULT_BALANCE = float(os.getenv("ACCOUNT_BALANCE", "175"))
DEFAULT_RISK = float(os.getenv("RISK_PERCENT", "3"))
DEFAULT_LEVERAGE = int(os.getenv("LEVERAGE", "20"))
FEE_RATE = float(os.getenv("FEE_RATE", "0.0004"))

pending_trades = {}


def normalize_numbers(text):
    trans = str.maketrans(
        "۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩",
        "01234567890123456789"
    )
    return text.translate(trans)


def parse_signal(text):
    text = normalize_numbers(text)
    signal = {}

    m = re.search(r"Pair:\s*([A-Z0-9]+)", text, re.I)
    if m:
        signal["pair"] = m.group(1).upper()

    m = re.search(r"Position:\s*(BUY|SELL|LONG|SHORT)", text, re.I)
    if m:
        side = m.group(1).upper()
        if side == "LONG":
            side = "BUY"
        elif side == "SHORT":
            side = "SELL"
        signal["position"] = side

    m = re.search(
        r"Entry\s+(?:Market|Limit)?\s*:?\s*([\d.]+)",
        text,
        re.I
    )
    if m:
        signal["entry"] = float(m.group(1))

    m = re.search(r"\bSL\s*:\s*([\d.]+)", text, re.I)
    if m:
        signal["stop_loss"] = float(m.group(1))

    m = re.search(r"#S(\d+)", text, re.I)
    if m:
        signal["signal_id"] = "S" + m.group(1)

    tps = re.findall(r"TP\d+\s*:\s*([\d.]+)", text, re.I)
    if tps:
        signal["take_profits"] = [float(x) for x in tps]

    return signal


def validate_signal(signal):
    entry = signal.get("entry")
    sl = signal.get("stop_loss")
    side = signal.get("position")

    if entry is None:
        return False, "قیمت ورود مشخص نیست."

    if sl is None:
        return False, "حد ضرر مشخص نیست."

    if side == "BUY" and sl >= entry:
        return False, "برای خرید، SL باید پایین‌تر از ورود باشد."

    if side == "SELL" and sl <= entry:
        return False, "برای فروش، SL باید بالاتر از ورود باشد."

    return True, "OK"


def calculate_trade(trade):
    entry = float(trade["entry"])
    sl = float(trade["stop_loss"])
    leverage = int(trade.get("leverage", DEFAULT_LEVERAGE))
    risk = float(trade.get("risk_percent", DEFAULT_RISK))
    balance = float(trade.get("balance", DEFAULT_BALANCE))

    stop_fraction = abs(entry - sl) / entry
    manual_margin = trade.get("margin_override")

    if manual_margin is not None:
        margin = float(manual_margin)
        position_value = margin * leverage
        loss_at_sl = position_value * stop_fraction
        actual_risk = loss_at_sl / balance * 100
    else:
        loss_at_sl = balance * risk / 100
        position_value = loss_at_sl / stop_fraction
        margin = position_value / leverage
        actual_risk = risk

    fee = position_value * FEE_RATE * 2

    return {
        "balance": round(balance, 2),
        "leverage": leverage,
        "risk": round(actual_risk, 2),
        "margin": round(margin, 2),
        "position": round(position_value, 2),
        "loss": round(loss_at_sl, 2),
        "stop_percent": round(stop_fraction * 100, 4),
        "fee": round(fee, 2)
    }


def send_message(chat_id, text):
    if not BOT_TOKEN:
        return

    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"

    try:
        requests.post(
            url,
            json={"chat_id": chat_id, "text": text},
            timeout=10
        )
    except Exception:
        pass


def preview(trade):
    c = calculate_trade(trade)

    side = (
        "خرید / LONG"
        if trade["position"] == "BUY"
        else "فروش / SHORT"
    )

    tps = trade.get("take_profits", [])
    tp_text = ", ".join(str(x) for x in tps) if tps else "ندارد"

    return (
        "🧪 پیش‌نمایش — DRY RUN\n"
        "هیچ معامله واقعی ثبت نشده.\n\n"
        f"سیگنال: {trade.get('signal_id', '-')}\n"
        f"ارز: {trade.get('pair')}\n"
        f"جهت: {side}\n"
        f"ورود: {trade.get('entry')}\n"
        f"SL: {trade.get('stop_loss')}\n"
        f"TP: {tp_text}\n\n"
        f"موجودی: {c['balance']} USDT\n"
        f"لوریج: {c['leverage']}x\n"
        f"مارجین: {c['margin']} USDT\n"
        f"حجم پوزیشن: {c['position']} USDT\n"
        f"فاصله SL: {c['stop_percent']}%\n"
        f"ضرر در SL: {c['loss']} USDT ({c['risk']}%)\n"
        f"Fee تقریبی: {c['fee']} USDT\n\n"
        "دستورات:\n"
        "لوریج 50\n"
        "ریسک 5\n"
        "مارجین 100\n"
        "پیش فرض\n"
        "لغو\n"
        "تایید"
    )


def handle_command(chat_id, text):
    if chat_id not in pending_trades:
        return False

    text = normalize_numbers(text.strip()).lower()
    trade = pending_trades[chat_id]

    m = re.search(r"(?:لوریج|لورج|leverage)\s*:?\s*(\d+)", text)
    if m:
        trade["leverage"] = int(m.group(1))
        send_message(chat_id, "✅ لوریج تغییر کرد.\n\n" + preview(trade))
        return True

    m = re.search(r"(?:ریسک|risk)\s*:?\s*([\d.]+)", text)
    if m:
        trade["risk_percent"] = float(m.group(1))
        trade.pop("margin_override", None)
        send_message(chat_id, "✅ ریسک تغییر کرد.\n\n" + preview(trade))
        return True

    m = re.search(r"(?:مارجین|margin)\s*:?\s*([\d.]+)", text)
    if m:
        trade["margin_override"] = float(m.group(1))
        send_message(chat_id, "✅ مارجین تغییر کرد.\n\n" + preview(trade))
        return True

    if text in ["پیش فرض", "پیش‌فرض", "default"]:
        trade.pop("margin_override", None)
        trade["risk_percent"] = DEFAULT_RISK
        trade["leverage"] = DEFAULT_LEVERAGE
        send_message(chat_id, "🔄 برگشت به پیش‌فرض.\n\n" + preview(trade))
        return True

    if text in ["لغو", "cancel"]:
        pending_trades.pop(chat_id, None)
        send_message(chat_id, "❌ معامله لغو شد.")
        return True

    if text in ["تایید", "تأیید", "confirm"]:
        c = calculate_trade(trade)
        send_message(
            chat_id,
            "✅ تأیید آزمایشی انجام شد.\n"
            "هیچ سفارش واقعی ثبت نشده.\n\n"
            f"مارجین: {c['margin']} USDT\n"
            f"لوریج: {c['leverage']}x\n"
            f"حجم: {c['position']} USDT\n"
            f"ضرر احتمالی: {c['loss']} USDT"
        )
        return True

    return False


@app.route("/")
def home():
    return jsonify({
        "status": "ok",
        "mode": "DRY_RUN",
        "language": "fa"
    })


@app.route("/health")
def health():
    return jsonify({"status": "healthy"})


@app.route("/telegram", methods=["POST"])
def telegram_webhook():
    if WEBHOOK_SECRET:
        received = request.headers.get(
            "X-Telegram-Bot-Api-Secret-Token"
        )

        if received != WEBHOOK_SECRET:
            return jsonify({"error": "unauthorized"}), 403

    update = request.get_json(silent=True) or {}

    message = (
        update.get("message")
        or update.get("channel_post")
    )

    if not message:
        return jsonify({"status": "ignored"})

    chat_id = message.get("chat", {}).get("id")
    text = message.get("text") or message.get("caption")

    if not chat_id or not text:
        return jsonify({"status": "no_text"})

    if handle_command(chat_id, text):
        return jsonify({"status": "command_processed"})

    signal = parse_signal(text)

    if not signal.get("pair") or not signal.get("position"):
        return jsonify({"status": "not_signal"})

    valid, reason = validate_signal(signal)

    if not valid:
        send_message(
            chat_id,
            f"⚠️ سیگنال رد شد.\nدلیل: {reason}"
        )
        return jsonify({
            "status": "rejected",
            "reason": reason
        })

    signal["balance"] = DEFAULT_BALANCE
    signal["risk_percent"] = DEFAULT_RISK
    signal["leverage"] = DEFAULT_LEVERAGE

    pending_trades[chat_id] = signal

    send_message(
        chat_id,
        "📥 سیگنال جدید دریافت شد.\n\n"
        + preview(signal)
    )

    return jsonify({
        "status": "dry_run",
        "signal": signal
    })


def setup_webhook():
    if not BOT_TOKEN or not RENDER_URL or not WEBHOOK_SECRET:
        return

    url = f"https://api.telegram.org/bot{BOT_TOKEN}/setWebhook"

    try:
        requests.post(
            url,
            json={
                "url": f"{RENDER_URL}/telegram",
                "secret_token": WEBHOOK_SECRET
            },
            timeout=10
        )
    except Exception:
        pass


setup_webhook()


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=10000)
