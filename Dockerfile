FROM python:3.12-slim

WORKDIR /code

# Tailwind-CSS Standalone-CLI holen, um das Stylesheet fest im Image zu bauen
# (kein CDN/Internet mehr nötig, wenn die App später läuft)
RUN apt-get update && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/* \
    && curl -sLo /usr/local/bin/tailwindcss \
        https://github.com/tailwindlabs/tailwindcss/releases/latest/download/tailwindcss-linux-x64 \
    && chmod +x /usr/local/bin/tailwindcss

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app

# CSS aus app/static/input.css bauen, Templates werden automatisch gescannt
RUN tailwindcss -i ./app/static/input.css -o ./app/static/style.css --minify

RUN mkdir -p /data
VOLUME /data

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
