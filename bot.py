"""
BrawlBoxTG — Telegram box-opening simulator.
Python 3.10+, aiogram 3, SQLite.

BOT_TOKEN is read from the environment (set it in BotHost settings).
Database and downloaded portraits are stored in ./data/.
"""
from __future__ import annotations

import asyncio
import os
import random
import sqlite3
from contextlib import contextmanager
from pathlib import Path

from aiogram import Bot, Dispatcher, F
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError
from aiogram.types import (
    CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup,
    InputMediaPhoto, Message,
)
from aiogram.client.default import DefaultBotProperties

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
SPRITES_DIR = DATA_DIR / "sprites"
DB_PATH = DATA_DIR / "brawlbox.sqlite3"
BOX_PRICE = 100

# Global-release roster (December 2018): 22 original brawlers.
# Rarity grouping follows the classic in-game rarity labels.
BRAWLERS = [
    {"id": "shelly", "name": "Шелли", "rarity": "Стартовый", "tier": "starting"},
    {"id": "nita", "name": "Нита", "rarity": "Редкий", "tier": "rare"},
    {"id": "colt", "name": "Кольт", "rarity": "Редкий", "tier": "rare"},
    {"id": "bull", "name": "Булл", "rarity": "Редкий", "tier": "rare"},
    {"id": "brock", "name": "Брок", "rarity": "Редкий", "tier": "rare"},
    {"id": "barley", "name": "Барли", "rarity": "Редкий", "tier": "rare"},
    {"id": "poco", "name": "Поко", "rarity": "Редкий", "tier": "rare"},
    {"id": "jessie", "name": "Джесси", "rarity": "Сверхредкий", "tier": "super_rare"},
    {"id": "dynamike", "name": "Динамайк", "rarity": "Сверхредкий", "tier": "super_rare"},
    {"id": "rico", "name": "Рико", "rarity": "Сверхредкий", "tier": "super_rare"},
    {"id": "darryl", "name": "Дэррил", "rarity": "Сверхредкий", "tier": "super_rare"},
    {"id": "penny", "name": "Пенни", "rarity": "Сверхредкий", "tier": "super_rare"},
    {"id": "bo", "name": "Бо", "rarity": "Эпический", "tier": "epic"},
    {"id": "piper", "name": "Пайпер", "rarity": "Эпический", "tier": "epic"},
    {"id": "pam", "name": "Пэм", "rarity": "Эпический", "tier": "epic"},
    {"id": "frank", "name": "Фрэнк", "rarity": "Эпический", "tier": "epic"},
    {"id": "mortis", "name": "Мортис", "rarity": "Мифический", "tier": "mythic"},
    {"id": "tara", "name": "Тара", "rarity": "Мифический", "tier": "mythic"},
    {"id": "spike", "name": "Спайк", "rarity": "Легендарный", "tier": "legendary"},
    {"id": "crow", "name": "Ворон", "rarity": "Легендарный", "tier": "legendary"},
    {"id": "leon", "name": "Леон", "rarity": "Легендарный", "tier": "legendary"},
    # El Primo was part of the launch roster too.
    {"id": "el_primo", "name": "Эль Примо", "rarity": "Редкий", "tier": "rare"},
]

RARITY_WEIGHTS = [
    ("coins_5", 30),
    ("coins_50", 15),
    ("coins_200", 5),
    ("rare", 25),
    ("super_rare", 16),
    ("epic", 5),
    ("mythic", 3),
    ("legendary", 1),
]

RARITY_LABELS = {
    "rare": "редкий",
    "super_rare": "сверхредкий",
    "epic": "эпический",
    "mythic": "мифический",
    "legendary": "легендарный",
}

RARITY_COLORS = {
    "starting": "#8A8A8A",
    "rare": "#43B5E5",
    "super_rare": "#A45BEA",
    "epic": "#E64AA6",
    "mythic": "#E58B35",
    "legendary": "#E9C62E",
}

MAIN_TEXT = (
    "BrawlBoxTG - симулятор открытия ящиков из игры Brawl Stars прямо в Telegram 🤯\n"
    "Открывай Мегаящики 🤩\n"
    "Попадай в лидерборды 🥳\n"
    "Собери всю коллекцию 🥸\n"
    "📰 Наш канал @BrawlBoxTG"
)

db_lock = asyncio.Lock()


@contextmanager
def db_connect():
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db() -> None:
    with db_connect() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                username TEXT,
                first_name TEXT NOT NULL DEFAULT '',
                coins INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS collection (
                user_id INTEGER NOT NULL,
                brawler_id TEXT NOT NULL,
                obtained_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (user_id, brawler_id),
                FOREIGN KEY (user_id) REFERENCES users(user_id) ON DELETE CASCADE
            )
        """)
        # Existing users receive the starter brawler exactly once.
        conn.execute("""
            INSERT OR IGNORE INTO users (user_id, username, first_name)
            SELECT user_id, username, first_name FROM users
        """)


def ensure_user(user) -> None:
    with db_connect() as conn:
        conn.execute(
            """INSERT INTO users(user_id, username, first_name, coins)
               VALUES (?, ?, ?, 0)
               ON CONFLICT(user_id) DO UPDATE SET
                 username=excluded.username, first_name=excluded.first_name""",
            (user.id, user.username, user.first_name or ""),
        )
        conn.execute(
            "INSERT OR IGNORE INTO collection(user_id, brawler_id) VALUES (?, 'shelly')",
            (user.id,),
        )


def get_profile(user_id: int):
    with db_connect() as conn:
        user = conn.execute("SELECT * FROM users WHERE user_id=?", (user_id,)).fetchone()
        rows = conn.execute(
            "SELECT brawler_id FROM collection WHERE user_id=? ORDER BY obtained_at, brawler_id",
            (user_id,),
        ).fetchall()
    return user, {row["brawler_id"] for row in rows}


def main_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📦Открыть📦", callback_data="open_box")],
        [
            InlineKeyboardButton(text="📊Профиль📊", callback_data="profile"),
            InlineKeyboardButton(text="🏆Лидеры🏆", callback_data="leaders"),
        ],
        [
            InlineKeyboardButton(text="🛒Магазин🛒", callback_data="shop"),
            InlineKeyboardButton(text="❓Помощь❓", callback_data="help"),
        ],
    ])


def back_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⬅️ Назад", callback_data="home")]
    ])


def open_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🎁 Открыть мегаящик — 100 🪙", callback_data="roll_box")],
        [InlineKeyboardButton(text="⬅️ Назад", callback_data="home")],
    ])


async def replace_with_photo(bot: Bot, message: Message, photo_path: Path,
                             caption: str, keyboard: InlineKeyboardMarkup) -> None:
    """Keep the chat tidy: replace the current menu message with one photo message."""
    try:
        await message.delete()
    except TelegramBadRequest:
        pass
    if photo_path.exists():
        await bot.send_photo(
            chat_id=message.chat.id,
            photo=photo_path.open("rb"),
            caption=caption,
            reply_markup=keyboard,
        )
    else:
        await bot.send_message(message.chat.id, caption, reply_markup=keyboard)


async def show_home(message: Message) -> None:
    try:
        await message.edit_text(MAIN_TEXT, reply_markup=main_keyboard())
    except TelegramBadRequest:
        # If the current message is a photo, replace it with the home text.
        try:
            await message.delete()
        except TelegramBadRequest:
            pass
        await message.answer(MAIN_TEXT, reply_markup=main_keyboard())


async def show_profile(bot: Bot, message: Message, user) -> None:
    ensure_user(user)
    profile, owned = get_profile(user.id)
    username = f"@{user.username}" if user.username else (user.first_name or "без имени")
    names = [b["name"] for b in BRAWLERS if b["id"] in owned]
    collection_text = ", ".join(names) if names else "Пока пусто"
    caption = (
        f"😎 Имя - {username}\n"
        f"🪙 Монет - {profile['coins']}\n\n"
        f"🔷 Бойцы:\n{collection_text}"
    )

    # Telegram profile photos are requested on demand and are never stored in SQLite.
    try:
        photos = await bot.get_user_profile_photos(user.id, limit=1)
        if photos.total_count:
            file = await bot.get_file(photos.photos[0][-1].file_id)
            # send_photo accepts Telegram file_id; no local copy is needed.
            try:
                await message.delete()
            except TelegramBadRequest:
                pass
            await bot.send_photo(
                chat_id=message.chat.id,
                photo=photos.photos[0][-1].file_id,
                caption=caption,
                reply_markup=back_keyboard(),
            )
            return
    except (TelegramBadRequest, TelegramForbiddenError):
        pass

    try:
        await message.edit_text(caption, reply_markup=back_keyboard())
    except TelegramBadRequest:
        try:
            await message.delete()
        except TelegramBadRequest:
            pass
        await message.answer(caption, reply_markup=back_keyboard())


def weighted_drop() -> str:
    return random.choices(
        [item[0] for item in RARITY_WEIGHTS],
        weights=[item[1] for item in RARITY_WEIGHTS],
        k=1,
    )[0]


async def open_box(bot: Bot, message: Message, user) -> None:
    ensure_user(user)
    async with db_lock:
        with db_connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("SELECT coins FROM users WHERE user_id=?", (user.id,)).fetchone()
            if row["coins"] < BOX_PRICE:
                conn.rollback()
                try:
                    await message.edit_text(
                        "❌ Недостаточно монет для открытия! Нужно 100 монет!",
                        reply_markup=back_keyboard(),
                    )
                except TelegramBadRequest:
                    await message.answer(
                        "❌ Недостаточно монет для открытия! Нужно 100 монет!",
                        reply_markup=back_keyboard(),
                    )
                return

            conn.execute("UPDATE users SET coins=coins-? WHERE user_id=?", (BOX_PRICE, user.id))
            drop = weighted_drop()

            if drop.startswith("coins_"):
                amount = int(drop.split("_")[1])
                conn.execute("UPDATE users SET coins=coins+? WHERE user_id=?", (amount, user.id))
                conn.commit()
                new_balance = conn.execute("SELECT coins FROM users WHERE user_id=?", (user.id,)).fetchone()
                result_text = (
                    f"🪙 Тебе выпало {amount} монет!\n"
                    f"Баланс: {new_balance['coins']} 🪙"
                )
                try:
                    await message.edit_text(result_text, reply_markup=back_keyboard())
                except TelegramBadRequest:
                    await message.answer(result_text, reply_markup=back_keyboard())
                return

            candidates = [b for b in BRAWLERS if b["tier"] == drop]
            if not candidates:
                conn.rollback()
                await message.answer("Не удалось открыть ящик. Попробуй ещё раз.", reply_markup=back_keyboard())
                return

            brawler = random.choice(candidates)
            owned = conn.execute(
                "SELECT 1 FROM collection WHERE user_id=? AND brawler_id=?",
                (user.id, brawler["id"]),
            ).fetchone()

            if owned:
                # Duplicate compensation: keep duplicate drops useful.
                duplicate_coins = 50
                conn.execute("UPDATE users SET coins=coins+? WHERE user_id=?", (duplicate_coins, user.id))
                conn.commit()
                balance = conn.execute("SELECT coins FROM users WHERE user_id=?", (user.id,)).fetchone()["coins"]
                result_text = (
                    f"🔁 Повторный боец — {brawler['name']}!\n"
                    f"Компенсация: +{duplicate_coins} 🪙\n"
                    f"Баланс: {balance} 🪙"
                )
                try:
                    await message.edit_text(result_text, reply_markup=back_keyboard())
                except TelegramBadRequest:
                    await message.answer(result_text, reply_markup=back_keyboard())
                return

            conn.execute(
                "INSERT INTO collection(user_id, brawler_id) VALUES (?, ?)",
                (user.id, brawler["id"]),
            )
            conn.commit()

    image_path = SPRITES_DIR / f"{brawler['id']}.png"
    caption = (
        f"👤 Новый боец «{brawler['name']}»\n"
        f"💯 Редкость - {RARITY_LABELS[drop]}"
    )
    await replace_with_photo(bot, message, image_path, caption, back_keyboard())


async def main() -> None:
    token = os.getenv("BOT_TOKEN") or os.getenv("TELEGRAM_BOT_TOKEN")
    if not token:
        raise RuntimeError("Не найден BOT_TOKEN. Добавь токен бота в переменные окружения BotHost.")

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    SPRITES_DIR.mkdir(parents=True, exist_ok=True)
    init_db()

    # Portraits are bundled in data/sprites; no network download is needed.

    bot = Bot(token=token, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    dp = Dispatcher()

    @dp.message(F.text == "/start")
    async def cmd_start(message: Message):
        ensure_user(message.from_user)
        await message.answer(MAIN_TEXT, reply_markup=main_keyboard())

    @dp.callback_query(F.data == "home")
    async def cb_home(callback: CallbackQuery):
        await callback.answer()
        await show_home(callback.message)

    @dp.callback_query(F.data == "open_box")
    async def cb_open(callback: CallbackQuery):
        await callback.answer()
        try:
            await callback.message.edit_text(
                "📦 **Мегаящик**\n\nСтоимость открытия — 100 🪙\nУдачи!",
                reply_markup=open_keyboard(),
                parse_mode=ParseMode.MARKDOWN,
            )
        except TelegramBadRequest:
            await callback.message.answer(
                "📦 **Мегаящик**\n\nСтоимость открытия — 100 🪙\nУдачи!",
                reply_markup=open_keyboard(),
                parse_mode=ParseMode.MARKDOWN,
            )

    @dp.callback_query(F.data == "roll_box")
    async def cb_roll(callback: CallbackQuery):
        await callback.answer()
        await open_box(bot, callback.message, callback.from_user)

    @dp.callback_query(F.data == "profile")
    async def cb_profile(callback: CallbackQuery):
        await callback.answer()
        await show_profile(bot, callback.message, callback.from_user)

    @dp.callback_query(F.data == "leaders")
    async def cb_leaders(callback: CallbackQuery):
        await callback.answer()
        try:
            await callback.message.edit_text("🏆 Лидеры пока недоступны.", reply_markup=back_keyboard())
        except TelegramBadRequest:
            await callback.message.answer("🏆 Лидеры пока недоступны.", reply_markup=back_keyboard())

    @dp.callback_query(F.data == "shop")
    async def cb_shop(callback: CallbackQuery):
        await callback.answer()
        try:
            await callback.message.edit_text("🛒 Магазин пока недоступен.", reply_markup=back_keyboard())
        except TelegramBadRequest:
            await callback.message.answer("🛒 Магазин пока недоступен.", reply_markup=back_keyboard())

    @dp.callback_query(F.data == "help")
    async def cb_help(callback: CallbackQuery):
        await callback.answer()
        try:
            await callback.message.edit_text("❓ Помощь пока недоступна.", reply_markup=back_keyboard())
        except TelegramBadRequest:
            await callback.message.answer("❓ Помощь пока недоступна.", reply_markup=back_keyboard())

    print("BrawlBoxTG запущен.")
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
