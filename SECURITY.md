# Security Policy

> **How security issues are handled and how to keep your deployment safe**

---

## Table of Contents

- [Supported Versions](#supported-versions)
- [Reporting a Vulnerability](#reporting-a-vulnerability)
- [Security Features](#security-features)
- [Operator Hardening Checklist](#operator-hardening-checklist)
- [Data Handling & Privacy](#data-handling--privacy)
- [Security Scope](#security-scope)

---

## Supported Versions

This project is maintained as a rolling-release repository:

| Version | Supported |
|---------|-----------|
| Latest release (`v*` tag) | ✅ |
| Latest `master` | ✅ (best effort) |
| Older releases | ❌ — upgrade to the latest release |

Security fixes land on `master` first and are shipped with the next release tag. See the [CI/CD section in the README](./README.md#-cicd-pipeline) for how releases are built.

---

## Reporting a Vulnerability

**Please do not open a public GitHub issue for security vulnerabilities.**

To report a vulnerability privately:

1. Go to the **Security** tab of this repository
2. Click **Report a vulnerability** → **Advisory**
3. Describe the issue, including:
   - Affected component (e.g. `app/downloader.py`, `app/fileserver.py`, `config.py`)
   - Steps to reproduce (if any)
   - Impact and suggested fix (optional)

The maintainer will acknowledge reports as soon as possible, confirm the issue, and work toward a fix. Public disclosure is coordinated after a patch is available.

For **non-security bugs**, feature requests, or usage questions, please use [GitHub Issues](https://github.com/HoomanJCode/Telegram_Yt_Bot/issues) instead.

---

## Security Features

The bot is designed with privacy and access control in mind:

- 🍪 **Cookies in RAM only** — Cookie bytes are kept in memory and never written to disk (except temporary yt-dlp files); cleared on restart
- 👥 **Whitelist system** — Restrict the bot to specific Telegram user IDs via `WHITELIST_USERS`
- 👑 **Admin gating** — Lock `/cookies` to specific Telegram user IDs via `ADMIN_USERS` (fail-closed on malformed config)
- 🗑️ **Auto-cleanup** — Downloaded files auto-delete after `STORAGE_DAYS` (default: 2)
- 🔒 **SSL validation** — On TLS misconfiguration the bot exits with code 78 rather than silently falling back to HTTP
- 📝 **No sensitive logs** — API tokens and cookie contents are never logged
- 🚫 **No user data collection** — No analytics, no tracking, no external calls except yt-dlp fetching from YouTube
- 🧹 **Disk-full guard** — `MIN_DISK_FREE_MB` pre-flight check prevents ENOSPC mid-download

---

## Operator Hardening Checklist

If you run this bot, follow these steps to secure your deployment:

1. **Set `WHITELIST_USERS`** — Only allow known Telegram user IDs to use the bot
2. **Set `ADMIN_USERS`** — Restrict cookie uploads to yourself (cookie files are sensitive auth tokens)
3. **Use a private bot** — Set the bot to *private* in BotFather so only whitelisted users can find it
4. **Serve download links over HTTPS** — Use one of the [three HTTPS options](./docs/SSL_CLOUDFLARE.md) so files aren't transferred in plaintext
5. **Keep secrets out of logs and git** — `BOT_TOKEN` and cookie files must only live in `.env` (which is gitignored); never commit them
6. **Keep dependencies updated** — Regularly `docker compose pull` / rebuild to pick up yt-dlp and library security fixes
7. **Limit exposure of the file server** — `BASE_DOWNLOAD_LINK` should point at your own server; files auto-delete after `STORAGE_DAYS`

---

## Data Handling & Privacy

| Data | Storage | Retention |
|------|---------|-----------|
| Cookie bytes | RAM only | Until restart (`COOKIE_TTL_HOURS=0`) or TTL if set |
| Download history | `data/` JSON | Until cleanup / manual deletion |
| Downloaded files | `downloads/` | `STORAGE_DAYS` (default 2), then auto-delete |
| Logs | `bot.log` / journalctl | No tokens or cookies ever logged |

The bot performs **no analytics, telemetry, or user tracking**. Its only outbound network activity is yt-dlp fetching video metadata/media from YouTube (optionally routed through Cloudflare Warp if `USE_WARP=true`).

---

## Security Scope

This is an **educational project** — see the [License](./LICENSE) and the disclaimer in the [README](./README.md). It is not intended for production deployment, and the maintainers assume **no liability** for misuse. Security hardening guidance above is provided as a courtesy; operators are responsible for complying with all applicable laws and terms of service.

---

**Next:** [Configuration Guide](./docs/CONFIGURATION.md) → [Deployment Guide](./docs/DEPLOYMENT.md) → [Back to README](./README.md)