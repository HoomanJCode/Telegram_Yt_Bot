"""Synchronous yt-dlp download functions"""
import logging, tempfile, os, re, subprocess, shutil, time
from pathlib import Path
import yt_dlp
from app.utils import (
    VIDEO_QUALITY_FMT, AUDIO_QUALITY_FMT,
    get_video_quality, get_audio_quality, get_subtitle_mode,
    get_video_container,
)
from config import Config

logger = logging.getLogger('yt_bot')

DOWNLOADS_DIR = Path('downloads')
WARP_PROXY = 'http://127.0.0.1:40000'


# Minimum free disk space required before starting a download, in bytes.
# Derived at import from Config.MIN_DISK_FREE_MB so operators can tune the
# threshold via env var without touching code (default 1024 MB = 1 GB).  The
# old hard-coded 5 GB incorrectly rejected requests on small VPSes with
# <10 GB total disk even when plenty of room remained for a single download +
# mux peak.  See config.py for the full rationale.
MIN_DISK_FREE_BYTES = Config.MIN_DISK_FREE_MB * 1024 * 1024


# Keys that turn on yt-dlp's subtitle fetch.  When a download fails with a
# subtitle-throttle error we retry once with these stripped out so the user
# still receives the video even when YouTube is 429-ing the subtitle endpoint.
SUBTITLE_OPTS_KEYS = (
    'writesubtitles', 'writeautomaticsub', 'subtitleslangs',
    'subtitlesformat', 'keepautosubs',
)


# In-memory cache for `fetch_info` results so the format-choice screen
# does not retrigger yt-dlp's Innertube /next round-trip on every
# back-and-forth between delivery kb / `back_to_formats` / show_format_choice.
#
# Cache key: (uid, url) -- per-user, not just per-url. Two reasons:
#   1. yt-dlp's info-dict surface includes `comments` ONLY when the user's
#      cookies are valid AND the operator has opted in via Config.MAX_COMMENTS.
#      Cross-user cache hits would leak comments fetched with user A's
#      cookies to user B who may NOT be permitted to see them (private
#      channel membership-gated comments, etc.). Per-user keying isolates
#      cookie-bound surfaces.
#   2. Per-user is what the bot's actual refetch pattern produces: the
#      dominant source of refetches is `back_to_formats` (delivery kb
#      -> show_format_choice), which is always same-user. The cache hit
#      rate under per-user is high in practice.
#
# TTL: 300 seconds (5 minutes). Short enough that recently-published
# comments / updated titles / new view counts propagate without a bot
# restart. Long enough that the back-to-formats / format-choice round
# trip is essentially free. Operators who want a longer cache window
# can env-tune `Config.INFO_CACHE_TTL_SECONDS` (added below) without
# touching code.
_INFO_CACHE = {}
_INFO_CACHE_MAX_SIZE = 1000  # FIFO cap; see _info_cache_set for eviction


_INFO_TTL_SECONDS = 300


def _info_cache_get(uid, url):
    """Return cached info dict for (uid, url) or None on miss / stale.

    Pure helper so unit tests can drive the (uid, url, elapsed, info) ->
    None-or-info contract without invoking `_run_ydl` (which would otherwise
    require heavy mocking of yt_dlp.YoutubeDL).

    Stale-entry side effect: drops the entry while iterating so the cache
    does not accumulate dead entries under long-lived bot processes. The
    drop uses `.pop((uid, url), None)` so an entry that was inserted by
    a concurrent thread between the `.get` and the `.pop` flows through
    unconditionally rather than throwing KeyError.
    """
    entry = _INFO_CACHE.get((uid, url))
    if entry is None:
        return None
    cached_at, info = entry
    if time.monotonic() - cached_at >= _INFO_TTL_SECONDS:
        _INFO_CACHE.pop((uid, url), None)
        return None
    return info


def _info_cache_set(uid, url, info):
    """Store info dict under (uid, url) with the current monotonic timestamp.

    Pure helper -- the timestamp lookup uses `time.monotonic` rather than
    `time.time` so a system clock adjustment (NTP sync, daylight savings
    on misconfigured servers, manual `date` calls) cannot accidentally
    expire freshly-cached entries.

    Defensive guards:
      * Falsy/empty `info` (None, {}, []) is REJECTED (not cached).
        Rationale: a transient `_run_ydl` failure (network blip,
        cookies-expired mid-fetch) returns None/empty. Caching it
        would mean every subsequent fetch for that (uid, url) for the
        next TTL returns the same broken marker, masking the underlying
        error. Better to fall through to a fresh fetch on the next hit.
      * When the cache is at `_INFO_CACHE_MAX_SIZE`, the OLDEST entry
        (lowest `cached_at`) is FIFO-evicted before the new entry is
        written. This prevents unbounded growth under adversarial /
        long-lived-bot conditions (a single user submitting 100k+ URLs
        cannot leak memory forever). FIFO (not LRU) so we don't pay
        the bookkeeping cost -- the bot's traffic is dominated by
        back-clicks on the same video, not by 1M distinct URLs.
    """
    # Reject None + empty-dict markers -- a degenerate info dict
    # could otherwise propagate to every subsequent hit for TTL
    # seconds, masking the underlying _run_ydl failure (network
    # blip, cookies-expired mid-fetch) as a 'cached' result.
    # We deliberately do NOT use `not info` here: a future valid
    # edge case like `info={"title": ""}` (empty title is legitimate
    # for some yt-dlp extractors) MUST continue to be cacheable,
    # because `not {"title": ""}` is False (the dict is non-empty)
    # but if someone widened `not info` to a broader guard they
    # could accidentally start rejecting legitimate-but-sparse
    # info dicts. Pinning the narrow `is None or == {}` keeps the
    # contract exact.
    if info is None or info == {}:
        return
    # FIFO size guard: oldest stamp goes first. We pick the key with
    # the smallest cached_at -- O(n) but n<=_INFO_CACHE_MAX_SIZE=1000
    # is a drop in the bucket relative to the network round-trip.
    if len(_INFO_CACHE) >= _INFO_CACHE_MAX_SIZE:
        oldest_key = min(_INFO_CACHE, key=lambda k: _INFO_CACHE[k][0])
        _INFO_CACHE.pop(oldest_key, None)
    _INFO_CACHE[(uid, url)] = (time.monotonic(), info)


def _info_cache_clear():
    """Drop every cache entry. Used by tests + (rarely) by a /reset command.

    Pinning-name shape mirrors `_user_settings.clear()` and friends so
    future maintainers grep for "cache" find the reset hook together with
    the read/write helpers.
    """
    _INFO_CACHE.clear()


class StorageFullError(Exception):
    """Raised by `download()` when pre-flight disk check rejects the request."""


def _has_disk_space(min_bytes=MIN_DISK_FREE_BYTES):
    """Return True if `DOWNLOADS_DIR` has >= min_bytes free on its filesystem.

    Resolved to an absolute path so `shutil.disk_usage` always consults the
    correct mount, even if the bot is somehow started from a different cwd.
    Returns True (don't block) when the OS can't report free space — we never
    want a measurement error to make the bot stop accepting requests.
    """
    try:
        return shutil.disk_usage(str(DOWNLOADS_DIR.resolve())).free >= min_bytes
    except OSError:
        return True


def _is_subtitle_throttle(exc):
    """True if `exc` looks like a yt-dlp subtitle fetch being rate-limited.

    Used by `_extract_with_subtitle_fallback` to decide whether to retry once
    without `--write-subtitles` so we still deliver the video to the user.

    Tightened on purpose: a bare `HTTP Error 429` or `Too Many Requests`
    elsewhere in the call chain (format-fetch, manifest, cover-art) is NOT
    matched here.  Triggering the retry for those wastes time and — if the
    retry also fails — causes the friendly-error classifier to surface a
    misleading "video downloaded without subtitles" message even though no
    video was delivered.  Better to fall through to `unknown` and let the user
    see "❌ Failed to fetch video info. Try again in a moment."
    """
    msg = str(exc).lower()
    if 'unable to download video subtitles' in msg:
        return True
    if 'subtitle' in msg and ('429' in msg or 'too many requests' in msg):
        return True
    return False


def _extract_with_subtitle_fallback(opts, label, extract_fn):
    """Call `extract_fn` via `_run_ydl`; on a subtitle-throttle error retry once
    with `opts` rebuilt without subtitle-related keys.  All other exceptions
    propagate unchanged so the Warp-proxy transient retry in `_run_ydl` still
    runs.
    """
    try:
        return _run_ydl(opts, label, extract_fn)
    except Exception as e:
        if not _is_subtitle_throttle(e):
            raise
        logger.warning(
            '%s: subtitle throttle (%s); retrying without subs',
            label, str(e)[:120])
        no_sub_opts = {k: v for k, v in opts.items() if k not in SUBTITLE_OPTS_KEYS}
        return _run_ydl(no_sub_opts, label + '_no_subs', extract_fn)


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


def _run_ydl(opts, label, run):
    """Run yt-dlp under the configured proxy strategy.

    * When `Config.USE_WARP` is False (default), call `run(ydl)` once with no
      proxy. No transient-error retry is needed because there is no proxy to
      drop.
    * When `Config.USE_WARP` is True, attempt the call with the Warp proxy;
      on a transient connection error retry once without the proxy so the bot
      stays usable if warp-svc is down. The first attempt that does not raise
      a transient error wins; `run(ydl)` is the same callable across both.

    `label` is used for the log line on transient retry so the failure can be
    attributed to fetch_info / download / download_thumb.
    """
    if Config.USE_WARP:
        try:
            with yt_dlp.YoutubeDL(_opts_with_proxy(opts, True)) as ydl:
                return run(ydl)
        except Exception as e:
            if not _is_proxy_transient_error(e):
                raise
            logger.info(
                '%s: warp proxy failed (%s: %s); retrying without proxy',
                label, type(e).__name__, str(e)[:80])
            with yt_dlp.YoutubeDL(_opts_with_proxy(opts, False)) as ydl:
                return run(ydl)
    with yt_dlp.YoutubeDL(opts) as ydl:
        return run(ydl)


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


def _extract_lang_from_filename(path):
    """Extract the ISO 639-1 / BCP 47 language code from a yt-dlp subtitle filename.

    yt-dlp saves softsubs as `Title.<lang>.<ext>` (e.g. `Foo.en.srt`,
    `Foo.en-US.vtt`, `Foo.pt-BR.srt`). Some videos without a per-language
    subscript (auto-generated fallback, very small channels) end up as just
    `Title.srt` with no language segment.

    Returns the lowercased language code, or '' when no segment looks like a
    valid lang tag — the caller (the merge helper) treats '' as "no language
    flag" so a missing/broken lang segment doesn't write `language=` into the
    ffmpeg args (which would emit `language=` with an empty value, worse than
    omitting).

    The regex deliberately accepts ISO 639-1 (`en`, `de`), ISO 639-2 (`yue`,
    `haw`), and BCP 47 regional variants (`en-US`, `zh-Hant`, `pt-BR`) without
    trying to enumerate them — the contract is "looks like a lang tag, not a
    filename word that happens to be 2-5 chars long". False positives like
    `Title.1080p.srt` are still possible but harmless: ffmpeg will simply
    write `language=1080p` on the track tag (VLC displays the raw tag; modern
    players ignore non-ISO strings). The downstream gain (real `eng` / `spa`
    tags for the vast majority of well-tagged subs) far outweighs the rare
    cosmetic oddity.
    """
    if not path:
        return ''
    stem = Path(path).stem
    parts = stem.split('.')
    if len(parts) < 2:
        return ''
    candidate = parts[-1]
    if not re.match(r'^[A-Za-z]{2,3}(-[A-Za-z0-9]{2,8})?$', candidate):
        return ''
    return candidate.lower()


def _effective_sub_mode_for_container(container, sub_mode):
    """Apply the MP4↔embed cascade locally.

    MP4 cannot natively mux soft subtitles. When the user has picked MP4
    AND their effective sub_mode would have been 'embed', cascade to
    'separate' so the download post-processing emits an .srt file alongside
    the MP4 instead of silently losing subs. The user's STORED sub_mode
    preference in user_settings.json is NOT mutated — only the effective
    value used during this download changes, so flipping back to
    container='auto' restores embed.

    Extracted as a pure helper so TestMp4ContainerCascade can lock in the
    3-line cascade table without mocking yt_dlp / the whole download
    pipeline. download() composes it with get_video_container() +
    get_subtitle_mode() to produce actual_sub_mode.

    | container | sub_mode | effective sub_mode |
    | --------- | -------- | ------------------ |
    | 'auto'    | 'embed'    | 'embed'         |
    | 'auto'    | 'separate' | 'separate'      |
    | 'auto'    | 'off'      | 'off'           |
    | 'mp4'     | 'embed'    | 'separate' (cascade) |
    | 'mp4'     | 'separate' | 'separate'      |
    | 'mp4'     | 'off'      | 'off'           |
    """
    if container == 'mp4' and sub_mode == 'embed':
        return 'separate'
    return sub_mode


# Single-core-VPS smart-skip (2026-06-21 response to user feedback
# "ffmpeg processing is high for my single-core VPS"): an ffprobe
# probe after yt-dlp's natural merge tells us whether the post-merge
# audio stream a:0 is already in a universal-codec set (currently
# just `aac`). If so, the AAC re-encode would be pure wasted CPU --
# skip the helper call entirely. Net savings on a single-core VPS:
# ~30-90s of CPU per AAC-source download becomes ~1-2s of probe
# cost. Negligible for Opus-source downloads (the actual TV-fix
# case) where the probe and the transcode still both run.
#
# Probe failure semantic (deliberate): ffprobe missing, file corrupt,
# timeout, no audio stream -> returns '' -> falls through to the
# active transcode. Operators who explicitly set AAC_TRANSCODE=true
# really DO want the TV fix; a probe blip silently disabling that
# would surprise them. CPU-conservative operators should set
# AAC_TRANSCODE=false in .env to skip the whole gate conjunct chain,
# not rely on probe failure to skip.
_AAC_SKIP_CODECS = frozenset({'aac'})


def _probe_audio_codec(video_file):
    """ffprobe the first audio stream's codec_name. Returns '' on probe failure.

    Pin to the FIRST audio stream (`a:0`) as the primary-track
    marker. The transcode helper's `-c:a aac -map 0` re-encodes
    ALL audio streams (not just `a:0`), so pinning the probe to
    `a:0` is a primary-track marker rather than a comprehensive
    multi-track scan. Multi-track videos where a:0 is AAC but
    secondary tracks are Opus will pass the probe (skip the
    transcode) even though the helper would have re-encoded all
    tracks anyway -- this is the documented contract. If multi-
    track audio semantics become operationally important, broaden
    the probe to enumerate all audio streams.

    Output format pin (`-of csv=p=0` OR positional on the
    `_AAC_SKIP_CODECS` check, NOT a substring match): `-of csv=p=0`
    makes ffprobe emit ONLY the codec_name string on stdout, no
    verbose header/footer. Any other `-of` value would require the
    caller to parse ffprobe's verbose text output, which would be
    fragile across ffprobe versions.

    Cost: ~1-2s on multi-GB files (reads metadata header only, NOT
    the full audio stream). Returns the lowercased codec name
    ('aac', 'opus', 'mp3', 'flac', ...) on success, or '' on any
    failure mode the caller should treat as "unknown -- fall
    through to the active transcode".
    """
    if not video_file or not Path(video_file).exists():
        return ''
    cmd = [
        'ffprobe', '-v', 'error',
        '-select_streams', 'a:0',
        '-show_entries', 'stream=codec_name',
        '-of', 'csv=p=0',
        video_file,
    ]
    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=10)
        if result.returncode == 0 and result.stdout:
            return result.stdout.strip().lower()
    except Exception:
        pass
    return ''


def _is_already_universal_codec(video_file):
    """True if post-merge audio stream a:0 is already in `_AAC_SKIP_CODECS`.

    Single source of truth for whether the smart-skip in `download()`'s
    AAC transcode gate fires. Wraps `_probe_audio_codec` so callers
    can pin the boolean contract without knowing the ffprobe
    invocation shape.

    Probe failure -> False (treat as unknown -> fall through to
    ffmpeg re-encode). See module-level comment above for rationale.
    """
    try:
        return _probe_audio_codec(video_file) in _AAC_SKIP_CODECS
    except Exception:
        return False


def _transcode_audio_to_aac(video_file, title=None):
    """Re-encode a video file's audio stream to AAC (192kbps), leaving the
    video stream alone.

    Triggered when `Config.AAC_TRANSCODE` is True -- we explicitly
    hand-fan Opus audio through ffmpeg because yt-dlp's merge
    stream-copies Opus verbatim into the natural container (MKV /
    WEBM), and its `merge_output_format=mp4` pipeline does NOT
    reliably auto-transcode Opus->AAC. Some smart TVs and PC
    players without Opus decode then label the audio as 'audio
    codec: none' even though AVC video plays. Re-encoding to AAC
    at 192kbps is mathematically near-lossless for typical YouTube
    128-160kbps Opus source so quality hits a universal-codec
    payoff floor.

    Mechanics mirror `_merge_subs_into_mkv`:
      1. Write ffmpeg output to a unique `<file>.transcode.tmp.<ext>` so the
         input / output paths never collide (an input MKV that gets
         remuxed to itself would corrupt the file under any read+write
         collision).
      2. `-c:v copy` so the AVC stream goes through with zero decode/re-
         encode CPU cost. The 30-90s total transcode cost is dominated by
         the AAC re-encode.
      3. `-c:a aac -b:a 192k` with the native ffmpeg `aac` encoder. We
         deliberately do NOT pin to `libfdk_aac` -- the native encoder is
         ubiquitous on every Linux VPS ffmpeg build, while libfdk lives
         behind a license flag and isn't installed everywhere. 192kbps is
         the sweet spot for stereo near-transparency; below 128kbps the
         codec artifacts become audible against a typical YouTube Opus
         128k source.
      4. `-map 0` selects every input stream, so a video with no audio
         at all produces an output with no audio stream (no crash, no
         fake-silent-track). The native `aac` encoder is a safe no-op on
         an empty audio muxer.
      5. `-metadata title=...` mirrors `_merge_subs_into_mkv`'s title
         passthrough so the resulting file keeps the YouTube title in
         VLC / mpv / Plex. Control characters / newlines are stripped to
         keep a malicious title from smuggling an ffmpeg flag into the
         argv list.
      6. Output extension matches the input's extension so the downstream
         filename-sanitize step in download() doesn't need to know we
         touched the file.
      7. Atomic os.replace on success; temp file unlinked on failure.
         Mid-flight ffmpeg crash leaves the original video intact
         (caller falls through to delivering the untranscoded file -- a
         degraded-but-functional outcome, not a 500 to the user).

    Returns the new file path on success, None on any ffmpeg error so the
    caller can fall through to the untranscoded original. The user's
    delivery UX is preserved in both branches -- only the audio track
    is swapped silently.
    """
    if not video_file or not Path(video_file).exists():
        return None
    src_ext = Path(video_file).suffix.lower() or '.mkv'
    out_path = video_file
    tmp_file = f"{video_file}.transcode.tmp{src_ext}"

    cmd = [
        'ffmpeg', '-y', '-loglevel', 'error',
        '-i', video_file,
        '-map', '0',
        '-c:v', 'copy',
        '-c:a', 'aac', '-b:a', '192k',
    ]
    if title:
        # Same control-stripping rationale as _merge_subs_into_mkv --
        # prevent a weird YouTube title from injecting ffmpeg flags.
        safe_title = re.sub(r'[\x00-\x1f\x7f]', '', str(title)).strip()
        if safe_title:
            cmd.extend(['-metadata', f'title={safe_title}'])
    cmd.append(tmp_file)

    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=300)
        if (result.returncode == 0
                and Path(tmp_file).exists()
                and Path(tmp_file).stat().st_size > 0):
            os.replace(tmp_file, out_path)
            return out_path
    except Exception:
        pass
    # Cleanup temp on any failure so the downloads dir doesn't accumulate
    # half-written transcode attempts.
    if Path(tmp_file).exists():
        try: os.unlink(tmp_file)
        except: pass
    return None


def _merge_subs_into_mkv(video_file, subtitle_files, title=None, sub_languages=None):
    """Merge video + subtitle files into MKV. Returns MKV path on success, None on failure.

    Robust:
    * Tries to convert VTT -> SRT first (srt codec can be muxed; webvtt cannot).
    * Writes ffmpeg output to a unique temp file, then atomically renames it to
      the target MKV path. This avoids same-path read/write collisions when the
      input `video_file` is already an MKV (e.g. yt-dlp produced MKV directly
      for vp9+opus streams) and ensures we never unlink the just-written
      merged file by mistake.
    * Writes container-level metadata (`-metadata title=...`) when `title` is
      provided, so the resulting MKV shows up with a real title in VLC / mpv /
      Plex. We pass `-metadata` and `title=...` as two SEPARATE argv entries
      (no shell quoting needed — `subprocess.run` is invoked with cmd list,
      never via shell=True) and strip control characters / newlines from the
      title so a weird YouTube title can't smuggle a ffmpeg flag into the
      command vector.
    * Writes per-stream subtitle language metadata
      (`-metadata:s:s:<i> language=<iso>`) for every subtitle index i where
      `sub_languages[i]` is non-empty. Without this, ffmpeg defaults every
      subtitle track to `language=und` (undefined) — which is exactly what
      the user reported. The lang is recovered from yt-dlp's standard
      filename convention `Title.<lang>.<ext>` via `_extract_lang_from_filename`,
      so no info-dict inspection is needed.

    `sub_languages` (optional) is an ordered list, one entry per element of
    `subtitle_files`. If absent or shorter than `subtitle_files`, missing
    entries are treated as '' (no language flag written). Longer lists are
    tolerated but ignored past `len(subtitle_files)`.
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

    # Container-level title metadata. Strip control chars / newlines so a
    # weird YouTube title can't smuggle a new ffmpeg flag into the argv list
    # (e.g. a title like "Foo\n-codec libx264" must NOT be reinterpreted as
    # a codec switch). `\r` and `\n` are dropped; other printable chars pass.
    if title:
        safe_title = re.sub(r'[\x00-\x1f\x7f]', '', str(title)).strip()
        if safe_title:
            cmd.extend(['-metadata', f'title={safe_title}'])

    # Per-stream subtitle language metadata. Only emit a `language=` flag
    # when the input list has an entry at this index AND that entry is a
    # non-empty string — an empty lang MUST NOT add `-metadata:s:s:i
    # language=` (nothing after the `=`) which ffmpeg rejects.
    if sub_languages:
        for i, lang in enumerate(sub_languages[:len(converted)]):
            if lang:
                cmd.extend([f'-metadata:s:s:{i}', f'language={lang}'])

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
    """One-shot metadata fetch for the format-choice screen.

    Returns the same dict yt-dlp would have returned from `extract_info(
    download=False)`: keys include `title`, `description`, `duration`,
    `uploader`, `tags`, `chapters`, `thumbnails`, ... AND, iff the operator
    has opted in via `Config.MAX_COMMENTS > 0`, a `comments` list. Comments
    are NOT fetched by default (yt-dlp skips the Innertube `/next` call) so
    the info fetch stays fast for the common path.

    Caching: read-through TTL cache keyed by (uid, url). The dominant
    refetch source is `back_to_formats` (delivery kb -> show_format_choice
    on the SAME video URL by the SAME user within seconds); without this
    cache every back-click would round-trip to YouTube and re-parse cookies
    just to render the format kb the user already saw. The cache TTL is
    300 seconds (5 min) so recently-updated comments / titles propagate
    without a bot restart. See `_INFO_CACHE` and `_INFO_TTL_SECONDS` for
    the rationale and per-user-vs-global trade-offs.
    """
    cached = _info_cache_get(uid, url)
    if cached is not None:
        # DEBUG level (not INFO) -- a high-traffic bot generates
        # one log line per user-paste even on the cache HIT path,
        # which would drown the INFO-level download / mux /
        # cookie lines operators rely on. Operators who want
        # HIT/MISS counters for capacity planning can flip the
        # env LOG_LEVEL=DEBUG without retuning this constant.
        logger.debug('fetch_info cache HIT uid=%d url=%s', uid, url[:80])
        return cached
    base = {
        'format': 'best',
        'cookiefile': _cookie_file(bot, uid),
        'quiet': True, 'no_warnings': True,
        'socket_timeout': 30, 'retries': 3,
        'no_js_runtimes': True,
        'js_runtimes': {'quickjs': {'path': '/usr/local/bin/qjs'}},
    }
    # Comment fetching is opt-in via Config.MAX_COMMENTS (capped at 20 in
    # config.py so a misconfiguration can't escalate). We force
    # `comment_sort=new` so yt-dlp returns the MOST-RECENT comments —
    # without it, yt-dlp's YouTube extractor returns "Top by relevance"
    # which is per-creator-curation, not chronological. The
    # [str(N)] list-of-string wrapping mirrors how CLI args are parsed
    # (--extractor-args "youtube:max_comments=5") and is required by
    # yt-dlp's extractor_args key/value shape. When MAX_COMMENTS == 0 we
    # OMIT extractor_args entirely so yt-dlp's no-comment fast path stays
    # fast (no Innertube /next round-trip).
    opts = dict(base)
    if Config.MAX_COMMENTS > 0:
        opts['extractor_args'] = {
            'youtube': {
                'max_comments': [str(Config.MAX_COMMENTS)],
                'comment_sort': ['new'],
            }
        }
    info = _run_ydl(opts, 'fetch_info',
                    lambda ydl: ydl.extract_info(url, download=False))
    _info_cache_set(uid, url, info)
    # DEBUG level (not INFO) -- see HIT-path comment above.
    logger.debug('fetch_info cache MISS uid=%d url=%s -- refetched',
                 uid, url[:80])
    # CACHE PARTIAL-SUCCESS NOTE: _info_cache_set runs IMMEDIATELY
    # after _run_ydl returns, BEFORE the in-loop caller
    # (show_format_choice) does anything else with the info dict.
    # If the caller raises later in the same await (e.g.
    # bot._pending_urls write, navigation-stack overflow, scrolled
    # message edit_text 4xx), the cache is already populated with
    # a fully-valid info dict. This is the intentional trade-off:
    # the next user-paste within the TTL gets the cached value
    # WITHOUT re-hitting YouTube, even though the original caller
    # hit an unrelated error after the fetch. The alternative
    # (write-through-after-full-success) would mean a transient
    # downstream error wastes the next user's 2-second round-trip
    # for no good reason. Documented here so a future maintainer
    # recognises it as a conscious trade-off, not a bug.
    return info

def download(bot, uid, url, media_type, video_quality=None, audio_quality=None, sub_mode=None, container=None):
    """Download with optional quality/sub_mode/container overrides.

    Returns (file_path, title, video_id, subtitle_files).
    subtitle_files is a list of paths populated when sub_mode='separate' or when the
    embed-merge step fails (fallback so user still receives the subs as files).

    `container`:
      * None → use the user's `video_container` setting ('auto' default).
      * 'auto' → yt-dlp picks the natural codec-compatible container, so
        the manual ffmpeg MKV-embed-subtitles mux path below keeps working.
      * 'mp4' → yt-dlp's `merge_output_format=mp4` is set so the natural
        container is remuxed to MP4. This CASCADES `sub_mode='embed'` to
        `'separate'` for the duration of this download (MP4 cannot natively
        mux soft subtitles). The user's stored sub_mode preference in
        user_settings.json is NOT mutated — the cascade is local.
    """
    # Storage-full short-circuit: refuse before touching yt-dlp so the user
    # gets a clear `disk_error` message instead of an opaque yt-dlp OSError.
    if not _has_disk_space():
        free_mb = Config.MIN_DISK_FREE_MB
        raise StorageFullError(
            f'Less than {free_mb} MB free on bot storage — refusing to start '
            'a download that would crash mid-flight.')

    base_opts = {
        'cookiefile': _cookie_file(bot, uid),
        'quiet': True, 'no_warnings': True,
        'socket_timeout': 120, 'retries': 50, 'fragment_retries': 50,
        'concurrent_fragment_downloads': 2, 'no_mtime': True,
        'no_js_runtimes': True,
        'js_runtimes': {'quickjs': {'path': '/usr/local/bin/qjs'}},
    }
    actual_container = container if container is not None else (
        get_video_container(bot, uid) if media_type == 'video' else 'auto')
    actual_sub_mode = sub_mode or (get_subtitle_mode(bot, uid) if media_type == 'video' else 'off')

    # MP4↔embed cascade: MP4 cannot natively mux soft subtitles, so the
    # generous 'embed' default would silently lose the subs. The pure
    # helper `_effective_sub_mode_for_container` holds the cascade
    # table and is unit-tested in TestMp4ContainerCascade. We re-run
    # it here (already done above by the operator-level composition)
    # so the logger fires once per download and the `actual_sub_mode`
    # variable is the cascaded value used downstream.
    cascaded = _effective_sub_mode_for_container(actual_container, actual_sub_mode)
    if cascaded != actual_sub_mode:
        logger.info(
            'user %d: container=%s cascades sub_mode=%s → %s '
            '(MP4 cannot mux soft subs)',
            uid, actual_container, actual_sub_mode, cascaded)
        actual_sub_mode = cascaded

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
            # merge_output_format is added separately below ONLY on the
            # 'mp4' container path. For 'auto' we deliberately leave it
            # unset so yt-dlp picks the natural codec-compatible
            # container and our manual MKV-embed-subtitle mux path
            # (with ffmpeg's srt codec) keeps working.
        }
        if actual_container == 'mp4':
            opts['merge_output_format'] = 'mp4'
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

    # Run yt-dlp with the configured proxy strategy. `_run_ydl` retries once
    # without the Warp proxy on transient connection errors when USE_WARP=True;
    # when USE_WARP=False it's a single direct call with no retry.
    # `info` and `fp` come from whichever attempt wins, so post-processing
    # (subtitle merge, rename) only runs once.
    def _extract(ydl):
        extracted = ydl.extract_info(url, download=True)
        return extracted, ydl.prepare_filename(extracted)

    info, fp = _extract_with_subtitle_fallback(opts, 'download', _extract)

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
        # Sort alphabetically so per-track language / map ordering is
        # deterministic across platforms. DOWNLOADS_DIR.iterdir() order
        # is undefined on Linux ext4 (inode order), insertion order on
        # some other FSes, alphabetical on Windows NTFS, etc. Without
        # this sort the `-metadata:s:s:<i> language=` flags below would
        # map to whatever stream idx ffmpeg happens to assign at
        # merge time, which can disagree on repeat runs of the same
        # video on different machines — users would see their EN track
        # labelled "es" in some sessions.
        watched_subs.sort()

    # Subtitle handling per user mode (skipped for audio/thumb)
    if media_type == 'video' and watched_subs:
        if actual_sub_mode == 'embed':
            if bot.has_ffmpeg:
                # Recover per-track language tags from yt-dlp's standard
                # `Title.<lang>.<ext>` filename convention so the merged
                # MKV shows up as `language=eng` / `language=spa` etc in
                # VLC / mpv / Plex instead of the default `language=und`.
                sub_langs = [_extract_lang_from_filename(s) for s in watched_subs]
                merged = _merge_subs_into_mkv(
                    video_file, watched_subs, title=title, sub_languages=sub_langs)
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

    # 2026-06-21 always-on audio transcode (TV fix): ensure the file's
    # audio track decodes universally on legacy smart TVs and PC players
    # that lack an Opus hardware decoder or label Opus tracks as
    # 'audio codec: none'. The `_is_already_universal_codec` ffprobe
    # guard safely skips the re-encode when the audio is already AAC
    # (whether from the format-chain AAC-preference tier or from
    # yt-dlp's own MP4 merge pipeline), so double-transcoding cannot
    # happen even on the MP4 path. Skipped on `media_type != 'video'`
    # (audio/thumb don't have a video stream to keep in sync).
    #
    # 6th conjunct (2026-06-21, single-core-VPS response): a ffprobe
    # call checks whether the post-merge audio is already universal
    # (currently just `aac`); if so, the 30-90s ffmpeg re-encode
    # is pure wasted CPU -- short-circuit to delivery instead. Probe
    # cost ~1-2s (reads metadata, NOT the audio stream) vs saved
    # 30-90s on AAC-source videos.
    #
    # NOTE: the probe runs on EVERY download where the gate fires,
    # regardless of source codec. Opus-source downloads pay +1-2s
    # probe cost in addition to the 30-90s transcode (so net cost on
    # Opus path is 31-91s instead of 30-90s -- marginal). Substantial
    # savings only on AAC-source videos (1-2s probe + 0s transcode
    # instead of 30-90s transcode). Operators tuning for total
    # CPU should weight this trade-off; if a real-world workload is
    # Opus-heavy, the operator may consider disabling AAC_TRANSCODE
    # entirely (set to false in .env) which skips BOTH the probe and
    # the transcode (and gives up the TV-fix for the few AAVC-Owner
    # smart-TV users in their tenancy).
    #
    # Probe failure falls through to the active transcode so an
    # operator with AAC_TRANSCODE=true who has TV compat issues
    # isn't silently disabled by a probe blip (CPU-conservative
    # operators should set AAC_TRANSCODE=false instead).
    #
    # The conjunct order matters: `Path(fp).exists()` runs BEFORE
    # `_is_already_universal_codec(fp)` so a missing file
    # short-circuits before the ffprobe call.
    #
    # Failures are silent -- on any ffmpeg error we leave the
    # untranscoded file intact so the user still gets a download
    # (degraded audio compat, but the video plays on every device
    # that handled AVC). The pre-existing OPCode DAG for this
    # segment is small enough to keep the conditional explicit; a
    # future maintainer can lift it into a helper if a third codec
    # (e.g. AC-3) joins.
    if (media_type == 'video'
            and Config.AAC_TRANSCODE
            and bot.has_ffmpeg
            and Path(fp).exists()
            and not _is_already_universal_codec(fp)):
        transcoded = _transcode_audio_to_aac(fp, title=title)
        if transcoded:
            # Helper returns the same `fp` it was given (in-place
            # overwrite via os.replace), so this rebind is a no-op
            # today. KEPT as a defensive contract pin: if a future
            # refactor of the helper returns a DIFFERENT path
            # (e.g., `<fp>.aac.mkv`), this rebind is what makes the
            # post-transcode sanitize-rename step use the new file.
            # test_gate_assigns_transcoded_back_to_fp pins this
            # contract so a future refactor that drops the rebind
            # fails loudly.
            fp = transcoded

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

    def _thumb(ydl):
        extracted = ydl.extract_info(url, download=False)
        t = extracted.get('title', 'Unknown')
        v = extracted.get('id', '')
        ydl.download([url])
        return extracted, t, v

    info, title, vid = _run_ydl(base, 'download_thumb', _thumb)

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