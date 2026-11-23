# GhostClipper website + auth API — stdlib Python, zero pip dependencies.
FROM python:3.12-slim

WORKDIR /app
COPY server.py credits_ledger.py /app/
COPY site/ /app/site/
RUN mkdir -p /app/data

ENV PORT=8080 HOST=0.0.0.0 PYTHONUNBUFFERED=1
EXPOSE 8080

# config.json is supplied at deploy time (Render secret file or mounted volume).
# If absent, server.py falls back to DEFAULT_CONFIG with safe production values.
CMD ["python3", "server.py"]
