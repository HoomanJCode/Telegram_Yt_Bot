"""Handle /start dl_TOKEN shared links"""
import asyncio
from pathlib import Path
from datetime import datetime
from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from app.models import VideoRecord
from app.downloader import download, download_thumb
from app.utils import find_existing

async def handle_token_start(bot, uid, param, msg):
    bot_username = await _username(bot)
    token = param[3:] if param.startswith('dl_') else param
    req = bot._tokens.get(token)

    if not req:
        if len(token) == 11:
            for mt in ('video', 'audio', 'thumb'):
                if f"{token}:{mt}" in bot._global_file_ids:
                    await send_file(bot, msg, {'file_path': None, 'title': 'Cached', 'media_type': mt, 'video_id': token})
                    return
            for uv in bot.videos.values():
                for v in uv:
                    if v.video_id == token and Path(v.file_path).exists():
                        await send_file(bot, msg, v); return
        await msg.reply_text("❌ Expired.\nTry again from inline mode."); return

    if req['status'] == 'completed' and req['file_path'] and Path(req['file_path']).exists():
        await send_file(bot, msg, req)
    elif req['status'] == 'pending':
        await msg.reply_text("⏳ Starting download...", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔄 Check Progress", url=f"https://t.me/{bot_username}?start=dl_{token}")]]))
        asyncio.create_task(_do_download(bot, token))
    elif req['status'] == 'downloading':
        await msg.reply_text("⏳ Still downloading...", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔄 Try Again", url=f"https://t.me/{bot_username}?start=dl_{token}")]]))
    elif req['status'] == 'failed':
        await msg.reply_text("❌ Failed.\nTry again from inline mode.")

async def _do_download(bot, token):
    async with bot._download_semaphore:
        req = bot._tokens.get(token)
        if not req: return
        req['status'] = 'downloading'
        uid, url, mt = req['uid'], req['url'], req['media_type']
        try:
            if mt == 'thumb': fp, title, vid = await asyncio.get_event_loop().run_in_executor(None, download_thumb, bot, uid, url)
            else: fp, title, vid = await asyncio.get_event_loop().run_in_executor(None, download, bot, uid, url, mt)
            req['status'] = 'completed'; req['file_path'] = fp; req['title'] = title; req['video_id'] = vid
            sz = Path(fp).stat().st_size
            record = VideoRecord(title, url, vid, fp, sz, datetime.now().strftime('%Y-%m-%d %H:%M:%S'), media_type=mt)
            bot.videos.setdefault(uid, []).insert(0, record)
            while len(bot.videos.get(uid, [])) > 20: old = bot.videos[uid].pop(); Path(old.file_path).unlink(missing_ok=True)
            bot.save()
        except Exception as e: req['status'] = 'failed'; req['error'] = str(e)[:200]

async def send_file(bot, msg, record_or_req):
    from urllib.parse import quote
    from pathlib import Path
    from telegram.constants import ParseMode
    if isinstance(record_or_req, VideoRecord): record, fp, title, mt, vid = record_or_req, record_or_req.file_path, record_or_req.title, record_or_req.media_type, record_or_req.video_id
    else: record, fp, title, mt, vid = None, record_or_req.get('file_path'), record_or_req.get('title', 'Unknown'), record_or_req.get('media_type', 'video'), record_or_req.get('video_id', '')

    ck = f"{vid}:{mt}" if vid else None
    if ck and ck in bot._global_file_ids:
        try:
            fid = bot._global_file_ids[ck]
            if mt == 'thumb': await msg.reply_photo(photo=fid, caption=f"🖼️ {title}")
            elif mt == 'audio': await msg.reply_audio(audio=fid, title=title)
            else: await msg.reply_video(video=fid, caption=f"🎬 {title}", supports_streaming=True)
            return
        except: del bot._global_file_ids[ck]; bot.save()

    if record and record.telegram_file_id:
        try:
            if mt == 'thumb': await msg.reply_photo(photo=record.telegram_file_id, caption=f"🖼️ {title}")
            elif mt == 'audio': await msg.reply_audio(audio=record.telegram_file_id, title=title)
            else: await msg.reply_video(video=record.telegram_file_id, caption=f"🎬 {title}", supports_streaming=True)
            return
        except: record.telegram_file_id = None; bot.save()

    if not fp or not Path(fp).exists(): await msg.reply_text("❌ File deleted."); return
    mb = Path(fp).stat().st_size / 1024 / 1024
    if mb > bot.config.MAX_TELEGRAM_FILE_SIZE: await msg.reply_text(f"⚠️ Too large ({mb:.1f}MB)\n📥 `{bot.base_url}/{quote(Path(fp).name)}`", parse_mode=ParseMode.MARKDOWN); return

    s = await msg.reply_text("📤 Sending...")
    try:
        with open(fp, 'rb') as f:
            if mt == 'thumb': sent = await msg.reply_photo(photo=f, caption=f"🖼️ {title}"); fid = sent.photo[-1].file_id
            elif mt == 'audio': sent = await msg.reply_audio(audio=f, title=title, performer="YouTube"); fid = sent.audio.file_id
            else: sent = await msg.reply_video(video=f, caption=f"🎬 {title}", supports_streaming=True); fid = sent.video.file_id
        if ck: bot._global_file_ids[ck] = fid; bot.save()
        if record: record.telegram_file_id = fid; bot.save()
        await s.delete()
    except Exception as e: await s.edit_text("❌ Failed.")

async def _username(bot):
    if not bot._bot_username and bot._bot: me = await bot._bot.get_me(); bot._bot_username = me.username
    return bot._bot_username or "botname"