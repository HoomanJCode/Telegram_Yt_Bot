# app/handlers/formats.py
"""Format choice and delivery handlers.

Per-message state (2026-07-15 stale-button fix): every inline-keyboard
lookup on a delivery screen resolves through
`bot._delivery_screen[(chat_id, message_id)]`, NOT through
`bot.videos[uid][idx]`. The (chat_id, message_id) of the Telegram
message the kb is attached to is self-identifying, so a parallel
download that inserts a record at idx=0 cannot rewire an
untouched-but-stale keyboard's <callback> to the wrong record.
`_pending_urls` is keyed the same way for the format-picker read in
`choose_format` (writer: navigation.show_format_choice).

Index-free callback_data schema (all <64 bytes, well within
Telegram's cap):
  * delivery kb:        'tg_send' | 'lk_send' | 'backfmt'
  * also-get rows:      'morefmt_<mt>' (mt in video|audio|thumb)

show_delivery() drops the legacy `idx` parameter. After it renders
the kb-bearing reply it binds `_delivery_screen[(reply_msg.chat.id,
reply_msg.message_id)] = record` so the cb handler's
`_resolve_delivery_record(bot, c)` lookup is unambiguous.
"""
from pathlib import Path
from urllib.parse import quote
from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ChatType, ParseMode
from app.handlers.navigation import nav_push, NAV_RECENT
from app.utils import esc, find_existing, get_default_delivery, _path_on_disk


# ============================================================================
# Per-message state helpers (sync; called from cb handlers before any await)
# ============================================================================

def _delivery_screen_put(bot, chat_id, message_id, record):
    """Bind a delivery-screen message to its record.

    Bounded by an OrderedDict LRU cap so abandoned delivery messages
    (the user starts a new download without ever clicking the
    buttons on an old one) eventually evict without leaking memory.
    O(1) per call: `move_to_end` keeps the just-touched entry at
    the front, and `popitem(last=False)` evicts the least-recent
    entry when we exceed `bot._ephemeral_max` (1024).

    Sync (no `await` inside) so callers can issue it RIGHT AFTER
    `await msg.reply_text(...)` returns the kb-bearing Message;
    no other coroutine can interleave because asyncio is
    single-thread.
    """
    key = (chat_id, message_id)
    bot._delivery_screen[key] = record
    bot._delivery_screen.move_to_end(key)
    while len(bot._delivery_screen) > bot._ephemeral_max:
        bot._delivery_screen.popitem(last=False)


def _resolve_delivery_record(bot, c):
    """Look up the VideoRecord associated with the cb's message.

    Pulls from `bot._delivery_screen[(c.message.chat.id,
    c.message.message_id)]`. Performs LRU `move_to_end` on hit so
    recently-clicked deliveries stay in memory longer than
    forgotten ones. Returns None when the entry is missing OR when
    the underlying file has been pruned (operator cleared the
    downloads/ dir, retention sweep, server migration) so the
    caller can show "no longer available" instead of trying to
    deliver a 404-style exception to the user.

    Sync because OrderedDict ops and Path.stat are sync in this
    codebase; the only async work downstream is the Telegram API
    call which can be triggered without an intermediate await.
    """
    key = (c.message.chat.id, c.message.message_id)
    rec = bot._delivery_screen.get(key)
    if rec is None:
        return None
    bot._delivery_screen.move_to_end(key)
    if not _path_on_disk(rec.file_path):
        # Evict the dead entry so subsequent clicks on the same
        # stale kb don't re-run the stat. The LRU discipline would
        # eventually evict anyway, but a single dead record from a
        # user-deleted download can otherwise hold a slot for the
        # entire 1024-entry cap window.
        bot._delivery_screen.pop(key, None)
        return None
    return rec


async def _unavailable_message(bot, q):
    """Friendly recovery prompt when the kb's record is gone.

    NOTE: caller MUST already have called `await q.answer()` before
    calling this helper. Every current caller does (send_telegram,
    send_link, back_to_formats, also_get_other_format). We do NOT
    call q.answer() here because q.answer() with no alert is a
    no-op when called twice, but q.answer(text) with an alert
    would overwrite the caller's answer text.
    """
    await q.message.reply_text(
        "\u26a0\ufe0f That entry is no longer available. Try /recent for the current list.",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton('\U0001F4F9 Recent', callback_data='r'),
             InlineKeyboardButton('\U0001F519 Menu', callback_data='b')],
        ]),
        reply_to_message_id=q.message.message_id,
    )


def _more_format_buttons(current_media_type):
    """Build the 'Also get' rows for the OTHER media types in a delivery kb.

    No idx payload: the source record is resolved on click via
    `_resolve_delivery_record(bot, c)` keyed by the kb's own
    (chat_id, message_id). The new callback_data is
    `morefmt_<mt>` (mt in video|audio|thumb), well under Telegram's
    64-byte cap.

    Returns an empty list if there's nothing to advertise (independent
    of the call site's media_type — for now every media_type has 2
    siblings, but if a single-variant media type is introduced later
    the call site stays correct).
    """
    siblings = [
        ('video', '🎬 Video'),
        ('audio', '🎵 Audio'),
        ('thumb', '🖼️ Thumbnails'),
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
                callback_data=f'morefmt_{mt}',
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
    """Format-picker callback dispatcher.

    Reads `_pending_urls` by (chat_id, message_id) of the kb's
    message -- NOT by uid. After the 2026-07-15 fix each format
    picker is self-identifying and parallel flows cannot collide.
    """
    q = u.callback_query; await q.answer(); fmt = q.data
    uid = u.effective_user.id
    # Per-message key (the only bot attr the handler reads before
    # reaching the dedup branch). Keyed by the *kb's* chat + message
    # id, NOT the user's uid -- so a parallel new download for the
    # SAME user that has a brand-new picker does NOT clobber this
    # picker.
    key = (q.message.chat.id, q.message.message_id)
    if key not in bot._pending_urls: return
    url, video_id, _ = bot._pending_urls[key]
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
            await show_delivery(bot, q.message, existing)
            return
    elif mt == 'thumb':
        existing = find_existing(bot, uid, video_id, 'thumb')
        if existing:
            await q.answer("Already downloaded!")
            await show_delivery(bot, q.message, existing)
            return
    elif mt == 'video':
        exts = _video_variant_extensions(fmt)
        if exts:
            existing = find_existing(bot, uid, video_id, 'video',
                                     extensions=frozenset(exts))
            if existing:
                await q.answer("Already downloaded!")
                await show_delivery(bot, q.message, existing)
                return
    from app.handlers.messages import download_task
    async with bot._download_semaphore:
        await download_task(bot, uid, url, q.message, mt,
                            container_override=container_override)

async def show_delivery(bot, msg, record):
    """Render the 'Choose delivery' kb on a fresh reply to `msg`.

    Binds `_delivery_screen[(reply_msg.chat.id, reply_msg.message_id)]`
    to `record` AFTER the reply (so the message_id is known) so
    subsequent tg_send / lk_send / backfmt / morefmt_* callbacks
    can resolve via `_resolve_delivery_record(bot, c)` without
    indexing into bot.videos[uid] (which would shift on a parallel
    download).
    """
    uid = msg.chat.id  # private chat uid == chat_id; group
                       # delivery_screen key is the group chat_id.

    # Send any pending subtitle files first (separate-mode delivery).
    # Note (2026-07-15 fix): the legacy `also_get_other_format` used to
    # call `prune_missing(bot, uid)` here -- to drop files-deleted-
    # between-render-and-click. Per-message keying makes that
    # unnecessary for CB ROUTING (the kb's `message_id` doesn't
    # shift, so `_resolve_delivery_record` always finds the right
    # record). The on-disk-existence guard inside
    # `_resolve_delivery_record` is the new safety net for the
    # "file deleted after render" case: it pops the dead entry
    # and returns None, which the handler surfaces as the
    # `_unavailable_message` recovery prompt.
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
                        await msg.reply_text(
                            f"📝 Subtitle too large for Telegram ({size_kb/1024:.1f}MB)\n"
                            f"📥 `{bot.base_url}/{quote(Path(sub).name)}`",
                            parse_mode=ParseMode.MARKDOWN,
                            reply_to_message_id=msg.message_id,
                        )
        except Exception:
            pass

    # If user has a default set, skip the keyboard and deliver directly
    default = get_default_delivery(bot, uid)
    if default == 'telegram':
        await send_telegram_direct(bot, msg, record)
        return
    elif default == 'link':
        await send_link_direct(bot, msg, record)
        return

    # Default: ask user
    emoji = {'video': '🎬', 'audio': '🎵', 'thumb': '🖼️'}.get(record.media_type, '📹')
    mb = record.file_size / 1024 / 1024
    # NAV_RECENT (not NAV_FORMAT): the `b` button on the delivery kb
    # goes to /recent where this very record is in the list. The
    # format-picker re-entry path uses `backfmt` (see back_to_formats
    # below), not `b`.
    nav_push(bot, msg.chat.id, msg.message_id, NAV_RECENT)
    sub_hint = ''
    if pending_subs:
        sub_hint = f"\n📝 {len(pending_subs)} subtitle file(s) attached"
    if msg.chat.type in (ChatType.GROUP, ChatType.SUPERGROUP):
        from app.handlers.messages import _group_delivery_kb
        delivery_msg = await msg.reply_text(
            f"{emoji} *{esc(record.title[:200])}*\n📦 {mb:.2f} MB | {record.media_type}\n🕒 {record.download_time}{sub_hint}\n\nChoose delivery:",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=_group_delivery_kb(bot, uid),
            reply_to_message_id=msg.message_id)
        # Per-message bind: this delivery message becomes
        # self-identifying for the user's next click.
        _delivery_screen_put(bot, delivery_msg.chat.id,
                             delivery_msg.message_id, record)
        return
    # Private-chat path: build the standard delivery kb then append the
    # "Also get" rows so the user can grab the OTHER formats (audio /
    # thumb for a video record, video + thumb for an audio record,
    # video + audio for a thumb record) without re-pasting the URL.
    kb = delivery_kb(bot)
    more_rows = _more_format_buttons(record.media_type)
    if more_rows:
        # InlineKeyboardMarkup.inline_keyboard is a list of rows; we
        # extend rather than rebuilding so the delivery_kb() layout
        # stays the source of truth for the first 3 rows.
        kb.inline_keyboard.extend(more_rows)
    delivery_msg = await msg.reply_text(
        f"{emoji} *{esc(record.title[:200])}*\n📦 {mb:.2f} MB | {record.media_type}\n🕒 {record.download_time}{sub_hint}\n\nChoose delivery:",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=kb,
        reply_to_message_id=msg.message_id)
    # Per-message bind: bind the (chat, message_id) of the
    # kb-bearing REPLY (not the user's original URL message) so
    # _resolve_delivery_record finds the record on subsequent
    # tg_send / lk_send / backfmt / morefmt_* clicks.
    _delivery_screen_put(bot, delivery_msg.chat.id,
                         delivery_msg.message_id, record)


def delivery_kb(bot):
    """Build the private-chat delivery kb (no idx; the kb's message
    is self-identifying via _delivery_screen).

    Three buttons. All callbacks are short literal strings, well
    under Telegram's 64-byte cap.
    """
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📤 Send via Telegram", callback_data='tg_send')],
        [InlineKeyboardButton("📋 Get Download Link", callback_data='lk_send')],
        [InlineKeyboardButton("🔙 Back to formats", callback_data='backfmt')],
    ])

async def send_telegram_direct(bot, msg, record):
    """Send via Telegram without asking user"""
    from app.handlers.tokens import send_file
    await send_file(bot, msg, record)

async def send_link_direct(bot, msg, record):
    """Send download link without asking user"""
    if not Path(record.file_path).exists(): return
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

# ============================================================================
# Delivery-cb handlers: all resolve via _resolve_delivery_record
# ============================================================================

async def send_telegram(bot, u, c):
    """Handle `tg_send` cb. Resolves the record via
    _delivery_screen, never via bot.videos[uid][idx]."""
    q = u.callback_query; await q.answer()
    rec = _resolve_delivery_record(bot, c)
    if rec is None:
        await _unavailable_message(bot, q)
        return
    from app.handlers.tokens import send_file
    await send_file(bot, q.message, rec)
    await q.message.delete()

async def send_link(bot, u, c):
    """Handle `lk_send` cb. Resolves the record via _delivery_screen."""
    q = u.callback_query; await q.answer()
    rec = _resolve_delivery_record(bot, c)
    if rec is None:
        await _unavailable_message(bot, q)
        return
    if not Path(rec.file_path).exists():
        await _unavailable_message(bot, q)
        return
    url = f"{bot.base_url}/{quote(Path(rec.file_path).name)}"
    mb = Path(rec.file_path).stat().st_size / 1024 / 1024
    await q.message.reply_text(
        f"🎬 *{esc(rec.title[:200])}*\n\n"
        f"📦 {mb:.2f} MB\n"
        f"📥 {url}\n\n"
        f"⚠️ File will be deleted after {bot.config.STORAGE_DAYS} days.",
        parse_mode=ParseMode.MARKDOWN,
        disable_web_page_preview=False,
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("📥 Download", url=url)], [InlineKeyboardButton("🔙 Menu", callback_data='b')]]),
        reply_to_message_id=q.message.message_id)
    await q.message.delete()

async def back_to_formats(bot, u, c):
    """Handle `backfmt` cb. Re-renders the format picker for the
    same video. `_pending_urls` is rewritten by show_format_choice
    at the new picker message_id (NOT re-written here -- doing so
    would race against show_format_choice's own write)."""
    q = u.callback_query; await q.answer()
    rec = _resolve_delivery_record(bot, c)
    if rec is None:
        await _unavailable_message(bot, q)
        return
    if not rec.url or not rec.video_id:
        return
    from app.handlers.navigation import show_format_choice
    # show_format_choice writes _pending_urls keyed by the new
    # picker message_id. The deleted delivery message's
    # _delivery_screen entry is allowed to evict naturally (LRU).
    await show_format_choice(bot, u.effective_user.id, rec.url,
                             rec.video_id, q.message)
    await q.message.delete()


async def also_get_other_format(bot, u, c):
    """Handle `morefmt_<mt>` cb. The source record is resolved via
    _resolve_delivery_record (no idx in cb_data); the user just
    asked the bot to download the SAME video in a different
    media_type.

    Dedup mirrors choose_format: if the OTHER format already
    exists, surface the existing record's delivery screen rather
    than re-downloading.
    """
    q = u.callback_query; await q.answer()
    try:
        _, mt = q.data.split('_', 1)
    except ValueError:
        return
    if mt not in ('video', 'audio', 'thumb'):
        return
    rec = _resolve_delivery_record(bot, c)
    if rec is None:
        await _unavailable_message(bot, q)
        return
    if not rec.url or not rec.video_id:
        # Mirror back_to_formats' defensive guard: a source record
        # missing url/video_id can't be re-downloaded; surface the
        # standard "unavailable" prompt instead of crashing inside
        # download_task with a bad URL.
        await _unavailable_message(bot, q)
        return
    uid = u.effective_user.id
    # Dedup: don't re-download a media_type we already have for
    # this video_id. Video dedup is intentionally NOT
    # container-aware here -- the call sets container_override=None
    # which defers to the user's stored video_container; we
    # can't pick a single extension to dedup against. The
    # audio+thumb branches group on a single (video_id, mt) so
    # they're exact.
    if mt in ('audio', 'thumb'):
        existing = find_existing(bot, uid, rec.video_id, mt)
        if existing:
            await q.answer("Already downloaded!")
            await show_delivery(bot, q.message, existing)
            return
    from app.handlers.messages import download_task
    async with bot._download_semaphore:
        # container_override=None defers to the user's stored
        # video_container (or 'auto' for audio/thumb which
        # ignore container). Mirrors the auto_format='video'
        # path so the per-user quality cascade is honored
        # consistently.
        await download_task(bot, uid, rec.url, q.message, mt,
                            container_override=None)