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

# Approximate fee per side. Can be changed later in Render.
FEE_RATE = float(os.getenv("FEE_RATE", "0.0004"))

# Latest pending trade for each Telegram chat
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
    position = re.search(r"Position:\s*(BUY|SELL|LONG|SHORT)", text, re.I)

    entry = re.search(
        r"Entry\s+(?:Market|Limit)?\s*:?\s*([\d.]+)",
        text,
        re.I
    )

    stop_loss = re.search(r"\bSL\s*:\s*([\d.]+)", text, re.I)
    signal_id = re.search(r"#S(\d+)", text, re.I)

    tps = re.findall(
        r"TP\d+\s*:\s*([\d.]+)",
        text,
        re.I
    )

    leverage = re.search(
        r"(?:Leverage|لور(?:ی|ي)ج)\s*:?\s*(\d+)",
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

        if side == "SHORT":
            side = "SELL"

        signal["position"] = side

    if entry:
        signal["entry"] = float(entry.group(1))

    if stop_loss:
        signal["stop_loss"] = float(stop_loss.group(1))

    if tps:
        signal["take_profits"] = [float(x) for x in tps]

    if leverage:
        signal["leverage"] = int(leverage.group(1))

    if risk:
        signal["risk_percent"] = float(risk.group(1))

    if margin:
        signal["margin_override"] = float(margin.group(1))

    return signal


def validate_signal(signal):
    entry = signal.get("entry")
    sl = signal.get("stop_loss")
    side = signal.get("position")

    if not entry:
        return False, "قیمت ورود مشخص نیست."

    if not sl:
        return False, "حد ضرر مشخص نیست."

    if not side:
        return False, "جهت معامله مشخص نیست."

    if side == "BUY" and sl >= entry:
        return False, "برای خرید، حد ضرر باید پایین‌تر از قیمت ورود باشد."

    if side == "SELL" and sl <= entry:
        return False, "برای فروش، حد ضرر باید بالاتر از قیمت ورود باشد."

    return True, "OK"


def calculate_trade(trade):
    entry = trade["entry"]
    sl = trade["stop_loss"]

    leverage = int(
        trade.get("leverage", DEFAULT_LEVERAGE)
    )

    risk_percent = float(
        trade.get("risk_percent", DEFAULT_RISK)
    )

    balance = float(
        trade.get("balance", DEFAULT_BALANCE)
    )

    stop_distance = abs(entry - sl)
    stop_fraction = stop_distance / entry
    stop_percent = stop_fraction * 100

    margin_override = trade.get("margin_override")

    if margin_override is not None:
        margin = float(margin_override)
        position_value = margin * leverage
        loss_at_sl = position_value * stop_fraction

        actual_risk_percent = (
            loss_at_sl / balance * 100
            if balance > 0 else 0
        )

    else:
        max_loss = balance * (risk_percent / 100)

        position_value = (
            max_loss / stop_fraction
            if stop_fraction > 0 else 0
        )

        margin = (
            position_value / leverage
            if leverage > 0 else 0
        )

        loss_at_sl = max_loss
        actual_risk_percent = risk_percent

    open_fee = position_value * FEE_RATE
    close_fee = position_value * FEE_RATE
    estimated_roundtrip_fee = open_fee + close_fee

    return {
        "balance": round(balance, 2),
        "leverage": leverage,
        "risk_percent": round(actual_risk_percent, 2),
        "stop_percent": round(stop_percent, 4),
        "margin": round(margin, 2),
        "position_value": round(position_value, 2),
        "loss_at_sl": round(loss_at_sl, 2),
        "open_fee": round(open_fee, 2),
        "close_fee": round(close_fee, 2),
        "total_fee": round(estimated_roundtrip_fee, 2)
    }


def send_message(chat_id, text):
    if not BOT_TOKEN:
        return

    url = (
        f"https://api.telegram.org/"
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
    calc = calculate_trade(trade)

    direction = (
        "خرید / LONG"
        if trade["position"] == "BUY"
        else "فروش / SHORT"
    )

    tps = trade.get("take_profits", [])

    tp_text = (
        ", ".join(str(x) for x in tps)
        if tps else "ندارد"
    )

    text = (
        "🧪 پیش‌نمایش معامله — DRY RUN\n"
        "هیچ سفارشی ثبت نشده است.\n\n"

        f"🆔 سیگنال: {trade.get('signal_id', '-')}\n"
        f"💱 ارز: {trade.get('pair', '-')}\n"
        f"📈 جهت: {direction}\n"
        f"🎯 ورود: {trade.get('entry')}\n"
        f"🛑 حد ضرر: {trade.get('stop_loss')}\n"
        f"✅ تارگت‌ها: {tp_text}\n\n"

        f"💰 موجودی مبنا: {calc['balance']} USDT\n"
        f"⚙️ لوریج: {calc['leverage']}x\n"
        f"💵 مارجین: {calc['margin']} USDT\n"
        f"📊 حجم پوزیشن: {calc['position_value']} USDT\n"
        f"📉 فاصله SL: {calc['stop_percent']}%\n"
        f"⚠️ ضرر تقریبی در SL: "
        f"{calc['loss_at_sl']} USDT "
        f"({calc['risk_percent']}%)\n\n"

        f"💸 کارمزد تقریبی باز: {calc['open_fee']} USDT\n"
        f"💸 کارمزد تقریبی بسته‌شدن: "
        f"{calc['close_fee']} USDT\n"
        f"💸 مجموع تقریبی Fee: "
        f"{calc
