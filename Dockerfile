# Dockerfile для запуска бота в облаке (Koyeb, Render, Fly.io и т.д.)
FROM python:3.12-slim

WORKDIR /app

# Устанавливаем шрифты для корректного отображения кириллицы и символа ₽ на графиках
RUN apt-get update && \
    apt-get install -y --no-install-recommends fonts-dejavu fonts-noto-cjk && \
    rm -rf /var/lib/apt/lists/*

# Сначала ставим зависимости (кэшируется отдельно от кода)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Копируем код бота
COPY . .

# Папка для постоянной SQLite-базы (нужно подключить как Volume в Koyeb)
RUN mkdir -p /data

# Переменные окружения по умолчанию
# BOT_TOKEN обязательно задаётся через панель Koyeb (в секреах)
ENV DB_PATH=/data/finance.db \
    CHART_DIR=/tmp/charts \
    EXPORT_DIR=/tmp/exports \
    PYTHONUNBUFFERED=1

# Команда запуска
CMD ["python", "bot.py"]
