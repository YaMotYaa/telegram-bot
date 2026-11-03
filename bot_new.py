import asyncio
import logging
import os
import sqlite3
from pathlib import Path
from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command, CommandStart
from aiogram.types import Message, CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup

BASE = Path(__file__).resolve().parent
DB = BASE / 'bot_data.sqlite3'
ADMIN_ID = 435186547
DP = Dispatcher()

def db():
    con = sqlite3.connect(DB)
    con.row_factory = sqlite3.Row
    return con

def init():
    with db() as con:
        con.execute('CREATE TABLE IF NOT EXISTS categories (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL UNIQUE)')
        con.execute('CREATE TABLE IF NOT EXISTS products (id INTEGER PRIMARY KEY AUTOINCREMENT, category_id INTEGER NOT NULL, name TEXT NOT NULL, quantity INTEGER NOT NULL DEFAULT 0)')
        con.execute('CREATE TABLE IF NOT EXISTS pending (user_id INTEGER PRIMARY KEY, action TEXT NOT NULL, category_id INTEGER)')

def keyboard(items):
    return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=t, callback_data=d)] for t,d in items])

def home():
    return keyboard([('📦 Каталог', 'catalog'), ('👤 Профиль', 'profile'), ('💬 Поддержка', 'support'), ('ℹ️ Информация', 'info')])

def categories(admin=False):
    with db() as con:
        rows = con.execute('SELECT * FROM categories ORDER BY name').fetchall()
    buttons = [(r['name'], f"{'manage' if admin else 'category'}:{r['id']}") for r in rows]
    if admin:
        buttons += [('➕ Добавить категорию', 'addcat'), ('⬅️ Админка', 'admin')]
    else:
        buttons += [('⬅️ Главное меню', 'home')]
    return keyboard(buttons)

def products(category_id, admin=False):
    with db() as con:
        rows = con.execute('SELECT * FROM products WHERE category_id=? ORDER BY name', (category_id,)).fetchall()
    buttons = [(f"{'✅' if r['quantity'] else '❌'} {r['name']} — {r['quantity']} шт.", f"{'editproduct' if admin else 'product'}:{r['id']}") for r in rows]
    if admin:
        buttons += [('➕ Добавить товар', f'addproduct:{category_id}'), ('🗑 Удалить категорию', f'delcat:{category_id}'), ('⬅️ Категории', 'admincats')]
    else:
        buttons += [('⬅️ Категории', 'catalog')]
    return keyboard(buttons)

@DP.message(CommandStart())
async def start(m: Message):
    await m.answer('Добро пожаловать! Выберите раздел:', reply_markup=home())

@DP.message(Command('id'))
async def myid(m: Message):
    await m.answer(f'Ваш ID: {m.from_user.id}')

@DP.message(Command('admin'))
async def admin(m: Message):
    if m.from_user.id != ADMIN_ID:
        return await m.answer('Нет доступа')
    await m.answer('⚙️ Управление каталогом:', reply_markup=keyboard([('📦 Категории и товары', 'admincats'), ('⬅️ Главное меню', 'home')]))

@DP.callback_query()
async def callback(c: CallbackQuery):
    data = c.data or ''
    uid = c.from_user.id
    admin_action = data in ('admin','admincats','addcat') or data.startswith(('manage:', 'addproduct:', 'editproduct:', 'setqty:', 'delproduct:', 'delcat:'))
    if admin_action and uid != ADMIN_ID:
        return await c.answer('Нет доступа', show_alert=True)
    if data == 'home':
        await c.message.answer('Главное меню:', reply_markup=home())
    elif data == 'catalog':
        await c.message.answer('📦 Выберите категорию:', reply_markup=categories())
    elif data == 'admin':
        await c.message.answer('⚙️ Админка:', reply_markup=keyboard([('📦 Управление каталогом', 'admincats')]))
    elif data == 'admincats':
        await c.message.answer('⚙️ Категории:', reply_markup=categories(True))
    elif data.startswith(('category:', 'manage:')):
        catid = int(data.split(':')[1]); is_admin = data.startswith('manage:')
        with db() as con:
            row = con.execute('SELECT name FROM categories WHERE id=?', (catid,)).fetchone()
        if row:
            await c.message.answer(f"📦 {row['name']}", reply_markup=products(catid, is_admin))
    elif data == 'addcat':
        with db() as con:
            con.execute('INSERT OR REPLACE INTO pending VALUES (?, ?, NULL)', (uid, 'category'))
        await c.message.answer('Напиши название новой категории. Отмена: /cancel')
    elif data.startswith('addproduct:'):
        catid = int(data.split(':')[1])
        with db() as con:
            con.execute('INSERT OR REPLACE INTO pending VALUES (?, ?, ?)', (uid, 'product', catid))
        await c.message.answer('Напиши название товара. Отмена: /cancel')
    elif data.startswith(('product:', 'editproduct:')):
        pid = int(data.split(':')[1]); is_admin = data.startswith('editproduct:')
        with db() as con:
            p = con.execute('SELECT * FROM products WHERE id=?', (pid,)).fetchone()
        if p:
            msg = f"{p['name']}\n{'✅ В наличии' if p['quantity'] else '❌ Нет в наличии'}\nКоличество: {p['quantity']} шт."
            if is_admin:
                kb = keyboard([('✏️ Изменить количество', f'setqty:{pid}'), ('🗑 Удалить товар', f'delproduct:{pid}'), ('⬅️ Назад', f"manage:{p['category_id']}")])
            else:
                kb = keyboard([('⬅️ Назад', f"category:{p['category_id']}")])
            await c.message.answer(msg, reply_markup=kb)
    elif data.startswith('setqty:'):
        pid = int(data.split(':')[1])
        with db() as con:
            con.execute('INSERT OR REPLACE INTO pending VALUES (?, ?, ?)', (uid, 'quantity', pid))
        await c.message.answer('Введи количество (0 = нет в наличии). Отмена: /cancel')
    elif data.startswith('delproduct:'):
        pid = int(data.split(':')[1])
        with db() as con:
            p = con.execute('SELECT category_id FROM products WHERE id=?', (pid,)).fetchone()
            if p:
                con.execute('DELETE FROM products WHERE id=?', (pid,))
        await c.message.answer('✅ Товар удалён.', reply_markup=products(p['category_id'], True) if p else categories(True))
    elif data.startswith('delcat:'):
        catid = int(data.split(':')[1])
        with db() as con:
            con.execute('DELETE FROM products WHERE category_id=?', (catid,))
            con.execute('DELETE FROM categories WHERE id=?', (catid,))
        await c.message.answer('✅ Категория и её товары удалены.', reply_markup=categories(True))
    elif data == 'profile':
        await c.message.answer(f'👤 Ваш Telegram ID: {uid}', reply_markup=home())
    elif data == 'support':
        await c.message.answer('💬 По вопросам работы каталога обратитесь к администратору.', reply_markup=home())
    elif data == 'info':
        await c.message.answer('ℹ️ Здесь можно посмотреть актуальное наличие товаров.', reply_markup=home())
    await c.answer()

@DP.message(Command('cancel'))
async def cancel(m: Message):
    with db() as con:
        con.execute('DELETE FROM pending WHERE user_id=?', (m.from_user.id,))
    await m.answer('Отменено. /admin')

@DP.message(F.text)
async def text_handler(m: Message):
    if m.from_user.id != ADMIN_ID:
        return
    with db() as con:
        state = con.execute('SELECT * FROM pending WHERE user_id=?', (m.from_user.id,)).fetchone()
        if not state:
            return
        value = m.text.strip()
        if not value or len(value) > 100:
            return await m.answer('Введите текст от 1 до 100 символов.')
        if state['action'] == 'category':
            try:
                con.execute('INSERT INTO categories(name) VALUES (?)', (value,))
            except sqlite3.IntegrityError:
                return await m.answer('Такая категория уже существует. Введи другое название.')
            reply = '✅ Категория добавлена!'
        elif state['action'] == 'product':
            con.execute('INSERT INTO products(category_id, name, quantity) VALUES (?, ?, 0)', (state['category_id'], value))
            reply = '✅ Товар добавлен! Теперь установи количество через /admin.'
        else:
            try:
                qty = int(value)
                if qty < 0 or qty > 1000000:
                    raise ValueError
            except ValueError:
                return await m.answer('Введи целое число от 0 до 1000000.')
            con.execute('UPDATE products SET quantity=? WHERE id=?', (qty, state['category_id']))
            reply = '✅ Количество обновлено!'
        con.execute('DELETE FROM pending WHERE user_id=?', (m.from_user.id,))
    await m.answer(reply, reply_markup=keyboard([('⚙️ Каталог', 'admincats'), ('🏠 Главное меню', 'home')]))

async def main():
    logging.basicConfig(level=logging.INFO)
    token = os.getenv('BOT_TOKEN', '').strip()
    if not token and (BASE / 'token.txt').exists():
        token = (BASE / 'token.txt').read_text(encoding='utf-8-sig').strip()
    if not token:
        raise SystemExit('BOT_TOKEN не задан')
    init()
    await DP.start_polling(Bot(token=token))

if __name__ == '__main__':
    asyncio.run(main())
