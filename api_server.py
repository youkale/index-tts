#!/usr/bin/env python3
"""
API Server for IndexTTS
Independent API service based on gen_single method from webui.py
"""

from __future__ import annotations

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
import redis
import requests
from flask import Flask, request, jsonify

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
redis_client: Optional[redis.Redis] = None
s3_client: Optional[Any] = None
config: Dict[str, Any] = {}
tts_queue: Optional[RedisPriorityQueue] = None
upload_queue: Optional[RedisPriorityQueue] = None

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

        # Redis settings
        'redis_host': os.getenv('REDIS_HOST', 'localhost'),
        'redis_port': int(os.getenv('REDIS_PORT', '6379')),
        'redis_db': int(os.getenv('REDIS_DB', '0')),
        'redis_password': os.getenv('REDIS_PASSWORD'),
        'redis_tts_queue': os.getenv('REDIS_TTS_QUEUE', 'tts_tasks'),
        'redis_upload_queue': os.getenv('REDIS_UPLOAD_QUEUE', 'tts_results'),

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

def init_redis_client():
    """Initialize Redis client"""
    global redis_client
    try:
        redis_config = {
            'host': config['redis_host'],
            'port': config['redis_port'],
            'db': config['redis_db'],
            'decode_responses': True,
            'socket_connect_timeout': 10,  # 增加连接超时
            'socket_timeout': 30,          # 增加读写超时
            'socket_keepalive': True,      # 启用 keepalive
            'socket_keepalive_options': {},
            'retry_on_timeout': True,      # 超时时重试
            'health_check_interval': 30    # 健康检查间隔
        }

        if config['redis_password']:
            redis_config['password'] = config['redis_password']

        redis_client = redis.Redis(**redis_config)

        # Test connection
        redis_client.ping()
        logger.info("Redis client initialized successfully")
    except Exception as e:
        logger.error(f"Failed to initialize Redis client: {e}")
        raise

def init_priority_queues():
    """Initialize priority queues"""
    global tts_queue, upload_queue
    try:
        tts_queue = RedisPriorityQueue(redis_client, config['redis_tts_queue'])
        upload_queue = RedisPriorityQueue(redis_client, config['redis_upload_queue'])
        logger.info("Priority queues initialized successfully")
    except Exception as e:
        logger.error(f"Failed to initialize priority queues: {e}")
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

class RedisPriorityQueue:
    """Redis-based priority queue using ZSet"""

    def __init__(self, redis_client: redis.Redis, queue_name: str):
        self.redis_client = redis_client
        self.queue_name = queue_name

    def calculate_score(self, priority: int = 3) -> float:
        """Calculate score for priority queue: timestamp * (6 - priority)
        Lower score = higher priority (processed first)
        priority 1-5: 1=lowest, 5=highest priority
        """
        timestamp = time.time()
        # Invert priority: priority 5 -> weight 1, priority 1 -> weight 5
        priority_weight = 6 - priority
        return timestamp * priority_weight

    def enqueue(self, task_data: Dict[str, Any], priority: int = 3) -> bool:
        """Add task to priority queue"""
        try:
            score = self.calculate_score(priority)
            task_json = json.dumps(task_data)

            # Use zadd to add task with score
            result = self.redis_client.zadd(self.queue_name, {task_json: score})

            logger.info(f"Enqueued task {task_data.get('task_uuid', 'unknown')} with priority {priority}, score {score}")
            return result > 0

        except Exception as e:
            logger.error(f"Failed to enqueue task: {e}")
            return False

    def dequeue(self, timeout: int = 0) -> Optional[Dict[str, Any]]:
        """Get highest priority task from queue"""
        max_retries = 3
        retry_count = 0

        while retry_count < max_retries:
            try:
                if timeout > 0:
                    # Blocking pop with timeout
                    result = self.redis_client.bzpopmin(self.queue_name, timeout=timeout)
                    if result:
                        queue_name, task_json, score = result
                        return json.loads(task_json)
                else:
                    # Non-blocking pop
                    result = self.redis_client.zpopmin(self.queue_name, count=1)
                    if result:
                        task_json, score = result[0]
                        return json.loads(task_json)

                return None

            except redis.TimeoutError as e:
                retry_count += 1
                logger.warning(f"Redis timeout (attempt {retry_count}/{max_retries}): {e}")
                if retry_count < max_retries:
                    time.sleep(1)  # 短暂等待后重试
                    continue
                else:
                    logger.error(f"Redis timeout after {max_retries} attempts")
                    return None

            except redis.ConnectionError as e:
                retry_count += 1
                logger.warning(f"Redis connection error (attempt {retry_count}/{max_retries}): {e}")
                if retry_count < max_retries:
                    time.sleep(2)  # 等待更长时间后重试
                    try:
                        # 尝试重新连接
                        self.redis_client.ping()
                    except:
                        pass
                    continue
                else:
                    logger.error(f"Redis connection failed after {max_retries} attempts")
                    return None

            except Exception as e:
                logger.error(f"Unexpected error in dequeue: {e}")
                return None

        return None

    def size(self) -> int:
        """Get queue size"""
        try:
            return self.redis_client.zcard(self.queue_name)
        except Exception as e:
            logger.error(f"Failed to get queue size: {e}")
            return 0

    def clear(self) -> bool:
        """Clear all tasks from queue"""
        try:
            return self.redis_client.delete(self.queue_name) > 0
        except Exception as e:
            logger.error(f"Failed to clear queue: {e}")
            return False

def upload_to_s3(local_file_path: str, task_uuid: str, max_retries: int = 3) -> str:
    """Upload file to S3 with retry logic"""
    s3_key = f"{task_uuid}.wav"

    for attempt in range(max_retries):
        try:
            logger.info(f"[Task {task_uuid}] [S3] Upload attempt {attempt + 1}/{max_retries}")

            s3_client.upload_file(
                local_file_path,
                config['s3_bucket_name'],
                s3_key,
                ExtraArgs={'ContentType': 'audio/wav'}
            )

            # Generate the S3 URL based on service type
            s3_url = generate_s3_url(s3_key)

            logger.info(f"[Task {task_uuid}] [S3] Upload successful on attempt {attempt + 1}: {s3_url}")
            return s3_url

        except Exception as e:
            logger.warning(f"[Task {task_uuid}] [S3] Upload attempt {attempt + 1}/{max_retries} failed: {e}")

            if attempt < max_retries - 1:
                # 指数退避重试
                wait_time = 2 ** attempt  # 1秒, 2秒, 4秒
                logger.info(f"[Task {task_uuid}] [S3] Retrying in {wait_time} seconds...")
                time.sleep(wait_time)
            else:
                # 所有重试都失败
                logger.error(f"[Task {task_uuid}] [S3] All {max_retries} upload attempts failed")
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

def send_webhook_callback(hook_url: str, task_uuid: str, s3_url: str = None, status: str = "success", error_message: str = None, max_retries: int = 3):
    """Send webhook callback to the provided URL with retry logic"""

    # Validate hook_url
    try:
        parsed = urlparse(hook_url)
        if not all([parsed.scheme, parsed.netloc]) or parsed.scheme not in ['http', 'https']:
            logger.error(f"[Task {task_uuid}] Invalid hook_url: {hook_url}")
            return False
    except Exception as e:
        logger.error(f"[Task {task_uuid}] Failed to parse hook_url: {e}")
        return False

    payload = {
        "task_uuid": task_uuid,
        "status": status,
        "timestamp": int(time.time())
    }

    if status == "success" and s3_url:
        payload["s3_url"] = s3_url
    elif status == "failed" and error_message:
        payload["error_message"] = error_message

    logger.info(f"[Task {task_uuid}] [CALLBACK] Starting webhook callback to {hook_url}, status: {status}")

    # Retry logic with exponential backoff
    for attempt in range(max_retries):
        try:
            response = requests.post(
                hook_url,
                json=payload,
                timeout=40,  # 40秒超时
                headers={'Content-Type': 'application/json'}
            )

            # 检查所有2xx状态码
            if 200 <= response.status_code < 300:
                logger.info(f"[Task {task_uuid}] [CALLBACK] Webhook callback sent successfully (attempt {attempt + 1}/{max_retries}), status code: {response.status_code}")
                return True
            else:
                logger.warning(f"[Task {task_uuid}] [CALLBACK] Webhook failed with status {response.status_code} (attempt {attempt + 1}/{max_retries}), response: {response.text[:200]}")

        except requests.Timeout as e:
            logger.warning(f"[Task {task_uuid}] [CALLBACK] Webhook timeout (attempt {attempt + 1}/{max_retries}): {e}")
        except Exception as e:
            logger.error(f"[Task {task_uuid}] [CALLBACK] Failed to send webhook (attempt {attempt + 1}/{max_retries}): {e}")

        # 指数退避
        if attempt < max_retries - 1:
            wait_time = 2 ** attempt  # 1秒, 2秒, 4秒...
            logger.info(f"[Task {task_uuid}] [CALLBACK] Retrying in {wait_time} seconds...")
            time.sleep(wait_time)

    # 所有重试失败
    logger.error(f"[Task {task_uuid}] [CALLBACK] Webhook callback failed after {max_retries} attempts")

    # 保存到Redis待重试队列
    try:
        failed_webhook_data = {
            'hook_url': hook_url,
            'payload': payload,
            'task_uuid': task_uuid,
            'timestamp': int(time.time())
        }
        redis_client.rpush('failed_webhooks', json.dumps(failed_webhook_data))
        logger.info(f"[Task {task_uuid}] [CALLBACK] Saved failed webhook to Redis for later retry")
    except Exception as e:
        logger.error(f"[Task {task_uuid}] [CALLBACK] Failed to save failed webhook to Redis: {e}")

    return False

def process_tts_task(task_data: Dict[str, Any]):
    """Process a single TTS task"""
    task_uuid = task_data['task_uuid']
    start_time = time.time()
    logger.info(f"[Task {task_uuid}] [TTS] Starting TTS generation task")

    downloaded_files = []  # Track downloaded files for cleanup

    try:
        # Extract parameters
        spk_audio_prompt = task_data['spk_audio_prompt']
        text = task_data['text']
        hook_url = task_data['hook_url']
        params = task_data.get('params', {})

        logger.info(f"[Task {task_uuid}] [TTS] Text length: {len(text)} characters, speaker audio: {spk_audio_prompt[:100]}...")

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
        logger.info(f"[Task {task_uuid}] [TTS] Starting voice cloning and synthesis...")
        tts_start_time = time.time()

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

        tts_duration = time.time() - tts_start_time
        logger.info(f"[Task {task_uuid}] [TTS] Voice cloning completed in {tts_duration:.2f} seconds, output saved to: {output}")

        # Send result to upload queue instead of uploading directly
        result_data = {
            'task_uuid': task_uuid,
            'hook_url': hook_url,
            'local_file_path': output,
            'status': 'success',
            'timestamp': int(time.time()),
            'priority': task_data.get('priority', 3)  # 保持相同优先级
        }

        try:
            success = upload_queue.enqueue(result_data, priority=result_data['priority'])
            if success:
                total_duration = time.time() - start_time
                logger.info(f"[Task {task_uuid}] [TTS] TTS result sent to upload queue (total processing time: {total_duration:.2f}s)")
            else:
                raise Exception("Failed to enqueue result")
        except Exception as e:
            logger.error(f"[Task {task_uuid}] [TTS] Failed to send TTS result to Redis: {e}")
            # Fallback: try to clean up the file
            try:
                os.remove(output)
            except:
                pass

        # Clean up downloaded files
        for file_path in downloaded_files:
            try:
                os.remove(file_path)
                logger.info(f"[Task {task_uuid}] [TTS] Cleaned up downloaded file: {file_path}")
            except Exception as e:
                logger.warning(f"[Task {task_uuid}] [TTS] Failed to clean up downloaded file {file_path}: {e}")

    except Exception as e:
        total_duration = time.time() - start_time
        logger.error(f"[Task {task_uuid}] [TTS] TTS generation failed after {total_duration:.2f}s: {e}")

        # Send failure result to upload queue
        result_data = {
            'task_uuid': task_uuid,
            'hook_url': hook_url,
            'status': 'failed',
            'error_message': str(e),
            'timestamp': int(time.time()),
            'priority': task_data.get('priority', 3)  # 保持相同优先级
        }

        try:
            success = upload_queue.enqueue(result_data, priority=result_data['priority'])
            if success:
                logger.info(f"[Task {task_uuid}] [TTS] TTS failure result sent to upload queue")
            else:
                logger.error(f"[Task {task_uuid}] [TTS] Failed to enqueue failure result")
        except Exception as queue_error:
            logger.error(f"[Task {task_uuid}] [TTS] Failed to send TTS failure result to Redis: {queue_error}")

        # Clean up downloaded files in case of failure
        for file_path in downloaded_files:
            try:
                os.remove(file_path)
                logger.info(f"[Task {task_uuid}] [TTS] Cleaned up downloaded file after failure: {file_path}")
            except Exception as cleanup_error:
                logger.warning(f"[Task {task_uuid}] [TTS] Failed to clean up downloaded file {file_path}: {cleanup_error}")

def process_upload_task(result_data: Dict[str, Any]):
    """Process a completed TTS task - upload to S3 and send webhook"""
    task_uuid = result_data['task_uuid']
    hook_url = result_data['hook_url']
    status = result_data['status']

    logger.info(f"[Task {task_uuid}] [UPLOAD] Processing upload task with status: {status}")

    if status == 'success':
        local_file_path = result_data['local_file_path']
        s3_url = None

        try:
            # Upload to S3
            logger.info(f"[Task {task_uuid}] [S3] Starting upload to S3, file: {local_file_path}")
            upload_start_time = time.time()

            s3_url = upload_to_s3(local_file_path, task_uuid)

            upload_duration = time.time() - upload_start_time
            logger.info(f"[Task {task_uuid}] [S3] Upload completed in {upload_duration:.2f}s, S3 URL: {s3_url}")

        except Exception as e:
            logger.error(f"[Task {task_uuid}] [S3] Upload failed: {e}")
            # S3上传失败，发送失败webhook
            send_webhook_callback(hook_url, task_uuid, status="failed", error_message=f"Upload failed: {str(e)}")

            # Clean up local file on failure
            try:
                os.remove(local_file_path)
            except:
                pass
            return  # 直接返回，不继续处理

        # S3上传成功后，发送webhook回调
        # 注意：即使webhook失败，S3文件已经上传成功
        webhook_success = send_webhook_callback(hook_url, task_uuid, s3_url=s3_url, status="success")

        if not webhook_success:
            logger.warning(f"[Task {task_uuid}] [UPLOAD] Webhook callback failed but S3 upload succeeded. S3 URL: {s3_url}")
            # Webhook失败已经保存到failed_webhooks队列，可以后续重试

        # Clean up local file (无论webhook是否成功，S3已有副本)
        try:
            os.remove(local_file_path)
            logger.info(f"[Task {task_uuid}] [UPLOAD] Cleaned up local file: {local_file_path}")
        except Exception as e:
            logger.warning(f"[Task {task_uuid}] [UPLOAD] Failed to clean up local file: {e}")

        logger.info(f"[Task {task_uuid}] [UPLOAD] Task completed successfully")
    else:
        # Handle failed TTS task
        error_message = result_data.get('error_message', 'Unknown error')
        logger.info(f"[Task {task_uuid}] [UPLOAD] Handling failed TTS task, error: {error_message}")
        send_webhook_callback(hook_url, task_uuid, status="failed", error_message=error_message)

def redis_tts_consumer_worker():
    """Redis consumer worker that processes TTS generation tasks"""
    logger.info("Starting Redis TTS consumer worker")
    consecutive_errors = 0
    max_consecutive_errors = 10

    while True:
        try:
            # Blocking dequeue with 5 second timeout
            task_data = tts_queue.dequeue(timeout=5)

            if task_data is None:
                consecutive_errors = 0  # 重置错误计数
                continue  # No task available, continue polling

            task_uuid = task_data.get('task_uuid', 'unknown')
            priority = task_data.get('priority', 3)
            logger.info(f"Received TTS task: {task_uuid} with priority {priority}")

            try:
                # 处理任务
                process_tts_task(task_data)
                logger.info(f"Successfully processed TTS task: {task_uuid}")
                consecutive_errors = 0  # 重置错误计数

            except Exception as e:
                logger.error(f"Error processing TTS task {task_uuid}: {e}")
                # 任务已经从队列中移除，不需要额外处理

        except Exception as e:
            consecutive_errors += 1
            logger.error(f"Redis TTS consumer error ({consecutive_errors}/{max_consecutive_errors}): {e}")

            if consecutive_errors >= max_consecutive_errors:
                logger.critical(f"Too many consecutive errors ({consecutive_errors}), stopping worker")
                break

            # 根据错误次数调整休眠时间
            sleep_time = min(consecutive_errors * 2, 30)  # 最多休眠30秒
            time.sleep(sleep_time)

def redis_upload_consumer_worker():
    """Redis consumer worker that processes upload tasks"""
    logger.info("Starting Redis upload consumer worker")
    consecutive_errors = 0
    max_consecutive_errors = 10

    while True:
        try:
            # Blocking dequeue with 5 second timeout
            result_data = upload_queue.dequeue(timeout=5)

            if result_data is None:
                consecutive_errors = 0  # 重置错误计数
                continue  # No task available, continue polling

            task_uuid = result_data.get('task_uuid', 'unknown')
            priority = result_data.get('priority', 3)
            logger.info(f"Received upload task: {task_uuid} with priority {priority}")

            try:
                # 处理上传任务
                process_upload_task(result_data)
                logger.info(f"Successfully processed upload task: {task_uuid}")
                consecutive_errors = 0  # 重置错误计数

            except Exception as e:
                logger.error(f"Error processing upload task {task_uuid}: {e}")
                # 任务已经从队列中移除，不需要额外处理

        except Exception as e:
            consecutive_errors += 1
            logger.error(f"Redis upload consumer error ({consecutive_errors}/{max_consecutive_errors}): {e}")

            if consecutive_errors >= max_consecutive_errors:
                logger.critical(f"Too many consecutive errors ({consecutive_errors}), stopping worker")
                break

            # 根据错误次数调整休眠时间
            sleep_time = min(consecutive_errors * 2, 30)  # 最多休眠30秒
            time.sleep(sleep_time)

@app.route('/health', methods=['GET'])
def health_check():
    """Health check endpoint"""
    health_status = {
        'status': 'healthy',
        'timestamp': int(time.time()),
        'model_version': getattr(tts_model, 'model_version', '1.0') if tts_model else None,
        'components': {}
    }

    # Check Redis connection
    try:
        if redis_client:
            redis_client.ping()
            health_status['components']['redis'] = {
                'status': 'healthy',
                'tts_queue_size': tts_queue.size() if tts_queue else 0,
                'upload_queue_size': upload_queue.size() if upload_queue else 0
            }
        else:
            health_status['components']['redis'] = {'status': 'not_initialized'}
    except Exception as e:
        health_status['status'] = 'degraded'
        health_status['components']['redis'] = {
            'status': 'unhealthy',
            'error': str(e)
        }

    # Check TTS model
    if tts_model:
        health_status['components']['tts_model'] = {'status': 'loaded'}
    else:
        health_status['status'] = 'degraded'
        health_status['components']['tts_model'] = {'status': 'not_loaded'}

    return jsonify(health_status)

@app.route('/generate', methods=['POST'])
@requires_auth
def generate_tts():
    """Generate TTS audio"""
    try:
        data = request.json

        # Generate task UUID first for logging
        task_uuid = str(uuid.uuid4())

        logger.info(f"[Task {task_uuid}] [API] Received TTS generation request from {request.remote_addr}")

        # Validate required parameters
        required_fields = ['spk_audio_prompt', 'text', 'hook_url']
        for field in required_fields:
            if field not in data:
                logger.warning(f"[Task {task_uuid}] [API] Missing required field: {field}")
                return jsonify({'error': f'Missing required field: {field}'}), 400

        # Validate priority parameter
        priority = data.get('priority', 3)  # Default to medium priority
        if not isinstance(priority, int) or priority < 1 or priority > 5:
            logger.warning(f"[Task {task_uuid}] [API] Invalid priority value: {priority}")
            return jsonify({'error': 'Priority must be an integer between 1 and 5 (1=lowest, 5=highest)'}), 400

        # Log request details
        text_preview = data['text'][:100] + "..." if len(data['text']) > 100 else data['text']
        logger.info(f"[Task {task_uuid}] [API] Request details - priority: {priority}, text_length: {len(data['text'])}, text_preview: {text_preview}")
        logger.info(f"[Task {task_uuid}] [API] Speaker audio: {data['spk_audio_prompt'][:100]}..., hook_url: {data['hook_url']}")

        # Prepare task data
        task_data = {
            'task_uuid': task_uuid,
            'spk_audio_prompt': data['spk_audio_prompt'],
            'text': data['text'],
            'hook_url': data['hook_url'],
            'params': data.get('params', {}),
            'priority': priority,
            'timestamp': int(time.time())
        }

        # Send to Redis priority queue
        try:
            success = tts_queue.enqueue(task_data, priority=priority)
            if success:
                logger.info(f"[Task {task_uuid}] [API] Task queued successfully with priority {priority}, queue size: {tts_queue.size()}")
            else:
                raise Exception("Failed to enqueue task")
        except Exception as e:
            logger.error(f"[Task {task_uuid}] [API] Failed to send task to Redis: {e}")
            return jsonify({'error': 'Failed to queue task'}), 500

        return jsonify({
            'task_uuid': task_uuid,
            'status': 'queued',
            'message': 'Task has been queued for processing'
        })

    except Exception as e:
        logger.error(f"[API] Error in generate_tts: {e}")
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

        logger.info("Initializing Redis client...")
        init_redis_client()

        logger.info("Initializing priority queues...")
        init_priority_queues()

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
            target=redis_tts_consumer_worker,
            name=f"tts-worker-{i}",
            daemon=True
        )
        worker_thread.start()
        logger.info(f"Started TTS worker {i}")

    # Start upload worker threads
    for i in range(args.upload_workers):
        worker_thread = threading.Thread(
            target=redis_upload_consumer_worker,
            name=f"upload-worker-{i}",
            daemon=True
        )
        worker_thread.start()
        logger.info(f"Started upload worker {i}")

    # Start Flask app
    logger.info(f"Starting API server on {args.host}:{args.port}")
    logger.info(f"Architecture: Client → Flask API → Redis Priority Queue → TTS Workers → Redis Priority Queue → Upload Workers → S3 → Webhook")
    app.run(host=args.host, port=args.port, debug=False, threaded=True)

if __name__ == "__main__":
    main()
