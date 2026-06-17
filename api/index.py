import os
import json
import yt_dlp
import queue
import threading
from flask import Flask, request, jsonify, Response, send_file

app = Flask(__name__)

@app.route('/api/serve_file/<dl_type>/<filename>')
def serve_file(dl_type, filename):
    filepath = os.path.join("/tmp", filename)
    if os.path.exists(filepath):
        return send_file(filepath, as_attachment=True)
    return "File not found", 404

@app.route('/api/analyze', methods=['POST'])
def analyze():
    data = request.get_json()
    url = data.get('url')
    if not url:
        return jsonify({"error": "No URL provided"}), 400

    ydl_opts = {
        'skip_download': True,
        'quiet': True,
        'js_runtimes': { 'node': {} },
        'remote_components': ['ejs:github']
    }
    
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)
            
            # Format duration safely
            duration = info.get('duration')
            if duration:
                mins, secs = divmod(duration, 60)
                hours, mins = divmod(mins, 60)
                duration_str = f"{hours:02d}:{mins:02d}:{secs:02d}" if hours else f"{mins:02d}:{secs:02d}"
            else:
                duration_str = "Unknown"

            return jsonify({
                "title": info.get('title', 'Unknown Title'),
                "thumbnail": info.get('thumbnail', 'https://images.unsplash.com/photo-1618005182384-a83a8bd57fbe?q=80&w=1000&auto=format&fit=crop'),
                "duration": duration_str,
                "source": info.get('extractor', 'Unknown').capitalize()
            })
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/api/download', methods=['POST'])
def download():
    data = request.get_json()
    url = data.get('url')
    dl_type = data.get('type') # 'video' or 'audio'

    if not url:
        return jsonify({"error": "No URL provided"}), 400

    download_folder = "/tmp"
    os.makedirs(download_folder, exist_ok=True)

    def generate():
        q = queue.Queue()
        
        def hook(d):
            if d['status'] == 'downloading':
                progress = d.get('_percent_str', '0%').strip().replace('%', '')
                # Handle ANSI escape codes that yt-dlp might output
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

        # Configure yt-dlp opts based on type
        ydl_opts = {
            'outtmpl': f'{download_folder}/%(title)s.%(ext)s',
            'logger': QueueLogger(),
            'progress_hooks': [hook],
            'js_runtimes': { 'node': {} },
            'remote_components': ['ejs:github'],
            'quiet': False
        }

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
    print("Starting Media Intelligence Server...")
    print("Access via browser at http://localhost:5000")
    app.run(host='0.0.0.0', port=5000, debug=False, threaded=True)
