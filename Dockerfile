# MindVault in a container. Agents, keys and memory live in the /data volume.
#   docker compose up            (see docker-compose.yml)
FROM python:3.12-slim

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY server/ server/
COPY review/ review/
COPY scripts/ scripts/
COPY config/agents.example.json config/
COPY data/seed_blocks.json data/

ENV MEMORY_HOST=0.0.0.0 \
    MEMORY_PORT=8000 \
    MEMORY_LANG=en \
    MEMORY_DB=/data/memory.db \
    AGENTS_FILE=/data/agents.json \
    PYTHONUNBUFFERED=1

RUN useradd --create-home --uid 10001 mindvault \
    && mkdir -p /data /app/logs && chown mindvault /data /app/logs
USER mindvault
VOLUME ["/data"]
EXPOSE 8000

# init.py only creates what is missing (first start prints the agent keys), then the service starts.
CMD ["sh", "-c", "python scripts/init.py ${MINDVAULT_INIT_ARGS:-} && exec python -m server.app"]
