FROM python:3.12-slim-bookworm

WORKDIR /app
COPY server/requirements.txt ./requirements.txt
RUN python -m pip install --no-cache-dir -r requirements.txt

WORKDIR /app/packages
COPY pyproject.toml README.md ./
COPY mem0 ./mem0
RUN python -m pip install --no-cache-dir --no-deps .

WORKDIR /app
COPY server ./
ENV PYTHONUNBUFFERED=1 MEM0_TELEMETRY=false
EXPOSE 8000
CMD ["sh", "-c", "alembic upgrade head && exec uvicorn main:app --host 0.0.0.0 --port 8000"]
