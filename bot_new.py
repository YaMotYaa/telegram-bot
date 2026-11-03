import asyncio
import logging
import os
import sqlite3
import secrets
import html
import re
from datetime import datetime, timezone
from pathlib import Path
from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command, CommandStart
from aiogram.types import Message, CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, BotCommand

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
        con.execute('CREATE TABLE IF NOT EXISTS promo_history (user_id INTEGER NOT NULL, code TEXT NOT NULL, PRIMARY KEY(user_id,code))')
        con.execute('CREATE TABLE IF NOT EXISTS promo_uses (user_id INTEGER, code TEXT, PRIMARY KEY(user_id,code))')
        con.execute('DELETE FROM promo_uses WHERE rowid NOT IN (SELECT MIN(rowid) FROM promo_uses GROUP BY user_id)')
        con.execute('CREATE UNIQUE INDEX IF NOT EXISTS one_promo_per_user ON promo_uses(user_id)')
        cols = {r['name'] for r in con.execute('PRAGMA table_info(products)')}
        if 'description' not in cols:
            con.execute("ALTER TABLE products ADD COLUMN description TEXT NOT NULL DEFAULT ''")
        if 'photo_id' not in cols:
            con.execute("ALTER TABLE products ADD COLUMN photo_id TEXT")
        con.execute('CREATE TABLE IF NOT EXISTS bans (user_id INTEGER PRIMARY KEY, created_at TEXT NOT NULL)')
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
                     ('🚫 Бан пользователей', 'adminbans'),
                     ('🏠 Главное меню', 'home')])


def profile_keyboard():
    return keyboard([('🔗 Моя реферальная ссылка', 'referral'),
                     ('🎟 Активировать промокод', 'redeem'),
                     ('⬅️ Главное меню', 'home')])


def profile_text(uid):
    with db() as con:
        u = con.execute('SELECT * FROM users WHERE id=?', (uid,)).fetchone()
        promos = con.execute('SELECT COUNT(*) AS n FROM promo_uses WHERE user_id=?', (uid,)).fetchone()['n']
    if not u:
        return 'Профиль пока не создан. Отправьте команду /start.'
    return (f"👤 Ваш профиль\n\n"
            f"Имя: {html.escape(u['name'] or 'Не указано')}\n"
            f"Идентификатор: {uid}\n"
            f"Приглашено друзей: {u['referrals']}\n"
            f"Реферальная скидка: {'10%' if u['discount'] else 'нет'}\n"
            f"Активный промокод: {'есть' if promos else 'нет'}\n"
            f"Максимальная скидка: 10%.")


def is_banned(uid):
    with db() as con:
        return con.execute('SELECT 1 FROM bans WHERE user_id=?', (uid,)).fetchone() is not None


def product_buttons(p, admin):
    pid = p['id']
    back = f"manage:{p['category_id']}" if admin else f"category:{p['category_id']}"
    if not admin:
        return keyboard([('⬅️ Назад', back)])
    return keyboard([('📝 Изменить описание', f'desc:{pid}'),
                     ('📷 Добавить / заменить фото', f'photo:{pid}'),
                     ('🗑 Удалить фото', f'removephoto:{pid}'),
                     ('✏️ Изменить количество', f'setqty:{pid}'),
                     ('🗑 Удалить товар', f'delproduct:{pid}'),
                     ('⬅️ Назад', back)])


def product_caption(p):
    return (f"{p['name']}\n"
            f"{'В наличии' if p['quantity'] else 'Нет в наличии'}\n"
            f"Количество: {p['quantity']} шт.\n\n"
            f"Описание: {p['description'] or 'Не добавлено'}")


async def show_product(c, p, admin):
    # Фото отправляется отдельным сообщением; кнопки остаются у карточки.
    await c.message.edit_text(product_caption(p), reply_markup=product_buttons(p, admin))
    if p['photo_id']:
        await c.message.answer_photo(p['photo_id'], caption=f"Фото товара: {p['name']}")


def home():
    return keyboard([('📦 Каталог', 'catalog'), ('👤 Профиль', 'profile'), ('🧾 Мои покупки', 'mypurchases'), ('💬 Поддержка', 'support'), ('ℹ️ Информация', 'info')])

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
    if is_banned(m.from_user.id):
        return await m.answer('Доступ к боту ограничен.')
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
    if uid != ADMIN_ID and is_banned(uid):
        return await c.answer('Доступ к боту ограничен.', show_alert=True)
    register_user(c.from_user)
    admin_action = data in ('admin','admincats','addcat','adminpromos','admindiscounts','adminorders','adminbans') or data.startswith(('manage:', 'addproduct:', 'editproduct:', 'setqty:', 'delproduct:', 'delcat:', 'desc:', 'photo:', 'removephoto:'))
    if admin_action and uid != ADMIN_ID:
        return await c.answer('Нет доступа', show_alert=True)
    if data in ('home', 'profile', 'admin', 'catalog', 'admincats') or data.startswith(('editproduct:', 'manage:', 'category:')):
        with db() as con:
            con.execute('DELETE FROM pending WHERE user_id=?', (uid,))
    if data == 'home':
        await c.message.edit_text('Главное меню:', reply_markup=home())
    elif data == 'catalog':
        await c.message.edit_text('📦 Выберите категорию:', reply_markup=categories())
    elif data == 'admin':
        await c.message.edit_text('⚙️ Админка:', reply_markup=admin_menu())
    elif data == 'admincats':
        await c.message.edit_text('⚙️ Категории:', reply_markup=categories(True))
    elif data.startswith(('category:', 'manage:')):
        catid = int(data.split(':')[1]); is_admin = data.startswith('manage:')
        with db() as con:
            row = con.execute('SELECT name FROM categories WHERE id=?', (catid,)).fetchone()
        if row:
            await c.message.edit_text(f"📦 {row['name']}", reply_markup=products(catid, is_admin))
    elif data == 'addcat':
        with db() as con:
            con.execute('INSERT OR REPLACE INTO pending VALUES (?, ?, NULL)', (uid, 'category'))
        await c.message.edit_text('Напиши название новой категории. Отмена: /cancel')
    elif data.startswith('addproduct:'):
        catid = int(data.split(':')[1])
        with db() as con:
            con.execute('INSERT OR REPLACE INTO pending VALUES (?, ?, ?)', (uid, 'product', catid))
        await c.message.edit_text('Напиши название товара. Отмена: /cancel')
    elif data.startswith(('product:', 'editproduct:')):
        pid = int(data.split(':')[1]); is_admin = data.startswith('editproduct:')
        with db() as con:
            p = con.execute('SELECT * FROM products WHERE id=?', (pid,)).fetchone()
        if p:
            await show_product(c, p, is_admin)
    elif data.startswith(('desc:', 'photo:')):
        pid = int(data.split(':')[1])
        with db() as con:
            p = con.execute('SELECT id FROM products WHERE id=?', (pid,)).fetchone()
            if p:
                con.execute('INSERT OR REPLACE INTO pending VALUES (?, ?, ?)',
                            (uid, 'description' if data.startswith('desc:') else 'photo', pid))
        if p:
            await c.message.edit_text(
                'Отправьте текст описания (до 1000 символов).' if data.startswith('desc:')
                else 'Отправьте одну фотографию товара.',
                reply_markup=keyboard([('⬅️ Назад', f'editproduct:{pid}')]))
    elif data.startswith('removephoto:'):
        pid = int(data.split(':')[1])
        with db() as con:
            con.execute('UPDATE products SET photo_id=NULL WHERE id=?', (pid,))
            p = con.execute('SELECT * FROM products WHERE id=?', (pid,)).fetchone()
        if p:
            await show_product(c, p, True)
    elif data == 'adminbans':
        await c.message.edit_text('🚫 Управление доступом\n\n'
                                  'Заблокировать пользователя: /ban 123456789\n'
                                  'Разблокировать пользователя: /unban 123456789\n'
                                  'Проверить блокировку: /banstatus 123456789',
                                  reply_markup=keyboard([('⬅️ Назад', 'admin')]))
    elif data.startswith('setqty:'):
        pid = int(data.split(':')[1])
        with db() as con:
            con.execute('INSERT OR REPLACE INTO pending VALUES (?, ?, ?)', (uid, 'quantity', pid))
        await c.message.edit_text('Укажите количество товара. Если товара нет в наличии, введите 0. Для отмены отправьте /cancel.')
    elif data.startswith('delproduct:'):
        pid = int(data.split(':')[1])
        with db() as con:
            p = con.execute('SELECT category_id FROM products WHERE id=?', (pid,)).fetchone()
            if p:
                con.execute('DELETE FROM products WHERE id=?', (pid,))
        await c.message.edit_text('✅ Товар удалён.', reply_markup=products(p['category_id'], True) if p else categories(True))
    elif data.startswith('delcat:'):
        catid = int(data.split(':')[1])
        with db() as con:
            con.execute('DELETE FROM products WHERE category_id=?', (catid,))
            con.execute('DELETE FROM categories WHERE id=?', (catid,))
        await c.message.edit_text('✅ Категория и её товары удалены.', reply_markup=categories(True))
    elif data == 'profile':
        await c.message.edit_text(profile_text(uid), reply_markup=profile_keyboard())
    elif data == 'mypurchases':
        with db() as con:
            rows = con.execute('SELECT id,status,created_at FROM purchases WHERE user_id=? ORDER BY id DESC LIMIT 20', (uid,)).fetchall()
        statuses = {'pending':'Ожидает подтверждения', 'confirmed':'Подтверждена', 'rejected':'Отклонена'}
        msg = '🧾 Мои покупки\n\n'
        msg += ('\n\n'.join(f"Заявка №{r['id']}\nСтатус: {statuses.get(r['status'],r['status'])}\nДата: {r['created_at'][:10]}" for r in rows) if rows else 'Пока нет заявок на покупки.')
        await c.message.edit_text(msg, reply_markup=keyboard([('⬅️ Назад', 'home')]))
    elif data == 'referral':
        me = await c.bot.get_me()
        link = f'https://t.me/{me.username}?start=ref_{uid}'
        await c.message.edit_text('💌 Ваша персональная ссылка для приглашения друзей:\n' + link +
                               '\n\nЗа каждого нового приглашённого друга предоставляется скидка 10% на обычные товары. '
                               'Скидка не суммируется и списывается только после подтверждения покупки.',
                               reply_markup=keyboard([('⬅️ Назад', 'profile')]))
    elif data == 'redeem':
        with db() as con:
            con.execute('INSERT OR REPLACE INTO pending VALUES (?, ?, NULL)', (uid, 'redeem'))
        await c.message.edit_text('Введите промокод одним сообщением. Промокод может содержать латинские буквы и цифры.', reply_markup=keyboard([('⬅️ Назад', 'profile')]))
    elif data == 'adminpromos':
        await c.message.edit_text('🎟 Создать одноразовый промокод: /newpromo\n'
                               'Создать промокод с выбранным названием: /newpromo КОД 1\n'
                               'Отключить: /delpromo КОД\n'
                               'Промокод даёт скидку 10% на обычные товары.\nОдин активный код на пользователя.', reply_markup=keyboard([('⬅️ Назад', 'admin')]))
    elif data == 'admindiscounts':
        await c.message.edit_text('👥 Проверить скидку: /discount 123456789\n'
                               'Удалить скидку: /cleardiscount 123456789', reply_markup=keyboard([('⬅️ Назад', 'admin')]))
    elif data == 'adminorders':
        with db() as con:
            orders = con.execute("SELECT id,user_id FROM purchases WHERE status='pending' ORDER BY id DESC LIMIT 15").fetchall()
        msg = '🧾 Ожидают подтверждения:\n' + ('\n'.join(f"#{o['id']} — пользователь {o['user_id']}" for o in orders) if orders else 'Пока пусто')
        await c.message.edit_text(msg + '\n\nПодтвердить заявку: /confirm НОМЕР\nОтклонить заявку: /reject НОМЕР', reply_markup=keyboard([('⬅️ Назад', 'admin')]))
    elif data == 'support':
        await c.message.edit_text('💬 Поддержка\n\nПо вопросам работы каталога и наличия товаров обращайтесь к администратору: @nikotinmal.\n\nМы ответим на ваше сообщение при первой возможности.', reply_markup=keyboard([('⬅️ Назад', 'home')]))
    elif data == 'info':
        await c.message.edit_text('ℹ️ Здесь можно посмотреть актуальное наличие товаров.', reply_markup=keyboard([('⬅️ Назад', 'home')]))
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
    if len(args) == 1:
        code = 'GIFT-' + secrets.token_hex(4).upper()
        uses = 1
    elif len(args) == 3 and re.fullmatch(r'[A-Za-z0-9_-]{3,24}', args[1]) and args[2].isdigit() and 1 <= int(args[2]) <= 100000:
        code, uses = args[1].upper(), int(args[2])
    else:
        return await m.answer('Создать один уникальный промокод: /newpromo\nИли: /newpromo КОД 1')
    with db() as con:
        existing = con.execute('SELECT 1 FROM promocodes WHERE code=?', (code,)).fetchone()
        if existing:
            return await m.answer('Такой код уже существует. Выберите другой.')
        con.execute('INSERT INTO promocodes(code,uses_left,active) VALUES (?,?,1)', (code, uses))
    await m.answer('Промокод создан. Нажмите на код, чтобы скопировать:\n'
                   f'<code>{html.escape(code)}</code>\n'
                   f'Активаций: {uses}\nСкидка: 10%.',
                   parse_mode='HTML')


@DP.message(Command('delpromo'))
async def delpromo(m: Message):
    if m.from_user.id != ADMIN_ID:
        return
    args = (m.text or '').split()
    if len(args) != 2:
        return await m.answer('Пример: /delpromo КОД')
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
        return await m.answer('Укажите идентификатор пользователя: /discount 123456789')
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
        return await m.answer('Укажите идентификатор пользователя: /cleardiscount 123456789')
    with db() as con:
        con.execute('UPDATE users SET discount=0 WHERE id=?', (uid,))
        con.execute('DELETE FROM promo_uses WHERE user_id=?', (uid,))
    await m.answer('✅ Скидки пользователя сброшены.')


@DP.message(Command('purchase'))
async def purchase(m: Message):
    if is_banned(m.from_user.id):
        return await m.answer('Доступ к боту ограничен.')
    register_user(m.from_user)
    with db() as con:
        active = con.execute('SELECT 1 FROM purchases WHERE user_id=? AND status="pending"', (m.from_user.id,)).fetchone()
        if active:
            return await m.answer('🧾 У тебя уже есть заявка на подтверждение.')
        cur = con.execute('INSERT INTO purchases(user_id,created_at) VALUES (?,?)',
                          (m.from_user.id, datetime.now(timezone.utc).isoformat()))
        oid = cur.lastrowid
    await m.answer(f'🧾 Заявка #{oid} создана. Скидка пока не списана.')
    await m.bot.send_message(ADMIN_ID, f'🧾 Новая заявка #{oid} от {m.from_user.id}\nПодтвердить: /confirm {oid}\nОтклонить: /reject {oid}')


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


@DP.message(Command('ban', 'unban', 'banstatus'))
async def manage_ban(m: Message):
    if m.from_user.id != ADMIN_ID:
        return
    args = (m.text or '').split()
    try:
        uid = int(args[1])
        if uid <= 0 or uid == ADMIN_ID:
            raise ValueError
    except (IndexError, ValueError):
        return await m.answer('Укажите правильный числовой идентификатор пользователя.')
    command = args[0].split('@')[0]
    with db() as con:
        if command == '/ban':
            con.execute('INSERT OR IGNORE INTO bans VALUES (?,?)',
                        (uid, datetime.now(timezone.utc).isoformat()))
            response = 'Пользователь заблокирован.'
        elif command == '/unban':
            con.execute('DELETE FROM bans WHERE user_id=?', (uid,))
            response = 'Пользователь разблокирован.'
        else:
            response = ('Заблокирован.' if con.execute('SELECT 1 FROM bans WHERE user_id=?', (uid,)).fetchone() else 'Не заблокирован.')
    await m.answer(f'{uid}: {response}')


@DP.message(F.photo)
async def photo_handler(m: Message):
    if m.from_user.id != ADMIN_ID:
        return
    with db() as con:
        state = con.execute('SELECT * FROM pending WHERE user_id=?', (m.from_user.id,)).fetchone()
        if not state or state['action'] != 'photo':
            return await m.answer('Сначала откройте товар в админке и нажмите «Добавить фото».')
        pid = state['category_id']
        con.execute('UPDATE products SET photo_id=? WHERE id=?', (m.photo[-1].file_id, pid))
        con.execute('DELETE FROM pending WHERE user_id=?', (m.from_user.id,))
    await m.answer('Фотография сохранена.', reply_markup=keyboard([('⬅️ К товару', f'editproduct:{pid}')]))


@DP.message(F.text)
async def text_handler(m: Message):
    if m.from_user.id != ADMIN_ID and is_banned(m.from_user.id):
        return await m.answer('Доступ к боту ограничен.')
    register_user(m.from_user)
    with db() as con:
        state = con.execute('SELECT * FROM pending WHERE user_id=?', (m.from_user.id,)).fetchone()
        if not state:
            return
        if state['action'] == 'redeem':
            code = (m.text or '').strip().upper()
            promo = con.execute('SELECT * FROM promocodes WHERE code=? AND active=1 AND uses_left>0', (code,)).fetchone()
            used = con.execute('SELECT 1 FROM promo_uses WHERE user_id=?', (m.from_user.id,)).fetchone()
            old = con.execute('SELECT 1 FROM promo_history WHERE user_id=? AND code=?', (m.from_user.id,code)).fetchone()
            if not promo or used or old:
                return await m.answer('Промокод недействителен либо у вас уже есть активный промокод.')
            con.execute('INSERT INTO promo_uses(user_id,code) VALUES (?,?)', (m.from_user.id, code))
            con.execute('INSERT INTO promo_history(user_id,code) VALUES (?,?)', (m.from_user.id, code))
            con.execute('UPDATE promocodes SET uses_left=uses_left-1 WHERE code=?', (code,))
            con.execute('DELETE FROM pending WHERE user_id=?', (m.from_user.id,))
            return await m.answer('🎉 Промокод активирован! Скидка 10% на обычные товары. '
                                  'Она не суммируется с реферальной и списывается после подтверждения покупки.',
                                  reply_markup=keyboard([('⬅️ Назад', 'profile')]))
        if m.from_user.id != ADMIN_ID:
            return
        value = m.text.strip()
        if state['action'] == 'description':
            if len(value) > 1000:
                return await m.answer('Описание не должно превышать 1000 символов.')
            pid = state['category_id']
            con.execute('UPDATE products SET description=? WHERE id=?', (value, pid))
            con.execute('DELETE FROM pending WHERE user_id=?', (m.from_user.id,))
            return await m.answer('Описание сохранено.', reply_markup=keyboard([('⬅️ К товару', f'editproduct:{pid}')]))
        if state['action'] == 'photo':
            return await m.answer('Отправьте фотографию, а не текст.')
        if not value or len(value) > 100:
            return await m.answer('Введите текст от 1 до 100 символов.')
        if state['action'] == 'category':
            try:
                con.execute('INSERT INTO categories(name) VALUES (?)', (value,))
            except sqlite3.IntegrityError:
                return await m.answer('Такая категория уже существует. Укажите другое название.')
            reply = '✅ Категория добавлена!'
        elif state['action'] == 'product':
            con.execute('INSERT INTO products(category_id, name, quantity) VALUES (?, ?, 0)', (state['category_id'], value))
            reply = '✅ Товар добавлен! Количество товара можно указать в панели администратора.'
        else:
            try:
                qty = int(value)
                if qty < 0 or qty > 1000000:
                    raise ValueError
            except ValueError:
                return await m.answer('Введите целое число от 0 до 1 000 000.')
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
    bot = Bot(token=token)
    await bot.set_my_commands([BotCommand(command='start', description='Главное меню'),
                               BotCommand(command='purchase', description='Заявка на покупку'),
                               BotCommand(command='cancel', description='Отменить ввод')])
    await DP.start_polling(bot)

if __name__ == '__main__':
    asyncio.run(main())
