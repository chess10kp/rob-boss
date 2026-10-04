#!/usr/bin/env bash
set -Eeuo pipefail

ROOT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
HOST="${HOST:-127.0.0.1}"
PORT="${PORT:-8080}"
OUTPUT_DIR="${OUTPUT_DIR:-$HOME/.cache/rob-boss/agent-jobs}"
TOKEN_FILE="${TOKEN_FILE:-$HOME/.cache/rob-boss/agent-api-token}"
LOG_DIR="$(mktemp -d "${TMPDIR:-/tmp}/rob-boss-api.XXXXXX")"
SERVER_LOG="$LOG_DIR/server.log"
TUNNEL_LOG="$LOG_DIR/cloudflared.log"
SERVER_PID=""
TUNNEL_PID=""

cleanup() {
    trap - EXIT INT TERM
    if [[ -n "$TUNNEL_PID" ]] && kill -0 "$TUNNEL_PID" 2>/dev/null; then
        kill "$TUNNEL_PID" 2>/dev/null || true
        wait "$TUNNEL_PID" 2>/dev/null || true
    fi
    if [[ -n "$SERVER_PID" ]] && kill -0 "$SERVER_PID" 2>/dev/null; then
        kill "$SERVER_PID" 2>/dev/null || true
        wait "$SERVER_PID" 2>/dev/null || true
    fi
    rm -rf "$LOG_DIR"
}
trap cleanup EXIT INT TERM

command -v python3 >/dev/null || { echo "python3 is required" >&2; exit 1; }
command -v cloudflared >/dev/null || { echo "cloudflared is required" >&2; exit 1; }

cd "$ROOT_DIR"
python3 server.py \
    --host "$HOST" \
    --port "$PORT" \
    --output-dir "$OUTPUT_DIR" \
    --token-file "$TOKEN_FILE" \
    >"$SERVER_LOG" 2>&1 &
SERVER_PID=$!

for _ in {1..50}; do
    if curl --silent --show-error --fail --max-time 1 "http://$HOST:$PORT/healthz" >/dev/null 2>&1; then
        break
    fi
    if ! kill -0 "$SERVER_PID" 2>/dev/null; then
        cat "$SERVER_LOG" >&2
        exit 1
    fi
    sleep 0.2
done

if ! curl --silent --show-error --fail --max-time 2 "http://$HOST:$PORT/healthz" >/dev/null; then
    echo "API server did not become healthy" >&2
    cat "$SERVER_LOG" >&2
    exit 1
fi

cloudflared tunnel --no-autoupdate --url "http://$HOST:$PORT" >"$TUNNEL_LOG" 2>&1 &
TUNNEL_PID=$!

PUBLIC_URL=""
for _ in {1..100}; do
    PUBLIC_URL="$(grep -Eo 'https://[[:alnum:]-]+\.trycloudflare\.com' "$TUNNEL_LOG" | head -n 1 || true)"
    if [[ -n "$PUBLIC_URL" ]]; then
        break
    fi
    if ! kill -0 "$TUNNEL_PID" 2>/dev/null; then
        cat "$TUNNEL_LOG" >&2
        exit 1
    fi
    sleep 0.2
done

if [[ -z "$PUBLIC_URL" ]]; then
    echo "Cloudflare tunnel did not report a public URL" >&2
    cat "$TUNNEL_LOG" >&2
    exit 1
fi

printf 'BASE_URL=%s\n' "$PUBLIC_URL"
printf 'health: %s/healthz\n' "$PUBLIC_URL"
printf 'token file: %s\n' "$TOKEN_FILE"
printf 'hosting API; press Ctrl-C to stop\n'

wait "$TUNNEL_PID"
