FROM python:3.11-slim

# don't buffer stdout — otherwise container logs appear late or not at all
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /app

# deps first, so this layer is cached unless requirements.txt changes
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app

# run as a non-root user
RUN useradd --create-home --uid 1000 appuser \
    && chown -R appuser:appuser /app
USER appuser

# Container Apps injects PORT; default to 8000 for local `docker run`
ENV PORT=8000
EXPOSE 8000

# shell form so $PORT is expanded at runtime
CMD uvicorn app.main:api --host 0.0.0.0 --port ${PORT}
