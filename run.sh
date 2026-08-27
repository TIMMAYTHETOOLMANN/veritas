#!/bin/bash
# One-liner starter: fires up the Hermes bridge API + the queue worker.
set -e
cd "$(dirname "$0")"

echo "Starting VERITAS Queue System..."

python3 hermes_api.py &
API_PID=$!

# wait for the API to bind
sleep 2

# worker runs forever (Ctrl-C kills both)
trap 'kill $API_PID 2>/dev/null' EXIT
python3 worker.py