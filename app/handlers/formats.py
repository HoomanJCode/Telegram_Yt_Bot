# app/handlers/formats.py
"""Format choice and delivery handlers"""
from pathlib import Path
from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ParseMode, ChatType
from app.handlers.navigation import nav_push, NAV_FORMAT
from app.utils import esc, find_existing, get_default_delivery, _path_on_disk

def format_choice_kb(bot, uid, video_id):
    # Existing-detection uses the soft-fail-safe _path_on_disk so a transient
    # filesystem blip on the previously-downloaded file doesn't trigger a
    # confusing re-download prompt for the user.
    existing_media_types = {
        v.media_type for v in bot.videos.get(uid, [])
        if v.video_id == video_id and _path_on_disk(v.file_path)
    }
    kb = []
    # The video row is split into two buttons so the user explicitly picks
    # MP4 vs the natural container (MKV). Both buttons always do a fresh
    # download with the chosen container — there is no ✅ "Already
    # Downloaded" marker on the video variants because doing so would
    # falsely imply that clicking the OTHER variant was a no-op. Users
    # can have multiple variants of the same video in their history.
    # Trade-off reminders in the labels make the format ↔ subtitle
    # relationship explicit (MP4 lacks soft-sub embed).
    kb.append([InlineKeyboardButton(
        "🎬 Video (MKV) — best quality + auto-subs", callback_data='fmt_video_mkv')])
    kb.append([InlineKeyboardButton(
        "🎬 Video (MP4) — universal compat, subs separate", callback_data='fmt_video_mp4')])
    a_label = f"🎵 Audio ({'MP3' if bot.has_ffmpeg else 'M4A'})"
    if 'audio' in existing_media_types:
        a_label = "✅ 🎵 Audio - Downloaded"
    kb.append([InlineKeyboardButton(a_label, callback_data='fmt_audio')])
    t_label = "🖼️ Thumbnails"
    if 'thumb' in existing_media_types:
        t_label = "✅ 🖼️ Thumbnails - Downloaded"
    kb.append([InlineKeyboardButton(t_label, callback_data='fmt_thumb')])
    kb.append([InlineKeyboardButton("🔙 Back", callback_data='b')])
    return InlineKeyboardMarkup(kb)

async def choose_format(bot, u, c):
    q = u.callback_query; await q.answer(); uid, fmt = u.effective_user.id, q.data
    if uid not in bot._pending_urls: return
    url, video_id, _ = bot._pending_urls[uid]
    # Map (callback_data) -> (media_type, container_override). The container
    # override lets the format-picker buttons explicitly request a specific
    # variant per-click, regardless of the user's stored video_container
    # default (which only kicks in for the auto_format path or when the user
    # clicks Video under the old single-button keyboard).
    mt_map = {
        'fmt_video_mkv': ('video', 'auto'),
        'fmt_video_mp4': ('video', 'mp4'),
        'fmt_audio':     ('audio', 'auto'),
        'fmt_thumb':     ('thumb', 'auto'),
    }
    if fmt not in mt_map:
        return
    mt, container_override = mt_map[fmt]
    # Audio and thumb have only one variant in their picker, so dedup by
    # find_existing is still meaningful: a second click is genuinely
    # redundant work the user didn't intend. Video skips dedup because
    # the two variants (MKV / MP4) are independent download artifacts
    # even for the same video_id.
    if mt in ('audio', 'thumb'):
        existing = find_existing(bot, uid, video_id, mt)
        if existing:
            await q.answer("Already downloaded!")
            await show_delivery(bot, q.message, existing, bot.videos[uid].index(existing))
            return
    from app.handlers.messages import download_task
    async with bot._download_semaphore:
        await download_task(bot, uid, url, q.message, mt,
                            container_override=container_override)

async def show_delivery(bot, msg, record, idx):
    uid = msg.chat.id
    default = get_default_delivery(bot, uid)

    # Send any pending subtitle files first (separate-mode delivery)
    pending_subs = getattr(record, '_pending_subs', None)
    if pending_subs:
        try:
            for sub in pending_subs:
                if Path(sub).exists():
                    size_kb = Path(sub).stat().st_size / 1024
                    if size_kb < 50 * 1024:  # <50MB → send as Telegram doc
                        await msg.reply_document(
                            document=open(sub, 'rb'),
                            filename=Path(sub).name,
                            caption=f"📝 Subtitle: {Path(sub).name}",
                        )
                    else:
                        from urllib.parse import quote
                        await msg.reply_text(
                            f"📝 Subtitle too large for Telegram ({size_kb/1024:.1f}MB)\n"
                            f"📥 `{bot.base_url}/{quote(Path(sub).name)}`",
                            parse_mode=ParseMode.MARKDOWN,
                        )
        except Exception:
            pass

    # If user has a default set, skip the keyboard and deliver directly
    if default == 'telegram':
        await send_telegram_direct(bot, msg, record)
        return
    elif default == 'link':
        await send_link_direct(bot, msg, record)
        return

    # Default: ask user
    emoji = {'video': '🎬', 'audio': '🎵', 'thumb': '🖼️'}.get(record.media_type, '📹')
    mb = record.file_size / 1024 / 1024
    nav_push(bot, msg.chat.id, NAV_FORMAT, (record.url, record.video_id))
    from app.handlers.messages import _group_delivery_kb
    kb = _group_delivery_kb(bot, msg.chat.id) if msg.chat.type in (ChatType.GROUP, ChatType.SUPERGROUP) else delivery_kb(bot, msg.chat.id, idx)
    sub_hint = ''
    if pending_subs:
        sub_hint = f"\n📝 {len(pending_subs)} subtitle file(s) attached"
    await msg.reply_text(
        f"{emoji} *{esc(record.title[:200])}*\n📦 {mb:.2f} MB | {record.media_type}\n🕒 {record.download_time}{sub_hint}\n\nChoose delivery:",
        parse_mode=ParseMode.MARKDOWN, reply_markup=kb)

def delivery_kb(bot, uid, idx=None):
    idx_str = str(idx) if idx is not None else 'new'
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📤 Send via Telegram", callback_data=f'tg_{idx_str}')],
        [InlineKeyboardButton("📋 Get Download Link", callback_data=f'lk_{idx_str}')],
        [InlineKeyboardButton("🔙 Back to formats", callback_data=f'backfmt_{idx_str}')],
    ])

async def send_telegram_direct(bot, msg, record):
    """Send via Telegram without asking user"""
    from app.handlers.tokens import send_file
    await send_file(bot, msg, record)

async def send_link_direct(bot, msg, record):
    """Send download link without asking user"""
    if not Path(record.file_path).exists(): return
    from urllib.parse import quote
    url = f"{bot.base_url}/{quote(Path(record.file_path).name)}"
    mb = Path(record.file_path).stat().st_size / 1024 / 1024
    await msg.reply_text(
        f"🎬 *{esc(record.title[:200])}*\n\n"
        f"📦 {mb:.2f} MB\n"
        f"📥 {url}\n\n"
        f"⚠️ File will be deleted after {bot.config.STORAGE_DAYS} days.",
        parse_mode=ParseMode.MARKDOWN,
        disable_web_page_preview=False,
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("📥 Download", url=url)], [InlineKeyboardButton("🔙 Menu", callback_data='b')]]))

async def send_telegram(bot, u, c):
    q = u.callback_query; await q.answer(); uid = u.effective_user.id; data = q.data
    record = bot.videos[uid][0] if 'new' in data else bot.videos.get(uid, [None])[int(data.split('_')[1])]
    if not record: return
    from app.handlers.tokens import send_file
    await send_file(bot, q.message, record)
    await q.message.delete()

async def send_link(bot, u, c):
    q = u.callback_query; await q.answer(); uid = u.effective_user.id; data = q.data
    record = bot.videos[uid][0] if 'new' in data else bot.videos.get(uid, [None])[int(data.split('_')[1])]
    if not record or not Path(record.file_path).exists(): return
    from urllib.parse import quote
    url = f"{bot.base_url}/{quote(Path(record.file_path).name)}"
    mb = Path(record.file_path).stat().st_size / 1024 / 1024
    await q.message.reply_text(
        f"🎬 *{esc(record.title[:200])}*\n\n"
        f"📦 {mb:.2f} MB\n"
        f"📥 {url}\n\n"
        f"⚠️ File will be deleted after {bot.config.STORAGE_DAYS} days.",
        parse_mode=ParseMode.MARKDOWN,
        disable_web_page_preview=False,
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("📥 Download", url=url)], [InlineKeyboardButton("🔙 Menu", callback_data='b')]]))
    await q.message.delete()

async def back_to_formats(bot, u, c):
    q = u.callback_query; await q.answer(); uid = u.effective_user.id; data = q.data
    record = bot.videos[uid][0] if 'new' in data else bot.videos.get(uid, [None])[int(data.split('_')[1])]
    if not record: return
    bot._pending_urls[uid] = (record.url, record.video_id, record.title)
    from app.handlers.navigation import show_format_choice
    await show_format_choice(bot, uid, record.url, record.video_id, q.message)
    await q.message.delete()