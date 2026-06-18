# app/utils.py
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
    try:
        fp = DATA_DIR / 'user_langs.json'
        if fp.exists(): bot._user_langs = {int(k): v for k, v in json.loads(fp.read_text()).items()}
    except: pass
    try:
        fp = DATA_DIR / 'user_settings.json'
        if fp.exists(): bot._user_settings = {int(k): v for k, v in json.loads(fp.read_text()).items()}
    except: pass

def save_data(bot):
    try: (DATA_DIR / 'user_videos.json').write_text(json.dumps({str(k): [v.to_dict() for v in vs] for k, vs in bot.videos.items()}, indent=2))
    except: pass
    try: (DATA_DIR / 'cookie_file_ids.json').write_text(json.dumps({str(k): v for k, v in bot._cookie_file_ids.items()}, indent=2))
    except: pass
    try: (DATA_DIR / 'global_file_ids.json').write_text(json.dumps(bot._global_file_ids, indent=2))
    except: pass
    try: (DATA_DIR / 'user_langs.json').write_text(json.dumps({str(k): v for k, v in bot._user_langs.items()}, indent=2))
    except: pass
    try: (DATA_DIR / 'user_settings.json').write_text(json.dumps({str(k): v for k, v in bot._user_settings.items()}, indent=2))
    except: pass

def find_existing(bot, uid, video_id, media_type='video', quality='best'):
    """Find an existing record - quality-aware to allow separate entries per quality"""
    for v in bot.videos.get(uid, []):
        if v.video_id == video_id and v.media_type == media_type and Path(v.file_path).exists():
            return v
    return None

def get_default_delivery(bot, uid):
    """Return user's default delivery method: 'ask', 'telegram', 'link'"""
    return bot._user_settings.get(uid, {}).get('default_delivery', 'ask')

# ----- Quality / Subtitle settings -----

VIDEO_QUALITY_OPTIONS = ['best', '2160p', '1440p', '1080p', '720p', '480p', '360p', 'worst']
AUDIO_QUALITY_OPTIONS = ['best', '320', '256', '192', '128', '96', 'worst']
SUBTITLE_MODE_OPTIONS = ['embed', 'separate', 'off']

VIDEO_QUALITY_FMT = {
    'best':   'bv*+ba/b',
    '2160p':  'bv*[height<=2160]+ba/b[height<=2160]',
    '1440p':  'bv*[height<=1440]+ba/b[height<=1440]',
    '1080p':  'bv*[height<=1080]+ba/b[height<=1080]',
    '720p':   'bv*[height<=720]+ba/b[height<=720]',
    '480p':   'bv*[height<=480]+ba/b[height<=480]',
    '360p':   'bv*[height<=360]+ba/b[height<=360]',
    'worst':  'worst',
}

AUDIO_QUALITY_FMT = {
    'best':   'bestaudio/best',
    '320':    'ba[abr<=320]/ba',
    '256':    'ba[abr<=256]/ba',
    '192':    'ba[abr<=192]/ba',
    '128':    'ba[abr<=128]/ba',
    '96':     'ba[abr<=96]/ba',
    'worst':  'worstaudio',
}

VIDEO_QUALITY_LABELS = {
    'best': '🏆 Best', '2160p': '📺 4K', '1440p': '📺 1440p', '1080p': '📺 1080p',
    '720p': '📺 720p', '480p': '📺 480p', '360p': '📺 360p', 'worst': '⬇️ Worst',
}
AUDIO_QUALITY_LABELS = {
    'best': '🏆 Best', '320': '🎵 320kbps', '256': '🎵 256kbps',
    '192': '🎵 192kbps', '128': '🎵 128kbps', '96': '🎵 96kbps', 'worst': '⬇️ Worst',
}
SUBTITLE_MODE_LABELS = {
    'embed': '🔗 Embed (MKV)', 'separate': '📎 Separate file', 'off': '🚫 Off',
}

def _ensure_settings(bot, uid):
    """Ensure user has a settings dict with all keys populated"""
    if uid not in bot._user_settings or not isinstance(bot._user_settings.get(uid), dict):
        bot._user_settings[uid] = {}
    s = bot._user_settings[uid]
    s.setdefault('default_delivery', 'ask')
    s.setdefault('video_quality', 'best')
    s.setdefault('audio_quality', 'best')
    s.setdefault('subtitle_mode', 'embed')
    return s

def get_video_quality(bot, uid):
    return _ensure_settings(bot, uid).get('video_quality', 'best')

def get_audio_quality(bot, uid):
    return _ensure_settings(bot, uid).get('audio_quality', 'best')

def get_subtitle_mode(bot, uid):
    """'embed' = merge into MKV; 'separate' = send .srt alongside; 'off' = no subs"""
    return _ensure_settings(bot, uid).get('subtitle_mode', 'embed')