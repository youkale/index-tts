#!/bin/bash

# IndexTTS API Server Stop Script

PID_FILE="./api_server.pid"

if [ ! -f "$PID_FILE" ]; then
    echo "Error: PID file not found. Server may not be running."
    exit 1
fi

PID=$(cat "$PID_FILE")

if [ -z "$PID" ]; then
    echo "Error: PID file is empty."
    exit 1
fi

# Check if process is running
if ps -p "$PID" > /dev/null 2>&1; then
    echo "Stopping API server (PID: $PID)..."
    kill "$PID"

    # Wait for process to stop
    for i in {1..10}; do
        if ! ps -p "$PID" > /dev/null 2>&1; then
            echo "API server stopped successfully."
            rm "$PID_FILE"
            exit 0
        fi
        echo "Waiting for server to stop... ($i/10)"
        sleep 1
    done

    # Force kill if still running
    if ps -p "$PID" > /dev/null 2>&1; then
        echo "Server didn't stop gracefully, forcing kill..."
        kill -9 "$PID"
        sleep 1

        if ! ps -p "$PID" > /dev/null 2>&1; then
            echo "API server force stopped."
            rm "$PID_FILE"
            exit 0
        else
            echo "Error: Failed to stop server."
            exit 1
        fi
    fi
else
    echo "Process with PID $PID is not running."
    rm "$PID_FILE"
    exit 0
fi
