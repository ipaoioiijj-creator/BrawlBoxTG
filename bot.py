"""
BrawlBoxTG
Python 3.10+ | aiogram 3.x
Токен: переменная окружения BOT_TOKEN (или TELEGRAM_BOT_TOKEN).
База: data/brawlbox.sqlite3. Портреты необязательны: data/sprites/<brawler_id>.webp/png.
"""
from __future__ import annotations

import asyncio
import os
import random
import sqlite3
from contextlib import contextmanager
from pathlib import Path

from aiogram import Bot, Dispatcher, F
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError
from aiogram.filters import Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import (
    CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message,
)

ADMIN_ID = 8799426613
BOX_PRICE = 100
BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
SPRITES_DIR = DATA_DIR / "sprites"
DB_PATH = DATA_DIR / "brawlbox.sqlite3"

START_TEXT = (
    "BrawlBoxTG - симулятор открытия ящиков из игры Brawl Stars прямо в Telegram 🤯\n"
    "Открывай Мегаящики 🤩\n"
    "Попадай в лидерборды 🥳\n"
    "Собери всю коллекцию 🥸\n"
    "📰 Наш канал @BrawlBoxTG"
)

# Оригинальный состав глобального релиза. Редкости — классические игровые.
BRAWLERS = [
    ("shelly", "Шелли", "Стартовый", "starting"),
    ("nita", "Нита", "Редкий", "rare"),
    ("colt", "Кольт", "Редкий", "rare"),
    ("bull", "Булл", "Редкий", "rare"),
    ("brock", "Брок", "Редкий", "rare"),
    ("barley", "Барли", "Редкий", "rare"),
    ("poco", "Поко", "Редкий", "rare"),
    ("el_primo", "Эль Примо", "Редкий", "rare"),
    ("jessie", "Джесси", "Сверхредкий", "super_rare"),
    ("dynamike", "Динамайк", "Сверхредкий", "super_rare"),
    ("rico", "Рико", "Сверхредкий", "super_rare"),
    ("darryl", "Дэррил", "Сверхредкий", "super_rare"),
    ("penny", "Пенни", "Сверхредкий", "super_rare"),
    ("bo", "Бо", "Эпический", "epic"),
    ("piper", "Пайпер", "Эпический", "epic"),
    ("pam", "Пэм", "Эпический", "epic"),
    ("frank", "Фрэнк", "Эпический", "epic"),
    ("mortis", "Мортис", "Мифический", "mythic"),
    ("tara", "Тара", "Мифический", "mythic"),
    ("spike", "Спайк", "Легендарный", "legendary"),
    ("crow", "Ворон", "Легендарный", "legendary"),
    ("leon", "Леон", "Легендарный", "legendary"),
]
BY_ID = {item[0]: item for item in BRAWLERS}
RARITY_WEIGHTS = [
    ("coins_5", 30), ("coins_50", 15), ("coins_200", 5),
    ("rare", 25), ("super_rare", 16), ("epic", 5),
    ("mythic", 3), ("legendary", 1),
]

class AdminFlow(StatesGroup):
    block_target = State()
    unblock_target = State()
    coins_target = State()
    coins_amount = State()
    clear_target = State()

@contextmanager
def db():
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

def init_db():
    with db() as conn:
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
                PRIMARY KEY(user_id, brawler_id),
                FOREIGN KEY(user_id) REFERENCES users(user_id) ON DELETE CASCADE
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS bans (
                user_id INTEGER PRIMARY KEY,
                banned_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
        """)

def register_user(user):
    with db() as conn:
        conn.execute("""
            INSERT INTO users(user_id, username, first_name)
            VALUES (?, ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET
                username=excluded.username, first_name=excluded.first_name
        """, (user.id, user.username, user.first_name or ""))
        conn.execute(
            "INSERT OR IGNORE INTO collection(user_id, brawler_id) VALUES (?, 'shelly')",
            (user.id,),
        )

def is_banned(user_id: int) -> bool:
    with db() as conn:
        return conn.execute("SELECT 1 FROM bans WHERE user_id=?", (user_id,)).fetchone() is not None

def resolve_user(target: str):
    """Accept numeric Telegram ID or @username. Username lookup uses known bot users."""
    target = target.strip()
    with db() as conn:
        if target.isdigit():
            row = conn.execute("SELECT * FROM users WHERE user_id=?", (int(target),)).fetchone()
        else:
            username = target.lstrip("@").casefold()
            row = conn.execute(
                "SELECT * FROM users WHERE lower(username)=? ORDER BY user_id LIMIT 1",
                (username,),
            ).fetchone()
    return row

def home_keyboard(user_id: int):
    rows = [
        [InlineKeyboardButton(text="📦Открыть📦", callback_data="open_box")],
        [
            InlineKeyboardButton(text="📊Профиль📊", callback_data="profile"),
            InlineKeyboardButton(text="🏆Лидеры🏆", callback_data="leaders"),
        ],
        [
            InlineKeyboardButton(text="🛒Магазин🛒", callback_data="shop"),
            InlineKeyboardButton(text="❓Помощь❓", callback_data="help"),
        ],
    ]
    if user_id == ADMIN_ID:
        rows.append([InlineKeyboardButton(text="⚙️ Админ-панель", callback_data="admin")])
    return InlineKeyboardMarkup(inline_keyboard=rows)

def back_keyboard(user_id: int):
    rows = [[InlineKeyboardButton(text="⬅️ Назад", callback_data="home")]]
    if user_id == ADMIN_ID:
        rows.append([InlineKeyboardButton(text="⚙️ Админ-панель", callback_data="admin")])
    return InlineKeyboardMarkup(inline_keyboard=rows)

def admin_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🚫 Заблокировать", callback_data="admin:block")],
        [InlineKeyboardButton(text="📋 Чёрный список", callback_data="admin:blacklist")],
        [InlineKeyboardButton(text="🔓 Разблокировать", callback_data="admin:unblock")],
        [InlineKeyboardButton(text="🪙 Выдать монеты", callback_data="admin:coins")],
        [InlineKeyboardButton(text="🧹 Очистить данные", callback_data="admin:clear")],
        [InlineKeyboardButton(text="⬅️ Назад", callback_data="home")],
    ])

async def safe_edit(message: Message, text: str, markup=None):
    try:
        await message.edit_text(text, reply_markup=markup)
    except TelegramBadRequest:
        # Telegram не умеет превращать фото-сообщение в текстовое.
        try:
            await message.delete()
        except TelegramBadRequest:
            pass
        await message.answer(text, reply_markup=markup)

async def deny_if_banned(event) -> bool:
    user = event.from_user
    if not user or not is_banned(user.id):
        return False
    if isinstance(event, CallbackQuery):
        await event.answer("Ваш аккаунт заблокирован администрацией бота.", show_alert=True)
    elif isinstance(event, Message):
        await event.answer("Ваш аккаунт заблокирован администрацией бота.")
    return True

def get_profile(user_id: int):
    with db() as conn:
        user = conn.execute("SELECT * FROM users WHERE user_id=?", (user_id,)).fetchone()
        owned = conn.execute(
            "SELECT brawler_id FROM collection WHERE user_id=? ORDER BY obtained_at",
            (user_id,),
        ).fetchall()
    return user, [r["brawler_id"] for r in owned]

def pick_brawler(rarity: str):
    options = [b for b in BRAWLERS if b[3] == rarity]
    return random.choice(options) if options else None

async def deliver_brawler_result(bot: Bot, message: Message, brawler, user_id: int):
    brawler_id, name, rarity, _tier = brawler
    caption = f"👤Новый боец «{name}»\n💯 Редкость - {rarity.lower()}"
    # Спрайты необязательны. Поддерживаем webp и png; при отсутствии просто текст.
    image_path = next(
        (p for p in (SPRITES_DIR / f"{brawler_id}.webp",
                     SPRITES_DIR / f"{brawler_id}.png") if p.is_file()),
        None,
    )
    if image_path:
        try:
            await message.delete()
        except TelegramBadRequest:
            pass
        await bot.send_photo(
            chat_id=message.chat.id,
            photo=image_path.open("rb"),
            caption=caption,
            reply_markup=back_keyboard(user_id),
        )
    else:
        await safe_edit(message, caption, back_keyboard(user_id))

async def open_box(bot: Bot, callback: CallbackQuery):
    uid = callback.from_user.id
    register_user(callback.from_user)
    with db() as conn:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute("SELECT coins FROM users WHERE user_id=?", (uid,)).fetchone()
        if row["coins"] < BOX_PRICE:
            conn.rollback()
            await safe_edit(
                callback.message,
                "❌ Недостаточно монет для открытия! Нужно 100 монет!",
                back_keyboard(uid),
            )
            return
        conn.execute("UPDATE users SET coins=coins-? WHERE user_id=?", (BOX_PRICE, uid))
        drop = random.choices(
            [x[0] for x in RARITY_WEIGHTS],
            weights=[x[1] for x in RARITY_WEIGHTS],
            k=1,
        )[0]
        if drop.startswith("coins_"):
            amount = int(drop.split("_")[1])
            conn.execute("UPDATE users SET coins=coins+? WHERE user_id=?", (amount, uid))
            balance = conn.execute("SELECT coins FROM users WHERE user_id=?", (uid,)).fetchone()["coins"]
            conn.commit()
            await safe_edit(
                callback.message,
                f"Выпало {amount} монет!\nБаланс: {balance}",
                back_keyboard(uid),
            )
            return

        brawler = pick_brawler(drop)
        if brawler is None:
            conn.rollback()
            await safe_edit(callback.message, "Не удалось открыть ящик. Попробуй ещё раз.", back_keyboard(uid))
            return
        exists = conn.execute(
            "SELECT 1 FROM collection WHERE user_id=? AND brawler_id=?",
            (uid, brawler[0]),
        ).fetchone()
        if exists:
            # Дубликаты конвертируются в 50 монет.
            conn.execute("UPDATE users SET coins=coins+50 WHERE user_id=?", (uid,))
            balance = conn.execute("SELECT coins FROM users WHERE user_id=?", (uid,)).fetchone()["coins"]
            conn.commit()
            await safe_edit(
                callback.message,
                f"Повторный боец «{brawler[1]}».\nКомпенсация: 50 монет.\nБаланс: {balance}",
                back_keyboard(uid),
            )
            return
        conn.execute(
            "INSERT INTO collection(user_id, brawler_id) VALUES (?, ?)",
            (uid, brawler[0]),
        )
        conn.commit()

    await deliver_brawler_result(bot, callback.message, brawler, uid)

async def main():
    token = os.getenv("BOT_TOKEN") or os.getenv("TELEGRAM_BOT_TOKEN")
    if not token:
        raise RuntimeError("Добавь BOT_TOKEN в переменные окружения BotHost.")
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    SPRITES_DIR.mkdir(parents=True, exist_ok=True)
    init_db()

    bot = Bot(token=token, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    dp = Dispatcher(storage=MemoryStorage())

    @dp.message(Command("start"))
    async def start(message: Message, state: FSMContext):
        await state.clear()
        register_user(message.from_user)
        if await deny_if_banned(message):
            return
        await message.answer(START_TEXT, reply_markup=home_keyboard(message.from_user.id))

    @dp.message(StateFilter(None))
    async def message_guard(message: Message):
        if message.from_user:
            register_user(message.from_user)
            await deny_if_banned(message)

    @dp.callback_query()
    async def callback_router(callback: CallbackQuery, state: FSMContext):
        user = callback.from_user
        register_user(user)
        # Заблокированным оставляем возможность открыть только профиль, где виден 🚫.
        if is_banned(user.id) and callback.data != "profile":
            await callback.answer("Ваш аккаунт заблокирован администрацией бота.", show_alert=True)
            return

        data = callback.data or ""
        if data == "home":
            await callback.answer()
            await state.clear()
            await safe_edit(callback.message, START_TEXT, home_keyboard(user.id))
        elif data == "open_box":
            await callback.answer()
            # Никакого промежуточного меню: нажатие сразу открывает ящик.
            await open_box(bot, callback)
        elif data == "profile":
            await callback.answer()
            profile, owned = get_profile(user.id)
            username = f"@{user.username}" if user.username else (user.first_name or "Без имени")
            if is_banned(user.id):
                username += " 🚫"
            names = [BY_ID[x][1] for x in owned if x in BY_ID]
            coins = profile["coins"] if profile else 0
            text = f"😎 Имя - {username}\n🪙 Монет - {coins}\n\n🔷 Бойцы:\n" + ("\n".join(names) if names else "Пока нет бойцов")
            # Профиль заблокированного только для просмотра, кнопка назад не даёт действий.
            await safe_edit(callback.message, text, back_keyboard(user.id) if not is_banned(user.id) else None)
        elif data == "leaders":
            await callback.answer()
            await safe_edit(callback.message, "Лидеры пока не доступны", back_keyboard(user.id))
        elif data == "shop":
            await callback.answer()
            await safe_edit(callback.message, "Магазин пока недоступен", back_keyboard(user.id))
        elif data == "help":
            await callback.answer()
            await safe_edit(callback.message, "Помощь пока недоступна", back_keyboard(user.id))
        elif data == "admin" and user.id == ADMIN_ID:
            await callback.answer()
            await state.clear()
            await safe_edit(callback.message, "Админ-панель", admin_keyboard())
        elif data == "admin:blacklist" and user.id == ADMIN_ID:
            await callback.answer()
            with db() as conn:
                rows = conn.execute("""
                    SELECT b.user_id, u.username, u.first_name
                    FROM bans b LEFT JOIN users u ON u.user_id=b.user_id
                    ORDER BY b.banned_at
                """).fetchall()
            if rows:
                lines = []
                for row in rows:
                    label = f"@{row['username']}" if row["username"] else (row["first_name"] or "Без имени")
                    lines.append(f"{label} — {row['user_id']}")
                body = "Чёрный список:\n" + "\n".join(lines)
            else:
                body = "Чёрный список пуст."
            await safe_edit(callback.message, body, admin_keyboard())
        elif data.startswith("admin:") and user.id == ADMIN_ID:
            await callback.answer()
            await state.clear()
            action = data.split(":", 1)[1]
            prompts = {
                "block": ("Пришли @username или Telegram ID пользователя для блокировки.", AdminFlow.block_target),
                "unblock": ("Пришли @username или Telegram ID пользователя для разблокировки.", AdminFlow.unblock_target),
                "coins": ("Кому выдать монеты? Пришли @username или Telegram ID.", AdminFlow.coins_target),
                "clear": ("Чьи данные очистить? Пришли @username, Telegram ID или слово ВСЕ.", AdminFlow.clear_target),
            }
            if action in prompts:
                prompt, next_state = prompts[action]
                await state.set_state(next_state)
                await safe_edit(callback.message, prompt, admin_keyboard())
        else:
            await callback.answer()

    async def notify_user(user_id: int, text: str):
        try:
            await bot.send_message(user_id, text)
        except (TelegramForbiddenError, TelegramBadRequest):
            pass

    @dp.message(StateFilter(AdminFlow.block_target))
    async def admin_block(message: Message, state: FSMContext):
        if message.from_user.id != ADMIN_ID:
            await state.clear()
            return
        target = resolve_user(message.text or "")
        if not target:
            await message.answer("Пользователь не найден. Он должен хотя бы раз запустить бота. Попробуй ещё раз.")
            return
        if target["user_id"] == ADMIN_ID:
            await message.answer("Нельзя заблокировать администратора.")
            return
        with db() as conn:
            conn.execute("INSERT OR IGNORE INTO bans(user_id) VALUES (?)", (target["user_id"],))
        await notify_user(target["user_id"], "ваш аккаунт был заблокирован администрацией бота")
        await state.clear()
        await message.answer(f"Пользователь {target['user_id']} заблокирован.", reply_markup=admin_keyboard())

    @dp.message(StateFilter(AdminFlow.unblock_target))
    async def admin_unblock(message: Message, state: FSMContext):
        if message.from_user.id != ADMIN_ID:
            await state.clear()
            return
        target = resolve_user(message.text or "")
        if not target:
            await message.answer("Пользователь не найден. Проверь @username или ID.")
            return
        with db() as conn:
            cur = conn.execute("DELETE FROM bans WHERE user_id=?", (target["user_id"],))
        if cur.rowcount:
            await notify_user(target["user_id"], "ваш аккаунт разблокирован")
            result = f"Пользователь {target['user_id']} разблокирован."
        else:
            result = "Этот пользователь не находится в чёрном списке."
        await state.clear()
        await message.answer(result, reply_markup=admin_keyboard())

    @dp.message(StateFilter(AdminFlow.coins_target))
    async def admin_coins_target(message: Message, state: FSMContext):
        if message.from_user.id != ADMIN_ID:
            await state.clear()
            return
        target = resolve_user(message.text or "")
        if not target:
            await message.answer("Пользователь не найден. Проверь @username или ID.")
            return
        await state.update_data(target_id=target["user_id"])
        await state.set_state(AdminFlow.coins_amount)
        await message.answer("Сколько монет выдать? Введи целое неотрицательное число.")

    @dp.message(StateFilter(AdminFlow.coins_amount))
    async def admin_coins_amount(message: Message, state: FSMContext):
        if message.from_user.id != ADMIN_ID:
            await state.clear()
            return
        raw = (message.text or "").strip()
        if not raw.isdigit():
            await message.answer("Нужно указать целое неотрицательное число.")
            return
        amount = int(raw)
        data = await state.get_data()
        with db() as conn:
            conn.execute("UPDATE users SET coins=coins+? WHERE user_id=?", (amount, data["target_id"]))
        await state.clear()
        await message.answer(f"Выдано {amount} монет пользователю {data['target_id']}.", reply_markup=admin_keyboard())

    @dp.message(StateFilter(AdminFlow.clear_target))
    async def admin_clear(message: Message, state: FSMContext):
        if message.from_user.id != ADMIN_ID:
            await state.clear()
            return
        target_text = (message.text or "").strip()
        with db() as conn:
            if target_text.casefold() == "все":
                conn.execute("UPDATE users SET coins=0")
                conn.execute("DELETE FROM collection")
                conn.execute("INSERT INTO collection(user_id, brawler_id) SELECT user_id, 'shelly' FROM users")
                result = "Данные всех пользователей очищены. Баны сохранены."
            else:
                target = resolve_user(target_text)
                if not target:
                    await message.answer("Пользователь не найден. Проверь @username или ID.")
                    return
                conn.execute("UPDATE users SET coins=0 WHERE user_id=?", (target["user_id"],))
                conn.execute("DELETE FROM collection WHERE user_id=?", (target["user_id"],))
                conn.execute("INSERT OR IGNORE INTO collection(user_id, brawler_id) VALUES (?, 'shelly')", (target["user_id"],))
                result = f"Данные пользователя {target['user_id']} очищены. Блокировка сохранена."
        await state.clear()
        await message.answer(result, reply_markup=admin_keyboard())

    print("BrawlBoxTG запущен.")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
