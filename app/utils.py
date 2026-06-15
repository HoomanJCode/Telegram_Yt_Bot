"""Utility functions"""
import json, re, subprocess, time
from pathlib import Path
from datetime import datetime, timedelta

DATA_DIR = Path('data')
DOWNLOADS_DIR = Path('downloads')
YOUTUBE_RE = re.compile(r'(https?://)?(www\.)?(youtube\.com|youtu\.be)/\S+')

def check_ffmpeg():
    try: subprocess.run(['ffmpeg', '-version'], capture_output=True, timeout=5); return True
    except: return False

def ok(bot, uid): return not bot.config.get_whitelist() or uid in bot.config.get_whitelist()

def extract_url(text):
    m = YOUTUBE_RE.search(text)
    if m:
        u = m.group(0)
        if u.startswith('www.'): u = 'https://' + u
        elif not u.startswith('http'): u = 'https://' + u
        return u
    return None

def extract_video_id(url):
    for p in [r'(?:youtube\.com/watch\?v=|youtu\.be/|youtube\.com/embed/|youtube\.com/v/)([a-zA-Z0-9_-]{11})', r'youtube\.com/shorts/([a-zA-Z0-9_-]{11})']:
        m = re.search(p, url)
        if m: return m.group(1)
    return None

def esc(text):
    for c in '*_`[]': text = text.replace(c, '\\' + c)
    return text

def load_data(bot):
    from app.models import VideoRecord
    try:
        fp = DATA_DIR / 'user_videos.json'
        if fp.exists(): bot.videos = {int(k): [VideoRecord.from_dict(v) for v in vs] for k, vs in json.loads(fp.read_text()).items()}
    except: pass
    try:
        fp = DATA_DIR / 'cookie_file_ids.json'
        if fp.exists(): bot._cookie_file_ids = {int(k): v for k, v in json.loads(fp.read_text()).items()}
    except: pass
    try:
        fp = DATA_DIR / 'global_file_ids.json'
        if fp.exists(): bot._global_file_ids = json.loads(fp.read_text())
    except: pass

def save_data(bot):
    try: (DATA_DIR / 'user_videos.json').write_text(json.dumps({str(k): [v.to_dict() for v in vs] for k, vs in bot.videos.items()}, indent=2))
    except: pass
    try: (DATA_DIR / 'cookie_file_ids.json').write_text(json.dumps({str(k): v for k, v in bot._cookie_file_ids.items()}, indent=2))
    except: pass
    try: (DATA_DIR / 'global_file_ids.json').write_text(json.dumps(bot._global_file_ids, indent=2))
    except: pass

def find_existing(bot, uid, video_id, media_type='video'):
    for v in bot.videos.get(uid, []):
        if v.video_id == video_id and v.media_type == media_type and Path(v.file_path).exists(): return v
    return None