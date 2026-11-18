FROM debian:bookworm-slim AS codex

ARG CODEX_VERSION=0.169.0
ARG CODEX_SHA256=9af4d751fce183faedea629437d5252dbf458eeb04130cf4f97be844621ea985
RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates curl tar \
    && curl -fsSL "https://github.com/openai/codex/releases/download/rust-v${CODEX_VERSION}/codex-x86_64-unknown-linux-musl.tar.gz" -o /tmp/codex.tar.gz \
    && echo "${CODEX_SHA256}  /tmp/codex.tar.gz" | sha256sum -c - \
    && tar -xzf /tmp/codex.tar.gz -C /usr/local/bin \
    && mv /usr/local/bin/codex-x86_64-unknown-linux-musl /usr/local/bin/codex \
    && codex --version \
    && rm -f /tmp/codex.tar.gz \
    && rm -rf /var/lib/apt/lists/*

FROM python:3.12-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

COPY --from=codex /usr/local/bin/codex /usr/local/bin/codex

COPY collection_web.py codex_chat.py enrich_mtg_collection.py ./
COPY web ./web
COPY .agents/skills/mtg-deck-architect/ ./.agents/skills/mtg-deck-architect/

EXPOSE 8000

CMD ["python", "collection_web.py", "--host", "0.0.0.0", "--port", "8000"]
