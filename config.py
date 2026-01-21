"""Configuration constants for the remote browser proxy."""

import os

# Server settings
HOST = os.environ.get('APP_HOST', '0.0.0.0')
PORT = int(os.environ.get('APP_PORT', 5000))
DEBUG = os.environ.get('APP_ENV', 'dev') == 'dev'

# Viewport settings
DEFAULT_VIEWPORT_WIDTH = 800
DEFAULT_VIEWPORT_HEIGHT = 600
MIN_VIEWPORT_WIDTH = 640
MIN_VIEWPORT_HEIGHT = 480
MAX_VIEWPORT_WIDTH = 1280
MAX_VIEWPORT_HEIGHT = 1024

# JPEG quality settings
DEFAULT_QUALITY = 60
MIN_QUALITY = 20
MAX_QUALITY = 95

# Streaming settings
DEFAULT_FPS = 5
MIN_FPS = 1
MAX_FPS = 15

# Session settings
SESSION_TIMEOUT_SECONDS = 1800  # 30 minutes
MAX_SESSIONS = 10
CLEANUP_INTERVAL_SECONDS = 60

# Security settings
ALLOWED_SCHEMES = ['http', 'https']
BLOCKED_HOSTS = [
    'localhost',
    '127.0.0.1',
    '0.0.0.0',
]

# Adaptive quality thresholds (RTT in milliseconds)
QUALITY_THRESHOLDS = {
    'fast': {'max_rtt': 100, 'fps': 10, 'quality': 75},
    'medium': {'max_rtt': 300, 'fps': 5, 'quality': 60},
    'slow': {'max_rtt': 1000, 'fps': 2, 'quality': 40},
    'very_slow': {'max_rtt': float('inf'), 'fps': 1, 'quality': 30},
}

# Browser settings
BROWSER_ARGS = [
    '--disable-blink-features=AutomationControlled',
    '--disable-infobars',
]
USER_AGENT = (
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
    '(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
)
