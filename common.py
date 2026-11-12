"""Общие утилиты: админы, константы магазина, форматирование."""
import os
from decimal import Decimal
from html import escape

from aiogram.filters import Filter
from aiogram.types import CallbackQuery, Message, User

# Доступ к админ-панели: только эти аккаунты (через запятую в .env)
ADMIN_USERNAMES = {
    u.strip().lower().lstrip("@")
    for u in os.getenv("ADMIN_USERNAMES", "copelko777,copelko_oplata").split(",")
    if u.strip()
}
# Рекомендуется: числовые ID админов. Если заданы, проверка идёт по ID, а не по username
ADMIN_IDS = {int(x) for x in os.getenv("ADMIN_IDS", "").split(",") if x.strip().isdigit()}

# Кому клиент отправляет скрин и чек перевода
PAYMENT_CONTACT = os.getenv("PAYMENT_CONTACT", "@copelko_oplata")
# Ссылка, которую клиент получает после подтверждения оплаты
CHANNEL_LINK = os.getenv("CHANNEL_LINK", "https://t.me/+-tGNklAkcOY0ZWU5")
# Картинка для приветственного сообщения
WELCOME_IMAGE = os.path.join(os.path.dirname(__file__), "welcome.png")


def is_admin(user: User | None) -> bool:
    if user is None:
        return False
    if ADMIN_IDS:
        return user.id in ADMIN_IDS
    return (user.username or "").lower() in ADMIN_USERNAMES


class AdminFilter(Filter):
    async def __call__(self, event: Message | CallbackQuery) -> bool:
        return is_admin(event.from_user)


def money(v: Decimal | int | float) -> str:
    return f"{Decimal(v):,.2f}".replace(",", " ").replace(".00", "") + " ₽"


def who(username: str | None, tg_id: int, full_name: str | None = None) -> str:
    """Ник + юзернейм клиента, например «Иван (@ivan)»."""
    name = escape(full_name) if full_name else ""
    user = f"@{escape(username)}" if username else f"ID {tg_id}"
    return f"{name} ({user})" if name else user


def duration(days: int | None) -> str:
    return f"{days} дн." if days else "навсегда"
