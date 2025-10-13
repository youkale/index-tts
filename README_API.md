# IndexTTS API Server

Independent API service for IndexTTS text-to-speech generation using Redis priority queuing and S3 storage.

## Features

- **REST API**: Simple HTTP endpoints for TTS generation
- **Priority Processing**: Uses Redis ZSet for priority-based task queuing and async processing
- **Cloud Storage**: Automatic upload to S3-compatible storage
- **Webhook Callbacks**: Notify clients when tasks complete
- **Basic Authentication**: Simple username/password authentication
- **Health Monitoring**: Health check endpoint for monitoring

## Quick Start

### 1. Install Dependencies

```bash
# Install with API dependencies
uv sync --extra api

# Or install all extras
uv sync --all-extras
```

### 2. Setup Environment

Copy the example configuration:
```bash
cp config.env.example .env
```

Edit `.env` with your configuration:
```bash
# Basic Authentication
API_USERNAME=your_username
API_PASSWORD=your_password

# Redis Configuration
REDIS_HOST=localhost
REDIS_PORT=6379
REDIS_DB=0
REDIS_PASSWORD=
REDIS_TTS_QUEUE=tts_tasks
REDIS_UPLOAD_QUEUE=tts_results

# S3 Configuration
S3_ACCESS_KEY=your_access_key
S3_SECRET_KEY=your_secret_key
S3_BUCKET_NAME=your_bucket_name
S3_REGION=us-east-1
```

### 3. Setup Infrastructure

#### Redis
```bash
# Using Docker
docker run -d --name redis \
  -p 6379:6379 \
  redis:7-alpine

# Or install locally
# Ubuntu/Debian
sudo apt-get install redis-server

# macOS
brew install redis
```

#### S3-Compatible Storage

The API supports various S3-compatible storage services:

**Supported Storage Services:**
- ✅ **AWS S3** - Standard Amazon S3 storage
- ✅ **Cloudflare R2** - Cloudflare's S3-compatible storage
- ✅ **MinIO** - Self-hosted S3-compatible storage
- ✅ **DigitalOcean Spaces** - S3-compatible object storage
- ✅ **Backblaze B2** - S3-compatible cloud storage
- ✅ **Alibaba Cloud OSS** - With S3-compatible API
- ✅ **Any S3-compatible service** - Using custom endpoint URLs

##### AWS S3
```bash
# Standard AWS S3 - no endpoint URL needed
S3_ENDPOINT_URL=
S3_REGION=us-east-1
```

##### Cloudflare R2
```bash
# Cloudflare R2 Storage
S3_ENDPOINT_URL=https://your-account-id.r2.cloudflarestorage.com
S3_PUBLIC_URL=https://your-custom-domain.com  # Optional: Custom domain for public access
S3_REGION=auto
```

**Cloudflare R2 Public Access Options:**
- **Direct R2 URL**: Files accessible via R2's default domain
- **Custom Domain**: Configure a custom domain in Cloudflare for branded URLs
- **CDN Integration**: Use Cloudflare CDN for global distribution

**URL Configuration Priority:**
1. **S3_PUBLIC_URL** - Used for public file access (highest priority)
2. **S3_ENDPOINT_URL** - Used for API operations and fallback access
3. **Default AWS format** - Used when no custom endpoints are configured

**Example Configurations:**

**Cloudflare R2 with Custom Domain:**
```bash
S3_ENDPOINT_URL=https://abc123.r2.cloudflarestorage.com
S3_PUBLIC_URL=https://cdn.yourdomain.com
```

**Cloudflare R2 without Custom Domain:**
```bash
S3_ENDPOINT_URL=https://abc123.r2.cloudflarestorage.com
# S3_PUBLIC_URL=  # Leave empty to use endpoint URL
```

##### MinIO (for local testing)
```bash
# Using MinIO for local testing
docker run -d --name minio \
  -p 9000:9000 -p 9001:9001 \
  -e MINIO_ACCESS_KEY=minioadmin \
  -e MINIO_SECRET_KEY=minioadmin \
  minio/minio server /data --console-address ":9001"

# Configuration
S3_ENDPOINT_URL=http://localhost:9000
S3_PUBLIC_URL=http://localhost:9000/your-bucket-name  # Public access URL
S3_ACCESS_KEY=minioadmin
S3_SECRET_KEY=minioadmin
```

### 4. Start the API Server

```bash
python api_server.py --model_dir ./checkpoints --host 127.0.0.1 --port 7861
```

## API Endpoints

### Health Check
```bash
GET /health
```

Response:
```json
{
    "status": "healthy",
    "timestamp": 1640995200,
    "model_version": "2.0"
}
```

### Generate TTS
```bash
POST /generate
Authorization: Basic <base64(username:password)>
Content-Type: application/json
```

Request body:
```json
{
    "spk_audio_prompt": "https://example.com/speaker_voice.wav",
    "text": "Hello, this is a test of text-to-speech generation.",
    "hook_url": "https://your-app.com/webhook/tts-complete",
    "priority": 4,
    "params": {
        "emo_control_method": 1,
        "emo_weight": 0.65,
        "temperature": 0.8,
        "max_text_tokens_per_segment": 120
    }
}
```

Response:
```json
{
    "task_uuid": "550e8400-e29b-41d4-a716-446655440000",
    "status": "queued",
    "message": "Task has been queued for processing"
}
```

### Webhook Callback

When the task completes, a POST request will be sent to your `hook_url`:

**Success:**
```json
{
    "task_uuid": "550e8400-e29b-41d4-a716-446655440000",
    "status": "success",
    "s3_url": "https://your-bucket.s3.amazonaws.com/550e8400-e29b-41d4-a716-446655440000.wav",
    "timestamp": 1640995200
}
```

**Failure:**
```json
{
    "task_uuid": "550e8400-e29b-41d4-a716-446655440000",
    "status": "failed",
    "error_message": "Error description here",
    "timestamp": 1640995200
}
```

## Parameters

### Required Parameters
- `spk_audio_prompt`: URL or local path to speaker reference audio file
- `text`: Text to synthesize
- `hook_url`: Webhook URL for completion notifications

### Optional Parameters
- `priority`: Task priority level (1-5, default: 3)
  - 1: Lowest priority
  - 2: Low priority
  - 3: Medium priority (default)
  - 4: High priority
  - 5: Highest priority

### Audio Input Support
The API supports both local file paths and HTTP/HTTPS URLs for audio inputs:
- **URLs**: `https://example.com/speaker_voice.wav`
- **Local paths**: `./uploads/speaker_voice.wav`

When URLs are provided, the server automatically downloads the audio files temporarily for processing and cleans them up after completion.

### Optional Parameters (in `params` object)
- `emo_control_method`: Emotion control method (0-3)
  - 0: Same as speaker voice
  - 1: Use emotion reference audio
  - 2: Use emotion vectors
  - 3: Use emotion text description
- `emo_audio_prompt`: URL or path to emotion reference audio (when emo_control_method=1)
- `emo_weight`: Emotion weight (0.0-1.0, default: 0.65)
- `temperature`: Sampling temperature (0.1-2.0, default: 0.8)
- `top_p`: Top-p sampling (0.0-1.0, default: 0.8)
- `top_k`: Top-k sampling (0-100, default: 30)
- `max_text_tokens_per_segment`: Max tokens per segment (default: 120)
- `do_sample`: Enable sampling (default: true)
- `num_beams`: Number of beams for beam search (default: 3)
- `repetition_penalty`: Repetition penalty (default: 10.0)
- `max_mel_tokens`: Maximum mel tokens (default: 1500)

### Emotion Vector Parameters (when emo_control_method=2)
- `vec1` to `vec8`: Emotion vector values (0.0-1.0)
  - vec1: Joy (喜)
  - vec2: Anger (怒)
  - vec3: Sadness (哀)
  - vec4: Fear (惧)
  - vec5: Disgust (厌恶)
  - vec6: Depression (低落)
  - vec7: Surprise (惊喜)
  - vec8: Calm (平静)

## Command Line Options

```bash
python api_server.py [OPTIONS]

Options:
  --host TEXT              Host to run the API server on [default: 127.0.0.1]
  --port INTEGER           Port to run the API server on [default: 7861]
  --model_dir TEXT         Model checkpoints directory [default: ./checkpoints]
  --fp16                   Use FP16 for inference if available
  --deepspeed             Use DeepSpeed to accelerate if available
  --cuda_kernel           Use CUDA kernel for inference if available
  --tts-workers INTEGER   Number of TTS generation workers [default: 1]
  --upload-workers INTEGER Number of upload workers [default: 1]
```

## Testing

Run the test suite:
```bash
python test_api.py
```

## Architecture

```
Client → Flask API → Redis Priority Queue → TTS Workers → Redis Priority Queue → Upload Workers → S3 → Webhook
```

1. **Client sends request** to `/generate` endpoint
2. **API validates** request and generates task UUID
3. **Task queued** in Redis priority queue with priority level
4. **TTS worker consumes** highest priority task from Redis
5. **TTS generation** using IndexTTS2 model
6. **Result sent** to Redis priority queue with same priority
7. **Upload worker consumes** highest priority result from Redis
8. **Audio uploaded** to S3 storage
9. **Webhook callback** sent to client
10. **Cleanup** temporary files

This architecture separates TTS generation from S3 upload operations, preventing upload delays from blocking TTS processing.

## Error Handling

- Invalid parameters return 400 Bad Request
- Authentication failures return 401 Unauthorized
- Server errors return 500 Internal Server Error
- Failed tasks trigger webhook with error details

## Monitoring

- Use `/health` endpoint for service monitoring
- Check Redis queue sizes for processing health
- Monitor S3 upload success rates
- Track webhook delivery success
