# Separate, kleine Stufe nur um den aktuellen Git-Commit-Hash zu ermitteln -
# das eigentliche .git-Verzeichnis landet dadurch nie im finalen Image.
FROM python:3.12-slim AS gitinfo
RUN apt-get update && apt-get install -y --no-install-recommends git \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /src
COPY .git ./.git
RUN git rev-parse --short HEAD > /build_hash.txt 2>/dev/null || echo "unknown" > /build_hash.txt


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
COPY VERSION ./VERSION
COPY --from=gitinfo /build_hash.txt ./BUILD_HASH

# CSS aus app/static/input.css bauen, Templates werden automatisch gescannt
RUN tailwindcss -i ./app/static/input.css -o ./app/static/style.css --minify

RUN mkdir -p /data
VOLUME /data

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers", "--forwarded-allow-ips=*"]
