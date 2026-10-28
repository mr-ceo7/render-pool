#!/usr/bin/env bash
# ==============================================================================
# entrypoint.sh - Render Container Startup & Graceful Sync Lifecycle
# ==============================================================================

set -e

echo "[entrypoint] === Starting Render Pooled Service ==="

# 1. Pull snapshot from Firebase Storage on startup
if [ -f "/app/render_sync.py" ]; then
    echo "[entrypoint] Running initial state pull from Firebase Storage..."
    python3 /app/render_sync.py pull || echo "[entrypoint] Warning: Initial pull failed or bucket empty. Continuing..."
fi

# Define graceful shutdown handler for SIGTERM / SIGINT
cleanup() {
    echo ""
    echo "[entrypoint] Received termination signal (SIGTERM/SIGINT)!"
    echo "[entrypoint] Flushing state back to Firebase Storage before shutdown..."
    if [ -f "/app/render_sync.py" ]; then
        python3 /app/render_sync.py push || echo "[entrypoint] Warning: Shutdown push failed."
    fi
    echo "[entrypoint] Cleanup complete. Terminating process."
    kill -TERM "$PID" 2>/dev/null || true
    wait "$PID" 2>/dev/null || true
    exit 0
}

trap cleanup SIGTERM SIGINT

PORT="${PORT:-8000}"
echo "[entrypoint] Starting FastAPI Uvicorn server on port ${PORT}..."

# Start Uvicorn in background so bash can trap SIGTERM
uvicorn main:app --host 0.0.0.0 --port "${PORT}" &
PID=$!

# Wait for server process
wait "$PID"
