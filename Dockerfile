FROM python:3.12-slim

# zoneinfo needs the tz database; everything else is the standard library.
RUN pip install --no-cache-dir tzdata \
 && useradd --create-home --uid 10001 leaderboard

WORKDIR /app
COPY --chown=leaderboard:leaderboard . /app
RUN mkdir -p /app/data && chown leaderboard:leaderboard /app/data

USER leaderboard
ENV LB_HOST=0.0.0.0 \
    LB_PORT=8790 \
    PYTHONUNBUFFERED=1
EXPOSE 8790
VOLUME ["/app/data"]

HEALTHCHECK --interval=30s --timeout=3s CMD python3 -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8790/healthz', timeout=2)"

CMD ["python3", "server.py"]
