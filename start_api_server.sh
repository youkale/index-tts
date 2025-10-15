#!/bin/bash

# IndexTTS API Server Startup Script
# Uses Redis for priority-based task queuing

set -e

# Parse command line arguments
DAEMON_MODE=false
LOG_FILE="./api_server.log"

while [[ $# -gt 0 ]]; do
    case $1 in
        -d|--daemon)
            DAEMON_MODE=true
            shift
            ;;
        --log-file)
            LOG_FILE="$2"
            shift 2
            ;;
        *)
            # Pass through other arguments to the Python script
            break
            ;;
    esac
done

echo "Starting IndexTTS API Server..."

# Check if uv is available
if ! command -v uv &> /dev/null; then
    echo "Error: uv is not installed. Please install uv first:"
    echo "  curl -LsSf https://astral.sh/uv/install.sh | sh"
    exit 1
fi

# Check if .env file exists
if [ ! -f .env ]; then
    echo "Warning: .env file not found. Using default configuration."
    echo "Copy config.env.example to .env and configure it for production use."
fi

# Load environment variables if .env exists
if [ -f .env ]; then
    export $(cat .env | grep -v '^#' | xargs)
fi

# Default values
MODEL_DIR=${MODEL_DIR:-"./checkpoints"}
HOST=${HOST:-"127.0.0.1"}
PORT=${PORT:-"7861"}
TTS_WORKERS=${TTS_WORKERS:-"1"}
UPLOAD_WORKERS=${UPLOAD_WORKERS:-"1"}
REDIS_HOST=${REDIS_HOST:-"localhost"}
REDIS_PORT=${REDIS_PORT:-"6379"}

# Check if model directory exists
if [ ! -d "$MODEL_DIR" ]; then
    echo "Error: Model directory $MODEL_DIR does not exist."
    echo "Please download the IndexTTS model first."
    exit 1
fi

# Check required model files
REQUIRED_FILES=("bpe.model" "gpt.pth" "config.yaml" "s2mel.pth" "wav2vec2bert_stats.pt")
for file in "${REQUIRED_FILES[@]}"; do
    if [ ! -f "$MODEL_DIR/$file" ]; then
        echo "Error: Required model file $MODEL_DIR/$file not found."
        exit 1
    fi
done

# Check Redis connection
echo "Checking Redis connection..."
if command -v redis-cli &> /dev/null; then
    if redis-cli -h "$REDIS_HOST" -p "$REDIS_PORT" ping &> /dev/null; then
        echo "✓ Redis connection successful"
    else
        echo "Warning: Cannot connect to Redis at $REDIS_HOST:$REDIS_PORT"
        echo "Make sure Redis is running and accessible"
        echo "You can start Redis with: redis-server"
    fi
else
    echo "Note: redis-cli not found, skipping Redis connection check"
fi

# Create necessary directories
mkdir -p upload_audio
mkdir -p outputs

echo "Configuration:"
echo "  Model Directory: $MODEL_DIR"
echo "  Host: $HOST"
echo "  Port: $PORT"
echo "  TTS Workers: $TTS_WORKERS"
echo "  Upload Workers: $UPLOAD_WORKERS"
echo "  Redis Host: $REDIS_HOST:$REDIS_PORT"
echo "  S3 Bucket: ${S3_BUCKET_NAME:-not configured}"
echo "  Daemon Mode: $DAEMON_MODE"
if [ "$DAEMON_MODE" = true ]; then
    echo "  Log File: $LOG_FILE"
fi

# Start the API server
if [ "$DAEMON_MODE" = true ]; then
    echo "Starting API server in daemon mode..."
    nohup uv run api_server.py \
        --model_dir "$MODEL_DIR" \
        --host "$HOST" \
        --port "$PORT" \
        --tts-workers "$TTS_WORKERS" \
        --upload-workers "$UPLOAD_WORKERS" \
        "$@" > "$LOG_FILE" 2>&1 &

    PID=$!
    echo "$PID" > api_server.pid
    echo "API server started with PID: $PID"
    echo "Log file: $LOG_FILE"
    echo "To stop the server, run: kill \$(cat api_server.pid)"
    echo "To view logs, run: tail -f $LOG_FILE"
else
    uv run api_server.py \
        --model_dir "$MODEL_DIR" \
        --host "$HOST" \
        --port "$PORT" \
        --tts-workers "$TTS_WORKERS" \
        --upload-workers "$UPLOAD_WORKERS" \
        "$@"
fi
