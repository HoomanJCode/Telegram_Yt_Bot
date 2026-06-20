"""Command handlers: /start, /help, /recent, /status, /cancel"""
import asyncio
import shutil
import socket
import subprocess
from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from app.handlers.navigation import nav_clear, show_recent

def _port_reachable(host, port, timeout=1.0):
    """True if a TCP connection to `host:port` succeeds within `timeout` seconds."""
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except (ConnectionRefusedError, socket.timeout, OSError):
        return False

def _run_sync(cmd, timeout=1.5):
    """Run `cmd` in a subprocess with a hard timeout. Returns (rc, stdout, stderr_or_msg)."""
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return r.returncode, (r.stdout or '').strip(), (r.stderr or '').strip()
    except subprocess.TimeoutExpired:
        return -1, '', 'timeout'
    except FileNotFoundError:
        return 127, '', 'not found'
    except Exception as e:
        return -1, '', str(e)[:80]

async def _gather_proxy_info():
    """Return a list of human-readable lines describing the current proxy state.

    Safe to call from the bot's async event loop: every subprocess / socket
    probe is fenced with a hard timeout so a stuck warp-cli or systemd unit
    can never hold the handler. The three external probes run concurrently
    so total latency is bounded by the slowest probe (≤3s) rather than the
    sum of all per-probe timeouts (≤6s).
    """
    from config import Config
    loop = asyncio.get_running_loop()

    if not Config.USE_WARP:
        return [
            "⚪ Cloudflare Warp: ⛔ disabled (USE_WARP=false)",
            "   yt-dlp connects directly with no proxy.",
            "   Set USE_WARP=true in .env + redeploy to enable.",
        ]

    lines = ["🟢 Cloudflare Warp: ✅ enabled (USE_WARP=true)"]

    # 1) warp-cli installed? `shutil.which` is an in-process PATH lookup that
    #    avoids the subprocess "command -v" trap (where `command` is a shell
    #    builtin and may not exist as a binary on every distro).
    warp_cli_installed = shutil.which('warp-cli') is not None
    lines.append(f"   • warp-cli installed: {'✅' if warp_cli_installed else '❌'}")

    async def _svc_active():
        # systemctl is-active codes: 0=active, 3=inactive, 4=no such unit,
        # 127=systemctl missing (non-systemd systems).
        return await loop.run_in_executor(
            None, lambda: _run_sync(['systemctl', 'is-active', 'warp-svc'], timeout=1.0))

    async def _tcp_probe():
        return await loop.run_in_executor(
            None, lambda: _port_reachable('127.0.0.1', 40000, 1.0))

    async def _warp_cli_status():
        # warp-cli can stall on first IPC after a warp-svc restart, so give
        # it a slightly longer per-call timeout. Only meaningful if installed.
        if warp_cli_installed:
            return await loop.run_in_executor(
                None, lambda: _run_sync(['warp-cli', 'status'], timeout=3.0))
        return None

    # `return_exceptions=True` so a single misbehaving probe cannot orphan
    # the other in-flight subprocess / socket waits. Today every probe swallows
    # its own errors (`_run_sync` returns a tuple, `_port_reachable` returns
    # False on OSError), so gather doesn't normally need this — but the
    # flag makes the helper robust against future probes that mistakenly raise.
    svc_rc, tcp_ok, cli = await asyncio.gather(
        _svc_active(), _tcp_probe(), _warp_cli_status(),
        return_exceptions=True)

    # 2) warp-svc systemd unit state
    if isinstance(svc_rc, Exception):
        lines.append(f"   • warp-svc daemon: ❓ probe raised {type(svc_rc).__name__}")
    elif svc_rc == 0:
        lines.append("   • warp-svc daemon: 🟢 active")
    elif svc_rc == 3:
        lines.append("   • warp-svc daemon: 🔴 inactive")
    elif svc_rc == 127:
        lines.append("   • warp-svc daemon: ❓ systemctl not available (non-Linux?)")
    else:
        lines.append(f"   • warp-svc daemon: ❓ systemctl exit {svc_rc}")

    # 3) TCP probe — does the local proxy port answer?
    if isinstance(tcp_ok, Exception):
        lines.append(f"   • proxy 127.0.0.1:40000: ❓ probe raised {type(tcp_ok).__name__}")
    else:
        lines.append(
            f"   • proxy 127.0.0.1:40000: {'✅ reachable' if tcp_ok else '❌ unreachable'}")

    # 4) warp-cli status
    if cli is not None:
        if isinstance(cli, Exception):
            lines.append(f"   • warp-cli state: ❓ probe raised {type(cli).__name__}")
        else:
            rc, out, err = cli
            # Look at stdout AND stderr so we never miss the state line just
            # because warp-cli redirected it. We never SURFACE this combined
            # text to the user — warp-cli output can include license / endpoint
            # metadata we don't want to leak via Telegram. Each side is stripped
            # independently so whitespace-only stderr noise doesn't pad the
            # substring check.
            blob = (out.strip() + ' ' + err.strip()).strip()
            if 'Connected' in blob and 'Disconnected' not in blob:
                lines.append("   • warp-cli state: 🟢 connected")
            elif 'Disconnected' in blob:
                lines.append("   • warp-cli state: ⚪ disconnected")
            elif blob == 'timeout':
                lines.append("   • warp-cli state: ❓ timed out")
            elif blob == 'not found':
                lines.append("   • warp-cli state: ❓ binary missing")
            elif rc != 0:
                lines.append(f"   • warp-cli state: ⚠ exit {rc}")
            else:
                lines.append("   • warp-cli state: ⚠ undetermined")

    return lines

async def start_cmd(bot, u, c):
    uid = u.effective_user.id; args = c.args
    from app.utils import ok
    if not ok(bot, uid): await u.message.reply_text("⛔"); return
    if args and args[0].startswith('dl_'):
        from app.handlers.tokens import handle_token_start
        await handle_token_start(bot, uid, args[0], u.message); return
    # Set default language from Telegram
    if uid not in bot._user_langs:
        bot._user_langs[uid] = u.effective_user.language_code or 'en'
        bot.save()
    from app.handlers.messages import _ensure
    await _ensure(bot, uid)
    nav_clear(bot, uid)
    from app.handlers.navigation import welcome_text, menu
    await u.message.reply_text(await welcome_text(bot), reply_markup=menu(bot, uid))

async def help_cmd(bot, u, c):
    from app.handlers.navigation import menu
    await u.message.reply_text(
        "📚 Send YouTube link.\n"
        "📱 Inline: @botname <link>\n"
        "/settings /status /cookies /recent",
        reply_markup=menu(bot, u.effective_user.id))


async def settings_cmd(bot, u, c):
    """Per-user settings viewer/editor (`/settings`).

    Wraps the existing inline menu (which already exposes per-setting
    buttons) with an intro text summarizing the current values so users
    who prefer text commands over inline buttons have a discoverable
    entry point. Does not introduce new state — reuses
    `get_video_quality`/`get_audio_quality`/`get_subtitle_mode`/
    `get_default_delivery` and the existing `setvq_*`/`setaq_*`/
    `setsm_*`/`setdelivery_*`/`setlang_*` callback handlers.
    """
    from app.utils import ok
    from app.utils import (
        VIDEO_QUALITY_LABELS, AUDIO_QUALITY_LABELS, SUBTITLE_MODE_LABELS,
    )
    from app.handlers.navigation import menu
    uid = u.effective_user.id
    if not ok(bot, uid):
        await u.message.reply_text("⛔")
        return

    settings = bot._user_settings.get(uid, {})
    vq = settings.get('video_quality', 'best')
    aq = settings.get('audio_quality', 'best')
    sm = settings.get('subtitle_mode', 'embed')
    delivery = settings.get('default_delivery', 'ask')
    delivery_labels = {
        'ask': 'Ask each time', 'telegram': 'Telegram', 'link': 'Link',
    }
    intro = (
        "⚙️ *Your settings* (tap a button below to change):\n\n"
        f"🎬 Video: {VIDEO_QUALITY_LABELS.get(vq, vq)}\n"
        f"🎵 Audio: {AUDIO_QUALITY_LABELS.get(aq, aq)}\n"
        f"📝 Subs: {SUBTITLE_MODE_LABELS.get(sm, sm)}\n"
        f"📤 Delivery: {delivery_labels.get(delivery, delivery)}"
    )
    await u.message.reply_text(
        intro, parse_mode='Markdown', reply_markup=menu(bot, uid))

async def recent_cmd(bot, u, c):
    nav_clear(bot, u.effective_user.id); await show_recent(bot, u, c)

async def status_cmd(bot, u, c):
    """Report bot runtime state incl. Warp proxy health. Async-safe; every probe is bounded."""
    from app.utils import ok
    from app.handlers.navigation import menu
    uid = u.effective_user.id
    if not ok(bot, uid):
        await u.message.reply_text("⛔"); return
    lines = ["🤖 Bot status:"]
    try:
        lines.extend(await _gather_proxy_info())
    except Exception as e:
        lines.append(f"   ⚠ proxy probe failed: {type(e).__name__}: {str(e)[:80]}")
    await u.message.reply_text("\n".join(lines), reply_markup=menu(bot, uid))

async def cancel_cmd(bot, u, c):
    nav_clear(bot, u.effective_user.id)
    from app.handlers.navigation import menu
    from telegram.ext import ConversationHandler
    await u.message.reply_text("❌ Cancelled.", reply_markup=menu(bot, u.effective_user.id))
    return ConversationHandler.END