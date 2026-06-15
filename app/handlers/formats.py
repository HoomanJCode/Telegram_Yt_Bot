"""Format choice and delivery handlers"""
from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ParseMode
from app.handlers.navigation import nav_push, NAV_FORMAT
from app.utils import esc

def format_choice_kb(bot, uid, video_id):
    existing = {v.media_type for v in bot.videos.get(uid, []) if v.video_id == video_id and Path(v.file_path).exists()}
    from pathlib import Path
    kb = []
    v_label = "🎬 Video (MP4)"
    if 'video' in existing: v_label = "✅ 🎬 Video - Downloaded"
    kb.append([InlineKeyboardButton(v_label, callback_data='fmt_video')])
    a_label = f"🎵 Audio ({'MP3' if bot.has_ffmpeg else 'M4A'})"
    if 'audio' in existing: a_label = "✅ 🎵 Audio - Downloaded"
    kb.append([InlineKeyboardButton(a_label, callback_data='fmt_audio')])
    t_label = "🖼️ Thumbnails"
    if 'thumb' in existing: t_label = "✅ 🖼️ Thumbnails - Downloaded"
    kb.append([InlineKeyboardButton(t_label, callback_data='fmt_thumb')])
    kb.append([InlineKeyboardButton("🔙 Back", callback_data='b')])
    return InlineKeyboardMarkup(kb)

def delivery_kb(bot, uid, idx=None):
    idx_str = str(idx) if idx is not None else 'new'
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📤 Send via Telegram", callback_data=f'tg_{idx_str}')],
        [InlineKeyboardButton("📋 Get Download Link", callback_data=f'lk_{idx_str}')],
        [InlineKeyboardButton("🔙 Back to formats", callback_data=f'backfmt_{idx_str}')],
    ])

async def choose_format(bot, u, c):
    q = u.callback_query; await q.answer(); uid, fmt = u.effective_user.id, q.data
    if uid not in bot._pending_urls: return
    url, video_id, _ = bot._pending_urls[uid]
    from app.utils import find_existing
    from app.handlers.messages import download_task
    import asyncio
    for mt, fk in [('video', 'fmt_video'), ('audio', 'fmt_audio'), ('thumb', 'fmt_thumb')]:
        if fmt == fk:
            existing = find_existing(bot, uid, video_id, mt)
            if existing: await q.answer("Already downloaded!"); await show_delivery(bot, q.message, existing, bot.videos[uid].index(existing)); return
            async with bot._download_semaphore: await download_task(bot, uid, url, q.message, mt)

async def show_delivery(bot, msg, record, idx):
    emoji = {'video': '🎬', 'audio': '🎵', 'thumb': '🖼️'}.get(record.media_type, '📹')
    mb = record.file_size / 1024 / 1024
    nav_push(bot, msg.chat.id, NAV_FORMAT, (record.url, record.video_id))
    is_group = msg.chat.type in (ChatType.GROUP, ChatType.SUPERGROUP)
    from telegram.constants import ChatType
    from app.handlers.messages import _group_delivery_kb
    kb = _group_delivery_kb(bot, msg.chat.id) if is_group else delivery_kb(bot, msg.chat.id, idx)
    await msg.reply_text(f"{emoji} *{esc(record.title[:200])}*\n📦 {mb:.2f} MB | {record.media_type}\n🕒 {record.download_time}\n\nChoose delivery:", parse_mode=ParseMode.MARKDOWN, reply_markup=kb)

async def send_telegram(bot, u, c):
    q = u.callback_query; await q.answer(); uid = u.effective_user.id; data = q.data
    record = bot.videos[uid][0] if 'new' in data else bot.videos.get(uid, [None])[int(data.split('_')[1])]
    if not record: return
    from app.handlers.tokens import send_file
    await send_file(bot, q.message, record); await q.message.delete()

async def send_link(bot, u, c):
    q = u.callback_query; await q.answer(); uid = u.effective_user.id; data = q.data
    record = bot.videos[uid][0] if 'new' in data else bot.videos.get(uid, [None])[int(data.split('_')[1])]
    if not record or not Path(record.file_path).exists(): return
    from urllib.parse import quote; from pathlib import Path
    url = f"{bot.base_url}/{quote(Path(record.file_path).name)}"
    await q.message.reply_text(f"✅ *{esc(record.title[:200])}*\n\n📥 `{url}`\n\n⚠️ {bot.config.STORAGE_DAYS}d retention.", parse_mode=ParseMode.MARKDOWN, reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("📥 Download", url=url)], [InlineKeyboardButton("🔙 Menu", callback_data='b')]]))
    await q.message.delete()

async def back_to_formats(bot, u, c):
    q = u.callback_query; await q.answer(); uid = u.effective_user.id; data = q.data
    record = bot.videos[uid][0] if 'new' in data else bot.videos.get(uid, [None])[int(data.split('_')[1])]
    if not record: return
    bot._pending_urls[uid] = (record.url, record.video_id, record.title)
    from app.handlers.navigation import show_format_choice
    await show_format_choice(bot, uid, record.url, record.video_id, q.message); await q.message.delete()