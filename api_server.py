#!/usr/bin/env python3
"""
API Server for IndexTTS
Independent API service based on gen_single method from webui.py
"""

import argparse
import base64
import json
import logging
import os
import sys
import threading
import time
import uuid
from functools import wraps
from typing import Dict, Any, Optional
from urllib.parse import urlparse
import tempfile

import boto3
import requests
from flask import Flask, request, jsonify
from kafka import KafkaProducer, KafkaConsumer
from kafka.errors import KafkaError

# Add project paths
current_dir = os.path.dirname(os.path.abspath(__file__))
sys.path.append(current_dir)
sys.path.append(os.path.join(current_dir, "indextts"))

from indextts.infer_v2 import IndexTTS2

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = Flask(__name__)

# Global variables
tts_model: Optional[IndexTTS2] = None
kafka_producer: Optional[KafkaProducer] = None
s3_client: Optional[Any] = None
config: Dict[str, Any] = {}

# Default parameters based on the provided JSON
DEFAULT_PARAMS = {
    "emo_control_method": 1,  # "Use emotion reference audio"
    "emo_weight": 0.65,
    "emo_text": "",
    "emo_random": False,
    "max_text_tokens_per_segment": 120,
    "do_sample": True,
    "top_p": 0.8,
    "top_k": 30,
    "temperature": 0.8,
    "length_penalty": 0.0,
    "num_beams": 3,
    "repetition_penalty": 10.0,
    "max_mel_tokens": 1500,
    "vec1": 0, "vec2": 0, "vec3": 0, "vec4": 0,
    "vec5": 0, "vec6": 0, "vec7": 0, "vec8": 0
}

def load_config():
    """Load configuration from environment variables"""
    global config
    config = {
        # Basic auth
        'api_username': os.getenv('API_USERNAME', 'admin'),
        'api_password': os.getenv('API_PASSWORD', 'admin123'),

        # Kafka settings
        'kafka_bootstrap_servers': os.getenv('KAFKA_BOOTSTRAP_SERVERS', 'localhost:9092'),
        'kafka_task_topic': os.getenv('KAFKA_TASK_TOPIC', 'tts_tasks'),
        'kafka_result_topic': os.getenv('KAFKA_RESULT_TOPIC', 'tts_results'),
        'kafka_tts_consumer_group': os.getenv('KAFKA_TTS_CONSUMER_GROUP', 'tts_workers'),
        'kafka_upload_consumer_group': os.getenv('KAFKA_UPLOAD_CONSUMER_GROUP', 'upload_workers'),

        # S3 settings
        's3_access_key': os.getenv('S3_ACCESS_KEY'),
        's3_secret_key': os.getenv('S3_SECRET_KEY'),
        's3_bucket_name': os.getenv('S3_BUCKET_NAME'),
        's3_region': os.getenv('S3_REGION', 'us-east-1'),
        's3_endpoint_url': os.getenv('S3_ENDPOINT_URL'),  # For compatible S3 services
        's3_public_url': os.getenv('S3_PUBLIC_URL'),  # Public access URL (different from API endpoint)

        # Directories
        'upload_audio_dir': './upload_audio',
        'output_dir': './outputs'
    }

def check_auth(username, password):
    """Check if username/password combination is valid"""
    return username == config['api_username'] and password == config['api_password']

def authenticate():
    """Send a 401 response to enable basic auth"""
    return jsonify({'error': 'Authentication required'}), 401, {
        'WWW-Authenticate': 'Basic realm="Login Required"'
    }

def requires_auth(f):
    """Decorator for requiring basic authentication"""
    @wraps(f)
    def decorated(*args, **kwargs):
        auth = request.authorization
        if not auth or not check_auth(auth.username, auth.password):
            return authenticate()
        return f(*args, **kwargs)
    return decorated

def init_kafka_producer():
    """Initialize Kafka producer"""
    global kafka_producer
    try:
        kafka_producer = KafkaProducer(
            bootstrap_servers=config['kafka_bootstrap_servers'].split(','),
            value_serializer=lambda x: json.dumps(x).encode('utf-8'),
            key_serializer=lambda x: x.encode('utf-8') if x else None
        )
        logger.info("Kafka producer initialized successfully")
    except Exception as e:
        logger.error(f"Failed to initialize Kafka producer: {e}")
        raise

def init_s3_client():
    """Initialize S3 client"""
    global s3_client
    try:
        s3_config = {
            'aws_access_key_id': config['s3_access_key'],
            'aws_secret_access_key': config['s3_secret_key'],
            'region_name': config['s3_region']
        }

        if config['s3_endpoint_url']:
            s3_config['endpoint_url'] = config['s3_endpoint_url']

        s3_client = boto3.client('s3', **s3_config)
        logger.info("S3 client initialized successfully")
    except Exception as e:
        logger.error(f"Failed to initialize S3 client: {e}")
        raise

def init_directories():
    """Create necessary directories"""
    os.makedirs(config['upload_audio_dir'], exist_ok=True)
    os.makedirs(config['output_dir'], exist_ok=True)
    logger.info("Directories initialized")

def upload_to_s3(local_file_path: str, task_uuid: str) -> str:
    """Upload file to S3 and return the S3 URL"""
    try:
        s3_key = f"{task_uuid}.wav"
        s3_client.upload_file(
            local_file_path,
            config['s3_bucket_name'],
            s3_key,
            ExtraArgs={'ContentType': 'audio/wav'}
        )

        # Generate the S3 URL based on service type
        s3_url = generate_s3_url(s3_key)

        logger.info(f"File uploaded to S3: {s3_url}")
        return s3_url
    except Exception as e:
        logger.error(f"Failed to upload to S3: {e}")
        raise

def generate_s3_url(s3_key: str) -> str:
    """Generate the correct S3 URL based on the service provider"""
    bucket_name = config['s3_bucket_name']
    endpoint_url = config['s3_endpoint_url']
    public_url = config['s3_public_url']
    region = config['s3_region']

    # Priority 1: Use public URL if configured (for CDN/public access)
    if public_url:
        clean_public_url = public_url.rstrip('/')
        return f"{clean_public_url}/{s3_key}"

    # Priority 2: Use endpoint URL for direct access
    if endpoint_url:
        # For custom endpoints (R2, MinIO, etc.)
        # Clean up endpoint URL (remove trailing slash)
        clean_endpoint = endpoint_url.rstrip('/')

        # Check if it's Cloudflare R2 based on domain
        if 'r2.cloudflarestorage.com' in endpoint_url:
            # Cloudflare R2 uses account-specific endpoints
            return f"{clean_endpoint}/{s3_key}"
        elif 'amazonaws.com' not in endpoint_url:
            # For MinIO and other S3-compatible services
            return f"{clean_endpoint}/{bucket_name}/{s3_key}"
        else:
            # Custom AWS S3 endpoint
            return f"{clean_endpoint}/{bucket_name}/{s3_key}"
    else:
        # Priority 3: Standard AWS S3 URL format
        return f"https://{bucket_name}.s3.{region}.amazonaws.com/{s3_key}"

def is_url(path: str) -> bool:
    """Check if a path is a URL"""
    try:
        result = urlparse(path)
        return all([result.scheme, result.netloc])
    except:
        return False

def download_audio_from_url(url: str, task_uuid: str) -> str:
    """Download audio from URL to local temporary file"""
    try:
        logger.info(f"Downloading audio from URL: {url}")

        # Create temp file in upload_audio directory
        filename = f"{task_uuid}_prompt.wav"
        local_path = os.path.join(config['upload_audio_dir'], filename)

        # Download the file
        response = requests.get(url, timeout=30, stream=True)
        response.raise_for_status()

        # Check content type
        content_type = response.headers.get('content-type', '')
        if not any(audio_type in content_type.lower() for audio_type in ['audio', 'wav', 'mp3', 'flac']):
            logger.warning(f"Downloaded file may not be audio: {content_type}")

        # Save to local file
        with open(local_path, 'wb') as f:
            for chunk in response.iter_content(chunk_size=8192):
                f.write(chunk)

        logger.info(f"Audio downloaded to: {local_path}")
        return local_path

    except Exception as e:
        logger.error(f"Failed to download audio from URL {url}: {e}")
        raise Exception(f"Failed to download audio: {str(e)}")

def send_webhook_callback(hook_url: str, task_uuid: str, s3_url: str = None, status: str = "success", error_message: str = None):
    """Send webhook callback to the provided URL"""
    try:
        payload = {
            "task_uuid": task_uuid,
            "status": status,
            "timestamp": int(time.time())
        }

        if status == "success" and s3_url:
            payload["s3_url"] = s3_url
        elif status == "failed" and error_message:
            payload["error_message"] = error_message

        response = requests.post(
            hook_url,
            json=payload,
            timeout=30,
            headers={'Content-Type': 'application/json'}
        )

        if response.status_code == 200:
            logger.info(f"Webhook callback sent successfully for task {task_uuid}")
        else:
            logger.warning(f"Webhook callback failed with status {response.status_code} for task {task_uuid}")

    except Exception as e:
        logger.error(f"Failed to send webhook callback for task {task_uuid}: {e}")

def process_tts_task(task_data: Dict[str, Any]):
    """Process a single TTS task"""
    task_uuid = task_data['task_uuid']
    logger.info(f"Processing TTS generation for task {task_uuid}")

    downloaded_files = []  # Track downloaded files for cleanup

    try:
        # Extract parameters
        spk_audio_prompt = task_data['spk_audio_prompt']
        text = task_data['text']
        hook_url = task_data['hook_url']
        params = task_data.get('params', {})

        # Handle URL download for speaker audio prompt
        if is_url(spk_audio_prompt):
            local_spk_path = download_audio_from_url(spk_audio_prompt, f"{task_uuid}_spk")
            downloaded_files.append(local_spk_path)
            spk_audio_prompt = local_spk_path

        # Merge with default parameters
        final_params = {**DEFAULT_PARAMS, **params}

        # Generate output path
        output_path = os.path.join(config['output_dir'], f"{task_uuid}.wav")

        # Extract emotion vector if using emotion vector control
        vec = None
        if final_params['emo_control_method'] == 2:
            vec = [
                final_params['vec1'], final_params['vec2'], final_params['vec3'], final_params['vec4'],
                final_params['vec5'], final_params['vec6'], final_params['vec7'], final_params['vec8']
            ]
            vec = tts_model.normalize_emo_vec(vec, apply_bias=True)

        # Prepare emo_ref_path
        emo_ref_path = None
        if final_params['emo_control_method'] == 1:
            # Check if there's a separate emotion audio in params
            emo_audio_param = params.get('emo_audio_prompt')
            if emo_audio_param and is_url(emo_audio_param):
                local_emo_path = download_audio_from_url(emo_audio_param, f"{task_uuid}_emo")
                downloaded_files.append(local_emo_path)
                emo_ref_path = local_emo_path
            elif emo_audio_param:
                emo_ref_path = emo_audio_param
            else:
                emo_ref_path = spk_audio_prompt  # Use same audio for emotion reference

        # Set emo_text
        emo_text = final_params.get('emo_text', '')
        if emo_text == "":
            emo_text = None

        # Prepare generation kwargs
        generation_kwargs = {
            "do_sample": bool(final_params['do_sample']),
            "top_p": float(final_params['top_p']),
            "top_k": int(final_params['top_k']) if int(final_params['top_k']) > 0 else None,
            "temperature": float(final_params['temperature']),
            "length_penalty": float(final_params['length_penalty']),
            "num_beams": final_params['num_beams'],
            "repetition_penalty": float(final_params['repetition_penalty']),
            "max_mel_tokens": int(final_params['max_mel_tokens'])
        }

        # Generate TTS
        logger.info(f"Starting TTS generation for task {task_uuid}")
        output = tts_model.infer(
            spk_audio_prompt=spk_audio_prompt,
            text=text,
            output_path=output_path,
            emo_audio_prompt=emo_ref_path,
            emo_alpha=final_params['emo_weight'],
            emo_vector=vec,
            use_emo_text=(final_params['emo_control_method'] == 3),
            emo_text=emo_text,
            use_random=final_params['emo_random'],
            verbose=True,
            max_text_tokens_per_segment=int(final_params['max_text_tokens_per_segment']),
            **generation_kwargs
        )

        logger.info(f"TTS generation completed for task {task_uuid}")

        # Send result to upload queue instead of uploading directly
        result_data = {
            'task_uuid': task_uuid,
            'hook_url': hook_url,
            'local_file_path': output,
            'status': 'success',
            'timestamp': int(time.time())
        }

        try:
            kafka_producer.send(
                config['kafka_result_topic'],
                key=task_uuid,
                value=result_data
            ).get(timeout=10)
            logger.info(f"TTS result sent to upload queue for task {task_uuid}")
        except KafkaError as e:
            logger.error(f"Failed to send TTS result to Kafka: {e}")
            # Fallback: try to clean up the file
            try:
                os.remove(output)
            except:
                pass

        # Clean up downloaded files
        for file_path in downloaded_files:
            try:
                os.remove(file_path)
                logger.info(f"Cleaned up downloaded file: {file_path}")
            except Exception as e:
                logger.warning(f"Failed to clean up downloaded file {file_path}: {e}")

    except Exception as e:
        logger.error(f"TTS generation failed for task {task_uuid}: {e}")

        # Send failure result to upload queue
        result_data = {
            'task_uuid': task_uuid,
            'hook_url': hook_url,
            'status': 'failed',
            'error_message': str(e),
            'timestamp': int(time.time())
        }

        try:
            kafka_producer.send(
                config['kafka_result_topic'],
                key=task_uuid,
                value=result_data
            ).get(timeout=10)
            logger.info(f"TTS failure result sent to upload queue for task {task_uuid}")
        except KafkaError as e:
            logger.error(f"Failed to send TTS failure result to Kafka: {e}")

        # Clean up downloaded files in case of failure
        for file_path in downloaded_files:
            try:
                os.remove(file_path)
                logger.info(f"Cleaned up downloaded file after failure: {file_path}")
            except Exception as cleanup_error:
                logger.warning(f"Failed to clean up downloaded file {file_path}: {cleanup_error}")

def process_upload_task(result_data: Dict[str, Any]):
    """Process a completed TTS task - upload to S3 and send webhook"""
    task_uuid = result_data['task_uuid']
    hook_url = result_data['hook_url']
    status = result_data['status']

    logger.info(f"Processing upload for task {task_uuid} with status {status}")

    if status == 'success':
        local_file_path = result_data['local_file_path']
        try:
            # Upload to S3
            logger.info(f"Uploading to S3 for task {task_uuid}")
            s3_url = upload_to_s3(local_file_path, task_uuid)

            # Send success webhook
            send_webhook_callback(hook_url, task_uuid, s3_url=s3_url, status="success")

            # Clean up local file
            try:
                os.remove(local_file_path)
                logger.info(f"Cleaned up local file for task {task_uuid}")
            except Exception as e:
                logger.warning(f"Failed to clean up local file for task {task_uuid}: {e}")

            logger.info(f"Task {task_uuid} completed successfully")

        except Exception as e:
            logger.error(f"Upload failed for task {task_uuid}: {e}")
            # Send failure webhook
            send_webhook_callback(hook_url, task_uuid, status="failed", error_message=f"Upload failed: {str(e)}")

            # Clean up local file on failure too
            try:
                os.remove(local_file_path)
            except:
                pass
    else:
        # Handle failed TTS task
        error_message = result_data.get('error_message', 'Unknown error')
        send_webhook_callback(hook_url, task_uuid, status="failed", error_message=error_message)
        logger.info(f"Sent failure webhook for task {task_uuid}")

def kafka_tts_consumer_worker():
    """Kafka consumer worker that processes TTS generation tasks"""
    logger.info("Starting Kafka TTS consumer worker")

    try:
        consumer = KafkaConsumer(
            config['kafka_task_topic'],
            bootstrap_servers=config['kafka_bootstrap_servers'].split(','),
            group_id=config['kafka_tts_consumer_group'],
            value_deserializer=lambda x: json.loads(x.decode('utf-8')),
            auto_offset_reset='latest',
            enable_auto_commit=True
        )

        logger.info("Kafka TTS consumer started, waiting for tasks...")

        for message in consumer:
            try:
                task_data = message.value
                logger.info(f"Received TTS task: {task_data.get('task_uuid', 'unknown')}")
                process_tts_task(task_data)
            except Exception as e:
                logger.error(f"Error processing TTS task: {e}")

    except Exception as e:
        logger.error(f"Kafka TTS consumer error: {e}")

def kafka_upload_consumer_worker():
    """Kafka consumer worker that processes upload tasks"""
    logger.info("Starting Kafka upload consumer worker")

    try:
        consumer = KafkaConsumer(
            config['kafka_result_topic'],
            bootstrap_servers=config['kafka_bootstrap_servers'].split(','),
            group_id=config['kafka_upload_consumer_group'],
            value_deserializer=lambda x: json.loads(x.decode('utf-8')),
            auto_offset_reset='latest',
            enable_auto_commit=True
        )

        logger.info("Kafka upload consumer started, waiting for results...")

        for message in consumer:
            try:
                result_data = message.value
                logger.info(f"Received upload task: {result_data.get('task_uuid', 'unknown')}")
                process_upload_task(result_data)
            except Exception as e:
                logger.error(f"Error processing upload task: {e}")

    except Exception as e:
        logger.error(f"Kafka upload consumer error: {e}")

@app.route('/health', methods=['GET'])
def health_check():
    """Health check endpoint"""
    return jsonify({
        'status': 'healthy',
        'timestamp': int(time.time()),
        'model_version': getattr(tts_model, 'model_version', '1.0') if tts_model else None
    })

@app.route('/generate', methods=['POST'])
@requires_auth
def generate_tts():
    """Generate TTS audio"""
    try:
        data = request.json

        # Validate required parameters
        required_fields = ['spk_audio_prompt', 'text', 'hook_url']
        for field in required_fields:
            if field not in data:
                return jsonify({'error': f'Missing required field: {field}'}), 400

        # Generate task UUID
        task_uuid = str(uuid.uuid4())

        # Prepare task data
        task_data = {
            'task_uuid': task_uuid,
            'spk_audio_prompt': data['spk_audio_prompt'],
            'text': data['text'],
            'hook_url': data['hook_url'],
            'params': data.get('params', {}),
            'timestamp': int(time.time())
        }

        # Send to Kafka
        try:
            kafka_producer.send(
                config['kafka_task_topic'],
                key=task_uuid,
                value=task_data
            ).get(timeout=10)
            logger.info(f"Task {task_uuid} sent to Kafka")
        except KafkaError as e:
            logger.error(f"Failed to send task to Kafka: {e}")
            return jsonify({'error': 'Failed to queue task'}), 500

        return jsonify({
            'task_uuid': task_uuid,
            'status': 'queued',
            'message': 'Task has been queued for processing'
        })

    except Exception as e:
        logger.error(f"Error in generate_tts: {e}")
        return jsonify({'error': 'Internal server error'}), 500

@app.errorhandler(404)
def not_found(error):
    return jsonify({'error': 'Endpoint not found'}), 404

@app.errorhandler(500)
def internal_error(error):
    return jsonify({'error': 'Internal server error'}), 500

def main():
    global tts_model

    parser = argparse.ArgumentParser(
        description="IndexTTS API Server",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("--host", type=str, default="127.0.0.1", help="Host to run the API server on")
    parser.add_argument("--port", type=int, default=7861, help="Port to run the API server on")
    parser.add_argument("--model_dir", type=str, default="./checkpoints", help="Model checkpoints directory")
    parser.add_argument("--fp16", action="store_true", default=False, help="Use FP16 for inference if available")
    parser.add_argument("--deepspeed", action="store_true", default=False, help="Use DeepSpeed to accelerate if available")
    parser.add_argument("--cuda_kernel", action="store_true", default=False, help="Use CUDA kernel for inference if available")
    parser.add_argument("--tts-workers", type=int, default=1, help="Number of TTS generation workers")
    parser.add_argument("--upload-workers", type=int, default=1, help="Number of upload workers")
    args = parser.parse_args()

    # Load configuration
    load_config()

    # Check model directory
    if not os.path.exists(args.model_dir):
        logger.error(f"Model directory {args.model_dir} does not exist. Please download the model first.")
        sys.exit(1)

    # Check required model files
    required_files = [
        "bpe.model",
        "gpt.pth",
        "config.yaml",
        "s2mel.pth",
        "wav2vec2bert_stats.pt"
    ]

    for file in required_files:
        file_path = os.path.join(args.model_dir, file)
        if not os.path.exists(file_path):
            logger.error(f"Required file {file_path} does not exist. Please download it.")
            sys.exit(1)

    # Initialize components
    try:
        logger.info("Initializing TTS model...")
        tts_model = IndexTTS2(
            model_dir=args.model_dir,
            cfg_path=os.path.join(args.model_dir, "config.yaml"),
            use_fp16=args.fp16,
            use_deepspeed=args.deepspeed,
            use_cuda_kernel=args.cuda_kernel,
        )

        logger.info("Initializing Kafka producer...")
        init_kafka_producer()

        logger.info("Initializing S3 client...")
        init_s3_client()

        logger.info("Creating directories...")
        init_directories()

    except Exception as e:
        logger.error(f"Failed to initialize components: {e}")
        sys.exit(1)

    # Start TTS worker threads
    for i in range(args.tts_workers):
        worker_thread = threading.Thread(
            target=kafka_tts_consumer_worker,
            name=f"tts-worker-{i}",
            daemon=True
        )
        worker_thread.start()
        logger.info(f"Started TTS worker {i}")

    # Start upload worker threads
    for i in range(args.upload_workers):
        worker_thread = threading.Thread(
            target=kafka_upload_consumer_worker,
            name=f"upload-worker-{i}",
            daemon=True
        )
        worker_thread.start()
        logger.info(f"Started upload worker {i}")

    # Start Flask app
    logger.info(f"Starting API server on {args.host}:{args.port}")
    logger.info(f"Architecture: Client → Flask API → Kafka Task Queue → TTS Workers → Kafka Result Queue → Upload Workers → S3 → Webhook")
    app.run(host=args.host, port=args.port, debug=False, threaded=True)

if __name__ == "__main__":
    main()
