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


def _format_description(desc):
    """Render a 4-5 line description excerpt from yt-dlp's
    `info['description']` for the format-choice screen.

    Returns '' for empty / None input so the caller can skip the
    block cleanly with `if desc_block:` rather than a placeholder
    string that occupies vertical space.

    300-char cap keeps the format-picker kb legible — YouTube
    descriptions often run into several thousand characters verbatim
    and would crowd the chat if rendered in full.

    Operations applied (in order):
      1. `strip()` — trims trailing whitespace that YouTube
         descriptions sometimes have.
      2. `replace('\\n\\n', '\\n')` — YouTube uses `\\n\\n` paragraph
         breaks which double vertical space in a chat; collapsing to
         single `\\n` keeps the excerpt at ~4-5 visible lines for a
         typical video.
      3. Slice `[:300]` + `U+2026` ellipsis when over limit.
      4. Markdown escape via `esc()` — underscores (`install_pkg`),
         asterisks (`*bold*`), backticks (code), and brackets are
         present in descriptions constantly; without escape the
         surrounding ParseMode.MARKDOWN message would either render
         half of it or error out.
    """
    if not desc:
        return ''
    text = str(desc).strip().replace('\n\n', '\n')
    if len(text) > 300:
        text = text[:300] + '\u2026'
    return esc(text)


def _format_comments(comments):
    """Render a yt-dlp `comments` list as a short, Telegram-friendly excerpt.

    Each comment is shaped as `{author, text, like_count, ...}` per yt-dlp's
    docs, but partial-dict failures are common when YouTube returns an
    empty / shaped-differently comment object — we tolerate missing
    `author` (falls back to 'anon') and missing or empty `text` (renders
    as an empty line). Each line is truncated at 140 chars + U+2026
    ellipsis so that even with `Config.MAX_COMMENTS = 20` (~ 20 * 150 chars)
    the rendered excerpt stays well under Telegram's 4096-char message cap.

    Markdown escape via `esc()` so authors cannot smuggle markup that would
    disrupt the surrounding ParseMode.MARKDOWN message.

    Returns the empty string (NOT a placeholder) for empty/None input so
    the caller can branch with `if not block:` cleanly. Live / upcoming
    videos never carry a `comments` key — yt-dlp returns `info['comments']
    = []` (defensive default in the caller makes that harmless).
    """
    if not comments:
        return ''
    lines = []
    for c in comments:
        author = str(c.get('author') or 'anon')
        text = str(c.get('text') or '')
        if len(text) > 140:
            text = text[:140].rstrip() + '\u2026'
        lines.append(f'\U0001F464 @{esc(author)}: {esc(text)}')
    return '\n'.join(lines)

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
        if v.file_path and _path_on_disk(v.file_path) and v.video_id == video_id and v.media_type == media_type:
            return v
    return None

def _path_on_disk(p):
    """Cheap existence check that does NOT confuse transient OS errors with 'missing'.

    `Path(p).exists()` returns False both for genuinely-deleted files AND
    for any case where the underlying `stat()` syscall fails for a
    recoverable reason — NFS hiccup, mid-write EACCES, EIO, EBUSY on
    Windows, a temporarily-unmounted remote filesystem, etc. For a
    READ-ONLY consumer (e.g. find_existing for dedup) the False is
    harmless; it just re-downloads. For a WRITE-AND-PURGE consumer like
    `prune_missing`, however, returning False means we permanently drop
    the record from `user_videos.json` — a transient blip on the disk
    becomes a permanent data loss for the user. We stat() and treat
    FileNotFoundError as the actual "gone" signal; any other OSError
    is treated as "still on disk, skip prune and let the next /recent
    tap retry".

    Malformed paths (e.g. null bytes) raise ValueError from the Path()
    constructor — this is genuinely-broken stored data, not a transient
    blip, so we treat it as "gone" (False) so prune_missing cleans it up
    instead of leaving a perpetually broken entry.

    Empty/None path → False (short-circuited before any Path call: a
    plain `Path(None)` would raise TypeError, which is NOT an OSError
    subclass and would otherwise escape both except clauses).
    """
    if not p:
        return False
    try:
        Path(p).stat()
        return True
    except (FileNotFoundError, ValueError):
        return False
    except OSError:
        return True

def prune_missing(bot, uid):
    """Drop records whose file is genuinely gone on disk.

    Called eagerly when the user asks for /recent or clicks any recent
    entry. Operators sometimes clear the downloads/ folder out-of-band
    (manual disk cleanup, server migration, retention sweep running on a
    separate scheduler, etc.) and the bot's per-user index falls out of
    sync. Without this eager purge the user sees ✅/🗑️ mixed entries in
    /recent and downstream `show_delivery` blows up trying to send a
    vanished file.

    Uses `_path_on_disk` rather than `Path(...).exists()` so a transient
    filesystem hiccup (NFS blip, mid-write EACCES, etc.) cannot
    permanently delete the user's record from `user_videos.json`.

    Preserves record order so the page-based keyboard indices in
    show_recent stay aligned with the entries they're meant to deliver.

    Returns the number of records removed. Calls `bot.save()` exactly
    once if anything was actually removed. Safe on missing / empty per-
    user lists — returns 0 and does NOT call bot.save() in that case.
    """
    records = bot.videos.get(uid)
    if not records:
        return 0
    kept = [v for v in records if _path_on_disk(v.file_path)]
    removed = len(records) - len(kept)
    if removed:
        bot.videos[uid] = kept
        bot.save()
    return removed

def get_default_delivery(bot, uid):
    """Return user's default delivery method: 'ask', 'telegram', 'link'"""
    return bot._user_settings.get(uid, {}).get('default_delivery', 'ask')

# ----- Quality / Subtitle settings -----

VIDEO_QUALITY_OPTIONS = ['best', '2160p', '1440p', '1080p', '720p', '480p', '360p', 'worst']
AUDIO_QUALITY_OPTIONS = ['best', '320', '256', '192', '128', '96', 'worst']
SUBTITLE_MODE_OPTIONS = ['embed', 'separate', 'off']


AUTO_FORMAT_OPTIONS = ['ask', 'video', 'audio', 'thumb']
# When a user pastes a YouTube link in private chat, the bot normally shows
# a "Choose format" keyboard (video / audio / thumbnail). auto_format lets
# the user skip that step: 'video' / 'audio' / 'thumb' short-circuits
# straight to download_task(...) on link arrival; 'ask' (default) keeps the
# existing keyboard UX. Private chat only — groups already hard-code
# media_type='video' and we deliberately don't mix per-user settings into
# shared group delivery.
AUTO_FORMAT_LABELS = {
    'ask':   '❓ Ask each time',
    'video': '🎬 Auto Video',
    'audio': '🎵 Auto Audio',
    'thumb': '🖼️ Auto Thumb',
}
AUTO_FORMAT_SHORT = {
    'ask':   '?',
    'video': 'V',
    'audio': 'A',
    'thumb': 'T',
}

VIDEO_CONTAINER_OPTIONS = ['auto', 'mp4']
# Controls the output container for video downloads.
#   * 'auto' (default) — yt-dlp picks the natural container for the stream
#     combo (typically MKV for vp9+opus, MP4 for h264+aac). Our post-run
#     ffmpeg MKV mux works on whatever yt-dlp produced, so soft subtitles
#     can be EMBEDDED into MKV. Best for friends who want max subs control.
#   * 'mp4'  — yt-dlp's `merge_output_format=mp4` is set so the natural
#     container is remuxed to MP4 whenever possible. Universal device
#     compatibility (iOS / older Android / WhatsApp forwards). Trade-off:
#     MP4 cannot natively mux soft subtitles, so an effective cascade
#     forces `subtitle_mode='embed'` to behave as `subtitle_mode='separate'`
#     inside downloader.py and the user gets an .srt alongside the video
#     file. The user's stored sub_mode preference on disk is NOT mutated
#     (we do not silently rewrite user_settings); only the EFFECTIVE
#     sub_mode used during this download is cascaded.
VIDEO_CONTAINER_LABELS = {
    'auto': '🔀 Auto (best codec match)',
    'mp4':  '🎬 MP4 (universal compat)',
}
VIDEO_CONTAINER_SHORT = {
    # Compact one-glyph menu labels mirroring AUTO_FORMAT_SHORT's
    # `? / V / A / T` so the menu's "🎞 Container: …" row doesn't stick
    # out next to "⚡ Auto: V". The '•' (U+2022 BULLET) for 'auto' is a
    # neutral "best fit" marker — deliberately NOT a specific extension
    # because the natural yt-dlp container depends on stream codecs
    # (h264+aac → MP4, vp9+opus → WEBM, etc.).
    'auto': '•',
    'mp4':  'M',
}

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
    s.setdefault('auto_format', 'ask')
    s.setdefault('video_container', 'auto')
    return s

def get_video_quality(bot, uid):
    return _ensure_settings(bot, uid).get('video_quality', 'best')

def get_audio_quality(bot, uid):
    return _ensure_settings(bot, uid).get('audio_quality', 'best')

def get_subtitle_mode(bot, uid):
    """'embed' = merge into MKV; 'separate' = send .srt alongside; 'off' = no subs"""
    return _ensure_settings(bot, uid).get('subtitle_mode', 'embed')


def get_auto_format(bot, uid):
    """Private-chat auto-format on YouTube-link arrival.

    Returns one of `AUTO_FORMAT_OPTIONS`:
      * 'ask'   (default) — show the video/audio/thumb keyboard.
      * 'video' / 'audio' / 'thumb' — skip the keyboard and route
        directly to download_task(...) with the chosen media_type.

    Validates the stored value: if user_settings.json was hand-edited to
    contain a value outside AUTO_FORMAT_OPTIONS (e.g. legacy data), we
    fall back to 'ask' rather than letting an unknown value reach
    `download_task`, which only knows 'video' / 'audio' / 'thumb'.
    """
    val = _ensure_settings(bot, uid).get('auto_format', 'ask')
    return val if val in AUTO_FORMAT_OPTIONS else 'ask'


def get_video_container(bot, uid):
    """Per-user output container for video downloads.

    Returns one of `VIDEO_CONTAINER_OPTIONS`:
      * 'auto' (default) — yt-dlp picks the natural container; MKV
        subs embed works.
      * 'mp4'  — yt-dlp remuxes to MP4; effective sub_mode is forced
        to 'separate' inside downloader.py (MP4 cannot mux soft subs).

    Garbage fallback: any stored value outside VIDEO_CONTAINER_OPTIONS
    (e.g. legacy data, hand-edited JSON) collapses to 'auto' without
    mutating the user's settings dict, mirroring the read-only
    `get_auto_format` contract.
    """
    val = _ensure_settings(bot, uid).get('video_container', 'auto')
    return val if val in VIDEO_CONTAINER_OPTIONS else 'auto'


# ----- yt-dlp error classification -----# Categorize the text of a yt-dlp / Telegram exception so handlers can show a
# friendly message instead of the generic "❌ Failed.".
#
# Ordering matters: earlier rules win on overlap. Specific phrases (geo,
# live, age-restricted) come BEFORE generic ones ('not available', 'video
# unavailable') so they aren't shadowed.

_YT_ERROR_PATTERNS = (
    ('live_not_started', ('this live event will begin', 'live stream hasn')),
    ('live_ended',      ('this live event will end', 'the livestream has ended', 'livestream has ended')),
    ('geo_blocked',     ('not available in your country', 'this video is not available in your country')),
    ('age_restricted',  ('sign in to confirm your age', 'age-restricted', 'age restricted')),
    ('members_only',    ('members-only content', 'paid membership', 'paid members',
                         "this channel\u2019s members",  # right single quotation mark (U+2019)
                         "this channel's members",       # straight apostrophe (U+0027)
                         'membership program', 'become a member of this channel',
                         'for members only')),
    ('private',         ('private video', 'sign in if you', 'this video is private')),
    ('cookies_required',('login required', 'please log in to your account')),
    ('removed',         ('has been removed by the uploader', 'has been removed for copyright',
                         'removed for copyright')),
    ('unavailable',     ('video unavailable',)),
    ('playability',     ('playability',)),
    # Both new categories live at the end so they do not shadow the more
    # specific yt-dlp error families above.  The `subtitle_throttled` line
    # deliberately matches ONLY yt-dlp's canonical phrasing — bare
    # `HTTP Error 429` / `Too Many Requests` (e.g., format-fetch rate-limits)
    # fall through to `unknown` so the user gets an honest "try again"
    # message instead of a misleading "video downloaded" claim.
    ('subtitle_throttled', ('unable to download video subtitles',)),
    ('disk_error', ('less than 5 gb free', 'no space left on device',
                    'errno 28')),
)


# Friendly user-facing messages for each category. Kept short so they fit
# comfortably above the menu keyboard.
_YT_ERROR_MESSAGES = {
    'live_not_started': '⏳ Live stream hasn’t started yet. Try again in a few minutes.',
    'live_ended':       '⏳ This livestream has already ended.',
    'unavailable':      '🚫 Video unavailable.',
    'private':          '🔒 Private video. Upload updated cookies with /cookies and try again.',
    'age_restricted':   '🔞 Age-restricted. Your cookies may not be enough; refresh them.',
    'members_only':     '🔒 Members-only content. Your cookies may not unlock it.',
    'geo_blocked':      '🌍 Not available in your country / region.',
    'removed':          '⚠️ Removed by the uploader or for copyright.',
    'cookies_required': '🔑 Login required. Upload fresh cookies with /cookies.',
    'playability':      '🚫 YouTube refused playback. Try again or refresh cookies.',
    'subtitle_throttled': '✅ Video downloaded without subtitles (YouTube rate-limited them). Try again in a minute if you need them.',
    'disk_error':       '💾 Bot storage full. Free up some space or wait a few minutes and retry.',
    'unknown':          '❌ Failed to fetch video info. Try again in a moment.',
}


def classify_yt_error(message):
    """Map the text of a yt-dlp / Telegram exception to a stable category key.

    Returns one of the keys in `_YT_ERROR_MESSAGES`, or `'unknown'` if no
    pattern matches. Matching is case-insensitive substring.
    """
    if not message:
        return 'unknown'
    msg = str(message).lower()
    for category, fragments in _YT_ERROR_PATTERNS:
        if any(frag in msg for frag in fragments):
            return category
    return 'unknown'


def friendly_error_msg(category):
    """Return the user-facing message for a category. Falls back to `unknown`."""
    return _YT_ERROR_MESSAGES.get(category, _YT_ERROR_MESSAGES['unknown'])
