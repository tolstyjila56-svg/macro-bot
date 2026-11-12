"""Клиентская часть бота: приветствие с фото, покупка, «Я оплатил», мои заказы, рефералы."""
from html import escape

from aiogram import Bot, F, Router
from aiogram.filters import CommandObject, CommandStart
from aiogram.types import CallbackQuery, FSInputFile, KeyboardButton, Message, ReplyKeyboardMarkup, User
from aiogram.utils.keyboard import InlineKeyboardBuilder

import db
from admin import notify_admins_new_order
from common import PAYMENT_CONTACT, WELCOME_IMAGE, is_admin, money, who

router = Router()

BTN_BUY = "🛒 Купить макрос"
BTN_ORDERS = "📦 Мои заказы"
BTN_REFS = "🤝 Рефералы"
BTN_ADMIN = "⚙️ Админ-панель"

FEATURES = (
    "<b>Что умеет FreeToken?</b>\n"
    "• Автоматический поиск матча и повторный вход после завершения игры\n"
    "• Автоматический прыжок из автобуса по таймеру и плавный спуск\n"
    "• Ориентирование по карте и автоматическое движение в безопасную зону от бури\n"
    "• Распознавание застреваний и самостоятельный обход препятствий\n"
    "• Имитация действий игрока с перемещением на WASD, прыжками, ударами киркой и сменой слотов\n"
    "• Автоматический подбор предметов с земли на клавишу E\n"
    "• Автоматический выход в лобби"
)

DEFAULT_PAYMENT_DETAILS = "Реквизиты уточните у " + PAYMENT_CONTACT

# file_id фото после первой отправки — дальше Telegram не грузит файл заново
_photo_id: str | None = None


def main_kb(admin: bool) -> ReplyKeyboardMarkup:
    rows = [[KeyboardButton(text=BTN_BUY)], [KeyboardButton(text=BTN_ORDERS), KeyboardButton(text=BTN_REFS)]]
    if admin:
        rows.append([KeyboardButton(text=BTN_ADMIN)])
    return ReplyKeyboardMarkup(keyboard=rows, resize_keyboard=True)


async def ensure_customer(u: User, ref_code: str | None = None):
    return await db.get_or_create_customer(u.id, u.username, u.full_name, ref_code)


async def send_welcome(m: Message, admin: bool) -> None:
    global _photo_id
    product = await db.main_product()
    price = money(product["price"]) if product else "—"
    caption = f"👋 Привет, {escape(m.from_user.first_name or '')}!\n\n💰 Цена макроса — <b>{price}</b>\n\n{FEATURES}"  # type: ignore[union-attr]
    kb = InlineKeyboardBuilder()
    kb.button(text=f"🛒 Купить за {price}", callback_data="buy")
    sent = await m.answer_photo(_photo_id or FSInputFile(WELCOME_IMAGE), caption=caption, reply_markup=kb.as_markup())
    if sent.photo:
        _photo_id = sent.photo[-1].file_id
    # Отдельным сообщением ставим нижнее меню (к фото с инлайн-кнопкой его не прикрепить)
    await m.answer("Меню ниже 👇", reply_markup=main_kb(admin))


@router.message(CommandStart())
async def start(m: Message, command: CommandObject, bot: Bot):
    cust, created = await ensure_customer(m.from_user, command.args)  # type: ignore[arg-type]
    admin = is_admin(m.from_user)
    if admin:
        await db.add_admin_chat(m.chat.id)

    if created and cust["referred_by"]:
        ref_tg = await db.get_referrer_tg(cust["id"])
        if ref_tg:
            try:
                await bot.send_message(ref_tg, f"👋 По вашей ссылке пришёл {who(cust['username'], cust['telegram_id'], cust['full_name'])}!")
            except Exception:
                pass

    await send_welcome(m, admin)
    if admin:
        await m.answer("Вы администратор — панель: /admin или кнопка «⚙️ Админ-панель».")


@router.message(F.text == BTN_BUY)
async def buy_again(m: Message):
    await ensure_customer(m.from_user)  # type: ignore[arg-type]
    await send_welcome(m, is_admin(m.from_user))


@router.callback_query(F.data == "buy")
async def buy(c: CallbackQuery):
    product = await db.main_product()
    if not product:
        await c.answer("Продажи временно закрыты", show_alert=True)
        return
    details = await db.get_setting("payment_details", DEFAULT_PAYMENT_DETAILS)
    kb = InlineKeyboardBuilder()
    kb.button(text="✅ Я оплатил", callback_data="paid")
    await c.message.answer(  # type: ignore[union-attr]
        f"💳 <b>Оплата: {escape(product['name'])}</b>\n"
        f"Сумма: <b>{money(product['price'])}</b>\n\n"
        f"Переведите сумму по реквизитам:\n{escape(details)}\n\n"
        "После перевода нажмите кнопку «Я оплатил».",
        reply_markup=kb.as_markup(),
    )
    await c.answer()


@router.callback_query(F.data == "paid")
async def paid(c: CallbackQuery, bot: Bot):
    product = await db.main_product()
    if not product:
        await c.answer("Продажи временно закрыты", show_alert=True)
        return
    cust, _ = await ensure_customer(c.from_user)
    # Повторное нажатие не создаёт новый заказ, пока старый не обработан
    oid = await db.open_order(cust["id"])
    is_new = oid is None
    if is_new:
        oid = await db.create_order(cust["id"], product)

    await c.message.answer(  # type: ignore[union-attr]
        f"🧾 Заказ #{oid} принят.\n\n"
        f"Отправьте, пожалуйста, <b>скриншот и чек перевода</b> пользователю {PAYMENT_CONTACT}.\n"
        "После проверки оплаты бот пришлёт вам доступ."
    )
    await c.answer()
    if is_new:
        await notify_admins_new_order(bot, oid)


@router.message(F.text == BTN_ORDERS)
async def my_orders(m: Message):
    cust, _ = await ensure_customer(m.from_user)  # type: ignore[arg-type]
    orders = await db.customer_orders(cust["id"])
    if not orders:
        await m.answer("У вас пока нет заказов.")
        return
    lines = ["<b>Ваши заказы</b>"]
    for o in orders:
        lines.append(f"#{o['id']} · {escape(o['product_name'])} · {money(o['amount'])} · {db.STATUS_LABELS[o['status']]}")
    await m.answer("\n".join(lines))


@router.message(F.text == BTN_REFS)
async def referrals(m: Message, bot: Bot):
    cust, _ = await ensure_customer(m.from_user)  # type: ignore[arg-type]
    me = await bot.get_me()
    pct = await db.get_setting("referral_percent", "10")
    invited = await db.invited_count(cust["id"])
    link = f"https://t.me/{me.username}?start={cust['referral_code']}"
    await m.answer(
        "<b>Реферальная программа</b>\n\n"
        f"Приглашайте друзей и получайте {pct}% с каждой их оплаченной покупки.\n\n"
        f"Ваша ссылка:\n<code>{link}</code>\n\n"
        f"Приглашено: {invited}\nБонусный баланс: {money(cust['bonus_balance'])}"
    )
