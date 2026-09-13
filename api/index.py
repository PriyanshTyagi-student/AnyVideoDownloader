import os
import sys
import re
import json
import time
import stat
import queue
import socket
import ipaddress
import threading
import tempfile
import urllib.parse
from collections import defaultdict, deque

import yt_dlp
from flask import Flask, request, jsonify, Response, send_file, abort

# ==============================================================================
# Configuration & Directory Setup
# ==============================================================================

ROOT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
DOWNLOAD_DIR = os.path.join(tempfile.gettempdir(), 'anydownloader')
os.makedirs(DOWNLOAD_DIR, exist_ok=True)

# Secure Configuration Directory (Outside Web Root)
DEFAULT_CONFIG_DIR = os.path.expanduser('~/.config/anyvideodownloader')
os.makedirs(DEFAULT_CONFIG_DIR, mode=0o700, exist_ok=True)

# Administrator-Provided YouTube Cookies File Path
YOUTUBE_COOKIES_PATH = os.environ.get(
    'YOUTUBE_COOKIES_PATH',
    os.path.join(DEFAULT_CONFIG_DIR, 'youtube_cookies.txt')
)

# Multi-User Concurrency & Limits
MAX_CONCURRENT_DOWNLOADS = int(os.environ.get('MAX_CONCURRENT_DOWNLOADS', 3))
RATE_LIMIT_PER_MINUTE = int(os.environ.get('RATE_LIMIT_PER_MINUTE', 20))
MAX_DURATION_SECONDS = int(os.environ.get('MAX_DURATION_SECONDS', 10800))  # 3 hours max
CLEANUP_INTERVAL_SECONDS = int(os.environ.get('CLEANUP_INTERVAL_SECONDS', 900))  # 15 mins
CLEANUP_FILE_MAX_AGE_SECONDS = int(os.environ.get('CLEANUP_FILE_MAX_AGE_SECONDS', 3600))  # 1 hour

# Concurrency Semaphore
download_semaphore = threading.BoundedSemaphore(MAX_CONCURRENT_DOWNLOADS)

# Rate Limiter State
_rate_limit_lock = threading.Lock()
_rate_limit_records = defaultdict(deque)

# Whitelist of allowed static asset extensions
ALLOWED_STATIC_EXTENSIONS = {
    '.html', '.css', '.js', '.png', '.jpg', '.jpeg', '.gif',
    '.svg', '.ico', '.webp', '.woff', '.woff2', '.ttf'
}

# Whitelist of allowed downloaded media extensions
ALLOWED_MEDIA_EXTENSIONS = {'.mp4', '.mp3', '.m4a', '.webm', '.mkv'}

# ==============================================================================
# Security & Helper Functions
# ==============================================================================

def get_client_ip():
    """Extract real client IP considering Cloudflare and proxy headers."""
    cf_ip = request.headers.get('CF-Connecting-IP')
    if cf_ip:
        return cf_ip.strip()
    x_forwarded = request.headers.get('X-Forwarded-For')
    if x_forwarded:
        return x_forwarded.split(',')[0].strip()
    return request.remote_addr or '127.0.0.1'


def is_rate_limited(client_ip):
    """Sliding-window rate limiter per IP address."""
    now = time.time()
    window_start = now - 60.0
    with _rate_limit_lock:
        timestamps = _rate_limit_records[client_ip]
        while timestamps and timestamps[0] < window_start:
            timestamps.popleft()
        if len(timestamps) >= RATE_LIMIT_PER_MINUTE:
            return True
        timestamps.append(now)
        return False


def is_valid_public_url(url_string):
    """
    Validate that a URL is a well-formed HTTP/HTTPS URL and does not target
    loopback, private, or link-local IP addresses (SSRF protection).
    """
    if not url_string or not isinstance(url_string, str):
        return False, "Invalid URL format."
    if len(url_string) > 2048:
        return False, "URL exceeds maximum permitted length."

    try:
        parsed = urllib.parse.urlsplit(url_string.strip())
    except Exception:
        return False, "Malformed URL."

    if parsed.scheme not in ('http', 'https'):
        return False, "Only HTTP and HTTPS protocols are supported."

    hostname = parsed.hostname
    if not hostname:
        return False, "URL missing valid hostname."

    # Prevent loopback strings
    lower_host = hostname.lower()
    if lower_host in ('localhost', '127.0.0.1', '::1', '0.0.0.0'):
        return False, "Access to local host addresses is forbidden."

    # Resolve IP and check for private / internal network ranges
    try:
        addr_info = socket.getaddrinfo(hostname, None, proto=socket.IPPROTO_TCP)
        for entry in addr_info:
            ip_str = entry[4][0]
            ip_obj = ipaddress.ip_address(ip_str)
            if ip_obj.is_private or ip_obj.is_loopback or ip_obj.is_link_local or ip_obj.is_unspecified or ip_obj.is_multicast:
                return False, "Access to private or local network resources is restricted."
    except socket.gaierror:
        return False, "Could not resolve hostname."
    except Exception:
        return False, "Invalid address resolution."

    return True, None


def is_youtube_url(url_string):
    """Determine if a URL targets an authorized YouTube domain."""
    try:
        parsed = urllib.parse.urlsplit(url_string.strip())
        hostname = (parsed.hostname or '').lower()
        return (
            hostname == 'youtu.be' or
            hostname == 'youtube.com' or
            hostname.endswith('.youtube.com')
        )
    except Exception:
        return False


def get_authorized_youtube_cookie_file(url):
    """
    Returns the path to the administrator-provided YouTube cookie file if:
    1. The target URL is a YouTube domain.
    2. The cookie file exists and is readable.
    Never returns cookie file for non-YouTube requests.
    """
    if not is_youtube_url(url):
        return None

    if not YOUTUBE_COOKIES_PATH:
        return None

    if not os.path.exists(YOUTUBE_COOKIES_PATH) or not os.path.isfile(YOUTUBE_COOKIES_PATH):
        return None

    # Check file size
    try:
        st = os.stat(YOUTUBE_COOKIES_PATH)
        if st.st_size == 0:
            return None

        # Enforce restricted permissions (owner read/write only: 0600)
        current_mode = stat.S_IMODE(st.st_mode)
        if (current_mode & 0o077) != 0:
            try:
                os.chmod(YOUTUBE_COOKIES_PATH, 0o600)
            except Exception:
                pass
    except Exception:
        return None

    return YOUTUBE_COOKIES_PATH


def sanitize_error_message(raw_error):
    """
    Sanitize error messages so that local filesystem paths, cookie values,
    tokens, or internal system configurations are never leaked to clients.
    """
    if not raw_error:
        return "An unknown extraction error occurred."

    err_str = str(raw_error)

    # Specific YouTube error handling
    lower_err = err_str.lower()
    if any(phrase in lower_err for phrase in ['sign in to confirm', 'confirm you’re not a bot', 'bot verification', 'login required']):
        return "YouTube authentication requires valid administrator credentials or session update. Please contact the administrator."
    if 'private video' in lower_err:
        return "This video is private and cannot be accessed."
    if 'video unavailable' in lower_err:
        return "This video is unavailable or has been removed."
    if 'members-only content' in lower_err:
        return "This video is members-only content."
    if 'copyright' in lower_err:
        return "Media unavailable due to copyright restrictions."

    # Redact filesystem paths
    err_str = re.sub(r'(/[\w\-.]+)+', '[path]', err_str)
    # Redact IP addresses
    err_str = re.sub(r'\b\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}\b', '[ip]', err_str)
    # Redact common tokens or cookies
    err_str = re.sub(r'(cookie|token|auth|key)=[^\s&]+', r'\1=[redacted]', err_str, flags=re.IGNORECASE)

    # Cap length to prevent verbose server dumps
    if len(err_str) > 200:
        err_str = err_str[:197] + '...'

    return err_str


def get_ydl_opts(url, dl_type=None, extra_opts=None):
    """
    Construct safe yt-dlp options. Loads administrator YouTube cookies only
    when making an authorized YouTube request. Zero browser profile scraping.
    """
    opts = {
        'quiet': True,
        'no_warnings': True,
        'socket_timeout': 30,
        'js_runtimes': {'node': {}},
        'remote_components': ['ejs:github'],
    }

    # Attach administrator-provided cookies if target is YouTube
    cookie_path = get_authorized_youtube_cookie_file(url)
    if cookie_path:
        opts['cookiefile'] = cookie_path

    if extra_opts:
        opts.update(extra_opts)

    return opts


# ==============================================================================
# Background Temporary File Cleanup Daemon
# ==============================================================================

def cleanup_old_files():
    """Periodically purge temporary media files older than CLEANUP_FILE_MAX_AGE_SECONDS."""
    while True:
        try:
            time.sleep(CLEANUP_INTERVAL_SECONDS)
            now = time.time()
            if os.path.exists(DOWNLOAD_DIR):
                for filename in os.listdir(DOWNLOAD_DIR):
                    filepath = os.path.join(DOWNLOAD_DIR, filename)
                    try:
                        if os.path.isfile(filepath):
                            file_age = now - os.path.getmtime(filepath)
                            if file_age > CLEANUP_FILE_MAX_AGE_SECONDS:
                                os.remove(filepath)
                    except Exception:
                        pass
        except Exception:
            pass


cleanup_thread = threading.Thread(target=cleanup_old_files, daemon=True)
cleanup_thread.start()

# ==============================================================================
# Flask Application Setup & Middleware
# ==============================================================================

app = Flask(__name__, static_folder=None)


@app.after_request
def add_security_headers(response):
    response.headers['Access-Control-Allow-Origin'] = '*'
    response.headers['Access-Control-Allow-Headers'] = 'Content-Type,Authorization'
    response.headers['Access-Control-Allow-Methods'] = 'GET,PUT,POST,DELETE,OPTIONS'
    response.headers['X-Content-Type-Options'] = 'nosniff'
    response.headers['X-Frame-Options'] = 'SAMEORIGIN'
    return response


@app.before_request
def handle_preflight_and_rate_limiting():
    if request.method == 'OPTIONS':
        response = app.make_default_options_response()
        response.headers['Access-Control-Allow-Origin'] = '*'
        response.headers['Access-Control-Allow-Headers'] = 'Content-Type,Authorization'
        response.headers['Access-Control-Allow-Methods'] = 'GET,PUT,POST,DELETE,OPTIONS'
        return response

    # Apply rate limiting on API endpoints
    if request.path.startswith('/api/') or request.path in ('/analyze', '/download'):
        client_ip = get_client_ip()
        if is_rate_limited(client_ip):
            return jsonify({
                "error": "Rate limit exceeded. Please wait a moment before trying again."
            }), 429


# ==============================================================================
# Static File & Media Routes (Hardened against secret leakage)
# ==============================================================================

@app.route('/')
def index():
    return send_file(os.path.join(ROOT_DIR, 'index.html'))


@app.route('/<path:filename>')
def serve_static(filename):
    """
    Serve static assets from the application directory with strict extension whitelisting.
    Blocks any attempt to read .env, cookie files, scripts, or directories.
    """
    # Reject directory traversal and hidden files
    if '..' in filename or filename.startswith('.') or '/.' in filename:
        abort(404)

    # Check extension
    _, ext = os.path.splitext(filename)
    if ext.lower() not in ALLOWED_STATIC_EXTENSIONS:
        abort(404)

    file_path = os.path.abspath(os.path.join(ROOT_DIR, filename))
    # Ensure path stays within ROOT_DIR
    if not file_path.startswith(ROOT_DIR):
        abort(404)

    if os.path.exists(file_path) and os.path.isfile(file_path):
        return send_file(file_path)

    abort(404)


@app.route('/api/serve_file/<dl_type>/<filename>')
@app.route('/serve_file/<dl_type>/<filename>')
def serve_file(dl_type, filename):
    """
    Serve processed media files strictly from DOWNLOAD_DIR with validated filenames.
    Prevents path traversal and credential access.
    """
    safe_filename = os.path.basename(filename)
    _, ext = os.path.splitext(safe_filename)

    if ext.lower() not in ALLOWED_MEDIA_EXTENSIONS:
        return jsonify({"error": "Invalid or disallowed file type."}), 403

    filepath = os.path.join(DOWNLOAD_DIR, safe_filename)
    if os.path.exists(filepath) and os.path.isfile(filepath):
        return send_file(filepath, as_attachment=True)

    return jsonify({"error": "File not found or expired."}), 404


# ==============================================================================
# Core API Endpoints: Analyze & Download
# ==============================================================================

@app.route('/api/analyze', methods=['POST', 'OPTIONS'])
@app.route('/analyze', methods=['POST', 'OPTIONS'])
def analyze():
    if request.method == 'OPTIONS':
        return ('', 204)

    data = request.get_json(silent=True) or {}
    url = (data.get('url') or '').strip()

    valid, err_msg = is_valid_public_url(url)
    if not valid:
        return jsonify({"error": err_msg}), 400

    ydl_opts = get_ydl_opts(url, extra_opts={'skip_download': True})

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)

            if not info:
                return jsonify({"error": "Failed to extract media information."}), 404

            duration = info.get('duration')
            if duration and int(duration) > MAX_DURATION_SECONDS:
                return jsonify({
                    "error": f"Media duration exceeds maximum limit of {MAX_DURATION_SECONDS // 3600} hours."
                }), 400

            if duration:
                mins, secs = divmod(int(duration), 60)
                hours, mins = divmod(mins, 60)
                duration_str = f"{hours:02d}:{mins:02d}:{secs:02d}" if hours else f"{mins:02d}:{secs:02d}"
            else:
                duration_str = "Unknown"

            return jsonify({
                "title": info.get('title', 'Unknown Title'),
                "thumbnail": info.get('thumbnail') or 'https://images.unsplash.com/photo-1618005182384-a83a8bd57fbe?q=80&w=1000&auto=format&fit=crop',
                "duration": duration_str,
                "source": info.get('extractor', 'Unknown').capitalize()
            })
    except Exception as e:
        safe_msg = sanitize_error_message(e)
        return jsonify({"error": safe_msg}), 500


@app.route('/api/download', methods=['POST', 'OPTIONS'])
@app.route('/download', methods=['POST', 'OPTIONS'])
def download():
    if request.method == 'OPTIONS':
        return ('', 204)

    data = request.get_json(silent=True) or {}
    url = (data.get('url') or '').strip()
    dl_type = data.get('type', 'video')  # 'video' or 'audio'

    valid, err_msg = is_valid_public_url(url)
    if not valid:
        return jsonify({"error": err_msg}), 400

    if dl_type not in ('video', 'audio'):
        dl_type = 'video'

    # Check concurrency limit
    acquired = download_semaphore.acquire(blocking=False)
    if not acquired:
        return jsonify({
            "error": "Server is currently at maximum download capacity. Please try again in a few moments."
        }), 429

    def generate():
        q = queue.Queue()

        def hook(d):
            if d['status'] == 'downloading':
                progress = d.get('_percent_str', '0%').strip().replace('%', '')
                progress = re.sub(r'\x1b\[[0-9;]*m', '', progress)
                speed = d.get('_speed_str', '0 MiB/s').strip()
                speed = re.sub(r'\x1b\[[0-9;]*m', '', speed)
                eta = d.get('_eta_str', '00:00').strip()
                eta = re.sub(r'\x1b\[[0-9;]*m', '', eta)
                q.put({'type': 'progress', 'progress': progress, 'speed': speed, 'eta': eta})
            elif d['status'] == 'finished':
                q.put({'type': 'log', 'message': '[ffmpeg] Processing and merging formats...'})

        class QueueLogger:
            def debug(self, msg): pass
            def info(self, msg):
                # Clean any sensitive paths from log messages
                clean_msg = sanitize_error_message(msg)
                q.put({'type': 'log', 'message': clean_msg})
            def warning(self, msg):
                clean_msg = sanitize_error_message(msg)
                q.put({'type': 'log', 'message': clean_msg})
            def error(self, msg):
                clean_msg = sanitize_error_message(msg)
                q.put({'type': 'log', 'message': f"ERROR: {clean_msg}"})

        extra_opts = {
            'outtmpl': os.path.join(DOWNLOAD_DIR, '%(title)s.%(ext)s'),
            'logger': QueueLogger(),
            'progress_hooks': [hook],
            'quiet': False
        }

        if dl_type == 'audio':
            extra_opts.update({
                'format': 'bestaudio/best',
                'postprocessors': [{
                    'key': 'FFmpegExtractAudio',
                    'preferredcodec': 'mp3',
                    'preferredquality': '192',
                }],
            })
        else:
            extra_opts.update({
                'format': 'bestvideo+bestaudio/best',
                'merge_output_format': 'mp4',
            })

        ydl_opts = get_ydl_opts(url, dl_type=dl_type, extra_opts=extra_opts)

        def run_dl():
            try:
                with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                    info = ydl.extract_info(url, download=True)
                    filepath = ydl.prepare_filename(info)

                    if dl_type == 'audio':
                        filepath = os.path.splitext(filepath)[0] + '.mp3'
                    elif dl_type == 'video':
                        filepath = os.path.splitext(filepath)[0] + '.mp4'

                    filename = os.path.basename(filepath)

                q.put({'type': 'done', 'filename': filename, 'dl_type': dl_type})
            except Exception as e:
                safe_err = sanitize_error_message(e)
                q.put({'type': 'error', 'message': safe_err})
            finally:
                download_semaphore.release()

        t = threading.Thread(target=run_dl)
        t.start()

        try:
            while True:
                msg = q.get()
                yield f"data: {json.dumps(msg)}\n\n"
                if msg.get('type') in ['done', 'error']:
                    break
        except GeneratorExit:
            # Client disconnected early
            pass

    return Response(generate(), mimetype='text/event-stream')


# ==============================================================================
# Health Check Endpoint
# ==============================================================================

@app.route('/api/status')
def status():
    """Provides non-sensitive operational status."""
    has_youtube_cookies = bool(
        YOUTUBE_COOKIES_PATH and
        os.path.exists(YOUTUBE_COOKIES_PATH) and
        os.path.isfile(YOUTUBE_COOKIES_PATH) and
        os.path.getsize(YOUTUBE_COOKIES_PATH) > 0
    )
    return jsonify({
        "status": "online",
        "service": "AnyVideoDownloader",
        "youtube_authenticated": has_youtube_cookies,
        "max_concurrent_jobs": MAX_CONCURRENT_DOWNLOADS
    })


if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    print(f"Server starting on http://localhost:{port}")
    app.run(host='0.0.0.0', port=port)
