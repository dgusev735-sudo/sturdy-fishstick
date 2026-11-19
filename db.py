"""
Работа с базой данных SQLite.
Хранит пользователей, транзакции, категории и бюджеты.
"""
import sqlite3
from datetime import datetime, timedelta
from typing import Optional

from config import DB_PATH, DEFAULT_EXPENSE_CATEGORIES, DEFAULT_INCOME_CATEGORIES


def get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db() -> None:
    """Создаёт таблицы, если их ещё нет."""
    conn = get_conn()
    cur = conn.cursor()

    cur.execute("""
        CREATE TABLE IF NOT EXISTS users (
            user_id      INTEGER PRIMARY KEY,
            username     TEXT,
            first_name   TEXT,
            created_at   TEXT DEFAULT (datetime('now'))
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS categories (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id      INTEGER NOT NULL,
            name         TEXT NOT NULL,
            type         TEXT NOT NULL CHECK (type IN ('expense', 'income')),
            is_default   INTEGER DEFAULT 0,
            UNIQUE (user_id, name, type),
            FOREIGN KEY (user_id) REFERENCES users(user_id)
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS transactions (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id      INTEGER NOT NULL,
            type         TEXT NOT NULL CHECK (type IN ('expense', 'income')),
            amount       REAL NOT NULL,
            category     TEXT NOT NULL,
            note         TEXT,
            created_at   TEXT NOT NULL,
            FOREIGN KEY (user_id) REFERENCES users(user_id)
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS budgets (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id      INTEGER NOT NULL,
            category     TEXT NOT NULL,
            amount       REAL NOT NULL,
            period       TEXT NOT NULL DEFAULT 'month',
            UNIQUE (user_id, category, period),
            FOREIGN KEY (user_id) REFERENCES users(user_id)
        )
    """)

    cur.execute("CREATE INDEX IF NOT EXISTS idx_trx_user ON transactions(user_id)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_trx_date ON transactions(created_at)")

    conn.commit()
    conn.close()


def ensure_user(user_id: int, username: str = "", first_name: str = "") -> None:
    """Создаёт или обновляет пользователя и заполняет стандартные категории."""
    conn = get_conn()
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO users (user_id, username, first_name) VALUES (?, ?, ?) "
        "ON CONFLICT(user_id) DO UPDATE SET "
        "username=excluded.username, first_name=excluded.first_name",
        (user_id, username, first_name),
    )

    # Добавляем дефолтные категории, если у пользователя их ещё нет
    for name in DEFAULT_EXPENSE_CATEGORIES:
        cur.execute(
            "INSERT OR IGNORE INTO categories (user_id, name, type, is_default) "
            "VALUES (?, ?, 'expense', 1)",
            (user_id, name),
        )
    for name in DEFAULT_INCOME_CATEGORIES:
        cur.execute(
            "INSERT OR IGNORE INTO categories (user_id, name, type, is_default) "
            "VALUES (?, ?, 'income', 1)",
            (user_id, name),
        )

    conn.commit()
    conn.close()


def get_categories(user_id: int, type_: str) -> list[str]:
    """Возвращает список категорий пользователя заданного типа."""
    conn = get_conn()
    rows = conn.execute(
        "SELECT name FROM categories WHERE user_id=? AND type=? ORDER BY is_default DESC, name",
        (user_id, type_),
    ).fetchall()
    conn.close()
    return [r["name"] for r in rows]


def add_category(user_id: int, type_: str, name: str) -> bool:
    """Добавляет категорию. Возвращает True если успешно, False если уже есть."""
    conn = get_conn()
    cur = conn.cursor()
    try:
        cur.execute(
            "INSERT INTO categories (user_id, name, type, is_default) VALUES (?, ?, ?, 0)",
            (user_id, name.strip(), type_),
        )
        conn.commit()
        return True
    except sqlite3.IntegrityError:
        return False
    finally:
        conn.close()


def delete_category(user_id: int, type_: str, name: str) -> bool:
    conn = get_conn()
    cur = conn.cursor()
    cur.execute(
        "DELETE FROM categories WHERE user_id=? AND name=? AND type=?",
        (user_id, name, type_),
    )
    conn.commit()
    deleted = cur.rowcount > 0
    conn.close()
    return deleted


def add_transaction(
    user_id: int,
    type_: str,
    amount: float,
    category: str,
    note: str = "",
    when: Optional[datetime] = None,
) -> int:
    """Добавляет транзакцию. Возвращает id новой записи."""
    if when is None:
        when = datetime.now()
    conn = get_conn()
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO transactions (user_id, type, amount, category, note, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (user_id, type_, amount, category, note, when.isoformat(sep=" ")),
    )
    conn.commit()
    trx_id = cur.lastrowid
    conn.close()
    return trx_id


def delete_transaction(user_id: int, trx_id: int) -> bool:
    conn = get_conn()
    cur = conn.cursor()
    cur.execute(
        "DELETE FROM transactions WHERE id=? AND user_id=?",
        (trx_id, user_id),
    )
    conn.commit()
    deleted = cur.rowcount > 0
    conn.close()
    return deleted


def get_recent_transactions(user_id: int, limit: int = 10, offset: int = 0) -> list[dict]:
    conn = get_conn()
    rows = conn.execute(
        "SELECT * FROM transactions WHERE user_id=? ORDER BY created_at DESC LIMIT ? OFFSET ?",
        (user_id, limit, offset),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def count_transactions(user_id: int) -> int:
    conn = get_conn()
    row = conn.execute(
        "SELECT COUNT(*) AS c FROM transactions WHERE user_id=?", (user_id,)
    ).fetchone()
    conn.close()
    return row["c"]


def get_transactions_in_range(
    user_id: int, start: datetime, end: datetime, type_: Optional[str] = None
) -> list[dict]:
    conn = get_conn()
    if type_:
        rows = conn.execute(
            "SELECT * FROM transactions WHERE user_id=? AND type=? "
            "AND created_at >= ? AND created_at < ? ORDER BY created_at DESC",
            (user_id, type_, start.isoformat(sep=" "), end.isoformat(sep=" ")),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM transactions WHERE user_id=? "
            "AND created_at >= ? AND created_at < ? ORDER BY created_at DESC",
            (user_id, start.isoformat(sep=" "), end.isoformat(sep=" ")),
        ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_summary_by_category(
    user_id: int, start: datetime, end: datetime, type_: str
) -> list[tuple[str, float]]:
    """Возвращает [(category, total_sum), ...] отсортированный по убыванию суммы."""
    conn = get_conn()
    rows = conn.execute(
        "SELECT category, SUM(amount) AS total FROM transactions "
        "WHERE user_id=? AND type=? AND created_at >= ? AND created_at < ? "
        "GROUP BY category ORDER BY total DESC",
        (user_id, type_, start.isoformat(sep=" "), end.isoformat(sep=" ")),
    ).fetchall()
    conn.close()
    return [(r["category"], r["total"]) for r in rows if r["total"] is not None]


def get_totals(
    user_id: int, start: datetime, end: datetime
) -> tuple[float, float, float]:
    """Возвращает (расходы, доходы, баланс) за период."""
    conn = get_conn()
    row = conn.execute(
        "SELECT "
        "COALESCE(SUM(CASE WHEN type='expense' THEN amount ELSE 0 END), 0) AS exp, "
        "COALESCE(SUM(CASE WHEN type='income'  THEN amount ELSE 0 END), 0) AS inc "
        "FROM transactions WHERE user_id=? AND created_at >= ? AND created_at < ?",
        (user_id, start.isoformat(sep=" "), end.isoformat(sep=" ")),
    ).fetchone()
    conn.close()
    exp = row["exp"] or 0
    inc = row["inc"] or 0
    return exp, inc, inc - exp


def set_budget(user_id: int, category: str, amount: float, period: str = "month") -> None:
    conn = get_conn()
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO budgets (user_id, category, amount, period) VALUES (?, ?, ?, ?) "
        "ON CONFLICT(user_id, category, period) DO UPDATE SET amount=excluded.amount",
        (user_id, category, amount, period),
    )
    conn.commit()
    conn.close()


def delete_budget(user_id: int, category: str, period: str = "month") -> bool:
    conn = get_conn()
    cur = conn.cursor()
    cur.execute(
        "DELETE FROM budgets WHERE user_id=? AND category=? AND period=?",
        (user_id, category, period),
    )
    conn.commit()
    deleted = cur.rowcount > 0
    conn.close()
    return deleted


def get_budgets(user_id: int, period: str = "month") -> list[dict]:
    conn = get_conn()
    rows = conn.execute(
        "SELECT * FROM budgets WHERE user_id=? AND period=? ORDER BY category",
        (user_id, period),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_budget_status(user_id: int, category: str, period: str = "month") -> Optional[dict]:
    """Возвращает {budget, spent, percent} для одной категории или None."""
    conn = get_conn()
    row = conn.execute(
        "SELECT amount FROM budgets WHERE user_id=? AND category=? AND period=?",
        (user_id, category, period),
    ).fetchone()
    if not row:
        conn.close()
        return None

    now = datetime.now()
    if period == "month":
        start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        end = (start + timedelta(days=32)).replace(day=1)
    else:
        start = now - timedelta(days=now.weekday())
        start = start.replace(hour=0, minute=0, second=0, microsecond=0)
        end = start + timedelta(days=7)

    spent_row = conn.execute(
        "SELECT COALESCE(SUM(amount), 0) AS s FROM transactions "
        "WHERE user_id=? AND type='expense' AND category=? "
        "AND created_at >= ? AND created_at < ?",
        (user_id, category, start.isoformat(sep=" "), end.isoformat(sep=" ")),
    ).fetchone()
    conn.close()

    budget = row["amount"]
    spent = spent_row["s"] or 0
    percent = (spent / budget * 100) if budget > 0 else 0
    return {"budget": budget, "spent": spent, "percent": percent}


def get_all_user_transactions(user_id: int) -> list[dict]:
    conn = get_conn()
    rows = conn.execute(
        "SELECT * FROM transactions WHERE user_id=? ORDER BY created_at ASC",
        (user_id,),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]
