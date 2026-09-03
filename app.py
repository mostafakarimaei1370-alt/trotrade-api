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

    m = re.search(
        r"Pair:\s*([A-Z0-9]+)",
        text,
        re.I
    )
    if m:
        signal["pair"] = m.group(1).upper()

    m = re.search(
        r"Position:\s*(BUY|SELL|LONG|SHORT)",
        text,
        re.I
    )
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

    m = re.search(
        r"\bSL\s*:\s*([\d.]+)",
        text,
        re.I
    )
    if m:
        signal["stop_loss"] = float(m.group(1))

    m = re.search(
        r"#S(\d+)",
        text,
        re.I
    )
    if m:
        signal["signal_id"] = "S" + m.group(1)

    tps = re.findall(
        r"TP\d+\s*:\s*([\d.]+)",
        text,
        re.I
    )

    if tps:
        signal["take_profits"] = [
            float(x) for x in tps
        ]

    return signal


def validate_signal(signal):
    entry = signal.get("entry")
    sl = signal.get("stop_loss")
    side = signal.get("position")

    if entry is None:
        return False, "قیمت ورود مشخص نیست."

    if sl is None:
        return False, "حد ضرر مشخص نیست."

    if side is None:
        return False, "جهت معامله مشخص نیست."

    if entry <= 0:
        return False, "قیمت ورود معتبر نیست."

    if side == "BUY" and sl >= entry:
        return False, (
            "برای خرید، SL باید پایین‌تر "
            "از قیمت ورود باشد."
        )

    if side == "SELL" and sl <= entry:
        return False, (
            "برای فروش، SL باید بالاتر "
            "از قیمت ورود باشد."
        )

    return True, "OK"


def calculate_trade(trade):
    entry = float(trade["entry"])
    sl = float(trade["stop_loss"])

    leverage = int(
        trade.get(
            "leverage",
            DEFAULT_LEVERAGE
        )
    )

    risk = float(
        trade.get(
            "risk_percent",
            DEFAULT_RISK
        )
    )

    balance = float(
        trade.get(
            "balance",
            DEFAULT_BALANCE
        )
    )

    stop_fraction = abs(entry - sl) / entry

    roundtrip_fee_rate = FEE_RATE * 2

    manual_margin = trade.get(
        "margin_override"
    )

    if manual_margin is not None:
        margin = float(manual_margin)

        position_value = (
            margin * leverage
        )

        price_loss = (
            position_value *
            stop_fraction
        )

        fee = (
            position_value *
            roundtrip_fee_rate
        )

        total_loss = (
            price_loss + fee
        )

        if balance > 0:
            actual_risk = (
                total_loss /
                balance *
                100
            )
        else:
            actual_risk = 0

    else:
        target_total_loss = (
            balance *
            risk /
            100
        )

        total_loss_fraction = (
            stop_fraction +
            roundtrip_fee_rate
        )

        if total_loss_fraction > 0:
            position_value = (
                target_total_loss /
                total_loss_fraction
            )
        else:
            position_value = 0

        if leverage > 0:
            margin = (
                position_value /
                leverage
            )
        else:
            margin = 0

        price_loss = (
            position_value *
            stop_fraction
        )

        fee = (
            position_value *
            roundtrip_fee_rate
        )

        total_loss = (
            price_loss + fee
        )

        actual_risk = risk

    return {
        "balance": round(balance, 2),
        "leverage": leverage,
        "risk": round(actual_risk, 2),
        "margin": round(margin, 2),
        "position": round(position_value, 2),
        "price_loss": round(price_loss, 2),
        "fee": round(fee, 2),
        "total_loss": round(total_loss, 2),
        "stop_percent": round(
            stop_fraction * 100,
            4
        )
    }


def send_message(chat_id, text):
    if not BOT_TOKEN:
        return

    url = (
        "https://api.telegram.org/"
        f"bot{BOT_TOKEN}/sendMessage"
    )

    try:
        requests.post(
            url,
            json={
                "chat_id": chat_id,
                "text": text
            },
            timeout=10
        )
    except Exception:
        pass


def preview(trade):
    c = calculate_trade(trade)

    if trade["position"] == "BUY":
        side = "خرید / LONG"
    else:
        side = "فروش / SHORT"

    tps = trade.get(
        "take_profits",
        []
    )

    if tps:
        tp_text = ", ".join(
            str(x) for x in tps
        )
    else:
        tp_text = "ندارد"

    return (
        "🧪 پیش‌نمایش — DRY RUN\n"
        "هیچ معامله واقعی ثبت نشده.\n\n"

        f"🆔 سیگنال: "
        f"{trade.get('signal_id', '-')}\n"

        f"💱 ارز: "
        f"{trade.get('pair')}\n"

        f"📈 جهت: "
        f"{side}\n"

        f"🎯 ورود: "
        f"{trade.get('entry')}\n"

        f"🛑 SL: "
        f"{trade.get('stop_loss')}\n"

        f"✅ TP: "
        f"{tp_text}\n\n"

        f"💰 موجودی: "
        f"{c['balance']} USDT\n"

        f"⚙️ لوریج: "
        f"{c['leverage']}x\n"

        f"💵 مارجین: "
        f"{c['margin']} USDT\n"

        f"📊 حجم پوزیشن: "
        f"{c['position']} USDT\n"

        f"📉 فاصله SL: "
        f"{c['stop_percent']}%\n\n"

        f"🔻 ضرر قیمت تا SL: "
        f"{c['price_loss']} USDT\n"

        f"💸 Fee تقریبی: "
        f"{c['fee']} USDT\n"

        f"⚠️ زیان کل تقریبی: "
        f"{c['total_loss']} USDT "
        f"({c['risk']}%)\n\n"

        "✏️ دستورات:\n"
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

    text = normalize_numbers(
        text.strip()
    ).lower()

    trade = pending_trades[
        chat_id
    ]

    leverage_match = re.search(
        r"(?:لوریج|لورج|leverage)"
        r"\s*:?\s*(\d+)",
        text
    )

    if leverage_match:
        value = int(
            leverage_match.group(1)
        )

        if value <= 0:
            send_message(
                chat_id,
                "❌ لوریج معتبر نیست."
            )
            return True

        trade["leverage"] = value

        send_message(
            chat_id,
            "✅ لوریج تغییر کرد.\n\n"
            + preview(trade)
        )

        return True

    risk_match = re.search(
        r"(?:ریسک|risk)"
        r"\s*:?\s*([\d.]+)\s*%?",
        text
    )

    if risk_match:
        value = float(
            risk_match.group(1)
        )

        if value <= 0:
            send_message(
                chat_id,
                "❌ ریسک معتبر نیست."
            )
            return True

        trade["risk_percent"] = value

        trade.pop(
            "margin_override",
            None
        )

        send_message(
            chat_id,
            "✅ ریسک تغییر کرد.\n\n"
            + preview(trade)
        )

        return True

    margin_match = re.search(
        r"(?:مارجین|margin)"
        r"\s*:?\s*([\d.]+)",
        text
    )

    if margin_match:
        value = float(
            margin_match.group(1)
        )

        if value <= 0:
            send_message(
                chat_id,
                "❌ مارجین معتبر نیست."
            )
            return True

        trade["margin_override"] = value

        send_message(
            chat_id,
            "✅ مارجین تغییر کرد.\n\n"
            + preview(trade)
        )

        return True

    if text in [
        "پیش فرض",
        "پیش‌فرض",
        "default"
    ]:
        trade.pop(
            "margin_override",
            None
        )

        trade["risk_percent"] = (
            DEFAULT_RISK
        )

        trade["leverage"] = (
            DEFAULT_LEVERAGE
        )

        send_message(
            chat_id,
            "🔄 تنظیمات به پیش‌فرض "
            "برگشت.\n\n"
            + preview(trade)
        )

        return True

    if text in [
        "لغو",
        "cancel"
    ]:
        pending_trades.pop(
            chat_id,
            None
        )

        send_message(
            chat_id,
            "❌ معامله لغو شد.\n"
            "هیچ سفارش واقعی ثبت نشده."
        )

        return True

    if text in [
        "تایید",
        "تأیید",
        "confirm"
    ]:
        c = calculate_trade(
            trade
        )

        send_message(
            chat_id,
            "✅ تأیید آزمایشی انجام شد.\n\n"
            "⚠️ DRY RUN فعال است.\n"
            "هیچ معامله واقعی ثبت نشده.\n\n"

            f"مارجین: "
            f"{c['margin']} USDT\n"

            f"لوریج: "
            f"{c['leverage']}x\n"

            f"حجم پوزیشن: "
            f"{c['position']} USDT\n"

            f"ضرر قیمت تا SL: "
            f"{c['price_loss']} USDT\n"

            f"Fee تقریبی: "
            f"{c['fee']} USDT\n"

            f"زیان کل تقریبی: "
            f"{c['total_loss']} USDT "
            f"({c['risk']}%)"
        )

        return True

    return False


@app.route("/")
def home():
    return jsonify({
        "status": "ok",
        "mode": "DRY_RUN",
        "language": "fa"
    }), 200


@app.route("/health")
def health():
    return jsonify({
        "status": "healthy"
    }), 200


@app.route(
    "/telegram",
    methods=["POST"]
)
def telegram_webhook():

    if WEBHOOK_SECRET:
        received = request.headers.get(
            "X-Telegram-Bot-Api-Secret-Token"
        )

        if received != WEBHOOK_SECRET:
            return jsonify({
                "error": "unauthorized"
            }), 403

    update = request.get_json(
        silent=True
    ) or {}

    message = (
        update.get("message")
        or update.get("channel_post")
    )

    if not message:
        return jsonify({
            "status": "ignored"
        }), 200

    chat_id = (
        message.get(
            "chat",
            {}
        ).get("id")
    )

    text = (
        message.get("text")
        or message.get("caption")
    )

    if not chat_id or not text:
        return jsonify({
            "status": "no_text"
        }), 200

    if handle_command(
        chat_id,
        text
    ):
        return jsonify({
            "status":
            "command_processed"
        }), 200

    signal = parse_signal(
        text
    )

    if (
        not signal.get("pair")
        or not signal.get("position")
    ):
        return jsonify({
            "status": "not_signal"
        }), 200

    valid, reason = (
        validate_signal(signal)
    )

    if not valid:
        send_message(
            chat_id,
            "⚠️ سیگنال رد شد.\n\n"
            f"دلیل: {reason}\n\n"
            "هیچ معامله‌ای انجام نشد."
        )

        return jsonify({
            "status": "rejected",
            "reason": reason
        }), 200

    signal["balance"] = (
        DEFAULT_BALANCE
    )

    signal["risk_percent"] = (
        DEFAULT_RISK
    )

    signal["leverage"] = (
        DEFAULT_LEVERAGE
    )

    pending_trades[
        chat_id
    ] = signal

    send_message(
        chat_id,
        "📥 سیگنال جدید دریافت شد.\n\n"
        + preview(signal)
    )

    return jsonify({
        "status": "dry_run",
        "signal": signal,
        "calculation":
        calculate_trade(signal)
    }), 200


def setup_webhook():

    if (
        not BOT_TOKEN
        or not RENDER_URL
        or not WEBHOOK_SECRET
    ):
        return

    telegram_url = (
        "https://api.telegram.org/"
        f"bot{BOT_TOKEN}/setWebhook"
    )

    webhook_url = (
        f"{RENDER_URL}/telegram"
    )

    try:
        requests.post(
            telegram_url,
            json={
                "url": webhook_url,
                "secret_token":
                WEBHOOK_SECRET
            },
            timeout=10
        )
    except Exception:
        pass


setup_webhook()


if __name__ == "__main__":
    app.run(
        host="0.0.0.0",
        port=10000
    )
