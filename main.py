# =========================
# CONFIGURACIÓN
# =========================

import os
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
FINNHUB_API_KEY = os.getenv("FINNHUB_API_KEY")

CHECK_INTERVAL_MINUTES = 15

# =========================
# IMPORTS
# =========================

import yfinance as yf
import re
import numpy as np
import requests
import sqlite3
from datetime import datetime, timedelta

from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    filters
)

from apscheduler.schedulers.background import BackgroundScheduler

from reportlab.platypus import SimpleDocTemplate, Paragraph
from reportlab.lib.styles import getSampleStyleSheet

# =========================
# BASE DE DATOS
# =========================

conn = sqlite3.connect("bot.db", check_same_thread=False)
cursor = conn.cursor()

cursor.execute("""
CREATE TABLE IF NOT EXISTS alerts (
    chat_id INTEGER,
    ticker TEXT,
    threshold REAL,
    last_price REAL
)
""")

cursor.execute("""
CREATE TABLE IF NOT EXISTS users (
    chat_id INTEGER PRIMARY KEY
)
""")

cursor.execute("""
CREATE TABLE IF NOT EXISTS portfolio (
    chat_id INTEGER,
    ticker TEXT,
    amount REAL
)
""")

conn.commit()

# =========================
# RESOLVER NOMBRE → TICKER
# =========================

def resolver_ticker(texto):
    texto = texto.lower()
    texto = texto.replace("&", "and")
    texto = re.sub(r"[^a-z0-9\s]", "", texto)

    MAP = {
        "tesla": "TSLA",
        "apple": "AAPL",
        "amazon": "AMZN",
        "google": "GOOGL",
        "alphabet": "GOOGL",
        "meta": "META",
        "facebook": "META",
        "microsoft": "MSFT",
        "nvidia": "NVDA",
        "netflix": "NFLX",
        "coca cola": "KO",
        "berkshire hathaway": "BRK-B",
        "sp 500": "^GSPC",
        "nasdaq": "^IXIC",
        "dow jones": "^DJI"
    }

    for name, ticker in MAP.items():
        if name in texto:
            return ticker
    return None

# =========================
# ANÁLISIS FINANCIERO
# =========================

def analizar_accion(ticker, monto):
    hist = yf.Ticker(ticker).history(period="5y")
    if hist.empty:
        return None

    returns = hist["Close"].pct_change().dropna()
    annual = returns.mean() * 252
    vol = returns.std() * np.sqrt(252)
    price = hist["Close"].iloc[-1]

    riesgo = "Alto" if vol > 0.35 else "Medio" if vol > 0.2 else "Bajo"

    return {
        "precio": round(price, 2),
        "riesgo": riesgo,
        "vol": round(vol * 100, 2),
        "retornos": {
            "corto": round(monto * annual * 0.5, 2),
            "medio": round(monto * annual, 2),
            "largo": round(monto * annual * 1.5, 2)
        }
    }

# =========================
# OPINIÓN ASESOR
# =========================

def opinion(data):
    if data["riesgo"] == "Alto":
        return "Es una acción con potencial, pero hay que tolerar volatilidad."
    if data["riesgo"] == "Medio":
        return "Buen balance entre riesgo y crecimiento."
    return "Perfil defensivo, ideal para estabilidad."

# =========================
# NOTICIAS
# =========================

def noticias_empresa(ticker):
    today = datetime.today().strftime("%Y-%m-%d")
    past = (datetime.today() - timedelta(days=3)).strftime("%Y-%m-%d")

    url = (
        f"https://finnhub.io/api/v1/company-news"
        f"?symbol={ticker}&from={past}&to={today}&token={FINNHUB_API_KEY}"
    )

    r = requests.get(url).json()
    if not isinstance(r, list):
        return []

    return r[:2]

# =========================
# ALERTAS
# =========================

def check_alerts(app):
    cursor.execute("SELECT chat_id, ticker, threshold, last_price FROM alerts")
    for chat_id, ticker, threshold, last_price in cursor.fetchall():
        price = yf.Ticker(ticker).history(period="1d")["Close"][-1]
        change = ((price - last_price) / last_price) * 100

        if abs(change) >= threshold:
            app.bot.send_message(
                chat_id=chat_id,
                text=f"🔔 {ticker} se movió {round(change,2)}%"
            )
            cursor.execute(
                "UPDATE alerts SET last_price=? WHERE chat_id=? AND ticker=?",
                (price, chat_id, ticker)
            )
            conn.commit()

# =========================
# PDF PORTAFOLIO
# =========================

def generar_pdf(chat_id):
    cursor.execute("SELECT ticker, amount FROM portfolio WHERE chat_id=?", (chat_id,))
    rows = cursor.fetchall()

    file = f"portafolio_{chat_id}.pdf"
    doc = SimpleDocTemplate(file)
    styles = getSampleStyleSheet()
    content = [Paragraph("Reporte de Portafolio", styles["Title"])]

    for ticker, amount in rows:
        data = analizar_accion(ticker, amount)
        if data:
            content.append(
                Paragraph(
                    f"{ticker} — Riesgo: {data['riesgo']} — Precio: ${data['precio']}",
                    styles["Normal"]
                )
            )

    doc.build(content)
    return file

# =========================
# MENSAJES
# =========================

async def start(update, context):
    cursor.execute("INSERT OR IGNORE INTO users VALUES (?)", (update.message.chat_id,))
    conn.commit()

    await update.message.reply_text(
        "🤖 Soy tu asesor de inversión.\n\n"
        "Ejemplos:\n"
        "• Analiza Tesla con 5000\n"
        "• Compara Apple y Nvidia\n"
        "• Agrega Tesla 3000\n"
        "• Ver portafolio\n"
        "• Reporte PDF"
    )

async def mensaje(update, context):
    text = update.message.text.lower()
    chat_id = update.message.chat_id

    if "agrega" in text:
        ticker = resolver_ticker(text)
        monto = float(re.findall(r"\d+", text)[0])
        cursor.execute("INSERT INTO portfolio VALUES (?,?,?)", (chat_id, ticker, monto))
        conn.commit()
        await update.message.reply_text(f"{ticker} agregado al portafolio.")
        return

    if "portafolio" in text:
        cursor.execute("SELECT ticker, amount FROM portfolio WHERE chat_id=?", (chat_id,))
        rows = cursor.fetchall()
        if not rows:
            await update.message.reply_text("Tu portafolio está vacío.")
            return

        msg = "📊 Tu portafolio:\n\n"
        for t, a in rows:
            msg += f"{t}: ${a}\n"
        await update.message.reply_text(msg)
        return

    if "reporte" in text:
        file = generar_pdf(chat_id)
        await update.message.reply_document(open(file, "rb"))
        return

    ticker = resolver_ticker(text)
    if not ticker:
        await update.message.reply_text("No identifiqué la empresa.")
        return

    monto = float(re.findall(r"\d+", text)[0]) if re.findall(r"\d+", text) else 1000
    data = analizar_accion(ticker, monto)

    msg = (
        f"📈 {ticker}\n"
        f"Precio: ${data['precio']}\n"
        f"Riesgo: {data['riesgo']}\n\n"
        f"Corto: ${data['retornos']['corto']}\n"
        f"Medio: ${data['retornos']['medio']}\n"
        f"Largo: ${data['retornos']['largo']}\n\n"
        f"{opinion(data)}"
    )

    await update.message.reply_text(msg)

# =========================
# MAIN
# =========================

app = ApplicationBuilder().token(TELEGRAM_TOKEN).build()
app.add_handler(CommandHandler("start", start))
app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, mensaje))

scheduler = BackgroundScheduler()
scheduler.add_job(check_alerts, "interval", minutes=CHECK_INTERVAL_MINUTES, args=[app])
scheduler.start()

print("🤖 Bot asesor activo 24/7")
app.run_polling()
