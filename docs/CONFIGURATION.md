# Configuration Reference

> **All environment variables and their effects**

---

## Table of Contents

- [Required Variables](#required-variables)
- [Network & File Serving](#network--file-serving)
- [Access Control](#access-control)
- [Quality & Format Defaults](#quality--format-defaults)
- [Storage & Retention](#storage--retention)
- [Advanced Features](#advanced-features)
- [Logging](#logging)
- [SSL/TLS](#ssltls)
- [Complete .env Example](#complete-env-example)

---

## Required Variables

| Variable | Description | Default |
|----------|-------------|---------|
| `BOT_TOKEN` | Telegram Bot API token from [@BotFather](https://t.me/BotFather) | — |

```env
BOT_TOKEN=1234567890:ABCdefGHIjklMNOpqrsTUVwxyz
```

---

## Network & File Serving

| Variable | Description | Default |
|----------|-------------|---------|
| `BASE_DOWNLOAD_LINK` | Public URL for download links (the bot listens on the port parsed from this URL) | `http://your-server-ip:8000` |

```env
# Without Cloudflare:
BASE_DOWNLOAD_LINK=http://123.123.123.123:8000

# With Cloudflare Tunnel (no port needed):
BASE_DOWNLOAD_LINK=https://downloads.yourdomain.com

# With native HTTPS:
BASE_DOWNLOAD_LINK=https://downloads.yourdomain.com:8000
```

> **How the bot parses this**: It extracts the port by splitting on `:` and taking the last segment of the host part. If parsing fails, it falls back to port 8000.

---

## Access Control

### WHITELIST_USERS

| Variable | Description | Default |
|----------|-------------|---------|
| `WHITELIST_USERS` | Comma-separated Telegram user IDs allowed to use the bot | Empty (all users) |

```env
WHITELIST_USERS=123456789,987654321
```

The first gate checked by every handler (`utils.ok()`):
- **Unset/empty**: All users can interact with the bot
- **Set with IDs**: Only listed users can send commands and download

### ADMIN_USERS

| Variable | Description | Default |
|----------|-------------|---------|
| `ADMIN_USERS` | Comma-separated user IDs allowed to upload cookies via `/cookies` | Empty (all whitelisted users) |

```env
ADMIN_USERS=123456789
```

Security gating for the `/cookies` command (defense-in-depth):

- **Unset/empty (default)**: Every whitelisted user can upload cookies (legacy behavior)
- **Set with valid IDs**: Only those users can use `/cookies`. Others see: "🔒 Cookie uploads are admin-only."
- **Set but ALL tokens malformed** (e.g., `ADMIN_USERS=abc,def`): **FAIL-CLOSED** — all uploads denied + warning logged. The operator clearly intended to gate cookies but parsing failed.

---

## Quality & Format Defaults

These values set the **default** preferences. Users can change them per-chat via the `/settings` menu or inline buttons.

### Video Quality

Options: `best`, `2160p`, `1440p`, `1080p`, `720p`, `480p`, `360p`, `worst`

**Important**: Quality options above `1080p` (4K/1440p) force the H.264 codec pin (`[vcodec^=avc]`). YouTube serves these resolutions only in VP9/AV1, so selecting them will likely result in a `format_unavailable` error. The best H.264 quality is typically 1080p.

### Audio Quality

Options: `best`, `320`, `256`, `192`, `128`, `96`, `worst`

### Subtitle Mode

Options: `embed` (default, MKV only), `separate` (.srt files), `off`

### Auto Format (Private Chat Only)

Options: `ask` (default, shows format picker), `video`, `audio`, `thumb`

When set to `video`/`audio`/`thumb`, the bot skips the format-choice keyboard and downloads the chosen media type immediately. Useful for power users who always want the same format.

### Video Container

Options: `auto` (default), `mp4`

- **`auto`**: yt-dlp picks the natural container. Typically MKV for VP9+Opus streams, MP4 for H.264+AAC. Enables subtitle embedding into MKV.
- **`mp4`**: Forces `merge_output_format=mp4`. Better compatibility (iOS, older Android, WhatsApp). Caveat: MP4 cannot natively mux soft subtitles — `embed` mode automatically cascades to `separate`.

### Delivery Method

Options: `ask` (default), `telegram`, `link`

Sets how files are delivered after download:
- **`ask`**: Shows the delivery keyboard every time
- **`telegram`**: Automatically uploads via Telegram Bot API
- **`link`**: Generates a download link automatically

---

## Storage & Retention

| Variable | Description | Default |
|----------|-------------|---------|
| `STORAGE_DAYS` | Days before downloaded files auto-delete | `2` |
| `MIN_DISK_FREE_MB` | Minimum free disk space (MB) before refusing downloads | `1024` |

```env
STORAGE_DAYS=7
MIN_DISK_FREE_MB=2048
```

The bot enforces `STORAGE_DAYS` at startup via `_cleanup_orphans()`:
1. Removes orphaned fragments (`.ytdl`, `.part`, `.tmp.`, `.frag.` files)
2. Removes output-format files older than `STORAGE_DAYS` that aren't pinned in the user's video records

The pre-flight disk check (`_has_disk_space()`):
- **Refuses** to start a download if free space < `MIN_DISK_FREE_MB`
- Shows a friendly `💾 Bot storage full` message
- Set to `0` to disable the check (not recommended)

---

## Advanced Features

### Cloudflare Warp Proxy

| Variable | Description | Default |
|----------|-------------|---------|
| `USE_WARP` | Route yt-dlp through Cloudflare Warp (`127.0.0.1:40000`) | `false` |

```env
USE_WARP=true
```

When enabled:
1. All yt-dlp calls attempt the Warp proxy first
2. On transient connection errors (refused, reset, timeout), retries once **without** the proxy
3. Useful for geo-unblocking YouTube content or rate-limit avoidance

**Requires**: `warp-cli` installed, logged in, and connected on the VPS.

### AAC Transcode (TV Fix)

| Variable | Description | Default |
|----------|-------------|---------|
| `AAC_TRANSCODE` | Re-encode Opus audio to AAC for universal smart TV compatibility | `true` |

```env
AAC_TRANSCODE=false  # Disable if all your users have modern Opus-aware devices
```

When enabled:
1. After yt-dlp merge, probes the audio codec via ffprobe
2. If already AAC → skip (saves ~30-90s CPU)
3. If Opus or other → transcode to AAC 192kbps

Cost: ~30-90s extra on a single-core VPS for Opus-source videos.

### YouTube Comments

| Variable | Description | Default |
|----------|-------------|---------|
| `MAX_COMMENTS` | Number of recent comments to display after download (0 = off) | `0` |

```env
MAX_COMMENTS=5  # Show 5 most recent comments
```

- Range: `0` (off) to `20` (max-safe, hard-capped)
- Each comment adds ~1-2s to the fetch_info call
- Comments are sorted by newest first

### Cookie TTL

| Variable | Description | Default |
|----------|-------------|---------|
| `COOKIE_TTL_HOURS` | Hours before cookies expire (0 = until bot restart) | `0` |

```env
COOKIE_TTL_HOURS=24  # Expire cookies every 24 hours
```

---

## Logging

| Variable | Description | Default |
|----------|-------------|---------|
| `LOG_LEVEL` | Bot's logging level | `INFO` |

```env
LOG_LEVEL=WARNING  # Quiet mode — only warnings and errors
```

Valid values: `DEBUG`, `INFO`, `WARNING`, `ERROR`, `CRITICAL` (case-insensitive).

The log level is applied to the `yt_bot` logger. Third-party library loggers (`httpx`, `httpcore`, `telegram`, `aiohttp`) stay at `WARNING` regardless of this setting, so library DEBUG chatter never floods the logs.

Third-party library loggers (`httpx`, `httpcore`, `telegram`, `aiohttp`) stay at `WARNING` regardless of this setting. This prevents Telegram/HTTP library internals from flooding production logs.

---

## SSL/TLS

| Variable | Description | Default |
|----------|-------------|---------|
| `SSL_CERT_FILE` | Path to SSL certificate file (PEM) | Empty (HTTP mode) |
| `SSL_KEY_FILE` | Path to SSL private key file (PEM) | Empty (HTTP mode) |
| `SSL_CERT_B64` | Base64-encoded SSL certificate (CI/CD only) | — |
| `SSL_KEY_B64` | Base64-encoded SSL private key (CI/CD only) | — |

```env
SSL_CERT_FILE=/etc/letsencrypt/live/example.com/fullchain.pem
SSL_KEY_FILE=/etc/letsencrypt/live/example.com/privkey.pem
```

**Behaviour matrix**:

| SSL_CERT_FILE | SSL_KEY_FILE | Result |
|:---:|:---:|--------|
| Empty | Empty | Plain HTTP (default) |
| Set | Set | HTTPS (if files exist + valid) |
| Set | Empty | Bot crashes with SSLConfigError (exit 78) |
| Empty | Set | Bot crashes with SSLConfigError (exit 78) |
| Bad path | Bad path | Bot crashes with SSLConfigError (exit 78) |

**For CI/CD deployments**: Encode as base64 and set as GitHub Secrets:

```bash
base64 -w0 /path/to/fullchain.pem   # → SSL_CERT_B64
base64 -w0 /path/to/privkey.pem     # → SSL_KEY_B64
```

The deploy workflow decodes these to `/opt/TelegramYtBot/ssl/` and writes the paths to `.env`.

**Important notes:**
- The bot exits with code **78 (EX_CONFIG)** on SSL misconfiguration — systemd's `RestartPreventExitStatus=78` prevents auto-restart looping
- Certificate renewal does NOT hot-reload. Restart the bot after renewal
- If both SSL files are set AND the base URL starts with `http://` (not `https://`), a warning is logged — Telegram download links will fail

---

## Complete .env Example

```env
# === Required ===
BOT_TOKEN=1234567890:ABCdefGHIjklMNOpqrsTUVwxyz
BASE_DOWNLOAD_LINK=http://your-server-ip:8000

# === Access Control ===
# WHITELIST_USERS=123456789,987654321
ADMIN_USERS=123456789

# === Quality Defaults (users can override per-chat) ===
# Video: best, 2160p, 1440p, 1080p, 720p, 480p, 360p, worst
# Audio: best, 320, 256, 192, 128, 96, worst
# Subs: embed, separate, off

# === Storage ===
STORAGE_DAYS=2
MIN_DISK_FREE_MB=1024

# === Advanced ===
USE_WARP=false
AAC_TRANSCODE=true
MAX_COMMENTS=0
COOKIE_TTL_HOURS=0

# === Logging ===
LOG_LEVEL=INFO

# === SSL/TLS (optional) ===
# SSL_CERT_FILE=/etc/letsencrypt/live/example.com/fullchain.pem
# SSL_KEY_FILE=/etc/letsencrypt/live/example.com/privkey.pem
# SSL_CERT_B64=
# SSL_KEY_B64=
```

---

**Next:** [Usage Guide](./USAGE.md) → [SSL with Cloudflare](./SSL_CLOUDFLARE.md) → [Back to README](../README.md)
