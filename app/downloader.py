"""Synchronous yt-dlp download functions"""
import logging, tempfile, os, re, subprocess
from pathlib import Path
import yt_dlp
from app.utils import (
    VIDEO_QUALITY_FMT, AUDIO_QUALITY_FMT,
    get_video_quality, get_audio_quality, get_subtitle_mode,
)

logger = logging.getLogger('yt_bot')

DOWNLOADS_DIR = Path('downloads')
WARP_PROXY = 'http://127.0.0.1:40000'


# Transient connection-level errors that suggest the configured proxy
# (Cloudflare Warp at 127.0.0.1:40000) is unreachable. On these we retry
# yt-dlp once without a proxy so the bot stays usable when Warp is down.
_TRANSIENT_NET_ERROR_FRAGMENTS = (
    'connection refused',
    'connection reset',
    'connection aborted',
    'timed out',
    'temporary failure in name resolution',
    'name or service not known',
    'network is unreachable',
    'errno 111',  # ECONNREFUSED
    'errno 104',  # ECONNRESET
    'errno 110',  # ETIMEDOUT
)


def _is_proxy_transient_error(exc):
    """True if `exc` looks like a connection-level failure where dropping the
    proxy would be a sensible retry. Used by the Warp-fallback logic in
    fetch_info / download / download_thumb.
    """
    msg = str(exc).lower()
    return any(frag in msg for frag in _TRANSIENT_NET_ERROR_FRAGMENTS)


def _opts_with_proxy(base, with_proxy):
    """Return a copy of `base` opts with the Warp proxy enabled or stripped out.
    yt-dlp interprets a missing 'proxy' key as 'no proxy'.
    """
    out = dict(base)
    if with_proxy:
        out['proxy'] = WARP_PROXY
    return out


def _sanitize_filename(title):
    """Keep only alphanumeric, spaces, and basic punctuation"""
    name = re.sub(r"[^\w\s\-\.\(\)\[\],!&'-]", '', title)
    return name[:100].strip()


def _vtt_to_srt(vtt_path):
    """Convert a VTT subtitle file to SRT using ffmpeg. Returns the new SRT path or None."""
    try:
        srt_path = str(Path(vtt_path).with_suffix('.srt'))
        result = subprocess.run(
            ['ffmpeg', '-y', '-loglevel', 'error', '-i', vtt_path, srt_path],
            capture_output=True, text=True, timeout=30,
        )
        if result.returncode == 0 and Path(srt_path).exists() and Path(srt_path).stat().st_size > 0:
            try: os.unlink(vtt_path)
            except: pass
            return srt_path
    except Exception:
        pass
    return None


def _merge_subs_into_mkv(video_file, subtitle_files):
    """Merge video + subtitle files into MKV. Returns MKV path on success, None on failure.

    Robust:
    * Tries to convert VTT -> SRT first (srt codec can be muxed; webvtt cannot).
    * Writes ffmpeg output to a unique temp file, then atomically renames it to
      the target MKV path. This avoids same-path read/write collisions when the
      input `video_file` is already an MKV (e.g. yt-dlp produced MKV directly
      for vp9+opus streams) and ensures we never unlink the just-written
      merged file by mistake.
    """
    if not subtitle_files:
        return None
    mkv_file = str(Path(video_file).with_suffix('.mkv'))
    # Distinct path so ffmpeg never reads & writes the same file at once.
    tmp_file = f"{mkv_file}.merge.tmp.mkv"
    converted = []
    for sub in subtitle_files:
        if sub.lower().endswith('.vtt'):
            srt = _vtt_to_srt(sub)
            converted.append(srt or sub)
        else:
            converted.append(sub)

    cmd = ['ffmpeg', '-y', '-loglevel', 'error', '-i', video_file]
    for sub in converted:
        cmd.extend(['-i', sub])
    cmd.append('-map'); cmd.append('0')
    for i in range(len(converted)):
        cmd.append('-map'); cmd.append(f'{i + 1}:0')
    cmd.extend(['-c:v', 'copy', '-c:a', 'copy', '-c:s', 'srt'])
    cmd.append(tmp_file)

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
        if result.returncode == 0 and Path(tmp_file).exists() and Path(tmp_file).stat().st_size > 0:
            # Atomic rename temp -> final.
            os.replace(tmp_file, mkv_file)
            # Only delete the source video if its path differs from the output.
            # When the input was already an MKV (e.g. yt-dlp produced MKV), the
            # two paths match — unlinking would delete the merged result we
            # just wrote via os.replace.
            def _norm(p):
                return os.path.normcase(os.path.abspath(p))
            if _norm(video_file) != _norm(mkv_file):
                try: os.unlink(video_file)
                except: pass
            for sub in converted:
                try: os.unlink(sub)
                except: pass
            return mkv_file
    except Exception:
        pass
    if Path(tmp_file).exists():
        try: os.unlink(tmp_file)
        except: pass
    return None

def fetch_info(bot, uid, url):
    base = {
        'format': 'best',
        'cookiefile': _cookie_file(bot, uid),
        'quiet': True, 'no_warnings': True,
        'socket_timeout': 30, 'retries': 3,
        'no_js_runtimes': True,
        'js_runtimes': {'quickjs': {'path': '/usr/local/bin/qjs'}},
    }
    try:
        with yt_dlp.YoutubeDL(_opts_with_proxy(base, True)) as ydl:
            return ydl.extract_info(url, download=False)
    except Exception as e:
        if not _is_proxy_transient_error(e):
            raise
        # Warp proxy lookalive: retry once without the proxy opt.
        logger.info(
            'fetch_info: warp proxy failed (%s: %s); retrying without proxy',
            type(e).__name__, str(e)[:80])
        with yt_dlp.YoutubeDL(_opts_with_proxy(base, False)) as ydl:
            return ydl.extract_info(url, download=False)

def download(bot, uid, url, media_type, video_quality=None, audio_quality=None, sub_mode=None):
    """Download with optional quality/sub_mode overrides.

    Returns (file_path, title, video_id, subtitle_files).
    subtitle_files is a list of paths populated when sub_mode='separate' or when the
    embed-merge step fails (fallback so user still receives the subs as files).
    """
    base_opts = {
        'cookiefile': _cookie_file(bot, uid),
        'quiet': True, 'no_warnings': True,
        'socket_timeout': 120, 'retries': 50, 'fragment_retries': 50,
        'concurrent_fragment_downloads': 2, 'no_mtime': True,
        'no_js_runtimes': True,
        'js_runtimes': {'quickjs': {'path': '/usr/local/bin/qjs'}},
    }
    actual_sub_mode = sub_mode or (get_subtitle_mode(bot, uid) if media_type == 'video' else 'off')

    if media_type == 'video':
        vq = video_quality or get_video_quality(bot, uid)
        user_lang = bot._user_langs.get(uid, 'en')
        sub_langs = ['en']
        if user_lang != 'en':
            sub_langs.append(user_lang)

        opts = {
            **base_opts,
            'format': VIDEO_QUALITY_FMT.get(vq, VIDEO_QUALITY_FMT['best']),
            'outtmpl': str(DOWNLOADS_DIR / '%(title)s_v.%(ext)s'),
            # No merge_output_format hint: we always run a manual ffmpeg MKV
            # mux after download (with embedded subs), so yt-dlp can pick
            # the natural codec-compatible container for the streams.
        }
        if actual_sub_mode != 'off':
            opts.update({
                'writesubtitles': True,
                'writeautomaticsub': True,
                'subtitleslangs': sub_langs,
                # Prefer SRT so we don't always need post-run VTT->SRT conversion
                'subtitlesformat': 'srt/best/vtt',
                'keepautosubs': True,
            })
    elif media_type == 'audio':
        aq = audio_quality or get_audio_quality(bot, uid)
        opts = {
            **base_opts,
            'format': AUDIO_QUALITY_FMT.get(aq, AUDIO_QUALITY_FMT['best']),
            'outtmpl': str(DOWNLOADS_DIR / '%(title)s_a.%(ext)s'),
        }
        if bot.has_ffmpeg:
            target_bitrate = aq if aq in ('320', '256', '192', '128', '96') else '192'
            opts['postprocessors'] = [{'key': 'FFmpegExtractAudio', 'preferredcodec': 'mp3', 'preferredquality': target_bitrate}]
    else:
        opts = {**base_opts, 'format': 'best', 'outtmpl': str(DOWNLOADS_DIR / '%(title)s_v.%(ext)s')}

    sub_files_out = []

    # Run yt-dlp; on transient connection failure (Warp down) retry once
    # without the proxy opt. `info` and `fp` come from whichever attempt
    # wins, so post-processing (subtitle merge, rename) only runs once.
    info, fp = None, None
    try:
        with yt_dlp.YoutubeDL(_opts_with_proxy(opts, True)) as ydl:
            info = ydl.extract_info(url, download=True)
            fp = ydl.prepare_filename(info)
    except Exception as e:
        if not _is_proxy_transient_error(e):
            raise
        logger.info(
            'download: warp proxy failed (%s: %s); retrying without proxy',
            type(e).__name__, str(e)[:80])
        with yt_dlp.YoutubeDL(_opts_with_proxy(opts, False)) as ydl:
            info = ydl.extract_info(url, download=True)
            fp = ydl.prepare_filename(info)

    title = info.get('title', 'Unknown')
    vid = info.get('id', '')

    if media_type == 'audio' and bot.has_ffmpeg:
        fp = str(Path(fp).with_suffix('.mp3'))

    # Locate the actual video file (yt-dlp may rename extension after merge)
    safe_title = _sanitize_filename(title)
    video_file = fp
    if not Path(fp).exists():
        for ext in ('.mkv', '.mp4', '.webm'):
            candidate = DOWNLOADS_DIR / f'{Path(fp).stem}{ext}'
            if candidate.exists():
                video_file = str(candidate)
                break
        if not Path(video_file).exists():
            for f in DOWNLOADS_DIR.iterdir():
                if f.is_file() and safe_title in f.stem and f.suffix in ('.mp4', '.webm', '.mkv'):
                    video_file = str(f)
                    break

    # Find subtitle files for this video (srt or vtt)
    watched_subs = []
    if media_type == 'video':
        video_stem = Path(video_file).stem
        for f in DOWNLOADS_DIR.iterdir():
            if not f.is_file(): continue
            if f.suffix not in ('.vtt', '.srt'): continue
            if video_stem in f.stem or f.stem.startswith(video_stem + '.'):
                watched_subs.append(str(f))

    # Subtitle handling per user mode (skipped for audio/thumb)
    if media_type == 'video' and watched_subs:
        if actual_sub_mode == 'embed':
            if bot.has_ffmpeg:
                merged = _merge_subs_into_mkv(video_file, watched_subs)
                if merged:
                    fp = merged
                else:
                    # Merge failed: still give user the subs as files
                    sub_files_out = watched_subs
            else:
                # No ffmpeg: cannot embed. Fall back to separate so user gets subs.
                sub_files_out = watched_subs
        elif actual_sub_mode == 'separate':
            sub_files_out = watched_subs
        elif actual_sub_mode == 'off':
            for sub in watched_subs:
                try: os.unlink(sub)
                except: pass

    # Sanitize the final video filename
    ext = Path(fp).suffix
    new_path = DOWNLOADS_DIR / f"{safe_title}{ext}"
    counter = 1
    while new_path.exists() and str(new_path) != fp:
        new_path = DOWNLOADS_DIR / f"{safe_title}_{counter}{ext}"
        counter += 1
    if Path(fp).exists() and str(Path(fp)) != str(new_path):
        os.rename(fp, str(new_path))
        fp = str(new_path)

    # Rename subtitle files to match (preserving language tag)
    if sub_files_out:
        renamed_subs = []
        for sub in sub_files_out:
            if not Path(sub).exists(): continue
            ext_sub = Path(sub).suffix
            stem_parts = Path(sub).stem.split('.')
            lang = stem_parts[-1] if len(stem_parts) > 1 and len(stem_parts[-1]) <= 5 else ''
            base = f"{safe_title}.{lang}{ext_sub}" if lang else f"{safe_title}{ext_sub}"
            target = DOWNLOADS_DIR / base
            c = 1
            while target.exists() and str(target) != sub:
                target = DOWNLOADS_DIR / (f"{safe_title}.{lang}_{c}{ext_sub}" if lang else f"{safe_title}_{c}{ext_sub}")
                c += 1
            if str(target) != sub:
                try:
                    os.rename(sub, str(target))
                    renamed_subs.append(str(target))
                except:
                    renamed_subs.append(sub)
            else:
                renamed_subs.append(sub)
        sub_files_out = renamed_subs

    # Final fallback locator
    if Path(fp).exists():
        return fp, title, vid, sub_files_out
    for ext_check in ('.mkv', '.mp4', '.webm', '.mp3', '.m4a', '.opus'):
        alt = DOWNLOADS_DIR / f'{Path(fp).stem}{ext_check}'
        if alt.exists(): return str(alt), title, vid, sub_files_out
    for f in DOWNLOADS_DIR.iterdir():
        if f.is_file() and safe_title in f.stem and f.suffix not in ('.vtt', '.srt'):
            return str(f), title, vid, sub_files_out
    raise FileNotFoundError(title)

def download_thumb(bot, uid, url):
    base = {
        'cookiefile': _cookie_file(bot, uid),
        'quiet': True, 'no_warnings': True,
        'socket_timeout': 30, 'retries': 3,
        'skip_download': True, 'writethumbnail': True,
        'outtmpl': str(DOWNLOADS_DIR / '%(title)s_thumb.%(ext)s'),
        'no_js_runtimes': True,
        'js_runtimes': {'quickjs': {'path': '/usr/local/bin/qjs'}},
    }
    info, title, vid = None, None, None
    try:
        with yt_dlp.YoutubeDL(_opts_with_proxy(base, True)) as ydl:
            info = ydl.extract_info(url, download=False)
            title = info.get('title', 'Unknown')
            vid = info.get('id', '')
            ydl.download([url])
    except Exception as e:
        if not _is_proxy_transient_error(e):
            raise
        logger.info(
            'download_thumb: warp proxy failed (%s: %s); retrying without proxy',
            type(e).__name__, str(e)[:80])
        with yt_dlp.YoutubeDL(_opts_with_proxy(base, False)) as ydl:
            info = ydl.extract_info(url, download=False)
            title = info.get('title', 'Unknown')
            vid = info.get('id', '')
            ydl.download([url])

    safe_title = _sanitize_filename(title)
    for ext in ('.jpg', '.webp', '.png'):
        fp = DOWNLOADS_DIR / f'{safe_title}_thumb{ext}'
        if fp.exists(): return str(fp), title, vid, []
    for t in info.get('thumbnails', []):
        if t.get('url'):
            import urllib.request
            ext = t['url'].split('?')[0].split('.')[-1] or 'jpg'
            fp = DOWNLOADS_DIR / f'{safe_title}_thumb.{ext}'
            urllib.request.urlretrieve(t['url'], str(fp))
            return str(fp), title, vid, []
    raise FileNotFoundError("No thumbnail")

def _cookie_file(bot, uid):
    if uid not in bot._cookie_tmpfiles or not os.path.exists(bot._cookie_tmpfiles[uid]):
        tmp = tempfile.NamedTemporaryFile(mode='w', suffix='.txt', delete=False)
        tmp.write(bot._cookie_data[uid].decode('utf-8', errors='replace')); tmp.close()
        bot._cookie_tmpfiles[uid] = tmp.name
    return bot._cookie_tmpfiles[uid]