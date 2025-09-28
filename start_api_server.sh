#!/bin/bash

# IndexTTS API Server Startup Script

set -e

echo "Starting IndexTTS API Server..."

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

# Create necessary directories
mkdir -p upload_audio
mkdir -p outputs

echo "Configuration:"
echo "  Model Directory: $MODEL_DIR"
echo "  Host: $HOST"
echo "  Port: $PORT"
echo "  TTS Workers: $TTS_WORKERS"
echo "  Upload Workers: $UPLOAD_WORKERS"
echo "  Kafka Servers: ${KAFKA_BOOTSTRAP_SERVERS:-localhost:9092}"
echo "  S3 Bucket: ${S3_BUCKET_NAME:-not configured}"

# Start the API server
python api_server.py \
    --model_dir "$MODEL_DIR" \
    --host "$HOST" \
    --port "$PORT" \
    --tts-workers "$TTS_WORKERS" \
    --upload-workers "$UPLOAD_WORKERS" \
    "$@"
