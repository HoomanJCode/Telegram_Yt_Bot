# app/handlers/formats.py
"""Format choice and delivery handlers"""
from pathlib import Path
from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ParseMode, ChatType
from app.handlers.navigation import nav_push, NAV_FORMAT
from app.utils import esc, find_existing, get_default_delivery, _path_on_disk, prune_missing


# Friendly labels used by `_more_format_buttons` for the per-media-type
# "Also get:" rows in show_delivery. Keeping them here so the button
# copy and any future deep-linking to /help text share a single source.
_MORE_FORMAT_LABELS = {
    'video': '🎬 Video',
    'audio': '🎵 Audio',
    'thumb': '🖼️ Thumbnail',
}


def _more_format_buttons(idx, current_media_type):
    """Build the 'Also get' rows for the OTHER media types in a delivery kb.

    Telegram callback_data is capped at 64 bytes; the values we emit
    ('morefmt_{mt}_{idx}') are well under that cap since mt ∈ {video,
    audio, thumb} (≤5 chars) and idx is a small int even after months of
    user history. The cap is exercised explicitly in the test suite so a
    future rename of the prefix doesn't accidentally push past 64 bytes
    on longer variant names ('foobar_v', etc.).

    Returns an empty list if there's nothing to advertise (independent
    of the call site's media_type — for now every media_type has 2
    siblings, but if a single-variant media type is introduced later
    the call site stays correct).
    """
    siblings = [
        ('video', '🎬 Video'),
        ('audio', '🎵 Audio'),
        ('thumb', '🖼️ Thumbnail'),
    ]
    rows = []
    other = [(mt, label) for mt, label in siblings if mt != current_media_type]
    # One row, two buttons side-by-side, so the delivery kb stays within
    # Telegram's recommended visible-rows-on-a-3.5"-phone limit (delivery
    # kb already has 3 rows, +1 row is fine).
    if other:
        rows.append([
            InlineKeyboardButton(
                f"➕ {label}",
                callback_data=f'morefmt_{mt}_{idx}',
            )
            for mt, label in other
        ])
    return rows

def _VIDEO_VARIANT_BUTTONS():
    # Each entry maps a format-choice callback_data to the file
    # extension(s) that mark that variant "already downloaded". Central
    # here so format_choice_kb and choose_format agree on the
    # extension list without a parallel hard-coded list (the prior
    # architecture drifted once and would have drifted again).
    #   * 'fmt_video_mkv' → 'auto' container picker → `merge_output_format`
    #     is NOT set, so yt-dlp picks the natural container. The
    #     post-run ffmpeg MKV mux in downloader._embed_subs_to_mkv
    #     rewraps to .mkv when sub mode='embed', which is the
    #     default. We accept .mkv as the canonical match.
    #   * 'fmt_video_mp4' → 'mp4' container picker → `merge_output_format=mp4`
    #     forces .mp4 as the on-disk extension, so the cache check is
    #     exact (no need for variant fallback).
    return {
        'fmt_video_mkv': ('.mkv',),
        'fmt_video_mp4': ('.mp4',),
    }


def _video_variant_extensions(fmt):
    """Extension tuple for a format-choice callback, or None for non-video."""
    return _VIDEO_VARIANT_BUTTONS().get(fmt)


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
    # MP4 vs the natural container (MKV). 2026-06-21 update: each button
    # now independently shows a ✅ 'Downloaded' marker when its OWN
    # container variant is cached. Iterate _VIDEO_VARIANT_BUTTONS() rather
    # than hardcoding the (button -> extension) mapping so the keyboard
    # marker and the choose_format dedup branch share ONE source of
    # truth. A future maintainer who edits the mapping cannot silently
    # drift one side. The cross-variant intent is preserved -- having an
    # MKV does NOT mark the MP4 button as cached because the user might
    # want a fresh MP4 render (e.g. to share with iOS / older Android
    # friends). Trade-off reminders in the labels make the format ↔
    # subtitle relationship explicit (MP4 lacks soft-sub embed).
    video_buttons = {
        'fmt_video_mkv': ("🎬 Video (MKV) — best quality + auto-subs",
                          "✅ 🎬 Video (MKV) - Downloaded"),
        'fmt_video_mp4': ("🎬 Video (MP4) — universal compat, subs separate",
                          "✅ 🎬 Video (MP4) - Downloaded"),
    }
    for fmt, (plain, downloaded) in video_buttons.items():
        # _video_variant_extensions(...) returns the per-callback
        # extension tuple — sentinel None would mean a non-video button
        # snuck into this loop, which we guard against with `bool(exts)`
        # so a future maintainer adding a non-video variant here would
        # fail to match (positive assertion in the test suite) rather
        # than silently fall back to plain (false negative on ✅).
        exts = _video_variant_extensions(fmt)
        cached = bool(exts) and find_existing(
            bot, uid, video_id, 'video', extensions=frozenset(exts))
        kb.append([InlineKeyboardButton(
            downloaded if cached else plain, callback_data=fmt)])
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
    # Audio / thumb have only one variant, so a re-click dedupes cleanly.
    # Video dedup is container-aware (2026-06-21): the user's MKV button
    # only short-circuits when a CACHED .mkv is present, the MP4 button
    # only when a CACHED .mp4 is present. The cross-variant intent is
    # preserved -- having an MKV does NOT mark MP4 as cached because
    # the user might want a fresh MP4 render to share with iOS / older
    # Android friends. Mirrors the per-button ✅ "Downloaded" markers
    # in format_choice_kb above.
    if mt == 'audio':
        existing = find_existing(bot, uid, video_id, 'audio')
        if existing:
            await q.answer("Already downloaded!")
            await show_delivery(bot, q.message, existing, bot.videos[uid].index(existing))
            return
    elif mt == 'thumb':
        existing = find_existing(bot, uid, video_id, 'thumb')
        if existing:
            await q.answer("Already downloaded!")
            await show_delivery(bot, q.message, existing, bot.videos[uid].index(existing))
            return
    elif mt == 'video':
        exts = _video_variant_extensions(fmt)
        if exts:
            existing = find_existing(bot, uid, video_id, 'video',
                                     extensions=frozenset(exts))
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
                            caption=f"📝 Subtitle: {Path(sub).name}", reply_to_message_id=msg.message_id,
                        )
                    else:
                        from urllib.parse import quote
                        await msg.reply_text(
                            f"📝 Subtitle too large for Telegram ({size_kb/1024:.1f}MB)\n"
                            f"📥 `{bot.base_url}/{quote(Path(sub).name)}`",
                            parse_mode=ParseMode.MARKDOWN,
                            reply_to_message_id=msg.message_id,
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
    if msg.chat.type in (ChatType.GROUP, ChatType.SUPERGROUP):
        kb = _group_delivery_kb(bot, msg.chat.id)
        await msg.reply_text(
            f"{emoji} *{esc(record.title[:200])}*\n📦 {mb:.2f} MB | {record.media_type}\n🕒 {record.download_time}{sub_hint}\n\nChoose delivery:",
            parse_mode=ParseMode.MARKDOWN, reply_markup=kb, reply_to_message_id=msg.message_id)
        return
    # Private-chat path: build the standard delivery kb then append the
    # "Also get" rows so the user can grab the OTHER formats (audio /
    # thumb for a video record, video + thumb for an audio record,
    # video + audio for a thumb record) without re-pasting the URL.
    kb = delivery_kb(bot, msg.chat.id, idx)
    more_rows = _more_format_buttons(idx, record.media_type)
    if more_rows:
        # InlineKeyboardMarkup.inline_keyboard is a list of rows; we
        # extend rather than rebuilding so the delivery_kb() layout
        # stays the source of truth for the first 3 rows.
        kb.inline_keyboard.extend(more_rows)
    sub_hint = ''
    if pending_subs:
        sub_hint = f"\n📝 {len(pending_subs)} subtitle file(s) attached"
    await msg.reply_text(
        f"{emoji} *{esc(record.title[:200])}*\n📦 {mb:.2f} MB | {record.media_type}\n🕒 {record.download_time}{sub_hint}\n\nChoose delivery:",
        parse_mode=ParseMode.MARKDOWN, reply_markup=kb, reply_to_message_id=msg.message_id)

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
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("📥 Download", url=url)], [InlineKeyboardButton("🔙 Menu", callback_data='b')]]),
        reply_to_message_id=msg.message_id)

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
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("📥 Download", url=url)], [InlineKeyboardButton("🔙 Menu", callback_data='b')]]),
        reply_to_message_id=q.message.message_id)
    await q.message.delete()

async def back_to_formats(bot, u, c):
    q = u.callback_query; await q.answer(); uid = u.effective_user.id; data = q.data
    record = bot.videos[uid][0] if 'new' in data else bot.videos.get(uid, [None])[int(data.split('_')[1])]
    if not record: return
    bot._pending_urls[uid] = (record.url, record.video_id, record.title)
    from app.handlers.navigation import show_format_choice
    await show_format_choice(bot, uid, record.url, record.video_id, q.message)
    await q.message.delete()


async def also_get_other_format(bot, u, c):
    """Handler for the 'Also get <other format>' row the delivery kb shows.

    Invoked via `morefmt_<mt>_<idx>` callback_data. The user tapped this
    after a record at `idx` was just delivered, requesting that the bot
    kick off a SECOND download of the SAME video URL but in a different
    media_type. We look up the source record by index (NOT find_existing
    on video_id — the user might have multiple variants of the same video
    and chose THIS record specifically), promote its URL/ID back into
    `bot._pending_urls` so any downstream "fetch_info" path can re-use it,
    and run a fresh download_task with container_override=None so
    downloader's user-setting cascade (video_container, subtitle_mode)
    applies once more exactly like an unrelated next-click would.

    Dedup mirrors `choose_format`: if the OTHER format already exists as
    a record (e.g. user clicked Video, then "Also get audio", but they
    had previously downloaded the same video as audio), we don't
    re-download — show the existing record's delivery screen instead.
    """
    q = u.callback_query; await q.answer(); uid = u.effective_user.id
    try:
        _, mt, idx_str = q.data.split('_', 2)
        idx = int(idx_str)
    except ValueError:
        return
    if mt not in ('video', 'audio', 'thumb'):
        return
    # Eagerly prune so a concurrent successful download (which can shift
    # the 0-indexed bot.videos[uid] list between when show_delivery rendered
    # the morefmt_ callback and when the user tapped it) can't make us
    # point at a different record than the one the delivery screen was
    # actually showing. Mirrors the same defensive call in `_select`.
    prune_missing(bot, uid)
    videos = bot.videos.get(uid, [])
    if not videos or not (0 <= idx < len(videos)):
        # Record gone (pruned, removed, etc.) — let the user pick again.
        await q.message.reply_text(
            "⚠️ That entry is no longer available. Try again from /recent.",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton('📹 Recent', callback_data='r'), InlineKeyboardButton('🔙 Menu', callback_data='b')]]),
            reply_to_message_id=q.message.message_id)
        return
    record = videos[idx]
    url = record.url
    video_id = record.video_id
    # Dedup mirrors choose_format for audio + thumb: a second copy of
    # the same media_type is genuinely redundant work the user didn't
    # intend, so we surface the existing record's delivery screen
    # rather than re-downloading.
    #
    # Video dedup is intentionally NOT container-aware here (documented
    # asymmetry with choose_format): this branch is reached via
    # morefmt_video with container_override=None, which defers to the
    # user's stored video_container preference. We can't pick a single
    # extension to dedup against -- .mkv might collide with the user's
    # stored 'mp4' preference and false-positive into an "already
    # downloaded" toast the user did not ask for. The 2026-06-21 fix
    # addresses the equivalent MKV-button-re-click case in
    # choose_format where the user explicitly picks the container from
    # the keyboard.
    if mt in ('audio', 'thumb'):
        existing = find_existing(bot, uid, video_id, mt)
        if existing:
            await q.answer("Already downloaded!")
            await show_delivery(bot, q.message, existing, bot.videos[uid].index(existing))
            return
    # Re-prime _pending_urls so a future "back to formats" navigation
    # lands on this video's format picker rather than the user's last
    # unrelated URL.
    bot._pending_urls[uid] = (url, video_id, record.title)
    from app.handlers.messages import download_task
    async with bot._download_semaphore:
        # container_override=None defers to the user's stored video_container
        # (or 'auto' for audio/thumb paths which ignore container). This
        # mirrors the auto_format='video' path so the per-user quality
        # cascade is honored consistently.
        await download_task(bot, uid, url, q.message, mt, container_override=None)