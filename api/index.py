import os
import json
import requests
import queue
import threading
import time
from flask import Flask, request, jsonify, Response, send_from_directory

app = Flask(__name__)

@app.route('/api/analyze', methods=['POST'])
def analyze():
    data = request.get_json()
    url = data.get('url')
    if not url:
        return jsonify({"error": "No URL provided"}), 400

    title = "Target Media Acquired"
    thumbnail = "https://images.unsplash.com/photo-1618005182384-a83a8bd57fbe?q=80&w=1000&auto=format&fit=crop"
    duration = "Unknown"
    source = "External Platform"

    if 'youtube.com' in url or 'youtu.be' in url:
        try:
            oembed_url = f"https://www.youtube.com/oembed?url={url}&format=json"
            res = requests.get(oembed_url, timeout=5)
            if res.status_code == 200:
                meta = res.json()
                title = meta.get('title', title)
                thumbnail = meta.get('thumbnail_url', thumbnail)
                source = "YouTube"
        except Exception:
            pass

    return jsonify({
        "title": title,
        "thumbnail": thumbnail,
        "duration": duration,
        "source": source
    })

@app.route('/api/download', methods=['POST'])
def download():
    data = request.get_json()
    url = data.get('url')
    dl_type = data.get('type') # 'video' or 'audio'

    if not url:
        return jsonify({"error": "No URL provided"}), 400

    def generate():
        q = queue.Queue()
        
        def run_dl():
            try:
                q.put({'type': 'log', 'message': '[api] Initializing Remote Extraction Engine...'})
                time.sleep(0.5)
                q.put({'type': 'progress', 'progress': '25', 'speed': 'High', 'eta': '00:02'})
                q.put({'type': 'log', 'message': '[api] Bypassing restrictions via Cobalt Network...'})
                
                headers = {
                    'Accept': 'application/json',
                    'Content-Type': 'application/json',
                    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
                }
                payload = {
                    "url": url,
                    "isAudioOnly": (dl_type == 'audio')
                }
                
                time.sleep(0.5)
                q.put({'type': 'progress', 'progress': '60', 'speed': 'High', 'eta': '00:01'})
                
                response = requests.post('https://api.cobalt.tools/api/json', headers=headers, json=payload, timeout=20)
                
                q.put({'type': 'progress', 'progress': '95', 'speed': 'High', 'eta': '00:00'})
                
                if response.status_code == 200:
                    resp_data = response.json()
                    if resp_data.get('status') == 'error':
                        q.put({'type': 'error', 'message': resp_data.get('text', 'Remote API Error')})
                    else:
                        download_url = resp_data.get('url')
                        if download_url:
                            q.put({'type': 'done', 'downloadUrl': download_url})
                        else:
                            q.put({'type': 'error', 'message': 'No download URL returned.'})
                else:
                    q.put({'type': 'error', 'message': f'Extraction failed (Code {response.status_code})'})
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
