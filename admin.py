"""Админ-панель внутри бота. Доступна только аккаунтам из common.ADMIN_USERNAMES / ADMIN_IDS."""
from decimal import Decimal, InvalidOperation
from html import escape

from aiogram import Bot, F, Router
from aiogram.filters import Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder

import db
from common import CHANNEL_LINK, AdminFilter, duration, money, who

router = Router()
router.message.filter(AdminFilter())
router.callback_query.filter(AdminFilter())

CUSTOMER_STATUS_TEXT = {
    "paid": "✅ Оплата по заказу #{id} подтверждена!\n\n🙏 Спасибо за покупку!\n\nВаш доступ: " + CHANNEL_LINK,
    "cancelled": "❌ Заказ #{id} отменён. Если это ошибка — напишите администратору.",
}


class SetPayment(StatesGroup):
    text = State()


class AddProduct(StatesGroup):
    name = State()
    price = State()
    duration = State()
    description = State()


class EditPrice(StatesGroup):
    price = State()


class SetPercent(StatesGroup):
    value = State()


class SendToClient(StatesGroup):
    content = State()


def back(to: str = "adm:menu") -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text="⬅️ Назад", callback_data=to)
    return kb.as_markup()


def menu_kb() -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text="⏳ Не оплаченные", callback_data="adm:orders:new")
    kb.button(text="📋 Все заказы", callback_data="adm:orders:all")
    kb.button(text="🛍 Каталог", callback_data="adm:products")
    kb.button(text="👥 Рефералы", callback_data="adm:refs")
    kb.button(text="📊 Статистика", callback_data="adm:stats")
    kb.button(text="⚙️ Настройки", callback_data="adm:settings")
    kb.adjust(2)
    return kb.as_markup()


async def show(target: Message | CallbackQuery, text: str, markup: InlineKeyboardMarkup) -> None:
    """Редактирует сообщение при нажатии кнопки или отправляет новое."""
    if isinstance(target, CallbackQuery) and target.message:
        try:
            await target.message.edit_text(text, reply_markup=markup)  # type: ignore[union-attr]
        except Exception:
            await target.message.answer(text, reply_markup=markup)
        await target.answer()
    elif isinstance(target, Message):
        await target.answer(text, reply_markup=markup)


# ---------- Меню ----------

@router.message(Command("admin"))
@router.message(F.text == "⚙️ Админ-панель")
async def admin_menu(m: Message, state: FSMContext):
    await state.clear()
    await db.add_admin_chat(m.chat.id)
    await m.answer("<b>Админ-панель</b>", reply_markup=menu_kb())


@router.callback_query(F.data == "adm:menu")
async def admin_menu_cb(c: CallbackQuery, state: FSMContext):
    await state.clear()
    await show(c, "<b>Админ-панель</b>", menu_kb())


@router.message(Command("cancel"), StateFilter("*"))
async def cancel(m: Message, state: FSMContext):
    await state.clear()
    await m.answer("Отменено.", reply_markup=menu_kb())


@router.message(F.text.startswith("/"), ~StateFilter(None))
async def command_during_input(m: Message, state: FSMContext):
    # Любая команда во время ввода (реквизиты, цена и т.п.) отменяет ввод, а не сохраняется как текст
    await state.clear()
    await m.answer("Ввод отменён. Отправьте команду ещё раз.")


# ---------- Заказы ----------

def order_card(o) -> str:
    lines = [
        f"<b>Заказ #{o['id']}</b> — {db.STATUS_LABELS.get(o['status'], o['status'])}",
        f"Клиент: {who(o['username'], o['telegram_id'], o['full_name'])}",
        f"Telegram ID: <code>{o['telegram_id']}</code>",
        f"Товар: {escape(o['product_name'])}",
        f"Сумма: {money(o['amount'])}",
        f"Дата: {o['created_at']:%d.%m.%Y %H:%M}",
    ]
    if o["referrer_username"]:
        lines.append(f"Пригласил: @{escape(o['referrer_username'])}")
    if o["referral_reward"] > 0:
        lines.append(f"Бонус рефереру: {money(o['referral_reward'])}")
    return "\n".join(lines)


def order_kb(o) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    if o["status"] not in db.PAID:
        kb.button(text="✅ Заказ оплачен", callback_data=f"adm:st:{o['id']}:paid")
    if o["status"] != "cancelled":
        kb.button(text="❌ Отменить", callback_data=f"adm:st:{o['id']}:cancelled")
    if o["status"] != "new":
        kb.button(text="⏳ Вернуть в «не оплачен»", callback_data=f"adm:st:{o['id']}:new")
    kb.button(text="✉️ Написать клиенту", callback_data=f"adm:msg:{o['id']}")
    kb.button(text="⬅️ К заказам", callback_data="adm:orders:new")
    kb.adjust(1)
    return kb.as_markup()


async def notify_admins_new_order(bot: Bot, order_id: int) -> None:
    """Рассылает карточку нового заказа всем админам, которые хоть раз нажали /start."""
    o = await db.get_order(order_id)
    if not o:
        return
    for chat in await db.admin_chats():
        try:
            await bot.send_message(chat, "🆕 " + order_card(o), reply_markup=order_kb(o))
        except Exception:
            pass


@router.callback_query(F.data.startswith("adm:orders:"))
async def orders_list(c: CallbackQuery):
    status = c.data.split(":")[2]  # type: ignore[union-attr]
    rows = await db.list_orders(None if status == "all" else status)
    title = "Не оплаченные заказы" if status == "new" else "Последние заказы"
    kb = InlineKeyboardBuilder()
    for o in rows:
        icon = db.STATUS_LABELS[o["status"]].split()[0]
        name = o["full_name"] or ("@" + o["username"] if o["username"] else str(o["telegram_id"]))
        user = f" @{o['username']}" if o["full_name"] and o["username"] else ""
        kb.button(
            text=f"{icon} #{o['id']} · {name}{user} · {money(o['amount'])}",
            callback_data=f"adm:order:{o['id']}",
        )
    kb.button(text="⬅️ Назад", callback_data="adm:menu")
    kb.adjust(1)
    await show(c, f"<b>{title}</b>" + ("" if rows else "\n\nЗаказов нет."), kb.as_markup())


@router.callback_query(F.data.startswith("adm:order:"))
async def order_view(c: CallbackQuery):
    o = await db.get_order(int(c.data.split(":")[2]))  # type: ignore[union-attr]
    if not o:
        await c.answer("Заказ не найден", show_alert=True)
        return
    await show(c, order_card(o), order_kb(o))


@router.callback_query(F.data.startswith("adm:st:"))
async def order_set_status(c: CallbackQuery, bot: Bot):
    _, _, oid, status = c.data.split(":")  # type: ignore[union-attr]
    before = await db.get_order(int(oid))
    if before and before["status"] == status:
        # Второй админ уже обработал заказ — не шлём клиенту дубль
        await show(c, order_card(before) + "\n\nСтатус уже был установлен.", order_kb(before))
        return
    res = await db.set_order_status(int(oid), status)
    if res is None:
        await c.answer("Заказ не найден", show_alert=True)
        return
    delta, ref_tg = res
    o = await db.get_order(int(oid))

    # Уведомляем клиента и реферера
    if status in CUSTOMER_STATUS_TEXT:
        try:
            await bot.send_message(o["telegram_id"], CUSTOMER_STATUS_TEXT[status].format(id=oid))
        except Exception:
            pass
    if ref_tg and delta > 0:
        try:
            await bot.send_message(ref_tg, f"🎁 Вам начислен реферальный бонус {money(delta)}!")
        except Exception:
            pass

    note = f"\n\nРефереру: {'+' if delta > 0 else ''}{money(delta)}" if delta else ""
    await show(c, order_card(o) + note, order_kb(o))


@router.callback_query(F.data.startswith("adm:msg:"))
async def order_msg_start(c: CallbackQuery, state: FSMContext):
    oid = int(c.data.split(":")[2])  # type: ignore[union-attr]
    await state.set_state(SendToClient.content)
    await state.update_data(order_id=oid)
    await c.message.answer(  # type: ignore[union-attr]
        f"Отправьте сообщение для клиента по заказу #{oid}: текст, файл, фото — что угодно.\n/cancel — отмена"
    )
    await c.answer()


@router.message(SendToClient.content)
async def order_msg_send(m: Message, state: FSMContext, bot: Bot):
    data = await state.get_data()
    await state.clear()
    o = await db.get_order(data["order_id"])
    try:
        await bot.send_message(o["telegram_id"], f"📩 Сообщение по заказу #{o['id']}:")
        await m.copy_to(o["telegram_id"])
        await m.answer("Отправлено ✅", reply_markup=order_kb(o))
    except Exception as e:
        await m.answer(f"Не удалось отправить: {escape(str(e))}", reply_markup=order_kb(o))


# ---------- Каталог ----------

@router.callback_query(F.data == "adm:products")
async def products(c: CallbackQuery, state: FSMContext):
    await state.clear()
    rows = await db.list_products(only_active=False)
    kb = InlineKeyboardBuilder()
    for p in rows:
        mark = "🟢" if p["is_active"] else "⚪️"
        kb.button(text=f"{mark} {p['name']} · {money(p['price'])}", callback_data=f"adm:prod:{p['id']}")
    kb.button(text="➕ Добавить товар", callback_data="adm:padd")
    kb.button(text="⬅️ Назад", callback_data="adm:menu")
    kb.adjust(1)
    await show(c, "<b>Каталог</b>\n🟢 — в продаже, ⚪️ — скрыт", kb.as_markup())


async def product_view(target: Message | CallbackQuery, product_id: int):
    p = await db.get_product(product_id)
    if not p:
        if isinstance(target, CallbackQuery):
            await target.answer("Товар не найден", show_alert=True)
        return
    text = (
        f"<b>{escape(p['name'])}</b>\n"
        f"Цена: {money(p['price'])}\nСрок: {duration(p['duration_days'])}\n"
        f"Статус: {'в продаже' if p['is_active'] else 'скрыт'}\n\n{escape(p['description'] or '')}"
    )
    kb = InlineKeyboardBuilder()
    kb.button(text="🙈 Скрыть" if p["is_active"] else "👁 Показать", callback_data=f"adm:ptoggle:{p['id']}")
    kb.button(text="💲 Изменить цену", callback_data=f"adm:pprice:{p['id']}")
    kb.button(text="🗑 Удалить", callback_data=f"adm:pdel:{p['id']}")
    kb.button(text="⬅️ К каталогу", callback_data="adm:products")
    kb.adjust(2, 1, 1)
    await show(target, text, kb.as_markup())


@router.callback_query(F.data.startswith("adm:prod:"))
async def product_open(c: CallbackQuery):
    await product_view(c, int(c.data.split(":")[2]))  # type: ignore[union-attr]


@router.callback_query(F.data.startswith("adm:ptoggle:"))
async def product_toggle(c: CallbackQuery):
    pid = int(c.data.split(":")[2])  # type: ignore[union-attr]
    await db.toggle_product(pid)
    await product_view(c, pid)


@router.callback_query(F.data.startswith("adm:pdel:"))
async def product_delete_ask(c: CallbackQuery):
    pid = int(c.data.split(":")[2])  # type: ignore[union-attr]
    kb = InlineKeyboardBuilder()
    kb.button(text="Да, удалить", callback_data=f"adm:pdelok:{pid}")
    kb.button(text="Отмена", callback_data=f"adm:prod:{pid}")
    await show(c, "Удалить товар? Старые заказы сохранятся.", kb.as_markup())


@router.callback_query(F.data.startswith("adm:pdelok:"))
async def product_delete(c: CallbackQuery, state: FSMContext):
    await db.delete_product(int(c.data.split(":")[2]))  # type: ignore[union-attr]
    await products(c, state)


@router.callback_query(F.data.startswith("adm:pprice:"))
async def product_price_start(c: CallbackQuery, state: FSMContext):
    await state.set_state(EditPrice.price)
    await state.update_data(product_id=int(c.data.split(":")[2]))  # type: ignore[union-attr]
    await c.message.answer("Введите новую цену в рублях:\n/cancel — отмена")  # type: ignore[union-attr]
    await c.answer()


def parse_price(text: str | None) -> Decimal | None:
    try:
        v = Decimal((text or "").replace(",", ".").replace(" ", ""))
        return v if v >= 0 else None
    except InvalidOperation:
        return None


@router.message(EditPrice.price)
async def product_price_save(m: Message, state: FSMContext):
    price = parse_price(m.text)
    if price is None:
        await m.answer("Нужно число, например 299")
        return
    data = await state.get_data()
    await state.clear()
    await db.set_product_price(data["product_id"], price)
    await product_view(m, data["product_id"])


@router.callback_query(F.data == "adm:padd")
async def add_start(c: CallbackQuery, state: FSMContext):
    await state.set_state(AddProduct.name)
    await c.message.answer("Название товара:\n/cancel — отмена")  # type: ignore[union-attr]
    await c.answer()


@router.message(AddProduct.name)
async def add_name(m: Message, state: FSMContext):
    if not m.text or not m.text.strip():
        await m.answer("Введите название текстом")
        return
    await state.update_data(name=m.text.strip())
    await state.set_state(AddProduct.price)
    await m.answer("Цена в рублях:")


@router.message(AddProduct.price)
async def add_price(m: Message, state: FSMContext):
    price = parse_price(m.text)
    if price is None:
        await m.answer("Нужно число, например 299")
        return
    await state.update_data(price=str(price))
    await state.set_state(AddProduct.duration)
    await m.answer("Срок в днях (0 — навсегда):")


@router.message(AddProduct.duration)
async def add_duration(m: Message, state: FSMContext):
    if not (m.text or "").strip().isdigit():
        await m.answer("Нужно целое число дней, 0 — навсегда")
        return
    await state.update_data(duration=int(m.text.strip()))  # type: ignore[union-attr]
    await state.set_state(AddProduct.description)
    await m.answer("Описание (или «-», чтобы пропустить):")


@router.message(AddProduct.description)
async def add_description(m: Message, state: FSMContext):
    data = await state.get_data()
    await state.clear()
    desc = "" if (m.text or "").strip() == "-" else (m.text or "").strip()
    pid = await db.add_product(data["name"], Decimal(data["price"]), data["duration"] or None, desc)
    await m.answer("Товар добавлен ✅")
    await product_view(m, pid)


# ---------- Рефералы, статистика, настройки ----------

@router.callback_query(F.data == "adm:refs")
async def refs(c: CallbackQuery):
    rows = await db.top_referrers()
    pct = await db.get_setting("referral_percent", "10")
    lines = [f"<b>Реферальная программа</b> — бонус {pct}%\n", "<b>Топ пригласивших:</b>"]
    shown = [r for r in rows if r["invited"] > 0]
    for i, r in enumerate(shown, 1):
        lines.append(f"{i}. {who(r['username'], r['telegram_id'], r['full_name'])} — {r['invited']} чел., бонусы {money(r['bonus_balance'])}")
    if not shown:
        lines.append("Пока никого.")
    await show(c, "\n".join(lines), back())


@router.callback_query(F.data == "adm:stats")
async def stats(c: CallbackQuery):
    s = await db.stats()
    text = (
        "<b>Статистика</b>\n\n"
        f"Заказов всего: {s['orders_total']} (не оплачено: {s['orders_new']}, оплачено: {s['orders_paid']})\n"
        f"Выручка: {money(s['revenue'])} (за сутки: {money(s['revenue_day'])})\n"
        f"Клиентов: {s['customers']} (за сутки: +{s['customers_day']})\n"
        f"Пришли по рефке: {s['referred']}\n"
        f"Начислено рефералам: {money(s['ref_rewards'])}"
    )
    await show(c, text, back())


@router.callback_query(F.data == "adm:settings")
async def settings(c: CallbackQuery, state: FSMContext):
    await state.clear()
    pct = await db.get_setting("referral_percent", "10")
    details = await db.get_setting("payment_details") or "не указаны"
    kb = InlineKeyboardBuilder()
    kb.button(text="💳 Изменить реквизиты", callback_data="adm:setpay")
    kb.button(text="✏️ Изменить % бонуса", callback_data="adm:setpct")
    kb.button(text="⬅️ Назад", callback_data="adm:menu")
    kb.adjust(1)
    await show(
        c,
        "<b>Настройки</b>\n\n"
        f"💳 Реквизиты для оплаты:\n{escape(details)}\n\n"
        f"🤝 Реферальный бонус: {pct}% от суммы оплаченного заказа",
        kb.as_markup(),
    )


@router.callback_query(F.data == "adm:setpay")
async def set_pay_start(c: CallbackQuery, state: FSMContext):
    await state.set_state(SetPayment.text)
    await c.message.answer(  # type: ignore[union-attr]
        "Отправьте реквизиты одним сообщением (карта/СБП, банк, получатель).\n/cancel — отмена"
    )
    await c.answer()


@router.message(SetPayment.text)
async def set_pay_save(m: Message, state: FSMContext):
    if not m.text or not m.text.strip():
        await m.answer("Нужен текст с реквизитами")
        return
    await state.clear()
    await db.set_setting("payment_details", m.text.strip())
    await m.answer("Реквизиты сохранены ✅", reply_markup=menu_kb())


@router.callback_query(F.data == "adm:setpct")
async def set_pct_start(c: CallbackQuery, state: FSMContext):
    await state.set_state(SetPercent.value)
    await c.message.answer("Новый процент бонуса (0–100):\n/cancel — отмена")  # type: ignore[union-attr]
    await c.answer()


@router.message(SetPercent.value)
async def set_pct_save(m: Message, state: FSMContext):
    v = parse_price(m.text)
    if v is None or v > 100:
        await m.answer("Нужно число от 0 до 100")
        return
    await state.clear()
    pct = f"{v.normalize():f}"
    await db.set_setting("referral_percent", pct)
    await m.answer(f"Готово: бонус {pct}% ✅", reply_markup=menu_kb())

