"""
Telegram-бот «Деньги под контролем» — личный финансовый трекер.

Возможности:
- Добавление расходов и доходов (через меню или быстрым вводом текста)
- Отчёты за день / неделю / месяц
- Графики: круговая диаграмма по категориям, линейный тренд, статус бюджетов
- Бюджеты по категориям с предупреждением о превышении
- Экспорт всех операций в CSV
- Управление категориями: добавить / удалить
- Список последних операций, удаление по id

Запуск:
    1. Установите зависимости:  pip install -r requirements.txt
    2. Создайте бота через @BotFather и получите токен
    3. Запишите токен в .env  (BOT_TOKEN=...)
    4. Запустите:  python bot.py
"""
import csv
import io
import logging
import os
import re
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv
from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    ReplyKeyboardMarkup,
    ReplyKeyboardRemove,
)
from telegram.constants import ParseMode
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    ConversationHandler,
    ContextTypes,
    filters,
)

import db
import charts
from config import (
    BOT_TOKEN,
    CURRENCY_SYMBOL,
    PAGE_SIZE,
    fmt,
    category_with_emoji,
    DEFAULT_EXPENSE_CATEGORIES,
    DEFAULT_INCOME_CATEGORIES,
)

load_dotenv()
TOKEN = os.getenv("BOT_TOKEN", BOT_TOKEN)

# Подавляем информационное предупреждение PTB о per_message=False
# (это ожидаемое поведение для нашей архитектуры)
import warnings as _warnings
try:
    from telegram.warnings import PTBUserWarning
    _warnings.filterwarnings("ignore", category=PTBUserWarning)
except Exception:
    pass

logging.basicConfig(
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    level=logging.INFO,
)
log = logging.getLogger("finance-bot")

# Состояния ConversationHandler
(
    ADD_EXPENSE_AMOUNT,
    ADD_EXPENSE_CATEGORY,
    ADD_EXPENSE_NOTE,
    ADD_INCOME_AMOUNT,
    ADD_INCOME_CATEGORY,
    ADD_INCOME_NOTE,
    ADD_CATEGORY_TYPE,
    ADD_CATEGORY_NAME,
    DEL_CATEGORY_TYPE,
    DEL_CATEGORY_NAME,
    BUDGET_CATEGORY,
    BUDGET_AMOUNT,
    QUICK_NOTE,
) = range(13)

# Разметка главного меню
MAIN_KEYBOARD = ReplyKeyboardMarkup(
    [
        ["➕ Расход", "➕ Доход"],
        ["📊 Отчёт", "📈 График"],
        ["💰 Бюджеты", "📁 Экспорт"],
        ["🗂 Категории", "🕘 Последние", "❓ Помощь"],
    ],
    resize_keyboard=True,
)


# ============================================================
# Утилиты
# ============================================================
def period_bounds(period: str) -> tuple[datetime, datetime]:
    now = datetime.now()
    if period == "day":
        start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        end = start + timedelta(days=1)
    elif period == "week":
        start = now - timedelta(days=now.weekday())
        start = start.replace(hour=0, minute=0, second=0, microsecond=0)
        end = start + timedelta(days=7)
    elif period == "month":
        start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        end = (start + timedelta(days=32)).replace(day=1)
    elif period == "year":
        start = now.replace(month=1, day=1, hour=0, minute=0, second=0, microsecond=0)
        end = now.replace(month=1, day=1, hour=0, minute=0, second=0, microsecond=0) + timedelta(days=366)
    else:
        start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        end = (start + timedelta(days=32)).replace(day=1)
    return start, end


def parse_amount(text: str) -> Optional[float]:
    """
    Парсит сумму из строки. Поддерживает:
        '1500', '1500.50', '1500,50', '1 500', '1 500,50',
        '1500₽', '1500 р', '1500 руб', '1500р', '1500 rub'
    """
    if text is None:
        return None
    text = str(text).strip().lower()
    # Удаляем обозначения валют везде, где они встретятся
    text = re.sub(r"(руб(лей|ля|ль)?)|р\.?|₽|rub|usd|\$|€|eur|грн|₴|uah", "", text)
    # Удаляем все пробелы (в т.ч. неразрывные)
    text = text.replace(" ", "").replace("\u00a0", "")
    text = text.replace(",", ".")
    try:
        amount = float(text)
        return amount if amount > 0 else None
    except ValueError:
        return None


# Регулярка для извлечения суммы в начале строки.
# Поддерживает разделитель тысяч (пробел между группами по 3 цифры) и
# десятичный разделитель — точку или запятую. Допускает символы валют сразу после суммы.
_AMOUNT_RE = re.compile(
    r"^\s*(\d{1,3}(?:[ \u00a0]\d{3})+(?:[.,]\d+)?|\d+(?:[.,]\d+)?)"
    r"\s*(руб(лей|ля|ль)?|р\.?|₽|rub|usd|\$|€|eur|грн|₴|uah)?\s*(.*)$",
    re.IGNORECASE | re.DOTALL,
)


def parse_quick_input(text: str) -> Optional[tuple[float, Optional[str], Optional[str]]]:
    """
    Парсит быстрый ввод вида:
        1500                  -> (1500, None, None)
        1500 продукты          -> (1500, 'продукты', None)
        1500 продукты хлеб     -> (1500, 'продукты', 'хлеб')
        1500.50 кафе обед      -> (1500.50, 'кафе', 'обед')
        1 500,50 продукты      -> (1500.50, 'продукты', None)
        1500₽, 1500 р, 1500 руб — символы валют отбрасываются
    """
    if not text:
        return None
    text = text.strip()
    m = _AMOUNT_RE.match(text)
    if not m:
        return None
    amount_str = m.group(1).replace(" ", "").replace("\u00a0", "").replace(",", ".")
    try:
        amount = float(amount_str)
    except ValueError:
        return None
    if amount <= 0:
        return None
    rest = m.group(4).strip() if m.group(4) else ""
    if not rest:
        return (amount, None, None)
    rest_parts = rest.split(maxsplit=1)
    category = rest_parts[0] if len(rest_parts) >= 1 else None
    note = rest_parts[1] if len(rest_parts) >= 2 else None
    return (amount, category, note)


def find_category(user_id: int, type_: str, query: str) -> Optional[str]:
    """Поиск категории по подстроке (без учёта регистра)."""
    cats = db.get_categories(user_id, type_)
    q = query.strip().lower()
    # Точное совпадение
    for c in cats:
        if c.lower() == q:
            return c
    # Частичное совпадение (содержит)
    for c in cats:
        if q in c.lower():
            return c
    return None


def categories_keyboard(user_id: int, type_: str, prefix: str) -> InlineKeyboardMarkup:
    cats = db.get_categories(user_id, type_)
    buttons = []
    for c in cats:
        buttons.append([InlineKeyboardButton(category_with_emoji(c), callback_data=f"{prefix}|{c}")])
    if prefix == "expcat":
        buttons.append([InlineKeyboardButton("➕ Новая категория", callback_data="expcat|__new__")])
    elif prefix == "inccat":
        buttons.append([InlineKeyboardButton("➕ Новая категория", callback_data="inccat|__new__")])
    buttons.append([InlineKeyboardButton("❌ Отмена", callback_data="cancel")])
    return InlineKeyboardMarkup(buttons)


def period_keyboard(prefix: str) -> InlineKeyboardMarkup:
    kb = [
        [
            InlineKeyboardButton("Сегодня", callback_data=f"{prefix}|day"),
            InlineKeyboardButton("Неделя", callback_data=f"{prefix}|week"),
        ],
        [
            InlineKeyboardButton("Месяц", callback_data=f"{prefix}|month"),
            InlineKeyboardButton("Год", callback_data=f"{prefix}|year"),
        ],
        [InlineKeyboardButton("❌ Отмена", callback_data="cancel")],
    ]
    return InlineKeyboardMarkup(kb)


# ============================================================
# /start, /help, /menu
# ============================================================
async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    db.ensure_user(user.id, user.username or "", user.first_name or "")
    text = (
        f"👋 Привет, {user.first_name or 'друг'}!\n\n"
        "Я — *Деньги под контролем*, бот для учёта личных финансов.\n\n"
        "Что я умею:\n"
        "➕ Записывать *расходы* и *доходы*\n"
        "📊 Показывать *отчёты* за день, неделю, месяц, год\n"
        "📈 Строить *графики* по категориям и динамику по дням\n"
        "💰 Контролировать *бюджеты* по категориям\n"
        "📁 Экспортировать операции в *CSV*\n"
        "🗂 Управлять *категориями*\n\n"
        "*Быстрый ввод:* просто напишите сумму и категорию:\n"
        "  `1500 продукты хлеб` — добавит расход 1500 ₽ в категорию «Продукты» с заметкой «хлеб»\n"
        "  `50000 зарплата` — добавит доход 50000 ₽\n\n"
        "Или используй кнопки меню ниже 👇"
    )
    await update.message.reply_text(
        text, parse_mode=ParseMode.MARKDOWN, reply_markup=MAIN_KEYBOARD
    )


async def cmd_help(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    text = (
        "*Команды и кнопки:*\n\n"
        "➕ *Расход* — добавить расход по шагам\n"
        "➕ *Доход* — добавить доход по шагам\n"
        "📊 *Отчёт* — сводка по периодам\n"
        "📈 *График* — диаграмма расходов/доходов\n"
        "💰 *Бюджеты* — установить лимит по категории\n"
        "📁 *Экспорт* — выгрузить все операции в CSV\n"
        "🗂 *Категории* — добавить или удалить категорию\n"
        "🕘 *Последние* — последние 10 операций\n\n"
        "*Быстрый ввод:*\n"
        "  `1500` — спросит категорию\n"
        "  `1500 продукты` — расход в «Продукты»\n"
        "  `1500 продукты хлеб` — с заметкой\n"
        "  `+50000 зарплата` — доход 50000 ₽\n\n"
        "Команды: /start /help /cancel /report /chart /budget /export /last"
    )
    await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN, reply_markup=MAIN_KEYBOARD)


async def cmd_cancel(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    await update.message.reply_text("Действие отменено.", reply_markup=MAIN_KEYBOARD)
    ctx.user_data.clear()
    return ConversationHandler.END


# ============================================================
# ГЛАВНОЕ МЕНЮ — кнопки ReplyKeyboard
# ============================================================
async def on_menu_button(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    text = update.message.text.strip()
    user = update.effective_user
    db.ensure_user(user.id, user.username or "", user.first_name or "")

    if text == "➕ Расход":
        return await start_add_expense(update, ctx)
    if text == "➕ Доход":
        return await start_add_income(update, ctx)
    if text == "📊 Отчёт":
        await show_report_menu(update, ctx)
        return ConversationHandler.END
    if text == "📈 График":
        await show_chart_menu(update, ctx)
        return ConversationHandler.END
    if text == "💰 Бюджеты":
        await show_budget_menu(update, ctx)
        return ConversationHandler.END
    if text == "📁 Экспорт":
        await export_csv(update, ctx)
        return ConversationHandler.END
    if text == "🗂 Категории":
        await show_categories_menu(update, ctx)
        return ConversationHandler.END
    if text == "🕘 Последние":
        await show_last(update, ctx)
        return ConversationHandler.END
    if text == "❓ Помощь":
        await cmd_help(update, ctx)
        return ConversationHandler.END
    return ConversationHandler.END


# ============================================================
# ДОБАВЛЕНИЕ РАСХОДА (ConversationHandler)
# ============================================================
async def start_add_expense(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    await update.message.reply_text(
        "Введи сумму расхода (например, `1500` или `1500.50`):",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=ReplyKeyboardRemove(),
    )
    return ADD_EXPENSE_AMOUNT


async def add_expense_amount(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    amount = parse_amount(update.message.text)
    if amount is None:
        await update.message.reply_text("Не понял сумму. Введи число, например: `1500`", parse_mode=ParseMode.MARKDOWN)
        return ADD_EXPENSE_AMOUNT
    ctx.user_data["exp_amount"] = amount
    kb = categories_keyboard(update.effective_user.id, "expense", "expcat")
    await update.message.reply_text("Выбери категорию:", reply_markup=kb)
    return ADD_EXPENSE_CATEGORY


async def add_expense_category(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    q = update.callback_query
    await q.answer()
    data = q.data
    if data == "cancel":
        await q.edit_message_text("Отменено.")
        await q.message.reply_text("Что дальше?", reply_markup=MAIN_KEYBOARD)
        return ConversationHandler.END

    parts = data.split("|", 1)
    cat = parts[1] if len(parts) > 1 else ""

    if cat == "__new__":
        ctx.user_data["adding_cat_for"] = "expense"
        await q.edit_message_text("Введи название новой категории:")
        return ADD_CATEGORY_NAME

    ctx.user_data["exp_category"] = cat
    await q.edit_message_text(f"Категория: *{cat}*\nТеперь введи заметку (или отправь «-» чтобы пропустить):", parse_mode=ParseMode.MARKDOWN)
    return ADD_EXPENSE_NOTE


async def add_expense_note(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    note = update.message.text.strip()
    if note == "-":
        note = ""
    user = update.effective_user
    amount = ctx.user_data["exp_amount"]
    cat = ctx.user_data["exp_category"]
    db.add_transaction(user.id, "expense", amount, cat, note)
    text = (
        f"✅ *Расход добавлен*\n\n"
        f"Сумма: {fmt(amount)}\n"
        f"Категория: {cat}\n"
        f"Заметка: {note if note else '—'}\n"
    )
    await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN, reply_markup=MAIN_KEYBOARD)

    # Проверяем бюджет
    status = db.get_budget_status(user.id, cat, "month")
    if status:
        if status["spent"] > status["budget"]:
            await update.message.reply_text(
                f"⚠️ *Внимание!* Бюджет по категории «{cat}» превышен!\n"
                f"Лимит: {fmt(status['budget'])}  |  Потрачено: {fmt(status['spent'])}  ({status['percent']:.0f}%)",
                parse_mode=ParseMode.MARKDOWN,
            )
        elif status["percent"] >= 80:
            await update.message.reply_text(
                f"🔔 *Напоминание:* по категории «{cat}» потрачено {status['percent']:.0f}% бюджета "
                f"({fmt(status['spent'])} из {fmt(status['budget'])}).",
                parse_mode=ParseMode.MARKDOWN,
            )
    ctx.user_data.clear()
    return ConversationHandler.END


# ============================================================
# ДОБАВЛЕНИЕ ДОХОДА
# ============================================================
async def start_add_income(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    await update.message.reply_text(
        "Введи сумму дохода (например, `50000`):",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=ReplyKeyboardRemove(),
    )
    return ADD_INCOME_AMOUNT


async def add_income_amount(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    amount = parse_amount(update.message.text)
    if amount is None:
        await update.message.reply_text("Не понял сумму. Введи число, например: `50000`", parse_mode=ParseMode.MARKDOWN)
        return ADD_INCOME_AMOUNT
    ctx.user_data["inc_amount"] = amount
    kb = categories_keyboard(update.effective_user.id, "income", "inccat")
    await update.message.reply_text("Выбери категорию дохода:", reply_markup=kb)
    return ADD_INCOME_CATEGORY


async def add_income_category(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    q = update.callback_query
    await q.answer()
    data = q.data
    if data == "cancel":
        await q.edit_message_text("Отменено.")
        await q.message.reply_text("Что дальше?", reply_markup=MAIN_KEYBOARD)
        return ConversationHandler.END

    parts = data.split("|", 1)
    cat = parts[1] if len(parts) > 1 else ""

    if cat == "__new__":
        ctx.user_data["adding_cat_for"] = "income"
        await q.edit_message_text("Введи название новой категории дохода:")
        return ADD_CATEGORY_NAME

    ctx.user_data["inc_category"] = cat
    await q.edit_message_text(f"Категория: *{cat}*\nЗаметка (или «-» чтобы пропустить):", parse_mode=ParseMode.MARKDOWN)
    return ADD_INCOME_NOTE


async def add_income_note(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    note = update.message.text.strip()
    if note == "-":
        note = ""
    user = update.effective_user
    amount = ctx.user_data["inc_amount"]
    cat = ctx.user_data["inc_category"]
    db.add_transaction(user.id, "income", amount, cat, note)
    text = (
        f"✅ *Доход добавлен*\n\n"
        f"Сумма: +{fmt(amount)}\n"
        f"Категория: {cat}\n"
        f"Заметка: {note if note else '—'}\n"
    )
    await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN, reply_markup=MAIN_KEYBOARD)
    ctx.user_data.clear()
    return ConversationHandler.END


# ============================================================
# БЫСТРЫЙ ВВОД — простое число или «сумма категория заметка»
# ============================================================
async def on_quick_text(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    """
    Срабатывает, если пользователь НЕ находится в режиме ConversationHandler
    и просто прислал текст. Ловим быстрые команды.
    """
    text = update.message.text.strip()
    if not text:
        return ConversationHandler.END

    # Если начинается с + или со слова "доход" — это доход
    is_income = False
    raw = text
    if text.startswith("+"):
        raw = text[1:].strip()
        is_income = True
    elif text.lower().startswith("доход "):
        raw = text[5:].strip()
        is_income = True
    elif text.lower().startswith("расход "):
        raw = text[7:].strip()

    parsed = parse_quick_input(raw)
    if parsed is None:
        # Просто число без категории — спросим категорию
        amount = parse_amount(raw)
        if amount is None:
            await update.message.reply_text(
                "Не понял. Используй формат `1500 продукты` или нажми кнопку «➕ Расход».",
                parse_mode=ParseMode.MARKDOWN,
                reply_markup=MAIN_KEYBOARD,
            )
            return ConversationHandler.END

        # Запоминаем и спрашиваем категорию
        if is_income:
            ctx.user_data["inc_amount"] = amount
            kb = categories_keyboard(update.effective_user.id, "income", "inccat")
            await update.message.reply_text(f"Сумма дохода: +{fmt(amount)}\nВыбери категорию:", reply_markup=kb)
            return ADD_INCOME_CATEGORY
        else:
            ctx.user_data["exp_amount"] = amount
            kb = categories_keyboard(update.effective_user.id, "expense", "expcat")
            await update.message.reply_text(f"Сумма: {fmt(amount)}\nВыбери категорию:", reply_markup=kb)
            return ADD_EXPENSE_CATEGORY

    amount, cat_query, note = parsed
    type_ = "income" if is_income else "expense"

    cat = None
    if cat_query:
        cat = find_category(update.effective_user.id, type_, cat_query)

    if cat is None:
        # Категория не найдена — спросим выбором
        if is_income:
            ctx.user_data["inc_amount"] = amount
            ctx.user_data["inc_note"] = note or ""
            kb = categories_keyboard(update.effective_user.id, "income", "inccat")
            await update.message.reply_text(
                f"Сумма: +{fmt(amount)}\nКатегория «{cat_query}» не найдена, выбери из списка:",
                reply_markup=kb,
            )
            # FIXME: заметка теряется — сохраним в user_data и используем позже
            return ADD_INCOME_CATEGORY
        else:
            ctx.user_data["exp_amount"] = amount
            ctx.user_data["exp_note"] = note or ""
            kb = categories_keyboard(update.effective_user.id, "expense", "expcat")
            await update.message.reply_text(
                f"Сумма: {fmt(amount)}\nКатегория «{cat_query}» не найдена, выбери из списка:",
                reply_markup=kb,
            )
            return ADD_EXPENSE_CATEGORY

    # Нашли категорию — сохраняем сразу
    db.add_transaction(update.effective_user.id, type_, amount, cat, note or "")
    sign = "+" if type_ == "income" else "−"
    label = "Доход" if type_ == "income" else "Расход"
    await update.message.reply_text(
        f"✅ *{label} добавлен*\n\n{sign}{fmt(amount)}  •  {cat}" + (f"\nЗаметка: {note}" if note else ""),
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=MAIN_KEYBOARD,
    )
    # Проверяем бюджет для расхода
    if type_ == "expense":
        status = db.get_budget_status(update.effective_user.id, cat, "month")
        if status and status["spent"] > status["budget"]:
            await update.message.reply_text(
                f"⚠️ Бюджет по «{cat}» превышен: {fmt(status['spent'])} из {fmt(status['budget'])}",
                parse_mode=ParseMode.MARKDOWN,
            )
    return ConversationHandler.END


# ============================================================
# ОТЧЁТЫ
# ============================================================
async def show_report_menu(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    kb = period_keyboard("rep")
    await update.message.reply_text("Выбери период для отчёта:", reply_markup=kb)


async def on_report_period(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    q = update.callback_query
    await q.answer()
    if q.data == "cancel":
        await q.edit_message_text("Отменено.")
        return
    period = q.data.split("|", 1)[1]
    start, end = period_bounds(period)
    user_id = update.effective_user.id

    exp, inc, bal = db.get_totals(user_id, start, end)
    exp_by_cat = db.get_summary_by_category(user_id, start, end, "expense")
    inc_by_cat = db.get_summary_by_category(user_id, start, end, "income")

    period_names = {"day": "сегодня", "week": "за неделю", "month": "за месяц", "year": "за год"}
    title = period_names.get(period, "за период")

    lines = [f"📊 *Отчёт {title}*\n_{start.strftime('%d.%m.%Y')} — {end.strftime('%d.%m.%Y')}_\n"]
    lines.append(f"Расходы: −{fmt(exp)}")
    lines.append(f"Доходы:  +{fmt(inc)}")
    lines.append(f"Баланс:  {fmt(bal)}\n")

    if exp_by_cat:
        lines.append("*Расходы по категориям:*")
        for c, a in exp_by_cat[:10]:
            lines.append(f"  • {c}: {fmt(a)}")
        lines.append("")
    if inc_by_cat:
        lines.append("*Доходы по категориям:*")
        for c, a in inc_by_cat[:10]:
            lines.append(f"  • {c}: +{fmt(a)}")
        lines.append("")

    await q.edit_message_text("\n".join(lines), parse_mode=ParseMode.MARKDOWN)


# ============================================================
# ГРАФИКИ
# ============================================================
async def show_chart_menu(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    kb = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("🥧 Расходы (круг)", callback_data="chart|pie_expense"),
            InlineKeyboardButton("🥧 Доходы (круг)", callback_data="chart|pie_income"),
        ],
        [InlineKeyboardButton("📈 Динамика 30 дней", callback_data="chart|trend")],
        [InlineKeyboardButton("💰 Бюджеты", callback_data="chart|budget")],
        [InlineKeyboardButton("❌ Отмена", callback_data="cancel")],
    ])
    await update.message.reply_text("Какой график показать?", reply_markup=kb)


async def on_chart_callback(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    q = update.callback_query
    await q.answer()
    if q.data == "cancel":
        await q.edit_message_text("Отменено.")
        return
    action = q.data.split("|", 1)[1]
    user_id = update.effective_user.id

    if action == "pie_expense" or action == "pie_income":
        type_ = "expense" if action == "pie_expense" else "income"
        start, end = period_bounds("month")
        path = charts.make_pie_chart(user_id, start, end, type_)
        if path is None:
            await q.edit_message_text("Нет данных за месяц для этого графика.")
            return
        await q.message.reply_photo(
            photo=open(path, "rb"),
            caption=f"{'Расходы' if type_=='expense' else 'Доходы'} за месяц",
        )
        await q.edit_message_text("Готово ✅")
    elif action == "trend":
        path = charts.make_trend_chart(user_id, days=30)
        if path is None:
            await q.edit_message_text("Нет данных за 30 дней.")
            return
        await q.message.reply_photo(photo=open(path, "rb"), caption="Динамика за 30 дней")
        await q.edit_message_text("Готово ✅")
    elif action == "budget":
        path = charts.make_budget_chart(user_id)
        if path is None:
            await q.edit_message_text("Бюджетов пока не задано. Установи через кнопку «💰 Бюджеты».")
            return
        await q.message.reply_photo(photo=open(path, "rb"), caption="Статус бюджетов")
        await q.edit_message_text("Готово ✅")


# ============================================================
# БЮДЖЕТЫ
# ============================================================
async def show_budget_menu(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    user_id = update.effective_user.id
    budgets = db.get_budgets(user_id)
    if budgets:
        text = "*Текущие бюджеты (месяц):*\n\n"
        for b in budgets:
            status = db.get_budget_status(user_id, b["category"], "month")
            if status:
                emoji = "✅" if status["percent"] < 80 else ("⚠️" if status["percent"] < 100 else "🚨")
                text += f"{emoji} {b['category']}: {fmt(status['spent'])} / {fmt(b['amount'])} ({status['percent']:.0f}%)\n"
        text += "\nВыбери действие:"
    else:
        text = "Бюджетов пока нет. Установи лимит по категории:"

    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("➕ Добавить бюджет", callback_data="budm_add")],
        [InlineKeyboardButton("🗑 Удалить бюджет", callback_data="budm_del")],
        [InlineKeyboardButton("❌ Отмена", callback_data="cancel")],
    ])
    await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN, reply_markup=kb)
    return ConversationHandler.END


async def on_budget_action(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    q = update.callback_query
    await q.answer()
    if q.data == "cancel":
        await q.edit_message_text("Отменено.")
        return ConversationHandler.END

    action = q.data  # expecting "budm_add" or "budm_del"
    if action == "budm_add":
        cats = db.get_categories(update.effective_user.id, "expense")
        if not cats:
            await q.edit_message_text("Нет категорий. Сначала добавь через «🗂 Категории».")
            return ConversationHandler.END
        buttons = [[InlineKeyboardButton(category_with_emoji(c), callback_data=f"budcat|{c}")] for c in cats]
        buttons.append([InlineKeyboardButton("❌ Отмена", callback_data="cancel")])
        await q.edit_message_text("По какой категории ставим бюджет?", reply_markup=InlineKeyboardMarkup(buttons))
        return BUDGET_CATEGORY

    if action == "budm_del":
        budgets = db.get_budgets(update.effective_user.id)
        if not budgets:
            await q.edit_message_text("Удалять нечего — бюджетов нет.")
            return ConversationHandler.END
        buttons = [[InlineKeyboardButton(b["category"], callback_data=f"budrm|{b['category']}")] for b in budgets]
        buttons.append([InlineKeyboardButton("❌ Отмена", callback_data="cancel")])
        await q.edit_message_text("Какой бюджет удалить?", reply_markup=InlineKeyboardMarkup(buttons))
        return ConversationHandler.END

    return ConversationHandler.END


async def on_budget_category(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    q = update.callback_query
    await q.answer()
    if q.data == "cancel":
        await q.edit_message_text("Отменено.")
        return ConversationHandler.END
    cat = q.data.split("|", 1)[1]
    ctx.user_data["bud_category"] = cat
    await q.edit_message_text(f"Категория: *{cat}*\nВведи лимит на месяц (например, `15000`):", parse_mode=ParseMode.MARKDOWN)
    return BUDGET_AMOUNT


async def on_budget_amount(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    amount = parse_amount(update.message.text)
    if amount is None or amount <= 0:
        await update.message.reply_text("Не понял сумму. Введи число, например: `15000`", parse_mode=ParseMode.MARKDOWN)
        return BUDGET_AMOUNT
    cat = ctx.user_data["bud_category"]
    db.set_budget(update.effective_user.id, cat, amount, "month")
    await update.message.reply_text(
        f"✅ Бюджет установлен: *{cat}* — {fmt(amount)} в месяц",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=MAIN_KEYBOARD,
    )
    ctx.user_data.clear()
    return ConversationHandler.END


async def on_budget_delete(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    q = update.callback_query
    await q.answer()
    if q.data == "cancel":
        await q.edit_message_text("Отменено.")
        return ConversationHandler.END
    # budrm|<category>
    cat = q.data.split("|", 1)[1] if "|" in q.data else q.data
    db.delete_budget(update.effective_user.id, cat, "month")
    await q.edit_message_text(f"Бюджет по «{cat}» удалён.")
    await q.message.reply_text("Что дальше?", reply_markup=MAIN_KEYBOARD)
    return ConversationHandler.END


# ============================================================
# ЭКСПОРТ CSV
# ============================================================
async def export_csv(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    rows = db.get_all_user_transactions(user_id)
    if not rows:
        await update.message.reply_text("Нет операций для экспорта.", reply_markup=MAIN_KEYBOARD)
        return

    buf = io.StringIO()
    buf.write("\ufeff")  # BOM для корректного открытия в Excel
    writer = csv.writer(buf, delimiter=";")
    writer.writerow(["Дата", "Тип", "Категория", "Сумма", "Заметка"])
    for r in rows:
        type_label = "Расход" if r["type"] == "expense" else "Доход"
        sign = "" if r["type"] == "expense" else "+"
        writer.writerow([
            r["created_at"],
            type_label,
            r["category"],
            f"{sign}{r['amount']:.2f}",
            r["note"] or "",
        ])
    data = buf.getvalue().encode("utf-8")
    from datetime import datetime as _dt
    fname = f"finance_{_dt.now().strftime('%Y%m%d_%H%M%S')}.csv"
    await update.message.reply_document(
        document=io.BytesIO(data),
        filename=fname,
        caption=f"📁 Экспортировано {len(rows)} операций",
    )


# ============================================================
# КАТЕГОРИИ
# ============================================================
async def show_categories_menu(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    kb = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("➕ Добавить", callback_data="catm_add"),
            InlineKeyboardButton("🗑 Удалить", callback_data="catm_del"),
        ],
        [
            InlineKeyboardButton("📋 Показать все", callback_data="catm_list"),
        ],
        [InlineKeyboardButton("❌ Отмена", callback_data="cancel")],
    ])
    await update.message.reply_text("Управление категориями:", reply_markup=kb)
    return ConversationHandler.END


async def on_cat_action(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    q = update.callback_query
    await q.answer()
    if q.data == "cancel":
        await q.edit_message_text("Отменено.")
        return ConversationHandler.END
    action = q.data  # "catm_add" | "catm_del" | "catm_list"

    if action == "catm_list":
        user_id = update.effective_user.id
        exp_cats = db.get_categories(user_id, "expense")
        inc_cats = db.get_categories(user_id, "income")
        text = "*Расходы:*\n" + ", ".join(exp_cats) + "\n\n*Доходы:*\n" + ", ".join(inc_cats)
        await q.edit_message_text(text, parse_mode=ParseMode.MARKDOWN)
        return ConversationHandler.END

    if action == "catm_add":
        kb = InlineKeyboardMarkup([
            [
                InlineKeyboardButton("Расход", callback_data="cattype|expense"),
                InlineKeyboardButton("Доход", callback_data="cattype|income"),
            ],
            [InlineKeyboardButton("❌ Отмена", callback_data="cancel")],
        ])
        await q.edit_message_text("Категория для расходов или доходов?", reply_markup=kb)
        return ADD_CATEGORY_TYPE

    if action == "catm_del":
        kb = InlineKeyboardMarkup([
            [
                InlineKeyboardButton("Расход", callback_data="catdeltype|expense"),
                InlineKeyboardButton("Доход", callback_data="catdeltype|income"),
            ],
            [InlineKeyboardButton("❌ Отмена", callback_data="cancel")],
        ])
        await q.edit_message_text("Из каких категорий удалить?", reply_markup=kb)
        return DEL_CATEGORY_TYPE

    return ConversationHandler.END


async def on_cat_type(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    q = update.callback_query
    await q.answer()
    if q.data == "cancel":
        await q.edit_message_text("Отменено.")
        return ConversationHandler.END
    type_ = q.data.split("|", 1)[1]
    ctx.user_data["new_cat_type"] = type_
    label = "расходов" if type_ == "expense" else "доходов"
    await q.edit_message_text(f"Введи название новой категории {label}:")
    return ADD_CATEGORY_NAME


async def on_cat_name(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    name = update.message.text.strip()
    if not name or len(name) > 40:
        await update.message.reply_text("Название должно быть 1-40 символов. Попробуй ещё:")
        return ADD_CATEGORY_NAME

    type_ = ctx.user_data.get("new_cat_type") or ctx.user_data.get("adding_cat_for") or "expense"
    ok = db.add_category(update.effective_user.id, type_, name)
    if not ok:
        await update.message.reply_text("Такая категория уже существует.", reply_markup=MAIN_KEYBOARD)
        ctx.user_data.clear()
        return ConversationHandler.END

    label = "Расход" if type_ == "expense" else "Доход"
    await update.message.reply_text(
        f"✅ Категория «{name}» добавлена в {label.lower()}ы.",
        reply_markup=MAIN_KEYBOARD,
    )

    # Если мы добавляли категорию в процессе добавления транзакции — продолжим
    if "exp_amount" in ctx.user_data:
        ctx.user_data["exp_category"] = name
        await update.message.reply_text(f"Категория: *{name}*\nЗаметка (или «-» чтобы пропустить):", parse_mode=ParseMode.MARKDOWN)
        return ADD_EXPENSE_NOTE
    if "inc_amount" in ctx.user_data:
        ctx.user_data["inc_category"] = name
        await update.message.reply_text(f"Категория: *{name}*\nЗаметка (или «-» чтобы пропустить):", parse_mode=ParseMode.MARKDOWN)
        return ADD_INCOME_NOTE

    ctx.user_data.clear()
    return ConversationHandler.END


async def on_cat_del_type(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    q = update.callback_query
    await q.answer()
    if q.data == "cancel":
        await q.edit_message_text("Отменено.")
        return ConversationHandler.END
    type_ = q.data.split("|", 1)[1]
    cats = db.get_categories(update.effective_user.id, type_)
    if not cats:
        await q.edit_message_text("Категорий этого типа нет.")
        return ConversationHandler.END
    buttons = [[InlineKeyboardButton(c, callback_data=f"catdelname|{type_}|{c}")] for c in cats]
    buttons.append([InlineKeyboardButton("❌ Отмена", callback_data="cancel")])
    await q.edit_message_text("Какую категорию удалить?", reply_markup=InlineKeyboardMarkup(buttons))
    return DEL_CATEGORY_NAME


async def on_cat_del_name(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    q = update.callback_query
    await q.answer()
    if q.data == "cancel":
        await q.edit_message_text("Отменено.")
        return ConversationHandler.END
    parts = q.data.split("|")
    type_, cat = parts[1], "|".join(parts[2:])
    ok = db.delete_category(update.effective_user.id, type_, cat)
    if ok:
        await q.edit_message_text(f"Категория «{cat}» удалена.")
    else:
        await q.edit_message_text("Не удалось удалить.")
    await q.message.reply_text("Что дальше?", reply_markup=MAIN_KEYBOARD)
    return ConversationHandler.END


# ============================================================
# ПОСЛЕДНИЕ ОПЕРАЦИИ
# ============================================================
async def show_last(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    rows = db.get_recent_transactions(user_id, limit=10, offset=0)
    if not rows:
        await update.message.reply_text("Операций пока нет.", reply_markup=MAIN_KEYBOARD)
        return
    lines = ["*Последние 10 операций:*\n"]
    for r in rows:
        sign = "−" if r["type"] == "expense" else "+"
        dt = r["created_at"][:16].replace("T", " ")
        note = f" — {r['note']}" if r["note"] else ""
        lines.append(f"{dt}  {sign}{fmt(r['amount'])}  • {r['category']}{note}  [#{r['id']}]")
    await update.message.reply_text("\n".join(lines), parse_mode=ParseMode.MARKDOWN, reply_markup=MAIN_KEYBOARD)


# ============================================================
# /delete <id> — удаление операции по id
# ============================================================
async def cmd_delete(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if not ctx.args:
        await update.message.reply_text("Использование: `/delete 5` — где 5 это id операции (виден в «🕘 Последние»).", parse_mode=ParseMode.MARKDOWN)
        return
    try:
        trx_id = int(ctx.args[0])
    except ValueError:
        await update.message.reply_text("id должен быть числом.")
        return
    ok = db.delete_transaction(update.effective_user.id, trx_id)
    if ok:
        await update.message.reply_text(f"✅ Операция #{trx_id} удалена.", reply_markup=MAIN_KEYBOARD)
    else:
        await update.message.reply_text("Операция не найдена.", reply_markup=MAIN_KEYBOARD)


# ============================================================
# Сборка приложения
# ============================================================
def build_app() -> Application:
    if not TOKEN:
        log.error("BOT_TOKEN не задан! Создай .env с BOT_TOKEN=... или передай через переменную окружения.")
        sys.exit(1)

    app = Application.builder().token(TOKEN).build()

    # Главное ConversationHandler
    main_conv = ConversationHandler(
        entry_points=[
            MessageHandler(filters.Regex("^➕ Расход$"), start_add_expense),
            MessageHandler(filters.Regex("^➕ Доход$"), start_add_income),
        ],
        states={
            ADD_EXPENSE_AMOUNT: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_expense_amount)],
            ADD_EXPENSE_CATEGORY: [CallbackQueryHandler(add_expense_category, pattern=r"^(expcat|cancel)")],
            ADD_EXPENSE_NOTE: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_expense_note)],
            ADD_INCOME_AMOUNT: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_income_amount)],
            ADD_INCOME_CATEGORY: [CallbackQueryHandler(add_income_category, pattern=r"^(inccat|cancel)")],
            ADD_INCOME_NOTE: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_income_note)],
            ADD_CATEGORY_TYPE: [CallbackQueryHandler(on_cat_type, pattern=r"^(cattype|cancel)")],
            ADD_CATEGORY_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, on_cat_name)],
            DEL_CATEGORY_TYPE: [CallbackQueryHandler(on_cat_del_type, pattern=r"^(catdeltype|cancel)")],
            DEL_CATEGORY_NAME: [CallbackQueryHandler(on_cat_del_name, pattern=r"^(catdelname|cancel)")],
            BUDGET_CATEGORY: [CallbackQueryHandler(on_budget_category, pattern=r"^(budcat|cancel)")],
            BUDGET_AMOUNT: [MessageHandler(filters.TEXT & ~filters.COMMAND, on_budget_amount)],
        },
        fallbacks=[CommandHandler("cancel", cmd_cancel)],
        allow_reentry=True,
        per_user=True,
        per_chat=True,
        per_message=False,
    )

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(CommandHandler("menu", cmd_start))
    app.add_handler(CommandHandler("cancel", cmd_cancel))
    app.add_handler(CommandHandler("report", show_report_menu))
    app.add_handler(CommandHandler("chart", show_chart_menu))
    app.add_handler(CommandHandler("budget", show_budget_menu))
    app.add_handler(CommandHandler("export", export_csv))
    app.add_handler(CommandHandler("last", show_last))
    app.add_handler(CommandHandler("delete", cmd_delete))

    app.add_handler(main_conv)

    # Бюджеты (отдельный ConversationHandler на кнопках)
    budget_conv = ConversationHandler(
        entry_points=[
            MessageHandler(filters.Regex("^💰 Бюджеты$"), show_budget_menu),
            CallbackQueryHandler(on_budget_action, pattern=r"^(budm_add|budm_del|cancel)"),
        ],
        states={
            BUDGET_CATEGORY: [CallbackQueryHandler(on_budget_category, pattern=r"^(budcat|cancel)")],
            BUDGET_AMOUNT: [MessageHandler(filters.TEXT & ~filters.COMMAND, on_budget_amount)],
        },
        fallbacks=[CommandHandler("cancel", cmd_cancel)],
        allow_reentry=True,
        per_message=False,
    )
    app.add_handler(budget_conv)

    # Категории
    cat_conv = ConversationHandler(
        entry_points=[
            MessageHandler(filters.Regex("^🗂 Категории$"), show_categories_menu),
            CallbackQueryHandler(on_cat_action, pattern=r"^(catm_add|catm_del|catm_list|cancel)"),
        ],
        states={
            ADD_CATEGORY_TYPE: [CallbackQueryHandler(on_cat_type, pattern=r"^(cattype|cancel)")],
            ADD_CATEGORY_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, on_cat_name)],
            DEL_CATEGORY_TYPE: [CallbackQueryHandler(on_cat_del_type, pattern=r"^(catdeltype|cancel)")],
            DEL_CATEGORY_NAME: [CallbackQueryHandler(on_cat_del_name, pattern=r"^(catdelname|cancel)")],
        },
        fallbacks=[CommandHandler("cancel", cmd_cancel)],
        allow_reentry=True,
        per_message=False,
    )
    app.add_handler(cat_conv)

    # Callback для удаления бюджета (отдельный handler, без ConversationHandler)
    app.add_handler(CallbackQueryHandler(on_budget_delete, pattern=r"^budrm"))

    # Callback для отчётов
    app.add_handler(CallbackQueryHandler(on_report_period, pattern=r"^rep\|"))

    # Callback для графиков
    app.add_handler(CallbackQueryHandler(on_chart_callback, pattern=r"^chart\|"))

    # Меню-кнопки (без состояния)
    app.add_handler(MessageHandler(filters.Regex("^📊 Отчёт$"), show_report_menu))
    app.add_handler(MessageHandler(filters.Regex("^📈 График$"), show_chart_menu))
    app.add_handler(MessageHandler(filters.Regex("^📁 Экспорт$"), export_csv))
    app.add_handler(MessageHandler(filters.Regex("^🕘 Последние$"), show_last))
    app.add_handler(MessageHandler(filters.Regex("^❓ Помощь$"), cmd_help))

    # Быстрый ввод — всё остальное текстовое (должно идти последним)
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_quick_text))

    return app


def main() -> None:
    db.init_db()
    app = build_app()
    log.info("Бот запущен. Нажмите Ctrl+C для остановки.")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
