FROM python:3.11-slim-bookworm

WORKDIR /app

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt \
    && python -m playwright install --with-deps chromium

COPY bot.py schedule_reader.py ./

CMD ["python", "bot.py"]
