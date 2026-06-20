"""Message handler for private chats and groups"""
import asyncio, os, logging
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
from app.handlers.navigation import nav_clear, show_format_choice, menu

logger = logging.getLogger('yt_bot')

async def on_msg(bot, u, c):
    uid = u.effective_user.id; msg = u.message
    is_private = msg.chat.type == ChatType.PRIVATE
    is_group = msg.chat.type in (ChatType.GROUP, ChatType.SUPERGROUP)
    if is_private and not ok(bot, uid): return
    url = extract_url(msg.text)
    if not url: return
    video_id = extract_video_id(url)
    if not video_id:
        if is_private: await msg.reply_text("❌ Invalid URL.", reply_to_message_id=msg.message_id); return

    if is_group:
        if not await _check_group(bot, msg.chat_id, c.bot): return
        if not await _ensure(bot, uid): return
        async with bot._download_semaphore:
            await _group_download(bot, uid, url, msg, 'video', video_id)
        return

    if not await _ensure(bot, uid): await msg.reply_text("❌ Upload cookies first! /cookies", reply_to_message_id=msg.message_id); return
    nav_clear(bot, uid)
    # Auto-format: skip the keyboard, route to download_task directly.
    auto = get_auto_format(bot, uid)
    if auto != 'ask' and auto in AUTO_FORMAT_OPTIONS:
        existing = find_existing(bot, uid, video_id, auto)
        if existing:
            from app.handlers.formats import show_delivery
            await show_delivery(bot, msg, existing, bot.videos[uid].index(existing))
            return
        async with bot._download_semaphore:
            await download_task(bot, uid, url, msg, auto,
                                container_override=get_video_container(bot, uid))
        return
    await show_format_choice(bot, uid, url, video_id, msg)

async def _group_download(bot, uid, url, msg, media_type, video_id):
    try:
        existing = find_existing(bot, uid, video_id, media_type)
        fp = title = vid = None
        sub_files = []
        if existing:
            fp, title = existing.file_path, existing.title
        else:
            fp, title, vid, sub_files = await asyncio.get_event_loop().run_in_executor(None, download, bot, uid, url, media_type)
            sz = Path(fp).stat().st_size
            record = VideoRecord(title, url, vid, fp, sz, datetime.now().strftime('%Y-%m-%d %H:%M:%S'), media_type=media_type)
            bot.videos.setdefault(uid, []).insert(0, record)
            while len(bot.videos.get(uid, [])) > 20: old = bot.videos[uid].pop(); Path(old.file_path).unlink(missing_ok=True)
            bot.save()

        # If we got separate sub files (sub mode 'separate' or merge fallback), send them too
        for sub in sub_files:
            if not Path(sub).exists(): continue
            sz = Path(sub).stat().st_size / 1024
            try:
                with open(sub, 'rb') as fh:
                    await msg.reply_document(document=fh, filename=Path(sub).name,
                                             caption=f"📝 {Path(sub).name}", reply_to_message_id=msg.message_id)
            except Exception:
                pass

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
        await msg.reply_text(f"✅ *{esc(title[:200])}*\n📦 {mb:.2f} MB\n\nChoose delivery:", parse_mode=ParseMode.MARKDOWN, reply_markup=kb, reply_to_message_id=msg.message_id)
    except Exception as e:
        category = classify_yt_error(str(e))
        logger.error("Group download error [%s]: %s", category, str(e)[:200])
        await msg.reply_text(friendly_error_msg(category), reply_to_message_id=msg.message_id)

async def download_task(bot, uid, url, msg, media_type, container_override=None):
    from app.downloader import download_thumb
    s = await msg.reply_text(f"⏳ Downloading {media_type}...", reply_to_message_id=msg.message_id)
    try:
        if media_type == 'thumb':
            fp, title, vid, sub_files = await asyncio.get_event_loop().run_in_executor(None, download_thumb, bot, uid, url)
        else:
            # Pass through the per-call container choice so callers
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
        sz = Path(fp).stat().st_size
        record = VideoRecord(title, url, vid, fp, sz, datetime.now().strftime('%Y-%m-%d %H:%M:%S'), media_type=media_type)
        bot.videos.setdefault(uid, []).insert(0, record)
        while len(bot.videos.get(uid, [])) > 20: old = bot.videos[uid].pop(); Path(old.file_path).unlink(missing_ok=True)
        bot.save(); await s.delete()
        if sub_files:
            record._pending_subs = sub_files  # attach to record for show_delivery to send
        from app.handlers.formats import show_delivery
        await show_delivery(bot, msg, record, 0)
    except Exception as e:
        category = classify_yt_error(str(e))
        logger.error("Download task error [%s]: %s", category, str(e)[:200])
        await s.edit_text(friendly_error_msg(category), reply_markup=menu(bot, uid))

async def _ensure(bot, uid):
    if uid in bot._cookie_data: return True
    if uid in bot._cookie_file_ids:
        result = await _load_cookies(bot, uid)
        return result
    return False

async def _load_cookies(bot, uid):
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
    if chat_id in bot._group_admins and bot._group_admins[chat_id]: return True
    try:
        admins = await bot_client.get_chat_administrators(chat_id)
        bot._group_admins[chat_id] = {a.user.id for a in admins if ok(bot, a.user.id)}
        return bool(bot._group_admins[chat_id])
    except: return False

def _group_delivery_kb(bot, uid):
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📤 Send via Telegram", callback_data='tg_new')],
        [InlineKeyboardButton("📋 Get Download Link", callback_data='lk_new')],
    ])