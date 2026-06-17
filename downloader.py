import yt_dlp
import os

print("=" * 50)
print("ANY VIDEO DOWNLOADER - VIDEO")
print("=" * 50)

url = input("Paste video URL: ").strip()

download_folder = "Downloads/Video"
os.makedirs(download_folder, exist_ok=True)

ydl_opts = {
    'format': 'bestvideo+bestaudio/best',
    'outtmpl': f'{download_folder}/%(title)s.%(ext)s',
    'merge_output_format': 'mp4',
    'extractor_args': {
        'youtube': ['player_client=ios']
    },
    'js_runtimes': {
        'node': {}
    },
    'remote_components': ['ejs:github']
}

try:
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        ydl.download([url])

    print("\nDownload completed successfully!")

except Exception as e:
    print(f"\nError: {e}")