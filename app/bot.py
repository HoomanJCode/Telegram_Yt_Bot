# app/bot.py
"""Main bot class with shared state"""
import asyncio, os, time, logging, ssl
from collections import OrderedDict
from pathlib import Path
from typing import Any, Dict, List, Tuple
from app.models import VideoRecord
from app.fileserver import FileServer
from app.utils import check_ffmpeg, load_data, save_data

logger = logging.getLogger('yt_bot')

DATA_DIR = Path('data')
DOWNLOADS_DIR = Path('downloads')

# Output extensions yt-dlp writes when a download successfully completes.
# Anything else found in DOWNLOADS_DIR is by definition an orphaned fragment
# or partial and is safe to delete at startup.
_KNOWN_OUTPUT_EXT = {
    '.mp4', '.mkv', '.webm',         # video containers
    '.mp3', '.m4a', '.opus',         # audio containers
    '.jpg', '.webp', '.png',         # thumbnails
    '.srt', '.vtt',                  # subtitles
    '.info.json',                    # yt-dlp metadata sidecar (re-fetched on demand)
}

# yt-dlp fragment / partial extensions removed unconditionally at startup.
_ORPHAN_EXT = {'.ytdl', '.part'}


class SSLConfigError(RuntimeError):
    """Raised on a permanent SSL_CERT_FILE / SSL_KEY_FILE misconfiguration.

    Caught at app/__init__.py::main() which exits with code 78 (EX_CONFIG
    from sysexits.h). deploy.sh's systemd unit sets
    RestartPreventExitStatus=78 so the bot does NOT auto-restart on a
    config error — without this, `Restart=always + RestartSec=10` would
    loop forever (~5 lines/sec into journalctl) until the operator
    manually intervenes. The exception message is operator-actionable
    (specific path/env name, plus "Fix .env and restart" hint), so
    fixing the .env and running `systemctl start telegramytbot` is the
    full recovery flow.
    """


def _build_ssl_context():
    """Read SSL_CERT_FILE / SSL_KEY_FILE env vars, validate, return an
    `ssl.SSLContext` ready to pass to aiohttp.web.TCPSite.

    Behaviour matrix (defined here so the function is unit-testable
    independently from YouTubeDownloaderBot.__init__'s filesystem /
    FileServer plumbing):

    * Both env vars empty (legacy default): return None. Caller passes
      None to FileServer → plain HTTP. Backwards-compatible with
      pre-SSL deployments.
    * Only one of cert/key set: raise SSLConfigError. Reject on
      principle rather than guess which side is correct; a partial
      config is almost always a typo and silently picking a side
      would obscure the mistake.
    * Both set, files not regular files: raise SSLConfigError.
    * Both set + loadable as a PEM cert+key chain: return an
      `ssl.SSLContext` built with `Purpose.CLIENT_AUTH` (the correct
      specifier for a TLS server authenticating connecting clients).
    * Soft-warns (does NOT raise) if the private key is group/world
      readable (POSIX mode & 0o077). On Windows the stat bits don't
      reflect ACLs, so the warning is silently skipped there.

    This function does NOT call sys.exit; raising a typed exception
    keeps `__init__` exception-safe and lets main() own the exit
    semantics. The cross-validation against `BASE_DOWNLOAD_LINK` (warn
    on http:// + HTTPS-enabled) is intentionally NOT done here — that
    belongs to YouTubeDownloaderBot.__init__ because it needs `self.base_url`.
    """
    ssl_cert_raw = os.getenv('SSL_CERT_FILE', '')
    ssl_key_raw = os.getenv('SSL_KEY_FILE', '')
    ssl_cert = ssl_cert_raw.strip()
    ssl_key = ssl_key_raw.strip()
    if not (ssl_cert or ssl_key):
        # Legacy HTTP mode — backwards-compatible default.
        return None
    if not (ssl_cert and ssl_key):
        raise SSLConfigError(
            'SSL config error: SSL_CERT_FILE and SSL_KEY_FILE must both '
            'be set, or both empty. Currently SSL_CERT_FILE='
            f'{ssl_cert_raw!r} SSL_KEY_FILE={ssl_key_raw!r}. Fix .env '
            'and restart.')
    cert_path = Path(ssl_cert)
    key_path = Path(ssl_key)
    if not cert_path.is_file():
        raise SSLConfigError(
            f'SSL_CERT_FILE does not exist or is not a regular file: '
            f'{cert_path}. Common cause: filesystem path uses Windows '
            f'backslashes; use forward slashes. Fix .env and restart.')
    if not key_path.is_file():
        raise SSLConfigError(
            f'SSL_KEY_FILE does not exist or is not a regular file: '
            f'{key_path}. Fix .env and restart.')
    # Soft warning: a world/group-readable private key is the standard
    # "leaky key" footgun. We don't refuse to start (containers without
    # POSIX stat bits would block legit restarts); we just surface the
    # smell in journalctl.
    try:
        mode = key_path.stat().st_mode
        if mode & 0o077:
            logger.warning(
                'SECURITY: SSL_KEY_FILE (%s) is accessible to group or '
                'other (mode=0o%o). Run `chmod 600 %s` to lock it down.',
                key_path, mode & 0o777, key_path)
    except OSError:
        # stat() failure already caught above; second pass is best-effort.
        pass
    try:
        # Purpose.CLIENT_AUTH is the correct specifier for a server
        # authenticating connecting clients (vs CLIENT used by a TLS
        # client to verify a server). DO NOT pass check_hostname=True
        # here — that is client-side verification semantics; the
        # server only hands a cert to the client.
        ctx = ssl.create_default_context(ssl.Purpose.CLIENT_AUTH)
        ctx.load_cert_chain(certfile=str(cert_path), keyfile=str(key_path))
    except Exception as e:
        raise SSLConfigError(
            f'Failed to build SSL context from SSL_CERT_FILE={cert_path} '
            f'SSL_KEY_FILE={key_path}: {e}. Likely cause: cert and key '
            f'do not form a matching PEM pair, or fullchain.pem is '
            f'required but only cert.pem is provided.')
    logger.info(
        'Native HTTPS enabled (cert=%s key=%s). Certificate renewal '
        'requires a service restart to take effect.',
        cert_path, key_path)
    return ctx

class YouTubeDownloaderBot:
    def __init__(self):
        from config import Config
        self.config = Config()
        self.base_url = self.config.BASE_DOWNLOAD_LINK.rstrip('/')
        try: port = int(self.base_url.split(':')[-1]) if ':' in self.base_url.split('/')[2] else 8000
        except: port = 8000
        for d in (DATA_DIR, DOWNLOADS_DIR): d.mkdir(parents=True, exist_ok=True)
        self._cookie_file_ids: Dict[int, str] = {}
        self._cookie_data: Dict[int, bytes] = {}
        self._cookie_tmpfiles: Dict[int, str] = {}
        self._user_langs: Dict[int, str] = {}
        self._user_settings: Dict[int, dict] = {}  # uid -> {default_delivery: 'ask'|'telegram'|'link'}
        self._bot = None
        self._bot_username = None
        self.videos: Dict[int, List[VideoRecord]] = {}
        # Per-message ephemeral state. Keying by (chat_id, message_id) means
        # each Telegram message is self-identifying: inline-keyboard buttons
        # attached to an OLD message do NOT silently leak to the LATEST
        # URL/record when the user starts a second download before clicking
        # the first.
        #
        # Bug fixed: previously `_pending_urls[uid] = (url, ...)` was a single
        # per-user slot that a second `show_format_choice` overwrote, and
        # `bot.videos[uid]` inserted at index 0 on each new download, so
        # `tg_<idx>` callbacks on a stale delivery-screen message resolved
        # to the wrong record. With per-message keys, every inline keyboard
        # reads the state the bot wrote when IT rendered that message.
        #
        # `_delivery_screen` is bounded with a small OrderedDict LRU so
        # abandoned deliveries don't leak memory; re-keying pushes older
        # entries out. The cap matches a fully-active user flow by an order
        # of magnitude.
        # The OrderedDict-LRU cap is shared across all per-message ephemeral
        # state. _delivery_screen has been bounded since the 2026-07-15
        # fix landed; the parallel caps on _pending_urls and _nav_stack
        # were added after a code-review feedback round flagged the
        # bot could leak memory on a long-lived VPS that has processed
        # N URLs (one entry per `show_format_choice` write) and M menu
        # interactions (one entry per `nav_push`). The cap is large
        # enough that an actively-using bot never evicts a still-active
        # entry but small enough that a leaked parallel-downloader
        # inviter-sender cannot exhaust RAM.
        self._ephemeral_max = 1024
        self._pending_urls: "OrderedDict[Tuple[int, int], Tuple[str, str, str]]" = OrderedDict()
        self._nav_stack: "OrderedDict[Tuple[int, int], List[Tuple[str, Any]]]" = OrderedDict()
        self._delivery_screen: "OrderedDict[Tuple[int, int], VideoRecord]" = OrderedDict()
        self._tokens: Dict[str, dict] = {}
        self._group_admins: Dict[int, set] = {}
        self._global_file_ids: Dict[str, str] = {}
        self._download_semaphore = asyncio.Semaphore(1)
        self.has_ffmpeg = check_ffmpeg()
        # Native-HTTPS opt-in for the file server. Operators who set
        # BASE_DOWNLOAD_LINK=https://something historically found
        # "HTTPS doesn't work" because the aiohttp FileServer is plain
        # HTTP by default and there was no TLS listener anywhere.
        # _build_ssl_context() reads SSL_CERT_FILE / SSL_KEY_FILE,
        # returns an ssl_context (or None for legacy HTTP mode) and
        # raises SSLConfigError on a permanent misconfig; main()
        # catches that and exits with code 78 so systemd does NOT
        # auto-restart-loop on a PermanentlyWrong .env.
        ssl_context = _build_ssl_context()
        # Cross-validation: TLS is configured but the printed download
        # URL is http:// — the classic operator typo (missing "s").
        # We WARN rather than fail-fast: an operator mid-migration
        # (deliberately flipping in a follow-up commit) shouldn't be
        # blocked, and `BaseDownloadLink` change + fleet bot restart
        # is the natural remediation sequence.
        if ssl_context is not None and self.base_url.startswith('http://'):
            logger.warning(
                'SSL_CERT_FILE/SSL_KEY_FILE are configured but '
                'BASE_DOWNLOAD_LINK scheme is "http://" (%s). Telegram '
                'download links will hit TLS-handshake failure on '
                'connect. Set BASE_DOWNLOAD_LINK=https://... to match '
                'the cert.',
                self.base_url)
        self.file_server = FileServer(port=port, ssl_context=ssl_context)
        load_data(self)
        self._cleanup_orphans()

    def save(self):
        save_data(self)

    def _cleanup_orphans(self):
        """Enforce the `STORAGE_DAYS` retention that the UI promises.

        Two passes, each run once at startup before any handler fires:

        1. Unconditional sweep — remove any `.ytdl`/`.part` file or anything
           whose name contains `.tmp.`/`.frag.` and any file whose extension
           is not in `_KNOWN_OUTPUT_EXT`.  These are by definition dead
           fragments left behind by a crashed or killed download.
        2. Retention sweep — remove `_KNOWN_OUTPUT_EXT` files whose mtime
           is older than `STORAGE_DAYS` AND that aren't currently pinned in
           `self.videos`.  This keeps the `2d retention` promise honest even
           when `bot.videos` never hit the 20-item cap.

        Logs the bytes freed so an operator can spot a runaway disk.
        """
        if not DOWNLOADS_DIR.exists():
            return
        pinned = set()
        for records in self.videos.values():
            for r in records:
                try:
                    pinned.add(os.path.normcase(os.path.abspath(str(r.file_path))))
                except (OSError, ValueError):
                    continue
        try:
            cutoff = time.time() - int(self.config.STORAGE_DAYS) * 86400
        except (TypeError, ValueError):
            cutoff = time.time() - 2 * 86400  # mirror default STORAGE_DAYS

        removed_bytes = 0
        removed_files = 0
        try:
            entries = list(DOWNLOADS_DIR.iterdir())
        except OSError as e:
            logger.warning('startup cleanup: iterdir failed: %s', e)
            return

        for f in entries:
            try:
                if not f.is_file():
                    continue
                ap = os.path.normcase(os.path.abspath(str(f)))
                if ap in pinned:
                    continue
                name = f.name
                ext = f.suffix.lower()
                is_temp = (
                    ext in _ORPHAN_EXT
                    or '.tmp.' in name
                    or '.frag.' in name
                )
                is_orphan_ext = ext not in _KNOWN_OUTPUT_EXT
                if is_temp or is_orphan_ext:
                    try:
                        sz = f.stat().st_size
                        f.unlink()
                        removed_bytes += sz
                        removed_files += 1
                    except OSError:
                        continue
                    continue
                # Output-format file -> retention check
                try:
                    mtime = f.stat().st_mtime
                except FileNotFoundError:
                    continue
                if mtime < cutoff:
                    try:
                        sz = f.stat().st_size
                        f.unlink()
                        removed_bytes += sz
                        removed_files += 1
                    except OSError:
                        continue
            except OSError:
                continue

        if removed_files:
            logger.info(
                'startup cleanup freed %d files, %.2f MB',
                removed_files, removed_bytes / 1024 / 1024)

    async def _router(self, u, c):
        from app.handlers.navigation import router
        await router(self, u, c)