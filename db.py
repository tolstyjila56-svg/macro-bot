"""Работа с базой данных (Retool Database / PostgreSQL). Те же таблицы, что использует веб-панель."""
import os
import random
import string
from decimal import Decimal

import asyncpg

PAID = ("paid", "delivered")
STATUS_LABELS = {"new": "⏳ Не оплачен", "paid": "✅ Оплачен", "delivered": "📦 Выдан", "cancelled": "❌ Отменён"}

_pool: asyncpg.Pool | None = None


async def init() -> None:
    global _pool
    _pool = await asyncpg.create_pool(os.environ["DATABASE_URL"], min_size=1, max_size=5)


def pool() -> asyncpg.Pool:
    assert _pool is not None, "db.init() не вызван"
    return _pool


def _code() -> str:
    return "".join(random.choices("ABCDEFGHJKLMNPQRSTUVWXYZ23456789", k=8))


# ---------- Настройки ----------

async def get_setting(key: str, default: str = "") -> str:
    v = await pool().fetchval("SELECT value FROM shop_settings WHERE key = $1", key)
    return v if v is not None else default


async def set_setting(key: str, value: str) -> None:
    await pool().execute(
        "INSERT INTO shop_settings (key, value) VALUES ($1, $2) ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value",
        key, value,
    )


# ---------- Клиенты ----------

async def add_admin_chat(chat_id: int) -> None:
    """Запоминает чат админа, чтобы слать туда уведомления о заказах."""
    ids = await admin_chats()
    if chat_id not in ids:
        ids.append(chat_id)
        await set_setting("admin_chat_ids", ",".join(str(i) for i in ids))


async def admin_chats() -> list[int]:
    raw = await get_setting("admin_chat_ids")
    return [int(x) for x in raw.split(",") if x.strip()]


async def get_or_create_customer(
    tg_id: int, username: str | None, full_name: str | None, ref_code: str | None
):
    """Возвращает (клиент, создан_ли_сейчас). Реферер привязывается только при первом /start."""
    row = await pool().fetchrow("SELECT * FROM shop_customers WHERE telegram_id = $1", tg_id)
    if row:
        if row["username"] != username or row["full_name"] != full_name:
            row = await pool().fetchrow(
                "UPDATE shop_customers SET username = $1, full_name = $2 WHERE id = $3 RETURNING *",
                username, full_name, row["id"],
            )
        return row, False

    referrer_id = None
    if ref_code:
        referrer_id = await pool().fetchval(
            "SELECT id FROM shop_customers WHERE referral_code = $1", ref_code.strip().upper()
        )
    for _ in range(5):
        try:
            row = await pool().fetchrow(
                """INSERT INTO shop_customers (telegram_id, username, full_name, referral_code, referred_by)
                   VALUES ($1, $2, $3, $4, $5) RETURNING *""",
                tg_id, username, full_name, _code(), referrer_id,
            )
            return row, True
        except asyncpg.UniqueViolationError:
            continue  # совпал код — пробуем другой
    raise RuntimeError("Не удалось создать клиента")


async def get_customer(tg_id: int):
    return await pool().fetchrow("SELECT * FROM shop_customers WHERE telegram_id = $1", tg_id)


async def invited_count(customer_id: int) -> int:
    return await pool().fetchval("SELECT COUNT(*) FROM shop_customers WHERE referred_by = $1", customer_id)


async def get_referrer_tg(customer_id: int) -> int | None:
    return await pool().fetchval(
        "SELECT r.telegram_id FROM shop_customers c JOIN shop_customers r ON r.id = c.referred_by WHERE c.id = $1",
        customer_id,
    )


async def top_referrers(limit: int = 10):
    return await pool().fetch(
        """SELECT c.username, c.full_name, c.telegram_id, c.bonus_balance,
                  (SELECT COUNT(*) FROM shop_customers x WHERE x.referred_by = c.id) AS invited
             FROM shop_customers c
            ORDER BY invited DESC, c.bonus_balance DESC
            LIMIT $1""",
        limit,
    )


# ---------- Товары ----------

async def list_products(only_active: bool):
    q = "SELECT * FROM shop_products"
    if only_active:
        q += " WHERE is_active"
    return await pool().fetch(q + " ORDER BY is_active DESC, price ASC")


async def main_product():
    """Товар, который продаётся по кнопке «Купить» (первый активный)."""
    return await pool().fetchrow("SELECT * FROM shop_products WHERE is_active ORDER BY id LIMIT 1")


async def get_product(product_id: int):
    return await pool().fetchrow("SELECT * FROM shop_products WHERE id = $1", product_id)


async def add_product(name: str, price: Decimal, duration_days: int | None, description: str) -> int:
    return await pool().fetchval(
        "INSERT INTO shop_products (name, price, duration_days, description) VALUES ($1, $2, $3, $4) RETURNING id",
        name, price, duration_days, description,
    )


async def toggle_product(product_id: int) -> None:
    await pool().execute("UPDATE shop_products SET is_active = NOT is_active WHERE id = $1", product_id)


async def set_product_price(product_id: int, price: Decimal) -> None:
    await pool().execute("UPDATE shop_products SET price = $1 WHERE id = $2", price, product_id)


async def delete_product(product_id: int) -> None:
    await pool().execute("DELETE FROM shop_products WHERE id = $1", product_id)


# ---------- Заказы ----------

async def create_order(customer_id: int, product) -> int:
    return await pool().fetchval(
        """INSERT INTO shop_orders (customer_id, product_id, product_name, amount)
           VALUES ($1, $2, $3, $4) RETURNING id""",
        customer_id, product["id"], product["name"], product["price"],
    )


async def open_order(customer_id: int):
    """Неоплаченный заказ клиента, чтобы повторное «Я оплатил» не плодило дубли."""
    return await pool().fetchval(
        "SELECT id FROM shop_orders WHERE customer_id = $1 AND status = 'new' ORDER BY id DESC LIMIT 1", customer_id
    )


async def customer_orders(customer_id: int, limit: int = 10):
    return await pool().fetch(
        "SELECT * FROM shop_orders WHERE customer_id = $1 ORDER BY created_at DESC LIMIT $2", customer_id, limit
    )


async def list_orders(status: str | None, limit: int = 15):
    return await pool().fetch(
        """SELECT o.*, c.username, c.full_name, c.telegram_id
             FROM shop_orders o JOIN shop_customers c ON c.id = o.customer_id
            WHERE ($1::text IS NULL OR o.status = $1::text)
            ORDER BY o.created_at DESC LIMIT $2""",
        status, limit,
    )


async def get_order(order_id: int):
    return await pool().fetchrow(
        """SELECT o.*, c.username, c.full_name, c.telegram_id, r.username AS referrer_username
             FROM shop_orders o
             JOIN shop_customers c ON c.id = o.customer_id
             LEFT JOIN shop_customers r ON r.id = c.referred_by
            WHERE o.id = $1""",
        order_id,
    )


async def set_order_status(order_id: int, status: str):
    """Меняет статус и атомарно начисляет/списывает реферальный бонус.
    Возвращает (изменение_бонуса, telegram_id реферера) или None, если заказа нет."""
    async with pool().acquire() as con:
        async with con.transaction():
            o = await con.fetchrow(
                """SELECT o.status, o.amount, o.referral_reward, c.referred_by
                     FROM shop_orders o JOIN shop_customers c ON c.id = o.customer_id
                    WHERE o.id = $1 FOR UPDATE OF o""",
                order_id,
            )
            if not o:
                return None
            was_paid, is_paid = o["status"] in PAID, status in PAID
            reward: Decimal = o["referral_reward"]
            delta = Decimal(0)
            if not was_paid and is_paid and o["referred_by"]:
                pct = Decimal(await con.fetchval("SELECT value FROM shop_settings WHERE key = 'referral_percent'") or "0")
                reward = (o["amount"] * pct / 100).quantize(Decimal("0.01"))
                delta = reward
            elif was_paid and not is_paid and reward > 0:
                delta, reward = -reward, Decimal(0)

            await con.execute(
                "UPDATE shop_orders SET status = $1, referral_reward = $2, updated_at = NOW() WHERE id = $3",
                status, reward, order_id,
            )
            ref_tg = None
            if delta != 0:
                ref_tg = await con.fetchval(
                    "UPDATE shop_customers SET bonus_balance = bonus_balance + $1 WHERE id = $2 RETURNING telegram_id",
                    delta, o["referred_by"],
                )
            return delta, ref_tg


# ---------- Статистика ----------

async def stats():
    return await pool().fetchrow(
        """SELECT
             (SELECT COUNT(*) FROM shop_orders) AS orders_total,
             (SELECT COUNT(*) FROM shop_orders WHERE status = 'new') AS orders_new,
             (SELECT COUNT(*) FROM shop_orders WHERE status IN ('paid','delivered')) AS orders_paid,
             (SELECT COALESCE(SUM(amount), 0) FROM shop_orders WHERE status IN ('paid','delivered')) AS revenue,
             (SELECT COALESCE(SUM(amount), 0) FROM shop_orders
               WHERE status IN ('paid','delivered') AND created_at > NOW() - INTERVAL '1 day') AS revenue_day,
             (SELECT COUNT(*) FROM shop_customers) AS customers,
             (SELECT COUNT(*) FROM shop_customers WHERE created_at > NOW() - INTERVAL '1 day') AS customers_day,
             (SELECT COUNT(*) FROM shop_customers WHERE referred_by IS NOT NULL) AS referred,
             (SELECT COALESCE(SUM(referral_reward), 0) FROM shop_orders) AS ref_rewards"""
    )
