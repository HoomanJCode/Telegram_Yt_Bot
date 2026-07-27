"""Message handler for private chats and groups.

This module processes incoming text messages that contain YouTube links. It
coordinates URL extraction, format selection, download execution, and file
delivery. Group chats always receive video downloads; private chats respect
per-user settings like auto-format and default delivery.

AI RULE: If you modify this file, you must also update and fix the comments,
docstrings, and descriptions to keep them accurate and current. Every function
must have a descriptive docstring explaining its purpose, parameters, and
return values. Inline comments should explain WHY, not WHAT.
"""
import asyncio, os, logging, traceback
from datetime import datetime
from functools import partial
from pathlib import Path
from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ParseMode, ChatType
from app.models import VideoRecord
from app.downloader import download, fetch_info
from app.utils import (
    extract_url, extract_video_id, find_existing, esc, ok,
    get_default_delivery, get_auto_format, get_video_container,
    classify_yt_error, friendly_error_msg,
)
from app.utils import AUTO_FORMAT_OPTIONS
from app.handlers.navigation import nav_clear_user, show_format_choice, menu

logger = logging.getLogger('yt_bot')

async def on_msg(bot, u, c):
    """Handle a plain text message that may contain a YouTube link."""
    uid = u.effective_user.id; msg = u.message
    is_private = msg.chat.type == ChatType.PRIVATE
    is_group = msg.chat.type in (ChatType.GROUP, ChatType.SUPERGROUP)
    if is_private and not ok(bot, uid): return
    # Extract the first YouTube-looking URL from the message text. Ignore
    # everything else so casual chat does not trigger downloads.
    url = extract_url(msg.text)
    if not url:
        return
    # Extract the YouTube video id. Without a valid id we cannot deduplicate
    # or build cached keys, so reply with an friendly error in private chats.
    video_id = extract_video_id(url)
    if not video_id:
        if is_private:
            await msg.reply_text("❌ Invalid URL.", reply_to_message_id=msg.message_id)
            return

    # In groups, only allow downloads if an admin of the group is whitelisted.
    if is_group:
        if not await _check_group(bot, msg.chat_id, c.bot):
            return
        if not await _ensure(bot, uid): return
        async with bot._download_semaphore:
            await _group_download(bot, uid, url, msg, 'video', video_id)
        return

    # Private chats require valid cookies. If cookies are missing/expired,
    # prompt the user to upload them.
    if not await _ensure(bot, uid):
        await msg.reply_text("❌ Upload cookies first! /cookies", reply_to_message_id=msg.message_id)
        return
    # No `nav_clear_user(bot, uid)` here intentionally: with per-message
    # state, an OLD format-picker's nav_stack BELONGS to that picker --
    # clearing it would invalidate still-active 'b' buttons on stale
    # messages. The new flow's flow creates its own per-message key
    # automatically, so the two flows coexist safely.
    # Auto-format: if the user has chosen a default media type, skip the
    # format picker and start that download immediately.
    auto = get_auto_format(bot, uid)
    if auto != 'ask' and auto in AUTO_FORMAT_OPTIONS:
        existing = find_existing(bot, uid, video_id, auto)
        if existing:
            from app.handlers.formats import show_delivery
            await show_delivery(bot, msg, existing)
            return
        async with bot._download_semaphore:
            await download_task(bot, uid, url, msg, auto,
                                container_override=get_video_container(bot, uid))
        return
    # Default behaviour: show the format picker inline keyboard.
    await show_format_choice(bot, uid, url, video_id, msg)

async def _group_download(bot, uid, url, msg, media_type, video_id):
    """Download a video in a group chat context and send the delivery keyboard."""
    try:
        existing = find_existing(bot, uid, video_id, media_type)
        fp = title = vid = None
        sub_files = []
        if existing:
            # Cache hit: reuse the existing on-disk file and VideoRecord.
            fp, title = existing.file_path, existing.title
            record = existing
            # Clear any stale subtitle attachment from a prior delivery so
            # show_delivery doesn't waste I/O trying to send now-deleted
            # subtitle files (silently swallowed by except Exception: pass).
            record._pending_subs = None
        else:
            # No cached record: run the actual download in a thread pool so
            # the event loop stays responsive.
            fp, title, vid, sub_files = await asyncio.get_event_loop().run_in_executor(None, download, bot, uid, url, media_type)
            sz = Path(fp).stat().st_size
            record = VideoRecord(title, url, vid, fp, sz, datetime.now().strftime('%Y-%m-%d %H:%M:%S'), media_type=media_type)
            bot.videos.setdefault(uid, []).insert(0, record)
            while len(bot.videos.get(uid, [])) > 20: old = bot.videos[uid].pop(); Path(old.file_path).unlink(missing_ok=True)
            bot.save()

        # If the download produced separate subtitle files, deliver them as
        # Telegram documents alongside the main file.
        for sub in sub_files:
            if not Path(sub).exists(): continue
            sz = Path(sub).stat().st_size / 1024
            try:
                with open(sub, 'rb') as fh:
                    await msg.reply_document(document=fh, filename=Path(sub).name,
                                             caption=f"📝 {Path(sub).name}", reply_to_message_id=msg.message_id)
            except Exception:
                pass

        # Respect the user's default delivery setting. Groups only show the
        # inline delivery keyboard if the default is 'ask'.
        default = get_default_delivery(bot, uid)
        if default == 'telegram':
            from app.handlers.tokens import send_file
            record = find_existing(bot, uid, video_id, media_type)
            if record: await send_file(bot, msg, record)
            return
        elif default == 'link':
            record = find_existing(bot, uid, video_id, media_type)
            if record and Path(record.file_path).exists():
                from urllib.parse import quote
                url_link = f"{bot.base_url}/{quote(Path(record.file_path).name)}"
                mb = Path(record.file_path).stat().st_size / 1024 / 1024
                await msg.reply_text(
                    f"🎬 *{esc(record.title[:200])}*\n\n📦 {mb:.2f} MB\n📥 {url_link}\n\n⚠️ {bot.config.STORAGE_DAYS}d retention.",
                    parse_mode=ParseMode.MARKDOWN, disable_web_page_preview=False,
                    reply_to_message_id=msg.message_id)
            return

        mb = Path(fp).stat().st_size / 1024 / 1024
        kb = _group_delivery_kb(bot, uid)
        delivery_msg = await msg.reply_text(f"✅ *{esc(title[:200])}*\n📦 {mb:.2f} MB\n\nChoose delivery:", parse_mode=ParseMode.MARKDOWN, reply_markup=kb, reply_to_message_id=msg.message_id)
        # Per-message keying (2026-07-15 stale-button fix): bind this
        # delivery message to the record so the kb's `tg_new` /
        # `lk_new` callbacks find it through `_delivery_screen`
        # instead of guessing via `bot.videos[uid][0]`. The LRU bound
        # on `_delivery_screen` caps memory growth.
        from app.handlers.formats import _delivery_screen_put
        _delivery_screen_put(bot, delivery_msg.chat.id, delivery_msg.message_id, record)
    except Exception as e:
        category = classify_yt_error(str(e))
        logger.error("Group download error [%s]: %s", category, str(e)[:200])
        await msg.reply_text(friendly_error_msg(category), reply_to_message_id=msg.message_id)

async def download_task(bot, uid, url, msg, media_type, container_override=None):
    """Download a single media type for a user and present delivery options."""
    from app.downloader import download_thumb
    # Show a progress message that will later be edited or deleted.
    s = await msg.reply_text(f"⏳ Downloading {media_type}...", reply_to_message_id=msg.message_id)
    try:
        # Thumbnails use a dedicated lightweight download path.
        if media_type == 'thumb':
            fp, title, vid, sub_files = await asyncio.get_event_loop().run_in_executor(None, download_thumb, bot, uid, url)
        else:
            # Video/audio downloads accept the optional container override
            # (format_choice_kb's MKV/MP4 buttons, auto_format=='video'
            # branch) get to override the user's stored container
            # setting exactly for this download without mutating it.
            #
            # The kwargs MUST be bound via functools.partial before the
            # call lands on the executor — BaseEventLoop.run_in_executor's
            # signature is (executor, func, *args), NOT **(executor, func,
            # *args, **kwargs), so any `container=` / `sub_mode=` /
            # `video_quality=` kwarg here raises:
            #
            #   TypeError: BaseEventLoop.run_in_executor() got an
            #   unexpected keyword argument 'video_quality'
            #
            # which surfaces in the VPS log as
            # "Download task error [unknown]" and silently breaks every
            # MKV / MP4 download (caught in the bug on the live bot —
            # 2026-06-20). `partial(...)` turns the kwargs into POSITIONAL
            # bound args on the wrapper callable, which run_in_executor
            # happily forwards to its thread pool.
            fp, title, vid, sub_files = await asyncio.get_event_loop().run_in_executor(
                None,
                partial(download, bot, uid, url, media_type,
                        video_quality=None, audio_quality=None,
                        sub_mode=None, container=container_override))
        # Build a VideoRecord from the downloaded file and prepend it to
        # the user's recent list (maximum 20 items).
        sz = Path(fp).stat().st_size
        record = VideoRecord(title, url, vid, fp, sz, datetime.now().strftime('%Y-%m-%d %H:%M:%S'), media_type=media_type)
        bot.videos.setdefault(uid, []).insert(0, record)
        while len(bot.videos.get(uid, [])) > 20: old = bot.videos[uid].pop(); Path(old.file_path).unlink(missing_ok=True)
        bot.save()
        # Attach any separate subtitle files to the record so that the
        # delivery screen can send them along with the main file.
        if sub_files:
            record._pending_subs = sub_files
        from app.handlers.formats import show_delivery
        await show_delivery(bot, msg, record)
        # Delete the progress message only after the delivery screen has
        # been rendered successfully.
        # show_delivery succeeds. If show_delivery raises, `s`
        # is still alive and the except block can edit it safely.
        await s.delete()
    except Exception as e:
        category = classify_yt_error(str(e))
        logger.error(
            "Download task error [%s] uid=%d url=%s media=%s: %s\n%s",
            category, uid, url[:120], media_type, str(e)[:200],
            traceback.format_exc())
        # `s` may have been deleted by the successful path above;
        # try edit_text first (it's still alive on an early-raise)
        # and fall back to a fresh reply if the message is gone.
        try:
            await s.edit_text(friendly_error_msg(category), reply_markup=menu(bot, uid))
        except Exception:
            await msg.reply_text(friendly_error_msg(category), reply_markup=menu(bot, uid))

async def _ensure(bot, uid):
    """Ensure the user has valid cookies loaded.

    Returns True if cookies are in memory or can be restored from a
    previously saved file_id. Returns False otherwise.
    """
    if uid in bot._cookie_data: return True
    if uid in bot._cookie_file_ids:
        result = await _load_cookies(bot, uid)
        return result
    return False

async def _load_cookies(bot, uid):
    """Restore cookie bytes from Telegram using the stored file_id."""
    logger.info("Restoring cookies for user %d from Telegram", uid)
    if not bot._bot:
        logger.warning("No bot reference for cookie restore")
        return False
    try:
        file = await bot._bot.get_file(bot._cookie_file_ids[uid])
        cookie_bytes = await file.download_as_bytearray()
        bot._cookie_data[uid] = bytes(cookie_bytes)
        if uid in bot._cookie_tmpfiles:
            try: os.unlink(bot._cookie_tmpfiles[uid]); del bot._cookie_tmpfiles[uid]
            except: pass
        logger.info("Cookies restored for user %d", uid)
        return True
    except Exception as e:
        logger.error("Cookie restore failed %d: %s", uid, str(e)[:100])
        return False

async def _check_group(bot, chat_id, bot_client):
    """Return True if at least one admin of the group is whitelisted."""
    if chat_id in bot._group_admins and bot._group_admins[chat_id]: return True
    try:
        admins = await bot_client.get_chat_administrators(chat_id)
        bot._group_admins[chat_id] = {a.user.id for a in admins if ok(bot, a.user.id)}
        return bool(bot._group_admins[chat_id])
    except: return False

def _group_delivery_kb(bot, uid):
    """Build the inline keyboard used for group-chat deliveries."""
    """Group-chat delivery kb.

    After the 2026-07-15 fix the kb is bound to its record via
    `bot._delivery_screen[(delivery_msg.chat.id, delivery_msg.message_id)]`
    (see _group_download which calls `_delivery_screen_put`).
    The cb_data here is therefore index-free -- the per-message
    key in `_delivery_screen` is what resolves the record on
    click.
    """
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📤 Send via Telegram", callback_data='tg_send')],
        [InlineKeyboardButton("📋 Get Download Link", callback_data='lk_send')],
    ])