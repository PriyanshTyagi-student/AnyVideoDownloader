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

# If cookies.txt exists in the project root, automatically use it
if os.path.exists('cookies.txt'):
    ydl_opts['cookiefile'] = 'cookies.txt'

try:
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        ydl.download([url])

    print("\nDownload completed successfully!")

except Exception as e:
    err_msg = str(e)
    if any(k in err_msg.lower() for k in ['login required', 'cookies', 'rate-limit', 'cookies-from-browser']):
        print(f"\nNotice: Content requires authentication or cookies.")
        browser = input("Retry using cookies from your browser? (chrome/edge/firefox/brave or press Enter to skip): ").strip().lower()
        if browser in ['chrome', 'edge', 'firefox', 'brave', 'opera', 'vivaldi']:
            ydl_opts['cookiesfrombrowser'] = (browser,)
            try:
                print(f"Retrying with {browser.capitalize()} cookies...")
                with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                    ydl.download([url])
                print("\nDownload completed successfully!")
            except Exception as retry_err:
                print(f"\nRetry failed: {retry_err}")
        else:
            print(f"\nError: {e}")
    else:
        print(f"\nError: {e}")