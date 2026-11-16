"""Надсилання листів.

Якщо задано SMTP_HOST, лист іде через SMTP (наприклад, Gmail з паролем застосунку
або AWS SES SMTP). Якщо ні — лист лише записується в журнал контейнера: так зручно
розробляти й тестувати без поштового сервера.
"""
import logging
import os
import smtplib
from email.message import EmailMessage

log = logging.getLogger("mailer")

SMTP_HOST = os.getenv("SMTP_HOST", "")
SMTP_PORT = int(os.getenv("SMTP_PORT", "587"))
SMTP_USER = os.getenv("SMTP_USER", "")
SMTP_PASSWORD = os.getenv("SMTP_PASSWORD", "")
MAIL_FROM = os.getenv("MAIL_FROM", "AutoSlot <no-reply@autoslot.local>")


def enabled() -> bool:
    return bool(SMTP_HOST)


def send(to: str, subject: str, body: str) -> None:
    if not enabled():
        log.info("[EMAIL -> %s] %s | %s", to, subject, body)
        return
    msg = EmailMessage()
    msg["From"], msg["To"], msg["Subject"] = MAIL_FROM, to, subject
    msg.set_content(body)
    with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=10) as smtp:
        smtp.starttls()  # шифруємо з'єднання з поштовим сервером
        if SMTP_USER:
            smtp.login(SMTP_USER, SMTP_PASSWORD)
        smtp.send_message(msg)
    log.info("email sent to %s: %s", to, subject)
