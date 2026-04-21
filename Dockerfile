FROM python:3.12-slim-bookworm

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt

COPY febnik/ ./febnik/
# Падаем сборкой, если логотип не попал в контекст (закоммитьте febnik/web/static/brand/logo-registration.png)
RUN test -f febnik/web/static/brand/logo-registration.png
RUN test -f febnik/web/static/brand/sholk-bg.svg
RUN test -f febnik/web/static/fonts/denistina_en.ttf
RUN test -f febnik/web/static/fonts/CormorantGaramond-Regular.ttf

# База по умолчанию в volume /data (переопределяется DATABASE_URL)
ENV DATABASE_URL=sqlite+aiosqlite:////data/febnik.db

CMD ["python", "-m", "febnik.main"]
