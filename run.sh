#!/bin/bash
echo "Starting VERITAS Queue System..."

# Start the API server in the background
python3 hermes_api.py &
API_PID=$!

# Wait a second for API to bind
sleep 2

# Start the Worker (will run forever)
python3 worker.py

# If worker exits, kill API too
kill $API_PID