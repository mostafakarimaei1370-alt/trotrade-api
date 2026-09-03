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
    persian = "۰۱۲۳۴۵۶۷۸۹"
    arabic = "٠١٢٣٤٥٦٧٨٩"
    english = "0123456789"

    for p, e in zip(persian, english):
        text = text.replace(p, e)

    for a, e in zip(arabic, english):
        text = text.replace(a, e)

    return text


def parse_signal(text):
    text = normalize_numbers(text)
    signal = {}

    pair = re.search(r"Pair:\s*([A-Z0-9]+)", text, re.I)
    position = re.search(
        r"Position:\s*(BUY|SELL|LONG|SHORT)",
        text,
        re.I
    )

    entry = re.search(
        r"Entry\s+(?:Market|Limit)?\s*:?\s*([\d.]+)",
        text,
        re.I
    )

    stop_loss = re.search(
        r"\bSL\s*:\s*([\d.]+)",
        text,
        re.I
    )

    signal_id = re.search(
        r"#S(\d+)",
        text,
        re.I
    )

    tps = re.findall(
        r"TP\d+\s*:\s*([\d.]+)",
        text,
        re.I
    )

    leverage = re.search(
        r"(?:Leverage|لوریج|لورج)\s*:?\s*(\d+)",
        text,
        re.I
    )

    risk = re.search(
        r"(?:Risk|ریسک)\s*:?\s*([\d.]+)\s*%?",
        text,
        re.I
    )

    margin = re.search(
        r"(?:Margin|مارجین)\s*:?\s*([\d.]+)",
        text,
        re.I
    )

    if signal_id:
        signal["signal_id"] = "S" + signal_id.group(1)

    if pair:
        signal["pair"] = pair.group(1).upper()

    if position:
        side = position.group(1).upper()

        if side == "LONG":
            side = "BUY"
        elif side == "SHORT":
            side = "SELL"

        signal["position"] = side

    if entry:
        signal["entry"] = float(entry.group(1))

    if stop_loss:
        signal["stop_loss"] = float(stop_loss.group(1))

    if tps:
        signal["take_profits"] = [
            float(x) for x in tps
        ]

    if leverage:
        signal["leverage"] = int(
            leverage.group(1)
        )

    if risk:
        signal["risk_percent"] = float(
            risk.group(1)
        )

    if margin:
        signal["margin_override"] = float(
            margin.group(1)
        )

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

    if side == "BUY" and sl >= entry:
        return False, (
            "برای خرید، حد ضرر باید "
            "پایین‌تر از قیمت ورود باشد."
        )

    if side == "SELL" and sl <= entry:
        return False, (
            "برای فروش، حد ضرر باید "
            "بالاتر از قیمت ورود باشد."
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

    risk_percent = float(
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

    stop_distance = abs(entry - sl)

    if entry == 0:
        stop_fraction = 0
    else:
        stop_fraction = (
            stop_distance / entry
        )

    stop_percent = (
        stop_fraction * 100
    )

    margin_override = trade.get(
        "margin_override"
    )

    if margin_override is not None:
        margin = float(
            margin_override
        )

        position_value = (
            margin * leverage
        )

        loss_at_sl = (
            position_value *
            stop_fraction
        )

        if balance > 0:
            actual_risk_percent = (
                loss_at_sl /
                balance *
                100
            )
        else:
            actual_risk_percent = 0

    else:
        max_loss = (
            balance *
            risk_percent /
            100
        )

        if stop_fraction > 0:
            position_value = (
                max_loss /
                stop_fraction
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

        loss_at_sl = max_loss
        actual_risk_percent = (
            risk_percent
        )

    open_fee = (
        position_value *
        FEE_RATE
    )

    close_fee = (
        position_value *
        FEE_RATE
    )

    return {
        "balance": round(
            balance, 2
        ),
        "leverage": leverage,
        "risk_percent": round(
            actual_risk_percent, 2
        ),
        "stop_percent": round(
            stop_percent, 4
        ),
        "margin": round(
            margin, 2
        ),
        "position_value": round(
            position_value, 2
        ),
        "loss_at_sl": round(
            loss_at_sl, 2
        ),
        "open_fee": round(
            open_fee, 2
        ),
        "close_fee": round(
            close_fee, 2
        ),
        "total_fee": round(
            open_fee + close_fee,
            2
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


def build_preview(trade):
    calc = calculate_trade(
        trade
    )

    if trade["position"] == "BUY":
        direction = "خرید / LONG"
    else:
        direction = "فروش / SHORT"

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
        "🧪 پیش‌نمایش معامله — DRY RUN\n"
        "هیچ سفارش واقعی ثبت نشده است.\n\n"

        f"🆔 سیگنال: "
        f"{trade.get('signal_id', '-')}\n"

        f"💱 ارز: "
        f"{trade.get('pair', '-')}\n"

        f"📈 جهت: "
        f"{direction}\n"

        f"🎯 ورود: "
        f"{trade.get('entry')}\n"

        f"🛑 حد ضرر: "
        f"{trade.get('stop_loss')}\n"

        f"✅ تارگت‌ها: "
        f"{tp_text}\n\n"

        f"💰 موجودی مبنا: "
        f"{calc['balance']} USDT\n"

        f"⚙️ لوریج: "
        f"{calc['leverage']}x\n"

        f"💵 مارجین: "
        f"{calc['margin']} USDT\n"

        f"📊 حجم پوزیشن: "
        f"{calc['position_value']} USDT\n"

        f"📉 فاصله SL: "
        f"{calc['stop_percent']}%\n"

        f"⚠️ ضرر تقریبی در SL: "
        f"{calc['loss_at_sl']} USDT "
        f"({calc['risk_percent']}%)\n\n"

        f"💸 مجموع تقریبی Fee: "
        f"{calc['total_fee']} USDT\n\n"

        "برای تغییر همین معامله بنویس:\n"
        "لوریج 50\n"
        "ریسک 5\n"
        "مارجین 100\n\n"

        "برگشت به پیش‌فرض:\n"
        "پیش فرض\n\n"

        "لغو معامله:\n"
        "لغو\n\n"

        "تأیید آزمایشی:\n"
        "تایید"
    )


def handle_command(
    chat_id,
    text
):
    if chat_id not in pending_trades:
        return False

    raw = normalize_numbers(
        text.strip()
    )

    lower = raw.lower()

    trade = pending_trades[
        chat_id
    ]

    leverage_match = re.search(
        r"(?:لوریج|لورج|leverage)"
        r"\s*:?\s*(\d+)",
        lower
    )

    risk_match = re.search(
        r"(?:ریسک|risk)"
        r"\s*:?\s*([\d.]+)\s*%?",
        lower
    )

    margin_match = re.search(
        r"(?:مارجین|margin)"
        r"\s*:?\s*([\d.]+)",
        lower
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
            "✅ لوریج همین معامله "
            "تغییر کرد.\n\n"
            + build_preview(trade)
        )

        return True

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

        trade["risk_percent"] = (
            value
        )

        trade.pop(
            "margin_override",
            None
        )

        send_message(
            chat_id,
            "✅ ریسک همین معامله "
            "تغییر کرد.\n\n"
            + build_preview(trade)
        )

        return True

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

        trade["margin_override"] = (
            value
        )

        send_message(
            chat_id,
            "✅ مارجین همین معامله "
            "تغییر کرد.\n\n"
            + build_preview(trade)
        )

        return True

    if lower in [
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
            + build_preview(trade)
        )

        return True

    if lower in [
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
            "هیچ سفارش واقعی "
            "ثبت نشده است."
        )

        return True

    if lower in [
        "تایید",
        "تأیید",
        "confirm"
    ]:
        calc = calculate_trade(
            trade
        )

        send_message(
            chat_id,
            "✅ تأیید آزمایشی انجام شد.\n\n"
            "⚠️ هنوز DRY RUN فعال است.\n"
            "هیچ معامله واقعی ثبت نشده.\n\n"

            f"مارجین: "
            f"{calc['margin']} USDT\n"

            f"لوریج: "
            f"{calc['leverage']}x\n"

            f"حجم: "
            f"{calc['position_value']} USDT\n"

            f"ضرر احتمالی در SL: "
            f"{calc['loss_at_sl']} USDT"
        )

        return True

    return False


@app.route("/")
def home():
    return jsonify({
        "status": "ok",
        "mode": "DRY_RUN",
        "language": "fa",
        "message":
        "TroTrade Persian Signal Bot is running"
    })


@app.route("/health")
def health():
    return jsonify({
        "status": "healthy"
    })


@app.route(
    "/telegram",
    methods=["POST"]
)
def telegram_webhook():
    if WEBHOOK_SECRET:
        received_secret = (
            request.headers.get(
                "X-Telegram-Bot-Api-Secret-Token"
            )
        )

        if (
            received_secret !=
            WEBHOOK_SECRET
        ):
            return jsonify({
                "error":
                "unauthorized"
            }), 403

    update = (
        request.get_json(
            silent=True
        )
        or {}
    )

    message = (
        update.get("message")
        or update.get("channel_post")
    )

    if not message:
        return jsonify({
            "status": "ignored"
        })

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
        })

    if handle_command(
        chat_id,
        text
    ):
        return jsonify({
            "status":
            "command_processed"
        })

    signal = parse_signal(
        text
    )

    if (
        not signal.get("pair")
        or
        not signal.get("position")
    ):
        return jsonify({
            "status": "not_signal"
        })

    valid, reason = (
        validate_signal(
            signal
        )
    )

    if not valid:
        send_message(
