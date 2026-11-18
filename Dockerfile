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

ARG FORGE_VERSION=2.0.16
ARG FORGE_SHA256=feb1338c2f4a8c62e9289b6270caa7fcc38f2441d5ec4d7cff237c2c1a3b15d0

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates curl bzip2 openjdk-17-jre-headless \
    && java -version \
    && curl -fsSL --retry 3 "https://github.com/Card-Forge/forge/releases/download/forge-${FORGE_VERSION}/forge-installer-${FORGE_VERSION}.tar.bz2" -o /tmp/forge.tar.bz2 \
    && echo "${FORGE_SHA256}  /tmp/forge.tar.bz2" | sha256sum -c - \
    && mkdir -p /tmp/forge /opt/forge \
    && tar -xjf /tmp/forge.tar.bz2 -C /tmp/forge \
    && forge_jar="$(find /tmp/forge -type f -name 'forge-gui-desktop-*-jar-with-dependencies.jar' -print -quit)" \
    && test -n "${forge_jar}" \
    && cp -a "$(dirname "${forge_jar}")/." /opt/forge/ \
    && test -d /opt/forge/res \
    && java -version \
    && rm -rf /tmp/forge /tmp/forge.tar.bz2 \
    && rm -rf /var/lib/apt/lists/*

COPY --from=codex /usr/local/bin/codex /usr/local/bin/codex

COPY collection_web.py codex_chat.py enrich_mtg_collection.py collection_import.py collection_allocation.py forge_simulation.py ./
COPY web ./web
COPY .agents/skills/mtg-deck-architect/ ./.agents/skills/mtg-deck-architect/

EXPOSE 8000

CMD ["python", "collection_web.py", "--host", "0.0.0.0", "--port", "8000"]
