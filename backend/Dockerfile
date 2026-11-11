FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1

WORKDIR /app
COPY backend/requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

COPY backend /app/backend
COPY frontend /app/frontend

# Defense in depth: .dockerignore is what keeps backend/.env out of the build
# context, but if this image is ever built without it, a baked-in root-owned
# .env makes the app crash at startup (unreadable by the non-root user below)
# and leaks the API token into a layer. Config comes from the environment.
RUN rm -f /app/backend/.env /app/backend/.env.*

# Run as a non-root user: this process holds exchange API keys in its
# environment and should not have write access to the image.
RUN useradd --create-home --uid 10001 arbbot \
    && mkdir -p /app/backend/data \
    && chown -R arbbot:arbbot /app/backend/data
USER arbbot

WORKDIR /app/backend
EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
