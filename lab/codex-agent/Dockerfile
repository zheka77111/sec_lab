FROM python:3.12-slim

RUN apt-get update \
    && apt-get install -y nodejs npm git --no-install-recommends bash curl jq ca-certificates \
    && rm -rf /var/lib/apt/lists/* 

WORKDIR /workspace

RUN npm install -g @openai/codex
