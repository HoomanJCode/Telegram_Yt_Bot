"""Synchronous yt-dlp download functions"""
import tempfile, os, re, subprocess
from pathlib import Path
import yt_dlp

DOWNLOADS_DIR = Path('downloads')
WARP_PROXY = 'http://127.0.0.1:40000'

def _sanitize_filename(title):
    """Keep only alphanumeric, spaces, and basic punctuation"""
    name = re.sub(r"[^\w\s\-\.\(\)\[\],!&'-]", '', title)
    return name[:100].strip()

def fetch_info(bot, uid, url):
    opts = {
        'format': 'best',
        'cookiefile': _cookie_file(bot, uid),
        'quiet': True, 'no_warnings': True,
        'socket_timeout': 30, 'retries': 3,
        'proxy': WARP_PROXY,
        'js_runtimes': {'quickjs': '/usr/local/bin/qjs'},
    }
    with yt_dlp.YoutubeDL(opts) as ydl: return ydl.extract_info(url, download=False)

def download(bot, uid, url, media_type):
    base_opts = {
        'cookiefile': _cookie_file(bot, uid),
        'quiet': True, 'no_warnings': True,
        'socket_timeout': 120, 'retries': 50, 'fragment_retries': 50,
        'concurrent_fragment_downloads': 2, 'no_mtime': True,
        'proxy': WARP_PROXY,
        'js_runtimes': {'quickjs': '/usr/local/bin/qjs'},
    }
    
    if media_type == 'video':
        user_lang = bot._user_langs.get(uid, 'en')
        sub_langs = ['en']
        if user_lang != 'en':
            sub_langs.append(user_lang)
        
        opts = {
            **base_opts,
            'format': 'best[ext=mp4]/best',
            'outtmpl': str(DOWNLOADS_DIR / '%(title)s_v.%(ext)s'),
            'writesubtitles': True,
            'writeautomaticsub': True,
            'subtitleslangs': sub_langs,
            'subtitlesformat': 'vtt',
        }
    elif media_type == 'audio':
        opts = {**base_opts, 'format': 'bestaudio[ext=m4a]/bestaudio', 'outtmpl': str(DOWNLOADS_DIR / '%(title)s_a.%(ext)s')}
        if bot.has_ffmpeg:
            opts['postprocessors'] = [{'key': 'FFmpegExtractAudio', 'preferredcodec': 'mp3', 'preferredquality': '192'}]
    else:
        opts = {**base_opts, 'format': 'best', 'outtmpl': str(DOWNLOADS_DIR / '%(title)s_v.%(ext)s')}
    
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=True)
        title = info.get('title', 'Unknown')
        vid = info.get('id', '')
        fp = ydl.prepare_filename(info)
        
        if media_type == 'audio' and bot.has_ffmpeg:
            fp = str(Path(fp).with_suffix('.mp3'))
        
        # Manual subtitle merge for video
        if media_type == 'video' and bot.has_ffmpeg:
            safe_title = _sanitize_filename(title)
            video_file = None
            subtitle_files = []
            
            for ext in ('.mp4', '.webm', '.mkv'):
                candidate = DOWNLOADS_DIR / f'{Path(fp).stem}{ext}'
                if candidate.exists():
                    video_file = str(candidate)
                    break
            if not video_file:
                for f in DOWNLOADS_DIR.iterdir():
                    if f.is_file() and safe_title in f.stem and f.suffix in ('.mp4', '.webm', '.mkv'):
                        video_file = str(f)
                        break
            
            if video_file:
                video_stem = Path(video_file).stem
                for f in DOWNLOADS_DIR.iterdir():
                    if f.is_file() and f.suffix in ('.vtt', '.srt') and video_stem in f.stem:
                        subtitle_files.append(str(f))
            
            if video_file and subtitle_files:
                mkv_file = str(Path(video_file).with_suffix('.mkv'))
                cmd = ['ffmpeg', '-y', '-i', video_file]
                for sub in subtitle_files:
                    cmd.extend(['-i', sub])
                cmd.extend(['-map', '0'])
                for i in range(len(subtitle_files)):
                    cmd.extend(['-map', f'{i+1}'])
                cmd.extend(['-c', 'copy', '-c:s', 'srt', mkv_file])
                
                result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
                
                if result.returncode == 0 and Path(mkv_file).exists():
                    os.unlink(video_file)
                    for sub in subtitle_files:
                        try: os.unlink(sub)
                        except: pass
                    fp = mkv_file
        
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
            if f.is_file() and safe_title in f.stem and f.suffix not in ('.vtt', '.srt'):
                return str(f), title, vid
        raise FileNotFoundError(title)

def download_thumb(bot, uid, url):
    opts = {
        'cookiefile': _cookie_file(bot, uid),
        'quiet': True, 'no_warnings': True,
        'socket_timeout': 30, 'retries': 3,
        'skip_download': True, 'writethumbnail': True,
        'outtmpl': str(DOWNLOADS_DIR / '%(title)s_thumb.%(ext)s'),
        'proxy': WARP_PROXY,
        'js_runtimes': {'quickjs': '/usr/local/bin/qjs'},
    }
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