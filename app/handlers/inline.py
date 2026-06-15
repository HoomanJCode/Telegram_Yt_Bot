"""Inline query handler"""
import secrets, time
from uuid import uuid4
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, InlineQueryResultArticle, InputTextMessageContent
from telegram import InlineQueryResultCachedVideo, InlineQueryResultCachedAudio, InlineQueryResultCachedPhoto
from app.utils import extract_url, extract_video_id, find_existing, ok
from app.handlers.messages import _ensure

async def inline_query(bot, u, c):
    try:
        query = u.inline_query.query.strip()
        if not query: return
        url = extract_url(query)
        if not url: return
        uid = u.effective_user.id
        if not ok(bot, uid): return
        if not await _ensure(bot, uid):
            await u.inline_query.answer([], switch_pm_text="Upload cookies first", switch_pm_parameter="cookies"); return

        video_id = extract_video_id(url)
        bot_username = await _get_username(bot)
        results = []

        for media_type, emoji, label in [
            ('video', '🎬', 'Video (MP4)'), ('audio', '🎵', f"Audio ({'MP3' if bot.has_ffmpeg else 'M4A'})"), ('thumb', '🖼️', 'Thumbnail')
        ]:
            cache_key = f"{video_id}:{media_type}"
            if cache_key in bot._global_file_ids:
                try:
                    fid = bot._global_file_ids[cache_key]
                    if media_type == 'video': results.append(InlineQueryResultCachedVideo(id=str(uuid4()), video_file_id=fid, title=f"Cached {label}", description="Instant"))
                    elif media_type == 'audio': results.append(InlineQueryResultCachedAudio(id=str(uuid4()), audio_file_id=fid, title=f"Cached {label}"))
                    else: results.append(InlineQueryResultCachedPhoto(id=str(uuid4()), photo_file_id=fid, title=f"Cached {label}"))
                    continue
                except: pass

            existing = find_existing(bot, uid, video_id, media_type)
            if existing and existing.telegram_file_id:
                try:
                    if media_type == 'video': results.append(InlineQueryResultCachedVideo(id=str(uuid4()), video_file_id=existing.telegram_file_id, title=existing.title, description=f"📦 {existing.file_size/1024/1024:.1f} MB"))
                    elif media_type == 'audio': results.append(InlineQueryResultCachedAudio(id=str(uuid4()), audio_file_id=existing.telegram_file_id, title=existing.title))
                    else: results.append(InlineQueryResultCachedPhoto(id=str(uuid4()), photo_file_id=existing.telegram_file_id, title=existing.title))
                    bot._global_file_ids[cache_key] = existing.telegram_file_id; bot.save(); continue
                except: pass

            token = secrets.token_hex(4)
            bot._tokens[token] = {'uid': uid, 'url': url, 'video_id': video_id, 'media_type': media_type, 'status': 'completed' if existing and Path(existing.file_path).exists() else 'pending', 'file_path': existing.file_path if existing and Path(existing.file_path).exists() else None, 'title': existing.title if existing else None, 'created_at': time.time()}

            if existing and Path(existing.file_path).exists():
                results.append(InlineQueryResultArticle(id=str(uuid4()), title=f"{emoji} {label} - Ready", description=f"Click: {existing.title[:50]}", input_message_content=InputTextMessageContent(f"{emoji} {existing.title}"), reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("📥 Get File", url=f"https://t.me/{bot_username}?start=dl_{token}")]])))
            else:
                results.append(InlineQueryResultArticle(id=str(uuid4()), title=f"{emoji} Download {label}", description="Click to start download & receive", input_message_content=InputTextMessageContent(f"⏳ Click to start downloading {label}."), reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("📥 Start Download", url=f"https://t.me/{bot_username}?start=dl_{token}")]])))

        await u.inline_query.answer(results, cache_time=0)
    except Exception as e: pass

async def _get_username(bot):
    if not bot._bot_username and bot._bot: me = await bot._bot.get_me(); bot._bot_username = me.username
    return bot._bot_username or "botname"