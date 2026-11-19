"""
Конфигурация бота: валюта, категории по умолчанию, пути.
"""
import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent

# Путь к базе данных.
# В облаке (Koyeb/Render) нужно использовать постоянный том или /data, иначе БД теряется.
# Задаётся через переменную окружения DB_PATH, по умолчанию — файл рядом с кодом.
DB_PATH = Path(os.getenv("DB_PATH", BASE_DIR / "finance.db"))

# Папки для временных файлов (графики, экспорт). По умолчанию — рядом с кодом.
# В облаке можно указать /tmp (там всегда можно писать).
EXPORT_DIR = Path(os.getenv("EXPORT_DIR", BASE_DIR / "exports"))
CHART_DIR = Path(os.getenv("CHART_DIR", BASE_DIR / "charts"))
EXPORT_DIR.mkdir(parents=True, exist_ok=True)
CHART_DIR.mkdir(parents=True, exist_ok=True)

# Получаем токен из переменной окружения (для облака) или файла .env (локально)
BOT_TOKEN = os.getenv("BOT_TOKEN", "")

# Валюта по умолчанию
CURRENCY_SYMBOL = "₽"
CURRENCY_CODE = "RUB"
CURRENCY_FORMAT = "{amount:,.2f} ₽".replace(",", " ").replace(".00", "")

# Категории по умолчанию
DEFAULT_EXPENSE_CATEGORIES = [
    "Продукты",
    "Кафе и рестораны",
    "Транспорт",
    "Жильё",
    "Связь и интернет",
    "Подписки",
    "Здоровье",
    "Одежда",
    "Развлечения",
    "Образование",
    "Подарки",
    "Прочее",
]

DEFAULT_INCOME_CATEGORIES = [
    "Зарплата",
    "Фриланс",
    "Подработка",
    "Кэшбэк и бонусы",
    "Подарки",
    "Инвестиции",
    "Прочее",
]

# Эмодзи для категорий (для красивых кнопок)
CATEGORY_EMOJI = {
    "Продукты": "🛒",
    "Кафе и рестораны": "🍽",
    "Транспорт": "🚕",
    "Жильё": "🏠",
    "Связь и интернет": "📱",
    "Подписки": "📺",
    "Здоровье": "💊",
    "Одежда": "👕",
    "Развлечения": "🎮",
    "Образование": "📚",
    "Подарки": "🎁",
    "Прочее": "📦",
    "Зарплата": "💼",
    "Фриланс": "💻",
    "Подработка": "🔨",
    "Кэшбэк и бонусы": "💰",
    "Инвестиции": "📈",
}

# Количество транзакций на страницу в списке
PAGE_SIZE = 8


def fmt(amount: float) -> str:
    """Форматирование суммы: 1 234.50 ₽"""
    s = f"{amount:,.2f}"
    s = s.replace(",", " ").replace(".00", "")
    return f"{s} ₽"


def category_with_emoji(name: str) -> str:
    emoji = CATEGORY_EMOJI.get(name, "•")
    return f"{emoji} {name}"
