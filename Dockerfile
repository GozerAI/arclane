FROM python:3.12-slim

RUN apt-get update && apt-get install -y --no-install-recommends curl && rm -rf /var/lib/apt/lists/*

RUN useradd -r -m -u 1000 arclane

WORKDIR /app

COPY pyproject.toml .
COPY alembic.ini .
COPY migrations/ migrations/
COPY src/ src/
COPY frontend/ frontend/
COPY templates/ templates/

RUN pip install --no-cache-dir ".[monitoring]"

RUN mkdir -p /app/data && chown -R arclane:arclane /app

USER arclane

EXPOSE 8012

CMD ["uvicorn", "arclane.api.app:app", "--host", "0.0.0.0", "--port", "8012"]
