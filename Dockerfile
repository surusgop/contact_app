# --- build stage: needs git + the PAT to fetch private surus-auth ---
FROM python:3.11-slim AS builder

# Railway injects service variables as build args; declare it to receive it.
ARG GH_PAT

RUN apt-get update \
 && apt-get install -y --no-install-recommends git \
 && rm -rf /var/lib/apt/lists/*

RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

COPY requirements.txt .
RUN git config --global url."https://${GH_PAT}@github.com/".insteadOf "https://github.com/" \
 && pip install --no-cache-dir -r requirements.txt \
 && rm -f /root/.gitconfig

# --- runtime stage: no git, no token in any layer ---
FROM python:3.11-slim

WORKDIR /app

COPY --from=builder /opt/venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

COPY . .

EXPOSE 5000

CMD gunicorn app:app --bind 0.0.0.0:${PORT:-5000} --timeout 120 --workers 2 --threads 4
