import os
import json
import yt_dlp
import queue
import threading
import tempfile
from flask import Flask, request, jsonify, Response, send_file

ROOT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
DOWNLOAD_DIR = os.path.join(tempfile.gettempdir(), 'anydownloader')
os.makedirs(DOWNLOAD_DIR, exist_ok=True)

app = Flask(__name__, static_folder=ROOT_DIR, static_url_path='')

@app.after_request
def add_cors_headers(response):
    response.headers['Access-Control-Allow-Origin'] = '*'
    response.headers['Access-Control-Allow-Headers'] = 'Content-Type,Authorization'
    response.headers['Access-Control-Allow-Methods'] = 'GET,PUT,POST,DELETE,OPTIONS'
    return response

@app.before_request
def handle_preflight():
    if request.method == 'OPTIONS':
        response = app.make_default_options_response()
        response.headers['Access-Control-Allow-Origin'] = '*'
        response.headers['Access-Control-Allow-Headers'] = 'Content-Type,Authorization'
        response.headers['Access-Control-Allow-Methods'] = 'GET,PUT,POST,DELETE,OPTIONS'
        return response

@app.route('/')
def index():
    return send_file(os.path.join(ROOT_DIR, 'index.html'))

@app.route('/<path:filename>')
def serve_static(filename):
    file_path = os.path.join(ROOT_DIR, filename)
    if os.path.exists(file_path) and os.path.isfile(file_path):
        return send_file(file_path)
    return "Not found", 404

def get_base_ydl_opts():
    opts = {
        'quiet': True,
        'js_runtimes': {'node': {}},
        'remote_components': ['ejs:github'],
    }
    cookies_path = os.path.join(ROOT_DIR, 'cookies.txt')
    if os.path.exists(cookies_path):
        opts['cookiefile'] = cookies_path
    return opts

@app.route('/api/serve_file/<dl_type>/<filename>')
@app.route('/serve_file/<dl_type>/<filename>')
def serve_file(dl_type, filename):
    filepath = os.path.join(DOWNLOAD_DIR, filename)
    if os.path.exists(filepath):
        return send_file(filepath, as_attachment=True)
    # Check fallback /tmp
    tmp_path = os.path.join("/tmp", filename)
    if os.path.exists(tmp_path):
        return send_file(tmp_path, as_attachment=True)
    return "File not found", 404

@app.route('/api/analyze', methods=['POST', 'OPTIONS'])
@app.route('/analyze', methods=['POST', 'OPTIONS'])
def analyze():
    if request.method == 'OPTIONS':
        return ('', 204)
    data = request.get_json(silent=True) or {}
    url = data.get('url')
    if not url:
        return jsonify({"error": "No URL provided"}), 400

    ydl_opts = get_base_ydl_opts()
    ydl_opts['skip_download'] = True
    
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            try:
                info = ydl.extract_info(url, download=False)
            except Exception as extract_err:
                err_str = str(extract_err).lower()
                if ('cookiefile' not in ydl_opts) and any(k in err_str for k in ['login', 'cookie', 'rate-limit', 'bot']):
                    try:
                        fallback_opts = dict(ydl_opts)
                        fallback_opts['cookiesfrombrowser'] = ('chrome',)
                        with yt_dlp.YoutubeDL(fallback_opts) as ydl_fb:
                            info = ydl_fb.extract_info(url, download=False)
                    except Exception:
                        raise extract_err
                else:
                    raise extract_err

            duration = info.get('duration')
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
        return jsonify({"error": str(e)}), 500

@app.route('/api/download', methods=['POST', 'OPTIONS'])
@app.route('/download', methods=['POST', 'OPTIONS'])
def download():
    if request.method == 'OPTIONS':
        return ('', 204)
    data = request.get_json(silent=True) or {}
    url = data.get('url')
    dl_type = data.get('type', 'video') # 'video' or 'audio'

    if not url:
        return jsonify({"error": "No URL provided"}), 400

    def generate():
        q = queue.Queue()
        
        def hook(d):
            if d['status'] == 'downloading':
                progress = d.get('_percent_str', '0%').strip().replace('%', '')
                import re
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
                q.put({'type': 'log', 'message': msg})
            def warning(self, msg): 
                q.put({'type': 'log', 'message': msg})
            def error(self, msg): 
                q.put({'type': 'log', 'message': f"ERROR: {msg}"})

        ydl_opts = get_base_ydl_opts()
        ydl_opts.update({
            'outtmpl': os.path.join(DOWNLOAD_DIR, '%(title)s.%(ext)s'),
            'logger': QueueLogger(),
            'progress_hooks': [hook],
            'quiet': False
        })

        if dl_type == 'audio':
            ydl_opts.update({
                'format': 'bestaudio/best',
                'postprocessors': [{
                    'key': 'FFmpegExtractAudio',
                    'preferredcodec': 'mp3',
                    'preferredquality': '192',
                }],
            })
        else:
            ydl_opts.update({
                'format': 'bestvideo+bestaudio/best',
                'merge_output_format': 'mp4',
            })

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
                q.put({'type': 'error', 'message': str(e)})

        t = threading.Thread(target=run_dl)
        t.start()

        while True:
            msg = q.get()
            yield f"data: {json.dumps(msg)}\n\n"
            if msg.get('type') in ['done', 'error']:
                break

    return Response(generate(), mimetype='text/event-stream')

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    print(f"Server starting on http://localhost:{port}")
    app.run(host='0.0.0.0', port=port)
