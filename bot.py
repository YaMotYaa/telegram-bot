import asyncio
import json
import logging
import os
import sqlite3
from pathlib import Path

from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command, CommandStart
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

BASE = Path(__file__).resolve().parent
DB = BASE / 'bot_data.sqlite3'
LOG_CHANNEL_ID = -1004440719960
DEFAULT_BUTTONS = [
    ('catalog', '📦 Каталог', 'Каталог пока пуст. Администратор скоро добавит информацию.'),
    ('profile', '👤 Профиль', 'Добро пожаловать в профиль!'),
    ('support', '💬 Поддержка', 'Напишите администратору для получения помощи.'),
    ('info', 'ℹ️ Информация', 'Информация о проекте скоро появится.'),
]


def connect():
    con = sqlite3.connect(DB)
    con.row_factory = sqlite3.Row
    return con


def init_db():
    with connect() as con:
        con.execute('CREATE TABLE IF NOT EXISTS buttons (key TEXT PRIMARY KEY, label TEXT NOT NULL, body TEXT NOT NULL)')
        con.execute('CREATE TABLE IF NOT EXISTS admin_states (user_id INTEGER PRIMARY KEY, action TEXT NOT NULL, button_key TEXT NOT NULL)')
        for key, label, body in DEFAULT_BUTTONS:
            con.execute('INSERT OR IGNORE INTO buttons (key, label, body) VALUES (?, ?, ?)', (key, label, body))


def get_buttons():
    with connect() as con:
        return con.execute('SELECT key, label, body FROM buttons ORDER BY rowid').fetchall()


def main_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=r['label'], callback_data=f"open:{r['key']}")]
        for r in get_buttons()
    ])


def admin_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=f"✏️ {r['label']}", callback_data=f"edit:{r['key']}")]
        for r in get_buttons()
    ])


def edit_keyboard(key):
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text='Изменить название', callback_data=f'name:{key}')],
        [InlineKeyboardButton(text='Изменить текст', callback_data=f'body:{key}')],
    ])


def admin_id():
    try:
        return 435186547
    except ValueError:
        return 0


async def log_event(bot, text):
    try:
        await bot.send_message(LOG_CHANNEL_ID, text)
    except Exception:
        logging.exception('Не удалось отправить лог в канал')


dp = Dispatcher()


@dp.message(CommandStart())
async def start(message: Message, bot: Bot):
    await message.answer('Добро пожаловать! Выберите раздел:', reply_markup=main_keyboard())
    await log_event(bot, f'▶️ /start от пользователя {message.from_user.id}')


@dp.message(Command('id'))
async def show_id(message: Message):
    await message.answer(f'Ваш Telegram ID: {message.from_user.id}')


@dp.message(Command('admin'))
async def admin(message: Message):
    if message.from_user.id != admin_id():
        await message.answer('Нет доступа. Узнайте свой ID командой /id и укажите его в ADMIN_ID.')
        return
    await message.answer('⚙️ Выберите кнопку для редактирования:', reply_markup=admin_keyboard())


@dp.callback_query(F.data.startswith('open:'))
async def open_button(callback: CallbackQuery, bot: Bot):
    key = callback.data.split(':', 1)[1]
    with connect() as con:
        row = con.execute('SELECT * FROM buttons WHERE key=?', (key,)).fetchone()
    if not row:
        await callback.answer('Раздел не найден', show_alert=True)
        return
    await callback.message.answer(row['body'], reply_markup=main_keyboard())
    await callback.answer()
    await log_event(bot, f'🔘 {callback.from_user.id} открыл(а) {row["label"]}')


@dp.callback_query(F.data.startswith('edit:'))
async def edit_button(callback: CallbackQuery):
    if callback.from_user.id != admin_id():
        await callback.answer('Нет доступа', show_alert=True)
        return
    key = callback.data.split(':', 1)[1]
    await callback.message.answer('Что изменить?', reply_markup=edit_keyboard(key))
    await callback.answer()


@dp.callback_query(F.data.startswith(('name:', 'body:')))
async def ask_edit(callback: CallbackQuery):
    if callback.from_user.id != admin_id():
        await callback.answer('Нет доступа', show_alert=True)
        return
    action, key = callback.data.split(':', 1)
    with connect() as con:
        exists = con.execute('SELECT 1 FROM buttons WHERE key=?', (key,)).fetchone()
        if not exists:
            await callback.answer('Кнопка не найдена', show_alert=True)
            return
        con.execute('INSERT OR REPLACE INTO admin_states (user_id, action, button_key) VALUES (?, ?, ?)',
                    (callback.from_user.id, action, key))
    await callback.message.answer('Отправь новое название кнопки:' if action == 'name' else 'Отправь новый текст раздела:')
    await callback.answer()


@dp.message(F.text)
async def save_edit(message: Message, bot: Bot):
    if message.from_user.id != admin_id():
        return
    with connect() as con:
        state = con.execute('SELECT * FROM admin_states WHERE user_id=?', (message.from_user.id,)).fetchone()
        if not state:
            return
        value = message.text.strip()
        if not value or len(value) > (64 if state['action'] == 'name' else 3500):
            await message.answer('Слишком длинный или пустой текст. Попробуй короче.')
            return
        column = 'label' if state['action'] == 'name' else 'body'
        con.execute(f'UPDATE buttons SET {column}=? WHERE key=?', (value, state['button_key']))
        con.execute('DELETE FROM admin_states WHERE user_id=?', (message.from_user.id,))
    await message.answer('✅ Сохранено! Проверить: /start. Редактировать дальше: /admin')
    await log_event(bot, f'⚙️ Администратор изменил {state["button_key"]} ({state["action"]})')


async def main():
    logging.basicConfig(level=logging.INFO)
    token = os.environ.get('BOT_TOKEN', '').strip()
    if not token:
        token_file = BASE / 'token.txt'
        if token_file.exists():
            token = token_file.read_text(encoding='utf-8-sig').strip()
    if not token:
        raise SystemExit('Нет токена! Создайте token.txt рядом с bot.py и вставьте туда токен от BotFather.')
    init_db()
    bot = Bot(token=token)
    await dp.start_polling(bot)


if __name__ == '__main__':
    asyncio.run(main())
