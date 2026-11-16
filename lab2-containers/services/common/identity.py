"""Хто робить запит.

Сервіси самі не перевіряють JWT. Це робить gateway: перед захищеним запитом nginx
питає auth-service і передає далі заголовки X-User-Id, X-User-Email, X-User-Role.
Клієнт підробити ці заголовки не може, бо nginx завжди перезаписує їх своїми
значеннями. Тому сервіси довіряють їм, але лише за умови, що до сервісу можна
дістатися тільки через gateway (порти сервісів назовні не відкриті).
"""
from dataclasses import dataclass

from fastapi import Depends, Header, HTTPException


@dataclass
class Identity:
    id: str
    email: str
    role: str

    @property
    def is_admin(self) -> bool:
        return self.role == "ADMIN"


def current_user(x_user_id: str | None = Header(None), x_user_email: str | None = Header(None),
                 x_user_role: str | None = Header(None)) -> Identity:
    if not x_user_email:
        raise HTTPException(401, "Потрібно увійти")
    return Identity(id=x_user_id or "", email=x_user_email, role=x_user_role or "CLIENT")


def admin_user(user: Identity = Depends(current_user)) -> Identity:
    if not user.is_admin:
        raise HTTPException(403, "Доступ лише для адміністратора")
    return user
