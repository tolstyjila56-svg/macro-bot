"""Точка входа бота. Запуск: python main.py"""
import asyncio
import logging
import os

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from dotenv import load_dotenv

load_dotenv()

import admin  # noqa: E402
import db  # noqa: E402
import shop  # noqa: E402


async def main() -> None:
    logging.basicConfig(level=logging.INFO)
    await db.init()
    bot = Bot(os.environ["BOT_TOKEN"], default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    dp = Dispatcher()
    # Админ-роутер первым: его хендлеры срабатывают только для админа
    dp.include_router(admin.router)
    dp.include_router(shop.router)
    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
