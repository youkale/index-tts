#!/usr/bin/env python3
"""
Test script for IndexTTS API Server
"""

import base64
import json
import requests
import time

# Configuration
API_BASE_URL = "http://127.0.0.1:7861"
USERNAME = "admin"
PASSWORD = "admin123"

def test_health_check():
    """Test the health check endpoint"""
    print("Testing health check endpoint...")

    try:
        response = requests.get(f"{API_BASE_URL}/health")
        print(f"Health check status: {response.status_code}")
        print(f"Response: {response.json()}")
        return response.status_code == 200
    except Exception as e:
        print(f"Health check failed: {e}")
        return False

def test_generate_tts():
    """Test the TTS generation endpoint"""
    print("\nTesting TTS generation endpoint...")

    # Basic auth
    auth = (USERNAME, PASSWORD)

    # Test payload - you can use either local files or URLs
    payload = {
        "spk_audio_prompt": "examples/voice_01.wav",  # Using local example audio
        # "spk_audio_prompt": "https://example.com/speaker.wav",  # Or use URL
        "text": "Hello, this is a test of the IndexTTS API server.",
        "hook_url": "https://httpbin.org/post",  # Test webhook endpoint
        "params": {
            "emo_control_method": 0,  # Use speaker voice emotion
            # "emo_audio_prompt": "https://example.com/emotion.wav",  # Optional emotion audio URL
            "temperature": 0.8,
            "max_text_tokens_per_segment": 120
        }
    }

    try:
        response = requests.post(
            f"{API_BASE_URL}/generate",
            json=payload,
            auth=auth,
            timeout=30
        )

        print(f"Generate TTS status: {response.status_code}")
        print(f"Response: {response.json()}")
        return response.status_code == 200
    except Exception as e:
        print(f"Generate TTS failed: {e}")
        return False

def test_auth_required():
    """Test that authentication is required"""
    print("\nTesting authentication requirement...")

    payload = {
        "spk_audio_prompt": "examples/voice_01.wav",
        "text": "Test without auth",
        "hook_url": "https://httpbin.org/post"
    }

    try:
        response = requests.post(f"{API_BASE_URL}/generate", json=payload)
        print(f"No auth status: {response.status_code}")
        print(f"Response: {response.json()}")
        return response.status_code == 401
    except Exception as e:
        print(f"Auth test failed: {e}")
        return False

def main():
    """Run all tests"""
    print("IndexTTS API Server Test Suite")
    print("=" * 40)
    print("Architecture: Client → Flask API → Kafka Task Queue → TTS Workers → Kafka Result Queue → Upload Workers → S3 → Webhook")
    print("=" * 40)

    tests = [
        ("Health Check", test_health_check),
        ("Authentication Required", test_auth_required),
        ("Generate TTS", test_generate_tts),
    ]

    results = []
    for test_name, test_func in tests:
        print(f"\nRunning: {test_name}")
        result = test_func()
        results.append((test_name, result))
        print(f"Result: {'PASS' if result else 'FAIL'}")

    print("\n" + "=" * 40)
    print("Test Results Summary:")
    for test_name, result in results:
        status = "PASS" if result else "FAIL"
        print(f"  {test_name}: {status}")

    passed = sum(1 for _, result in results if result)
    total = len(results)
    print(f"\nPassed: {passed}/{total}")
    print("\nNote: The Generate TTS test only verifies task submission.")
    print("Actual TTS processing happens asynchronously via Kafka workers.")

if __name__ == "__main__":
    main()
