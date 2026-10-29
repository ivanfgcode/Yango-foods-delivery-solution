# Imagen oficial de Playwright: trae Chromium y sus dependencias de sistema.
FROM mcr.microsoft.com/playwright/python:v1.60.0-noble
WORKDIR /app
ENV PYTHONUNBUFFERED=1 TZ=America/La_Paz DATA_DIR=/app/data
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY app ./app
COPY bot ./bot
COPY config ./config
COPY tools ./tools
COPY tests/fixtures ./tests/fixtures
COPY run.py .
# data/ (SQLite, sesiones de las cuentas, imágenes subidas) va en un volumen de Railway montado en /app/data.
EXPOSE 8000
CMD ["sh", "-c", "python run.py --port ${PORT:-8000}"]
