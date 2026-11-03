import asyncio
import logging
import os
import sqlite3
import secrets
import re
from datetime import datetime, timezone
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
        con.execute('CREATE TABLE IF NOT EXISTS users (id INTEGER PRIMARY KEY, name TEXT, username TEXT, invited_by INTEGER, referrals INTEGER NOT NULL DEFAULT 0, discount INTEGER NOT NULL DEFAULT 0, joined_at TEXT NOT NULL)')
        con.execute('CREATE TABLE IF NOT EXISTS promocodes (code TEXT PRIMARY KEY, uses_left INTEGER NOT NULL, active INTEGER NOT NULL DEFAULT 1)')
        con.execute('CREATE TABLE IF NOT EXISTS promo_uses (user_id INTEGER, code TEXT, PRIMARY KEY(user_id,code))')
        con.execute('CREATE TABLE IF NOT EXISTS purchases (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL, status TEXT NOT NULL DEFAULT "pending", created_at TEXT NOT NULL)')


def keyboard(items):
    return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=t, callback_data=d)] for t,d in items])

def register_user(user, inviter=None):
    uid = user.id
    with db() as con:
        existing = con.execute('SELECT id FROM users WHERE id=?', (uid,)).fetchone()
        if existing:
            con.execute('UPDATE users SET name=?, username=? WHERE id=?',
                        (user.full_name, user.username, uid))
            return
        valid_inviter = inviter if inviter and inviter != uid and con.execute(
            'SELECT 1 FROM users WHERE id=?', (inviter,)).fetchone() else None
        con.execute('INSERT INTO users(id,name,username,invited_by,joined_at) VALUES (?,?,?,?,?)',
                    (uid, user.full_name, user.username, valid_inviter,
                     datetime.now(timezone.utc).isoformat()))
        if valid_inviter:
            con.execute('UPDATE users SET referrals=referrals+1, discount=1 WHERE id=?',
                        (valid_inviter,))


def admin_menu():
    return keyboard([('📦 Категории и товары', 'admincats'),
                     ('🎟 Промокоды', 'adminpromos'),
                     ('👥 Скидки пользователей', 'admindiscounts'),
                     ('🧾 Подтверждение покупок', 'adminorders'),
                     ('🏠 Главное меню', 'home')])


def profile_keyboard():
    return keyboard([('🔗 Моя реферальная ссылка', 'referral'),
                     ('🎟 Активировать промокод', 'redeem'),
                     ('🏠 Главное меню', 'home')])


def profile_text(uid):
    with db() as con:
        u = con.execute('SELECT * FROM users WHERE id=?', (uid,)).fetchone()
        promos = con.execute('SELECT COUNT(*) AS n FROM promo_uses WHERE user_id=?', (uid,)).fetchone()['n']
    if not u:
        return 'Профиль пока не создан. Нажми /start.'
    return (f"👤 Твой уютный профиль ✨\\n\\n"
            f"🌟 Имя: {u['name']}\\n"
            f"🆔 ID: {uid}\\n"
            f"👯 Приглашено друзей: {u['referrals']}\\n"
            f"🎁 Реферальная скидка: {'10%' if u['discount'] else 'нет'}\\n"
            f"🎟 Активных промокодов: {promos}\\n"
            f"💡 Общая скидка не превышает 10%.")


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
    parts = (m.text or '').split(maxsplit=1)
    inviter = None
    if len(parts) == 2 and parts[1].startswith('ref_'):
        try:
            inviter = int(parts[1][4:])
        except ValueError:
            pass
    register_user(m.from_user, inviter)
    await m.answer('Добро пожаловать! Выберите раздел:', reply_markup=home())

@DP.message(Command('id'))
async def myid(m: Message):
    await m.answer(f'Ваш ID: {m.from_user.id}')

@DP.message(Command('admin'))
async def admin(m: Message):
    if m.from_user.id != ADMIN_ID:
        return await m.answer('Нет доступа')
    await m.answer('⚙️ Админка:', reply_markup=admin_menu())

@DP.callback_query()
async def callback(c: CallbackQuery):
    data = c.data or ''
    uid = c.from_user.id
    register_user(c.from_user)
    admin_action = data in ('admin','admincats','addcat','adminpromos','admindiscounts','adminorders') or data.startswith(('manage:', 'addproduct:', 'editproduct:', 'setqty:', 'delproduct:', 'delcat:'))
    if admin_action and uid != ADMIN_ID:
        return await c.answer('Нет доступа', show_alert=True)
    if data == 'home':
        await c.message.answer('Главное меню:', reply_markup=home())
    elif data == 'catalog':
        await c.message.answer('📦 Выберите категорию:', reply_markup=categories())
    elif data == 'admin':
        await c.message.answer('⚙️ Админка:', reply_markup=admin_menu())
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
        await c.message.answer(profile_text(uid), reply_markup=profile_keyboard())
    elif data == 'referral':
        me = await c.bot.get_me()
        link = f'https://t.me/{me.username}?start=ref_{uid}'
        await c.message.answer('💌 Твоя личная ссылка для друзей:\\n' + link +
                               '\\n\\nЗа каждого нового друга — скидка 10% на обычные товары. '
                               'Скидка не суммируется и списывается только после подтверждения покупки.',
                               reply_markup=profile_keyboard())
    elif data == 'redeem':
        with db() as con:
            con.execute('INSERT OR REPLACE INTO pending VALUES (?, ?, NULL)', (uid, 'redeem'))
        await c.message.answer('🎟 Напиши свой промокод одним сообщением. Отмена: /cancel')
    elif data == 'adminpromos':
        await c.message.answer('🎟 Создать промокод: /newpromo КОД КОЛИЧЕСТВО\\n'
                               'Отключить: /delpromo КОД\\n'
                               'Промокод даёт скидку 10% на обычные товары.')
    elif data == 'admindiscounts':
        await c.message.answer('👥 Проверить скидку: /discount ID\\n'
                               'Удалить скидку: /cleardiscount ID')
    elif data == 'adminorders':
        with db() as con:
            orders = con.execute("SELECT id,user_id FROM purchases WHERE status='pending' ORDER BY id DESC LIMIT 15").fetchall()
        msg = '🧾 Ожидают подтверждения:\\n' + ('\\n'.join(f"#{o['id']} — пользователь {o['user_id']}" for o in orders) if orders else 'Пока пусто')
        await c.message.answer(msg + '\\n\\nПодтвердить: /confirm НОМЕР\\nОтменить: /reject НОМЕР')
    elif data == 'support':
        await c.message.answer('💖 Приветик! Если что-то непонятно, возник вопросик или нужна помощь — не стесняйся, просто напиши @nikotinmal 💌\\nМы постараемся помочь! ✨', reply_markup=home())
    elif data == 'info':
        await c.message.answer('ℹ️ Здесь можно посмотреть актуальное наличие товаров.', reply_markup=home())
    await c.answer()

@DP.message(Command('cancel'))
async def cancel(m: Message):
    with db() as con:
        con.execute('DELETE FROM pending WHERE user_id=?', (m.from_user.id,))
    await m.answer('Отменено. /admin')

@DP.message(Command('newpromo'))
async def newpromo(m: Message):
    if m.from_user.id != ADMIN_ID:
        return
    args = (m.text or '').split()
    if len(args) != 3 or not re.fullmatch(r'[A-Za-z0-9_-]{3,24}', args[1]) or not args[2].isdigit() or not 1 <= int(args[2]) <= 100000:
        return await m.answer('Формат: /newpromo HELLO10 20')
    with db() as con:
        con.execute('INSERT OR REPLACE INTO promocodes(code,uses_left,active) VALUES (?,?,1)',
                    (args[1].upper(), int(args[2])))
    await m.answer('✅ Промокод создан (скидка 10%).')


@DP.message(Command('delpromo'))
async def delpromo(m: Message):
    if m.from_user.id != ADMIN_ID:
        return
    args = (m.text or '').split()
    if len(args) != 2:
        return await m.answer('Формат: /delpromo HELLO10')
    with db() as con:
        con.execute('UPDATE promocodes SET active=0 WHERE code=?', (args[1].upper(),))
    await m.answer('✅ Промокод отключён.')


@DP.message(Command('discount'))
async def discount(m: Message):
    if m.from_user.id != ADMIN_ID:
        return
    try:
        uid = int((m.text or '').split()[1])
    except (IndexError, ValueError):
        return await m.answer('Формат: /discount ID')
    with db() as con:
        u = con.execute('SELECT * FROM users WHERE id=?', (uid,)).fetchone()
        p = con.execute('SELECT COUNT(*) AS n FROM promo_uses WHERE user_id=?', (uid,)).fetchone()['n']
    await m.answer(f"Пользователь {uid}: реферальная скидка {'10%' if u and u['discount'] else 'нет'}, промокодов: {p}" if u else 'Пользователь не найден.')


@DP.message(Command('cleardiscount'))
async def cleardiscount(m: Message):
    if m.from_user.id != ADMIN_ID:
        return
    try:
        uid = int((m.text or '').split()[1])
    except (IndexError, ValueError):
        return await m.answer('Формат: /cleardiscount ID')
    with db() as con:
        con.execute('UPDATE users SET discount=0 WHERE id=?', (uid,))
        con.execute('DELETE FROM promo_uses WHERE user_id=?', (uid,))
    await m.answer('✅ Скидки пользователя сброшены.')


@DP.message(Command('purchase'))
async def purchase(m: Message):
    register_user(m.from_user)
    with db() as con:
        active = con.execute('SELECT 1 FROM purchases WHERE user_id=? AND status="pending"', (m.from_user.id,)).fetchone()
        if active:
            return await m.answer('🧾 У тебя уже есть заявка на подтверждение.')
        cur = con.execute('INSERT INTO purchases(user_id,created_at) VALUES (?,?)',
                          (m.from_user.id, datetime.now(timezone.utc).isoformat()))
        oid = cur.lastrowid
    await m.answer(f'🧾 Заявка #{oid} создана. Скидка пока не списана.')
    await m.bot.send_message(ADMIN_ID, f'🧾 Новая заявка #{oid} от {m.from_user.id}\\nПодтвердить: /confirm {oid}\\nОтклонить: /reject {oid}')


@DP.message(Command('confirm', 'reject'))
async def order_action(m: Message):
    if m.from_user.id != ADMIN_ID:
        return
    try:
        oid = int((m.text or '').split()[1])
    except (IndexError, ValueError):
        return await m.answer('Укажи номер заявки.')
    confirmed = (m.text or '').split()[0].split('@')[0] == '/confirm'
    with db() as con:
        order = con.execute("SELECT * FROM purchases WHERE id=? AND status='pending'", (oid,)).fetchone()
        if not order:
            return await m.answer('Заявка не найдена или уже обработана.')
        uid = order['user_id']
        con.execute('UPDATE purchases SET status=? WHERE id=?', ('confirmed' if confirmed else 'rejected', oid))
        if confirmed:
            con.execute('UPDATE users SET discount=0 WHERE id=?', (uid,))
            con.execute('DELETE FROM promo_uses WHERE user_id=?', (uid,))
    await m.answer('✅ Покупка подтверждена, скидка списана.' if confirmed else '❌ Заявка отклонена, скидка сохранена.')
    try:
        await m.bot.send_message(uid, '🎉 Покупка подтверждена! Использованная скидка списана.' if confirmed else 'Заявка отклонена. Скидка сохранена.')
    except Exception:
        logging.exception('Не удалось уведомить пользователя')


@DP.message(F.text)
async def text_handler(m: Message):
    register_user(m.from_user)
    with db() as con:
        state = con.execute('SELECT * FROM pending WHERE user_id=?', (m.from_user.id,)).fetchone()
        if not state:
            return
        if state['action'] == 'redeem':
            code = (m.text or '').strip().upper()
            promo = con.execute('SELECT * FROM promocodes WHERE code=? AND active=1 AND uses_left>0', (code,)).fetchone()
            used = con.execute('SELECT 1 FROM promo_uses WHERE user_id=? AND code=?', (m.from_user.id, code)).fetchone()
            if not promo or used:
                return await m.answer('😿 Промокод недействителен или уже использован.')
            con.execute('INSERT INTO promo_uses(user_id,code) VALUES (?,?)', (m.from_user.id, code))
            con.execute('UPDATE promocodes SET uses_left=uses_left-1 WHERE code=?', (code,))
            con.execute('DELETE FROM pending WHERE user_id=?', (m.from_user.id,))
            return await m.answer('🎉 Промокод активирован! Скидка 10% на обычные товары. '
                                  'Она не суммируется с реферальной и списывается после подтверждения покупки.',
                                  reply_markup=profile_keyboard())
        if m.from_user.id != ADMIN_ID:
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
