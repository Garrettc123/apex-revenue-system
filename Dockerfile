FROM python:3.11-slim

WORKDIR /app

# Install dependencies first (layer caching)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY . .

# Railway injects PORT at runtime; default to 5000 for local runs
ENV PORT=5000

EXPOSE $PORT

CMD gunicorn main:app --bind "0.0.0.0:$PORT" --workers 2 --timeout 120
