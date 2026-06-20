import logging
import os
from dotenv import load_dotenv

load_dotenv()

# Module-level logger for security/audit events that need to surface in
# journalctl. `Config.is_admin` logs here when ADMIN_USERS parses to an
# empty set due to all-malformed tokens — a fail-closed safety net that
# the operator notices on their next restart.
logger = logging.getLogger('yt_bot')

def _env_bool(key, default='false'):
    """Parse an env var as bool. Truthy = 1/true/yes/on (case-insensitive)."""
    return os.getenv(key, default).strip().lower() in ('1', 'true', 'yes', 'on')


def _env_int(key, default):
    """Parse an env var as a positive-or-zero int. Returns `default` on
    missing/empty/non-numeric input instead of raising — config errors must
    never prevent the bot from starting. Negative values are clamped to 0
    (i.e. min_bytes=0 = check disabled), so a fat-fingered `-1` cannot
    silently turn the disk-full check off without leaving a clear intent.
    """
    raw = os.getenv(key)
    if raw is None or raw.strip() == '':
        return default
    try:
        return int(raw.strip())
    except ValueError:
        return default


# The 5 Python `logging` standard levels that `setLevel()` accepts. Anything
# else (e.g. an operator typo like "VERBOSE") is treated as garbage so we
# fall back to the default instead of raising AttributeError deep inside
# logging internals when a wrong-but-resolvable name slips through.
_LOG_LEVEL_NAMES = ('DEBUG', 'INFO', 'WARNING', 'ERROR', 'CRITICAL')


def _env_log_level(key, default='INFO'):
    """Parse an env var as a Python `logging` level name.

    Returns the actual `logging.{LEVEL}` int constant (e.g. `logging.INFO`)
    so the caller can pass it straight into `logger.setLevel(...)`. Case-
    and whitespace-insensitive: `' debug '`, `'Debug'`, and `'DEBUG'` all
    return the same `logging.DEBUG`. On missing/empty/unknown value,
    returns the default (also as a `logging.LEVEL` constant), so a
    fat-fingered `LOG_LEVEL=VBOSE` quietly falls back to a sensible level
    rather than crashing the bot's log setup. The validation set is
    intentionally restricted to the 5 standard levels — even though
    `logging.getLevelNamesMapping()` exposes additional numeric levels,
    operators probably don't want to dial those in and almost certainly
    didn't mean `NOTSET` (= 0, = capture everything, = storage death
    sentence).
    """
    raw = os.getenv(key, default).strip().upper()
    if raw not in _LOG_LEVEL_NAMES:
        return getattr(logging, default.upper())
    return getattr(logging, raw)

class Config:
    BOT_TOKEN = os.getenv('BOT_TOKEN', 'YOUR_BOT_TOKEN_HERE')
    BASE_DOWNLOAD_LINK = os.getenv('BASE_DOWNLOAD_LINK', 'http://your-server-ip:8000')
    WHITELIST_USERS = os.getenv('WHITELIST_USERS', '')
    DOWNLOAD_DIR = 'downloads'
    MAX_TELEGRAM_FILE_SIZE = 50 * 1024 * 1024  # 50MB
    STORAGE_DAYS = int(os.getenv('STORAGE_DAYS', '2'))
    COOKIE_TTL_HOURS = int(os.getenv('COOKIE_TTL_HOURS', '0'))  # 0 = until restart
    # Route yt-dlp traffic through Cloudflare Warp at 127.0.0.1:40000.
    # When False (default) yt-dlp connects directly and the Warp fallback
    # retry logic in app/downloader.py is skipped entirely.
    # NOTE: the truthy set below is mirrored by the shell case normalization
    # in .github/workflows/deploy.yml — keep both in sync.
    USE_WARP = _env_bool('USE_WARP', 'false')
    # Minimum free disk space (in MB) required on DOWNLOADS_DIR's filesystem
    # before starting a download. Below this threshold the bot short-circuits
    # with a `disk_error` message instead of letting yt-dlp hit ENOSPC mid
    # download. Default 1024 MB (1 GB) comfortably covers typical 1080p mux
    # peaks (separate audio + video + fragments + final mp4 coexisting) on a
    # small VPS. Tune up if you monitor ENOSPC probes, tune down for tight
    # disks where occasional mid-download failures are acceptable. Set to 0
    # to disable the pre-flight check entirely (debug only — the operator
    # must own the consequence of an ENOSPC mid-download). Parsed via
    # _env_int so missing/empty/non-numeric values fall back to the default
    # instead of crashing bot startup.
    MIN_DISK_FREE_MB = max(0, _env_int('MIN_DISK_FREE_MB', 1024))
    # Bot log level. Operators running on a constrained VPS reported
    # `bot.log` filling their disk because the `yt_bot` logger was pinned
    # at INFO unconditionally — every download / cookie-restore / menu
    # event landed in journalctl → /var/log/<service>/bot{,_error}.log.
    # Default stays at INFO (matches the previous behaviour, so an upgrade
    # is non-disruptive for existing operators) but setting
    # `LOG_LEVEL=WARNING` in `.env` (or systemd's EnvironmentFile) cleanly
    # suppresses the noisy downloads-log for production while keeping
    # WARNING/ERROR visible for genuine issues. Parsed via _env_log_level
    # so missing/empty/garbage values fall back silently instead of
    # crashing bot startup. Valid values: DEBUG, INFO, WARNING, ERROR,
    # CRITICAL (case-insensitive, whitespace tolerated).
    LOG_LEVEL = _env_log_level('LOG_LEVEL', 'INFO')
    # NOTE: this value is captured into app.downloader.MIN_DISK_FREE_BYTES
    # at module import time. A bot restart is required for changes to
    # MIN_DISK_FREE_MB to take effect — live-tweaking Config at runtime
    # does not propagate to the pre-flight check.
    # Telegram user IDs (comma-separated) that are allowed to upload cookies.
    # Cookies are sensitive auth tokens — operators running a shared bot can
    # lock the /cookies entry point behind this gate. Exit semantics:
    #   * ADMIN_USERS unset/empty (default): every whitelisted user keeps the
    #     pre-existing upload path. The bot behaves exactly as before.
    #   * ADMIN_USERS set to a comma-separated ID list: only those uids can
    #     trigger `ask_cookies`/`recv_cookies`. Other whitelisted users see a
    #     "🔒 Cookie uploads are admin-only" reply and the conversation exits
    #     cleanly (ConversationHandler.END) so their next text message is not
    #     swallowed by the conversation's WAITING_FOR_COOKIES handlers.
    # NOT cached at import: `is_admin()` re-parses ADMIN_USERS each call so
    # test monkey-patching takes effect without a module reload.
    ADMIN_USERS = os.getenv('ADMIN_USERS', '')
    
    @classmethod
    def get_whitelist(cls):
        if not cls.WHITELIST_USERS:
            return set()
        return set(int(uid.strip()) for uid in cls.WHITELIST_USERS.split(',') if uid.strip())

    @classmethod
    def get_admin_set(cls):
        """Parse ADMIN_USERS on each call (NO import-time caching) so tests
        can monkey-patch Config.ADMIN_USERS without reloading the module.
        Returns a set[int] — empty if unset/empty/malformed.
        """
        raw = (cls.ADMIN_USERS or '').strip()
        if not raw:
            return set()
        out = set()
        for token in raw.split(','):
            token = token.strip()
            if not token:
                continue
            try:
                out.add(int(token))
            except ValueError:
                # Malformed ID — ignore the bad token, keep the rest. If the
                # whole env var is garbage we land at an empty set, which
                # `is_admin` treats as 'admin gating requested but no valid
                # uids listed' → deny all (safe default).
                continue
        return out

    @classmethod
    def is_admin(cls, uid):
        """Cookie-upload gate. Defense-in-depth layered over `ok()`.

        Three distinct semantics keyed off the raw `ADMIN_USERS` value:

        * Unset/empty (default): permissive. Every whitelisted user keeps
          the legacy cookie-upload path. Preserves prior behavior for
          deployments that didn't opt into admin gating.
        * Set to at least one valid uid: only listed uids can upload.
          Non-listed users get a "🔒 admin-only" reply and the
          conversation exits cleanly.
        * Set but ALL tokens malformed (e.g. "abc,def"): FAIL-CLOSED.
          The operator's intent was clearly to gate cookies (otherwise
          they wouldn't have set the var), but parsing yielded no valid
          ids. We refuse to silently fall back to permissive — an
          otherwise-unconfigured gate with parsing errors is a likely
          typo and a fail-open security toggle. A WARNING is logged so
          the typo surfaces in journalctl on the next restart.

        NOT cached: ADMIN_USERS is re-read each call so test
        monkey-patching (Config.ADMIN_USERS = ...) takes effect without
        a module reload.
        """
        raw = (cls.ADMIN_USERS or '').strip()
        if not raw:
            # Admin gating unconfigured → permissive (legacy behavior).
            return True
        admin_set = cls.get_admin_set()
        if not admin_set:
            # Admin gating IS configured but parsing produced zero valid
            # ids. Fail-closed: log a warning + deny.
            logger.warning(
                "ADMIN_USERS env var is set but no token parsed as an "
                "integer Telegram ID (raw=%r); denying all cookie "
                "uploads (fail-closed). Fix the env var and restart.",
                cls.ADMIN_USERS)
            return False
        return uid in admin_set