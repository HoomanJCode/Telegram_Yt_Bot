"""Message handler for private chats and groups"""
import asyncio
from datetime import datetime
from pathlib import Path
from telegram.constants import ParseMode, ChatType
from app.models import VideoRecord
from app.downloader import download, fetch_info
from app.utils import extract_url, extract_video_id, find_existing, esc, ok
from app.handlers.navigation import nav_clear, show_format_choice, menu
from app.handlers.formats import show_delivery

async def on_msg(bot, u, c):
    uid = u.effective_user.id; msg = u.message
    is_private = msg.chat.type == ChatType.PRIVATE
    is_group = msg.chat.type in (ChatType.GROUP, ChatType.SUPERGROUP)
    if is_private and not ok(bot, uid): return
    url = extract_url(msg.text)
    if not url: return
    video_id = extract_video_id(url)
    if not video_id:
        if is_private: await msg.reply_text("❌ Invalid URL."); return

    if is_group:
        if not await _check_group(bot, msg.chat_id, c.bot): return
        if not await _ensure(bot, uid): return
        async with bot._download_semaphore:
            await _group_download(bot, uid, url, msg, 'video', video_id)
        return

    if not await _ensure(bot, uid): await msg.reply_text("❌ Upload cookies first! /cookies"); return
    nav_clear(bot, uid); await show_format_choice(bot, uid, url, video_id, msg)

async def _group_download(bot, uid, url, msg, media_type, video_id):
    try:
        existing = find_existing(bot, uid, video_id, media_type)
        if existing: fp, title = existing.file_path, existing.title
        else:
            fp, title, vid = await asyncio.get_event_loop().run_in_executor(None, download, bot, uid, url, media_type)
            sz = Path(fp).stat().st_size
            record = VideoRecord(title, url, vid, fp, sz, datetime.now().strftime('%Y-%m-%d %H:%M:%S'), media_type=media_type)
            bot.videos.setdefault(uid, []).insert(0, record)
            while len(bot.videos.get(uid, [])) > 20: old = bot.videos[uid].pop(); Path(old.file_path).unlink(missing_ok=True)
            bot.save()
        mb = Path(fp).stat().st_size / 1024 / 1024
        kb = _group_delivery_kb(bot, uid)
        await msg.reply_text(f"✅ *{esc(title[:200])}*\n📦 {mb:.2f} MB\n\nChoose delivery:", parse_mode=ParseMode.MARKDOWN, reply_markup=kb, reply_to_message_id=msg.message_id)
    except Exception as e: pass

async def download_task(bot, uid, url, msg, media_type):
    from app.downloader import download_thumb
    s = await msg.reply_text(f"⏳ Downloading {media_type}...")
    try:
        if media_type == 'thumb': fp, title, vid = await asyncio.get_event_loop().run_in_executor(None, download_thumb, bot, uid, url)
        else: fp, title, vid = await asyncio.get_event_loop().run_in_executor(None, download, bot, uid, url, media_type)
        sz = Path(fp).stat().st_size
        record = VideoRecord(title, url, vid, fp, sz, datetime.now().strftime('%Y-%m-%d %H:%M:%S'), media_type=media_type)
        bot.videos.setdefault(uid, []).insert(0, record)
        while len(bot.videos.get(uid, [])) > 20: old = bot.videos[uid].pop(); Path(old.file_path).unlink(missing_ok=True)
        bot.save(); await s.delete()
        from app.handlers.formats import show_delivery  # <-- ADD THIS LINE
        await show_delivery(bot, msg, record, 0)
    except Exception as e: await s.edit_text("❌ Failed.", reply_markup=menu(bot, uid))

async def _ensure(bot, uid):
    if uid in bot._cookie_data: return True
    if uid in bot._cookie_file_ids: return await _load_cookies(bot, uid)
    return False

async def _load_cookies(bot, uid):
    if not bot._bot: return False
    try:
        file = await bot._bot.get_file(bot._cookie_file_ids[uid])
        cookie_bytes = await file.download_as_bytearray()
        bot._cookie_data[uid] = bytes(cookie_bytes)
        if uid in bot._cookie_tmpfiles:
            try: import os; os.unlink(bot._cookie_tmpfiles[uid]); del bot._cookie_tmpfiles[uid]
            except: pass
        return True
    except:
        del bot._cookie_file_ids[uid]; bot.save(); return False

async def _check_group(bot, chat_id, bot_client):
    if chat_id in bot._group_admins and bot._group_admins[chat_id]: return True
    try:
        admins = await bot_client.get_chat_administrators(chat_id)
        bot._group_admins[chat_id] = {a.user.id for a in admins if ok(bot, a.user.id)}
        return bool(bot._group_admins[chat_id])
    except: return False

def _group_delivery_kb(bot, uid):
    from telegram import InlineKeyboardButton, InlineKeyboardMarkup
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📤 Send via Telegram", callback_data='tg_new')],
        [InlineKeyboardButton("📋 Get Download Link", callback_data='lk_new')],
    ])