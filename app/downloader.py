"""Synchronous yt-dlp download functions"""
import tempfile, os, re
from pathlib import Path
import yt_dlp

DOWNLOADS_DIR = Path('downloads')

def _sanitize_filename(title):
    """Keep only alphanumeric, spaces, and basic punctuation"""
    name = re.sub(r"[^\w\s\-\.\(\)\[\],!&'-]", '', title)
    return name[:100].strip()

def fetch_info(bot, uid, url):
    opts = {'format': 'best', 'cookiefile': _cookie_file(bot, uid), 'quiet': True, 'no_warnings': True, 'socket_timeout': 30, 'retries': 3}
    with yt_dlp.YoutubeDL(opts) as ydl: return ydl.extract_info(url, download=False)

def download(bot, uid, url, media_type):
    base_opts = {'cookiefile': _cookie_file(bot, uid), 'quiet': True, 'no_warnings': True, 'socket_timeout': 120, 'retries': 50, 'fragment_retries': 50, 'concurrent_fragment_downloads': 2, 'no_mtime': True}
    
    if media_type == 'video':
        user_lang = bot._user_langs.get(uid, 'en')
        sub_langs = ['en']
        if user_lang != 'en':
            sub_langs.append(user_lang)
        
        opts = {
            **base_opts,
            'format': 'bestvideo+bestaudio/best',
            'outtmpl': str(DOWNLOADS_DIR / '%(title)s_v.mkv'),
            'merge_output_format': 'mkv',
            'writesubtitles': True,
            'writeautomaticsub': True,
            'subtitleslangs': sub_langs,
            'embedsubs': True,
        }
    else:
        opts = {**base_opts, 'format': 'bestaudio[ext=m4a]/bestaudio', 'outtmpl': str(DOWNLOADS_DIR / '%(title)s_a.%(ext)s')}
        if bot.has_ffmpeg: opts['postprocessors'] = [{'key': 'FFmpegExtractAudio', 'preferredcodec': 'mp3', 'preferredquality': '192'}]
    
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=True)
        title = info.get('title', 'Unknown')
        vid = info.get('id', '')
        fp = ydl.prepare_filename(info)
        if media_type == 'audio' and bot.has_ffmpeg: fp = str(Path(fp).with_suffix('.mp3'))
        
        ext = Path(fp).suffix
        safe_title = _sanitize_filename(title)
        new_path = DOWNLOADS_DIR / f"{safe_title}{ext}"
        counter = 1
        while new_path.exists() and str(new_path) != fp:
            new_path = DOWNLOADS_DIR / f"{safe_title}_{counter}{ext}"
            counter += 1
        
        if Path(fp).exists() and str(Path(fp)) != str(new_path):
            os.rename(fp, str(new_path))
            fp = str(new_path)
        
        if Path(fp).exists(): return fp, title, vid
        for ext_check in ('.mkv', '.mp4', '.webm', '.mp3', '.m4a', '.opus'):
            alt = DOWNLOADS_DIR / f'{Path(fp).stem}{ext_check}'
            if alt.exists(): return str(alt), title, vid
        for f in DOWNLOADS_DIR.iterdir():
            if f.is_file() and f.stem.startswith(safe_title): return str(f), title, vid
        raise FileNotFoundError(title)

def download_thumb(bot, uid, url):
    opts = {'cookiefile': _cookie_file(bot, uid), 'quiet': True, 'no_warnings': True, 'socket_timeout': 30, 'retries': 3, 'skip_download': True, 'writethumbnail': True, 'outtmpl': str(DOWNLOADS_DIR / '%(title)s_thumb.%(ext)s')}
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=False)
        title = info.get('title', 'Unknown')
        vid = info.get('id', '')
        ydl.download([url])
        safe_title = _sanitize_filename(title)
        for ext in ('.jpg', '.webp', '.png'):
            fp = DOWNLOADS_DIR / f'{safe_title}_thumb{ext}'
            if fp.exists(): return str(fp), title, vid
        for t in info.get('thumbnails', []):
            if t.get('url'):
                import urllib.request
                ext = t['url'].split('?')[0].split('.')[-1] or 'jpg'
                fp = DOWNLOADS_DIR / f'{safe_title}_thumb.{ext}'
                urllib.request.urlretrieve(t['url'], str(fp))
                return str(fp), title, vid
        raise FileNotFoundError("No thumbnail")

def _cookie_file(bot, uid):
    if uid not in bot._cookie_tmpfiles or not os.path.exists(bot._cookie_tmpfiles[uid]):
        tmp = tempfile.NamedTemporaryFile(mode='w', suffix='.txt', delete=False)
        tmp.write(bot._cookie_data[uid].decode('utf-8', errors='replace')); tmp.close()
        bot._cookie_tmpfiles[uid] = tmp.name
    return bot._cookie_tmpfiles[uid]