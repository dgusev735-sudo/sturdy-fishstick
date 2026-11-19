"""
Генерация графиков через matplotlib.
Все тексты на русском, шрифт Noto Sans SC + DejaVu Sans.
"""
import matplotlib
matplotlib.use("Agg")
import matplotlib.font_manager as fm
import matplotlib.pyplot as plt
from pathlib import Path
from datetime import datetime, timedelta
from typing import Optional

from config import CHART_DIR, fmt
import db

# Регистрируем шрифты для корректного отображения кириллицы и символа ₽
# DejaVu Sans имеет и кириллицу, и U+20BD (₽), Noto Sans SC — только кириллицу.
# Поэтому DejaVu Sans должен идти первым.
# Ищем шрифты в нескольких местах (Linux, macOS, Windows, Docker-контейнер).
_font_candidates = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/chinese/NotoSansSC-Regular.ttf",
    "/usr/share/fonts/truetype/chinese/NotoSansSC-Bold.ttf",
    "/usr/share/fonts/dejavu/DejaVuSans.ttf",
    "/System/Library/Fonts/Supplemental/Arial Unicode.ttf",  # macOS
    "C:/Windows/Fonts/arial.ttf",  # Windows
    "C:/Windows/Fonts/segoeui.ttf",
]
for _f in _font_candidates:
    if Path(_f).exists():
        try:
            fm.fontManager.addfont(_f)
        except Exception:
            pass
plt.rcParams["font.sans-serif"] = ["DejaVu Sans", "Noto Sans SC", "Arial Unicode MS", "Arial"]
plt.rcParams["font.family"] = "sans-serif"
plt.rcParams["axes.unicode_minus"] = False

# Цветовая палитра для категорий
COLORS = [
    "#EF4444", "#F59E0B", "#10B981", "#3B82F6",
    "#8B5CF6", "#EC4899", "#14B8A6", "#F97316",
    "#6366F1", "#84CC16", "#06B6D4", "#A855F7",
    "#64748B", "#EAB308",
]


def make_pie_chart(
    user_id: int,
    start: datetime,
    end: datetime,
    type_: str = "expense",
) -> Optional[Path]:
    """Создаёт круговую диаграмму по категориям. Возвращает путь к PNG или None."""
    data = db.get_summary_by_category(user_id, start, end, type_)
    data = [(c, a) for c, a in data if a > 0]
    if not data:
        return None

    labels = [c for c, _ in data]
    sizes = [a for _, a in data]

    fig, ax = plt.subplots(figsize=(8, 8), constrained_layout=True)
    wedges, texts, autotexts = ax.pie(
        sizes,
        labels=None,
        autopct=lambda p: f"{p:.1f}%\n{fmt(sum(sizes) * p / 100)}",
        startangle=90,
        colors=COLORS[: len(sizes)],
        pctdistance=0.75,
        wedgeprops={"linewidth": 2, "edgecolor": "white"},
    )
    for t in autotexts:
        t.set_fontsize(9)
        t.set_color("white")
        t.set_fontweight("bold")

    ax.set_title(
        f"{'Расходы' if type_ == 'expense' else 'Доходы'} по категориям\n"
        f"{start.strftime('%d.%m.%Y')} — {end.strftime('%d.%m.%Y')}",
        fontsize=14,
        fontweight="bold",
        pad=20,
    )

    legend_labels = [f"{lbl} — {fmt(sz)}" for lbl, sz in zip(labels, sizes)]
    ax.legend(
        wedges,
        legend_labels,
        loc="center left",
        bbox_to_anchor=(1.0, 0.5),
        fontsize=10,
        frameon=False,
    )

    ax.axis("equal")
    path = CHART_DIR / f"pie_{user_id}_{type_}_{int(datetime.now().timestamp())}.png"
    fig.savefig(path, dpi=120, bbox_inches=None)
    plt.close(fig)
    return path


def make_trend_chart(
    user_id: int,
    days: int = 30,
) -> Optional[Path]:
    """Линейный график расходов и доходов по дням."""
    end = datetime.now().replace(hour=23, minute=59, second=59)
    start = (end - timedelta(days=days - 1)).replace(hour=0, minute=0, second=0)

    conn = db.get_conn()
    rows = conn.execute(
        "SELECT DATE(created_at) AS d, type, SUM(amount) AS s FROM transactions "
        "WHERE user_id=? AND created_at >= ? AND created_at < ? "
        "GROUP BY d, type ORDER BY d",
        (user_id, start.isoformat(sep=" "), end.isoformat(sep=" ")),
    ).fetchall()
    conn.close()

    if not rows:
        return None

    dates_set = {(start + timedelta(days=i)).strftime("%Y-%m-%d") for i in range(days)}
    exp_by_date = {d: 0.0 for d in dates_set}
    inc_by_date = {d: 0.0 for d in dates_set}
    for r in rows:
        if r["type"] == "expense":
            exp_by_date[r["d"]] = r["s"] or 0
        else:
            inc_by_date[r["d"]] = r["s"] or 0

    dates_sorted = sorted(exp_by_date.keys())
    short_dates = [d[5:] for d in dates_sorted]  # MM-DD
    exp_values = [exp_by_date[d] for d in dates_sorted]
    inc_values = [inc_by_date[d] for d in dates_sorted]

    fig, ax = plt.subplots(figsize=(10, 5), constrained_layout=True)
    ax.plot(short_dates, exp_values, marker="o", linewidth=2, color="#EF4444", label="Расходы")
    ax.plot(short_dates, inc_values, marker="s", linewidth=2, color="#10B981", label="Доходы")
    ax.fill_between(short_dates, exp_values, alpha=0.15, color="#EF4444")
    ax.fill_between(short_dates, inc_values, alpha=0.15, color="#10B981")

    ax.set_title(f"Динамика за {days} дней", fontsize=14, fontweight="bold")
    ax.set_ylabel("Сумма, ₽", fontsize=11)
    ax.grid(True, linestyle="--", alpha=0.4)
    ax.legend(loc="upper left", fontsize=10, frameon=False)

    # Подписи по оси X — не больше 8-10, иначе сливаются
    step = max(1, len(short_dates) // 8)
    ax.set_xticks(range(0, len(short_dates), step))
    ax.set_xticklabels(short_dates[::step], rotation=45, ha="right", fontsize=9)

    path = CHART_DIR / f"trend_{user_id}_{int(datetime.now().timestamp())}.png"
    fig.savefig(path, dpi=120, bbox_inches=None)
    plt.close(fig)
    return path


def make_budget_chart(user_id: int) -> Optional[Path]:
    """Горизонтальные полосы прогресса по бюджетам."""
    budgets = db.get_budgets(user_id)
    if not budgets:
        return None

    items = []
    for b in budgets:
        status = db.get_budget_status(user_id, b["category"], b["period"])
        if status:
            items.append({
                "category": b["category"],
                "budget": status["budget"],
                "spent": status["spent"],
                "percent": status["percent"],
            })

    if not items:
        return None

    items.sort(key=lambda x: x["percent"], reverse=True)

    fig, ax = plt.subplots(figsize=(10, max(4, len(items) * 0.55 + 1)), constrained_layout=True)

    y_pos = range(len(items))
    budgets_vals = [i["budget"] for i in items]
    spent_vals = [i["spent"] for i in items]

    ax.barh(y_pos, budgets_vals, color="#E5E7EB", height=0.6, label="Бюджет")
    colors = ["#10B981" if i["percent"] < 80 else ("#F59E0B" if i["percent"] < 100 else "#EF4444") for i in items]
    ax.barh(y_pos, spent_vals, color=colors, height=0.6, label="Потрачено")

    ax.set_yticks(list(y_pos))
    ax.set_yticklabels([i["category"] for i in items], fontsize=10)
    ax.invert_yaxis()
    ax.set_xlabel("Сумма, ₽", fontsize=11)
    ax.set_title("Бюджеты: статус за текущий месяц", fontsize=14, fontweight="bold")
    ax.grid(True, axis="x", linestyle="--", alpha=0.4)
    ax.legend(loc="lower right", fontsize=9, frameon=False)

    # Подписи процентов справа
    for i, item in enumerate(items):
        ax.text(
            item["budget"] * 1.02,
            i,
            f"{item['percent']:.0f}%",
            va="center",
            fontsize=9,
            color=colors[i],
            fontweight="bold",
        )

    path = CHART_DIR / f"budget_{user_id}_{int(datetime.now().timestamp())}.png"
    fig.savefig(path, dpi=120, bbox_inches=None)
    plt.close(fig)
    return path
