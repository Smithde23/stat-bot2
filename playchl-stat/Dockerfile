FROM python:3.11-slim-bookworm

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends libatomic1 \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt \
    && python -m playwright install --with-deps chromium

COPY bot.py schedule_reader.py ea_club.py ./
COPY tests/ ./tests/
RUN python -m unittest discover -s tests -p 'test_*.py' -v

CMD ["python", "bot.py"]
