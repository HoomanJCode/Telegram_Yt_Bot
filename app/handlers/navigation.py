# app/handlers/navigation.py
"""Navigation stack, menus, back button"""
import asyncio
from pathlib import Path
from datetime import datetime
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, InputMediaPhoto
from telegram.constants import ParseMode
from telegram.error import BadRequest, TimedOut, NetworkError, RetryAfter
from app.utils import (
    esc, find_existing, prune_missing,
    VIDEO_QUALITY_OPTIONS, AUDIO_QUALITY_OPTIONS, SUBTITLE_MODE_OPTIONS,
    AUTO_FORMAT_OPTIONS, AUTO_FORMAT_LABELS, AUTO_FORMAT_SHORT,
    VIDEO_QUALITY_OPTIONS, VIDEO_QUALITY_LABELS, AUDIO_QUALITY_LABELS, SUBTITLE_MODE_LABELS,
    VIDEO_CONTAINER_OPTIONS, VIDEO_CONTAINER_LABELS, VIDEO_CONTAINER_SHORT,
    classify_yt_error, friendly_error_msg, _format_comments, _format_description,
    _format_meta, _info_thumbnail_url,
)
from app.models import VideoRecord
from app.downloader import fetch_info
from config import Config
import logging

NAV_MAIN = 'main'
NAV_RECENT = 'recent'
NAV_FORMAT = 'format'
NAV_DELIVERY = 'delivery'

# Telegram's edit_text caps the rendered message text at 4096 bytes. We
# leave 60 chars of SAFE_TEXT_MAX headroom for the trailing kb string
# that edit_text handles as a separate field (`reply_markup`), and for
# minor changes to the headline f-string over time. Below SAFE_TEXT_MAX
# we always emit extras (the comments block); above, we drop extras and
# emit only the title / duration / format-picker headline so the user
# never loses the format picker on a long-title / many-comments worst
# case. The constants are module-level so a future maintainer searching
# for "4096" finds the rationale in one place.
TELEGRAM_TEXT_MAX = 4096
SAFE_TEXT_MAX = TELEGRAM_TEXT_MAX - 60

def _nav_key(chat_id, message_id):
    """Build the canonical per-message nav-stack key.

    Centralized so every mutation site (nav_push / nav_pop / the
    'back' handler / tests) addresses the SAME key shape. A future
    refactor that swaps (chat_id, message_id) ordering or omits
    one of the two ids is caught by the inline tests in
    tests/test_formats.py::TestFormatChoiceKbMarkers and the new
    TestPerMessagePendingUrlsIsolation case.
    """
    return (chat_id, message_id)


def nav_push(bot, chat_id, message_id, action, data=None):
    """Push a (action, data) back-stack frame onto a SPECIFIC message's stack.

    Per-message keying (the 2026-07-15 stale-button fix) means the
    handle_back dispatcher ONLY pops frames that THIS message pushed,
    so a "Back" click on the format-picker for video 1 does NOT
    accidentally surface video 2's frame when the user has
    meanwhile moved on. The (chat_id, message_id) tuple uniquely
    identifies a Telegram message across the bot's whole
    per-message runtime, including group chats where chat_id is
    the group id (not a uid).

    LRU discipline: enforces `bot._ephemeral_max` on `_nav_stack`
    so a long-lived VPS that has processed N menu interactions
    (one nav_push per Back-button-eligible render) doesn't leak.
    Mirror of the same discipline on `_delivery_screen` (formats.py)
    and `_pending_urls` (added 2026-07-15 after code-review
    feedback flagged the asymmetry).
    """
    key = _nav_key(chat_id, message_id)
    if key not in bot._nav_stack:
        if len(bot._nav_stack) >= bot._ephemeral_max:
            bot._nav_stack.popitem(last=False)
        bot._nav_stack[key] = []
    bot._nav_stack.move_to_end(key)
    bot._nav_stack[key].append((action, data))


def nav_pop(bot, chat_id, message_id):
    """Pop the top frame from a single message's nav stack.

    Returns (NAV_MAIN, None) -- the universal safe fallback -- when
    no frame is on the stack so an accidental double-back-click
    never propagates a stale earlier flow's frame to the user.
    Caller doesn't need to null-check.
    """
    key = _nav_key(chat_id, message_id)
    if key in bot._nav_stack and bot._nav_stack[key]:
        return bot._nav_stack[key].pop()
    return (NAV_MAIN, None)


def nav_clear_user(bot, uid):
    """Drop every nav-stack frame that ANY message in this user's
    flows has pushed.

    Replaces the old per-uid `nav_clear` (which popped a single
    `bot._nav_stack[uid]` slot). Today per-message stacks belong
    to messages scattered across (chat_id, message_id) keys; we
    scan them and remove any whose keys belong to this uid.

    In private chat uid == chat_id so the scan narrows to one chat;
    in groups chat_id is the group id which a uid-bearing user is
    a member of -- we match uids at index 0 of the key tuple
    because we don't tag keys with both chat_id AND uid, and the
    chat_id happens to equal uid in the (only) private chat case.
    For groups the function still drops any private-chat frames
    belonging to that uid, silently leaving group-chat frames
    alone -- callers that care about group stacks should pass a
    real (chat_id, message_id) via nav_clear_message() below.

    Does NOT touch `_pending_urls` or `_delivery_screen`. Those
    define in-flight message content; clearing them on
    /start /cancel /recent would invalidate still-active inline
    keyboards on older messages, regressing the 2026-07-15 fix.
    """
    drops = [k for k in list(bot._nav_stack.keys()) if k[0] == uid]
    for k in drops:
        bot._nav_stack.pop(k, None)


def nav_clear_message(bot, chat_id, message_id):
    """Drop this single message's nav stack frame(s).

    Used internally by handle_back when a back-traversal lands on
    a destination that should start fresh (next-message reset).
    Not exposed in commands.py -- command-level flows go through
    nav_clear_user, which is broader and intentional.
    """
    bot._nav_stack.pop(_nav_key(chat_id, message_id), None)

logger = logging.getLogger('yt_bot')

def menu(bot, uid):
    has = uid in bot._cookie_data
    vc = len(bot.videos.get(uid, []))
    settings = bot._user_settings.get(uid, {})
    lang = bot._user_langs.get(uid, 'en')
    delivery = settings.get('default_delivery', 'ask')
    delivery_label = {'ask': 'Ask', 'telegram': 'Telegram', 'link': 'Link'}.get(delivery, 'Ask')
    vq = settings.get('video_quality', 'best')
    aq = settings.get('audio_quality', 'best')
    sm_stored = settings.get('subtitle_mode', 'embed')
    cn_stored = settings.get('video_container', 'auto')
    af = settings.get('auto_format', 'ask')
    vq_short = 'Best' if vq == 'best' else vq.upper() if vq != 'worst' else '~'
    aq_short = 'Best' if aq == 'best' else f"{aq}k" if aq != 'worst' else '~'
    # Container-aware subtitle mode: when the user has both container='mp4'
    # AND subtitle_mode='embed' set, the EFFECTIVE subtitle mode is
    # 'separate' (the embed-vs-MKV link is broken). Reflect that on the
    # button label so the user sees what they'll actually receive, not
    # what their settings dict nominally contains.
    sm_effective = 'separate' if (cn_stored == 'mp4' and sm_stored == 'embed') else sm_stored
    sm_short = {'embed': 'MKV', 'separate': 'SRT', 'off': 'Off'}.get(sm_effective, 'MKV')
    cn_short = VIDEO_CONTAINER_SHORT.get(cn_stored, 'MKV') if cn_stored in VIDEO_CONTAINER_OPTIONS else 'MKV'
    # Defensive: stored `af` may be legacy garbage; menu() reads raw
    # settings for the button label only — `get_auto_format` is the
    # authoritative validator (used in messages.py:on_msg).
    af_short = AUTO_FORMAT_SHORT.get(af, 'Ask') if af in AUTO_FORMAT_OPTIONS else 'Ask'
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📹 Recent Downloads", callback_data='r')],
        [InlineKeyboardButton("🍪 Upload Cookies", callback_data='c')],
        [InlineKeyboardButton(f"🎬 Video: {vq_short}", callback_data='vq'),
         InlineKeyboardButton(f"🎵 Audio: {aq_short}", callback_data='aq'),
         InlineKeyboardButton(f"📝 Subs: {sm_short}", callback_data='sm')],
        [InlineKeyboardButton(f"🌐 Language: {lang.upper()}", callback_data='lang'),
         InlineKeyboardButton(f"📤 Delivery: {delivery_label}", callback_data='delivery'),
         InlineKeyboardButton(f"🍪 {'✅' if has else '❌'}", callback_data='cs')],
        [InlineKeyboardButton(f"⚡ Auto: {af_short}", callback_data='af'),
         InlineKeyboardButton(f"🎞️ Container: {cn_short}", callback_data='cn')],
        [InlineKeyboardButton(f"📦 {vc} files", callback_data='vc')],
    ])

async def welcome_text(bot):
    username = await _username(bot)
    return f"👋 Welcome!\n\n🎥 YouTube Downloader Bot\n\n💡 Send YouTube link → Download!\n📱 Inline: @{username} <link>\n👥 Groups: Send link\n🗑️ Files: {bot.config.STORAGE_DAYS}d retention.\n\n🔒 Cookies: RAM only, auto-restore."

async def _username(bot):
    if not bot._bot_username and bot._bot: me = await bot._bot.get_me(); bot._bot_username = me.username
    return bot._bot_username or "botname"

async def show_format_choice(bot, uid, url, video_id, msg):
    from app.handlers.messages import _ensure
    if not await _ensure(bot, uid):
        await msg.reply_text("❌ Cookies expired. Upload with /cookies", reply_to_message_id=msg.message_id)
        return
    s = await msg.reply_text("🔍 Fetching info...", reply_to_message_id=msg.message_id)
    try:
        info = await asyncio.get_event_loop().run_in_executor(None, fetch_info, bot, uid, url)
        title, duration = info.get('title', '?'), info.get('duration', 0)
        # Per-message keying (2026-07-15 stale-button fix): the format picker
        # this fetch feeds into is `s`, the status placeholder we just
        # `reply_text`-ed. By the time the user sees it (after edit_text /
        # edit_media) the picker and `s` share the same `message_id`,
        # so keying `_pending_urls` here is correct and survives all of
        # Telegram's edit-paths without a follow-up write. The 64-byte
        # cb-data cap is the alternative approach we considered and
        # rejected: URLs regularly exceed it (YouTube playlist / share
        # links can be ~80 chars), so embedding them in callback_data
        # would silently break the picker on long-URL videos.
        bot._pending_urls[(s.chat.id, s.message_id)] = (url, video_id, title)
        # LRU discipline (2026-07-15): mirror the cap on _delivery_screen
        # and _nav_stack so a long-lived VPS doesn't leak via
        # _pending_urls. `move_to_end` keeps the just-touched
        # picker entry at the front, and `popitem(last=False)`
        # evicts the least-recent entry when we exceed
        # `bot._ephemeral_max`. Without this, every show_format_choice
        # call would grow the dict unbounded.
        bot._pending_urls.move_to_end((s.chat.id, s.message_id))
        while len(bot._pending_urls) > bot._ephemeral_max:
            bot._pending_urls.popitem(last=False)
        mins, secs = divmod(duration, 60) if duration else (0, 0)
        # Operator-toggleable: surface the most-recent comments only when
        # Config.MAX_COMMENTS > 0 (which we set via fetch_info's
        # extractor_args.youtube.max_comments). yt-dlp returns comments
        # newest-first because fetch_info forces `comment_sort=new`, so
        # `[:Config.MAX_COMMENTS]` takes the first N most-recent. Live /
        # upcoming videos return `info['comments'] == []` which renders as
        # an empty string and the block is skipped silently (no
        # placeholder UI for a missing-data edge case).
        #
        # `info.get('comments') or []` — defensive guard against yt-dlp
        # returning a partial/null comments object mid-fetch (rare but
        # observed on rate-limited responses). The outer `or []` lets
        # the slice + helper handle it as "no comments" rather than
        # raising TypeError on `None[:N]` and collapsing the whole
        # format-choice screen via the outer try/except.
        comments_block = _format_comments(
            (info.get('comments') or [])[:Config.MAX_COMMENTS])
        # 4-5 line description excerpt between title and duration. Same
        # yt-dlp give_back as title / duration / comments (one fetch)
        # so no extra HTTP is paid. `_format_description` returns ''
        # for empty/None input, so we branch on the helper's output
        # rather than concatenating a 0-length emoji prefix that would
        # leave a blank line above the duration row.
        desc_text = _format_description(info.get('description'))
        # uploader + view_count + upload_date come from the SAME
        # extract_info call as title/description/comments (all in
        # info dict, no extra fetch). Each is independently optional:
        # uploader may be None on non-YouTube extractors; view_count is
        # None on live / upcoming streams; upload_date can be missing
        # for scheduled content. `_format_meta` joins the non-empty
        # fields with single newlines.
        meta_text = _format_meta(
            info.get('uploader'),
            info.get('view_count'),
            info.get('upload_date'),
        )
        # Build `headline` from optional parts (title + duration are
        # always present; description + meta are each independently
        # optional). List-of-parts joined with chr(10) avoids the
        # 2-arm if/else repetition the previous round had — a future
        # maintainer reading this block now sees one natural
        # conditional-rebuild pipeline rather than two parallel
        # conditional literals. Structural-pin tests still match:
        # `if desc_text:` literal is preserved by `if desc_text:`,
        # the template f-strings (`{headline}{extras}` + overflow
        # variant) flow through unchanged.
        parts = [f"📹 *{esc(title[:200])}*"]
        if desc_text:
            parts.append(f"\U0001F4D6 {desc_text}")
        if meta_text:
            parts.append(meta_text)
        parts.append(f"⏱ {mins}:{secs:02d}")
        headline = chr(10).join(parts)
        extras = f"\n\n\U0001F4AC Top comments:\n{comments_block}" if comments_block else ''
        from app.handlers.formats import format_choice_kb
        # Telegram's edit_text caps the rendered text at TELEGRAM_TEXT_MAX
        # (4096) bytes; with MAX_COMMENTS=20 + a 200-char title (after
        # esc() potentially doubling — chars like * _ ` [ ] each grow by 1
        # byte) + description + the format-picker headline, our worst
        # case crosses 4096 and Telegram rejects edit_text with "Bad
        # Request: message is too long". Rather than truncate mid-comment
        # (which would render half a line that looks typed-broken to the
        # user), we DROP `extras` (the comments block) when overflow is
        # detected while preserving the title / description / duration
        # headline that build the user's context for the format pick.
        # The format picker is what the user actually needs to choose a
        # download — losing the comments excerpt on the rare worst-case
        # title is strictly better than losing the kb. The conditional
        # `headline` above (with or without description) is reused on
        # the overflow path so the description block is preserved even
        # when comments are dropped.
        text = f"{headline}{extras}\n\nChoose format:"
        if len(text) > SAFE_TEXT_MAX:
            text = f"{headline}\n\nChoose format:"
        # Post-fetch-attachment: convert the "🔍 Fetching info..." status
        # placeholder into a real Telegram photo with the format-choice
        # caption + kb when yt-dlp's info dict carries a thumbnail URL AND
        # the caption fits Telegram's 1024-char photo-caption cap. The
        # `edit_media` call replaces (does NOT add to) the status
        # message, so the user sees exactly one message at the end of
        # the fetch -- the photo, caption, and format-picker kb all on
        # the same line in their chat. Failing cases fall through to
        # the `edit_text` path below:
        #   * `thumb_url` falsy  -> no thumbnail in the info dict.
        #   * `len(text) > 1024` -> comments-block + description overflows
        #     the photo-caption cap; the text-only path keeps the whole
        #     caption visible.
        #   * `edit_media` raises -> Telegram-side Bad Request (invalid
        #     URL, server-side download failure, caption parse error).
        #     The outer try/except already handles it as 'unknown' so
        #     we deliberately `pass` and let the text-only path
        #     succeed; users get a slightly less rich layout but the
        #     download flow still works end-to-end.
        # Structural pin: the `_info_thumbnail_url(` discriminator is
        # the unit-testable contract for "which URL would Telegram
        # display"; the `edit_media` + `InputMediaPhoto` keywords are
        # the deployment pins so the photo-edit branch can't silently
        # regress to text-only (which would show no thumbnail to users
        # on videos that have one).
        thumb_url = _info_thumbnail_url(info)
        if thumb_url and len(text) <= 1024:
            try:
                await s.edit_media(
                    media=InputMediaPhoto(
                        media=thumb_url,
                        caption=text,
                        parse_mode=ParseMode.MARKDOWN,
                    ),
                    reply_markup=format_choice_kb(bot, uid, video_id),
                )
                return
            except (BadRequest, TimedOut, NetworkError, RetryAfter) as exc:
                # Network / Bad Request from Telegram -- fall back to
                # the text-only path so the user still gets the
                # format-picker even on a bot-side URL fetch failure.
                # NARROW except list -- a bare `except Exception: pass`
                # would silently swallow programming bugs (a `NameError`
                # from a future refactor that drops the `InputMediaPhoto`
                # import, an `AttributeError` from a malformed call
                # shape, an `asyncio.TimeoutError` from a hang on the
                # underlying Bot API transport) and present the user with
                # the text-only fallback as if Telegram itself rejected
                # the photo. The 4 telegram.* exceptions here are the
                # ONLY ones this branch should silently absorb -- every
                # other exception is a real bug class that should
                # surface via the outer try/except so the friendly_error
                # classifier can attach a real message instead of
                # pretending everything is fine.
                logger.info(
                    'show_format_choice: edit_media declined '
                    '(%s); falling back to edit_text', exc)
                pass
        await s.edit_text(
            text, parse_mode=ParseMode.MARKDOWN,
            reply_markup=format_choice_kb(bot, uid, video_id))
    except Exception as e:
        category = classify_yt_error(str(e))
        logger.error("Format choice error [%s]: %s", category, str(e)[:200])
        await s.edit_text(friendly_error_msg(category), reply_markup=menu(bot, uid))

async def show_recent(bot, u, c, page=0):
    uid = u.effective_user.id; msg = u.callback_query.message if u.callback_query else u.message
    # Eagerly drop records whose files no longer exist (operator cleared
    # downloads/ from VPS, retention sweep, server migration, etc.) so this
    # listing only shows entries that can actually be delivered, and so the
    # sel_<idx> callback_data below stays in sync with the post-prune list.
    pruned = prune_missing(bot, uid)
    videos = bot.videos.get(uid, [])
    if not videos:
        cleaned_line = f"\n🗑️ Cleaned {pruned} missing entries." if pruned else ""
        await msg.reply_text(f"📭 No files.{cleaned_line}", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Menu", callback_data='b')]]))
        return
    pp, tp = 5, max(1, (len(videos)+4)//5); page = max(0, min(page, tp-1)); pv = videos[page*pp:(page+1)*pp]
    emoji_map = {'video': '🎬', 'audio': '🎵', 'thumb': '🖼️'}
    txt = f"📹 Downloads ({page+1}/{tp})"
    if pruned:
        txt += f"\n🗑️ Cleaned {pruned} missing entries."
    txt += "\n\n"
    for i, v in enumerate(pv, page*pp+1):
        ex = "✅" if Path(v.file_path).exists() else "🗑️"
        txt += f"{ex} {emoji_map.get(v.media_type, '📹')} {i}. {esc(v.title[:50])}\n   📦 {v.file_size/1024/1024:.2f}MB | {v.download_time}\n\n"
    txt += f"⚠️ {bot.config.STORAGE_DAYS}d retention."
    kb = []
    for i, v in enumerate(pv, page*pp+1):
        # One row per entry: select-button on the left, 🗑️ delete on the
        # right. The existing callback_data pattern simplifies to `i-1`
        # because i is 1-indexed display and bot.videos[uid] is 0-indexed
        # storage; the page offset cancels out exactly so absolute idx is
        # correct across pagination.
        if Path(v.file_path).exists():
            row = [
                InlineKeyboardButton(
                    f"{emoji_map.get(v.media_type,'📹')} {i}. {v.title[:40]}",
                    callback_data=f'sel_{i-1}',
                ),
                InlineKeyboardButton(
                    f"🗑️ #{i}",
                    callback_data=f'd_{i-1}',
                ),
            ]
            kb.append(row)
    kb.append([InlineKeyboardButton("🗑️ Clear All", callback_data='clear_all')])
    nav = []
    if page > 0: nav.append(InlineKeyboardButton("⬅️", callback_data=f'p_{page-1}'))
    if page < tp-1: nav.append(InlineKeyboardButton("➡️", callback_data=f'p_{page+1}'))
    if nav: kb.append(nav)
    kb.append([InlineKeyboardButton("🔙 Menu", callback_data='b')])
    await msg.reply_text(txt, disable_web_page_preview=True, reply_markup=InlineKeyboardMarkup(kb))

async def handle_back(bot, u, c):
    q = u.callback_query; uid = u.effective_user.id
    # Per-message keying: pop the nav-stack frame belonging to the
    # message the user clicked `b` on. Earlier the key was uid-only
    # so any button on any of the user's messages would pop the same
    # shared per-uid stack -- the regression we just fixed surfaced
    # when a /start or new URL scrolled up enough to overwrite the
    # frame a stale `b` was about to pop.
    prev, data = nav_pop(bot, q.message.chat.id, q.message.message_id); await q.answer()
    if prev == NAV_MAIN:
        await q.message.reply_text(await welcome_text(bot), reply_markup=menu(bot, uid)); await q.message.delete()
    elif prev == NAV_RECENT:
        await show_recent(bot, u, c); await q.message.delete()
    elif prev == NAV_FORMAT:
        url, video_id = data
        # Re-rendering the format picker for the popped (url, video_id).
        # show_format_choice writes `_pending_urls` keyed by the new
        # picker message_id, so we don't re-write here (doing so would
        # race against show_format_choice's own write inside the same
        # event-loop tick).
        await show_format_choice(bot, uid, url, video_id, q.message); await q.message.delete()
    else:
        await q.message.reply_text(await welcome_text(bot), reply_markup=menu(bot, uid)); await q.message.delete()

async def router(bot, u, c):
    q = u.callback_query; await q.answer(); d, uid = q.data, u.effective_user.id
    if d == 'b': await handle_back(bot, u, c)
    elif d == 'r':
        # Per-message nav on the /recent trigger message so a back-click
        # on the /recent rendering's own message goes to NAV_MAIN
        # (defaults). The /recent-list entries themselves do not push a
        # frame on this message -- they push NAV_RECENT on the delivery
        # screen show_delivery renders, see formats.show_delivery.
        nav_push(bot, q.message.chat.id, q.message.message_id, NAV_MAIN); await show_recent(bot, u, c)
    elif d == 'lang': await _change_language(bot, u, c)
    elif d == 'delivery': await _change_delivery(bot, u, c)
    elif d == 'vq': await _change_video_quality(bot, u, c)
    elif d == 'aq': await _change_audio_quality(bot, u, c)
    elif d == 'sm': await _change_subtitle_mode(bot, u, c)
    elif d == 'af': await _change_auto_format(bot, u, c)
    elif d == 'cn': await _change_video_container(bot, u, c)
    elif d.startswith('setlang_'): await _set_language(bot, u, c)
    elif d.startswith('setdelivery_'): await _set_delivery(bot, u, c)
    elif d.startswith('setvq_'): await _set_video_quality(bot, u, c)
    elif d.startswith('setaq_'): await _set_audio_quality(bot, u, c)
    elif d.startswith('setsm_'): await _set_subtitle_mode(bot, u, c)
    elif d.startswith('setaf_'): await _set_auto_format(bot, u, c)
    elif d.startswith('setcn_'): await _set_video_container(bot, u, c)
    elif d == 'cs': await q.message.reply_text("✅ Cookies active" if uid in bot._cookie_data else "❌ Upload with /cookies")
    elif d == 'vc': await q.message.reply_text(f"📦 {len(bot.videos.get(uid,[]))} files")
    elif d == 'clear_all': await _clear_all(bot, u, c)
    elif d.startswith('fmt_'): from app.handlers.formats import choose_format; await choose_format(bot, u, c)
    elif d == 'backfmt': from app.handlers.formats import back_to_formats; await back_to_formats(bot, u, c)
    elif d.startswith('morefmt_'): from app.handlers.formats import also_get_other_format; await also_get_other_format(bot, u, c)
    elif d.startswith('tg_'): from app.handlers.formats import send_telegram; await send_telegram(bot, u, c)
    elif d.startswith('lk_'): from app.handlers.formats import send_link; await send_link(bot, u, c)
    elif d.startswith('sel_'): await _select(bot, u, c)
    elif d.startswith('d_'): await _delete(bot, u, c)
    elif d.startswith('p_'): await show_recent(bot, u, c, int(d.split('_')[1]))

async def _change_language(bot, u, c):
    q = u.callback_query; uid = u.effective_user.id
    current = bot._user_langs.get(uid, 'en')
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton(f"{'✅' if current == 'en' else '⬜'} English", callback_data='setlang_en')],
        [InlineKeyboardButton(f"{'✅' if current == 'fa' else '⬜'} فارسی", callback_data='setlang_fa')],
        [InlineKeyboardButton(f"{'✅' if current == 'ar' else '⬜'} العربية", callback_data='setlang_ar')],
        [InlineKeyboardButton(f"{'✅' if current == 'ru' else '⬜'} Русский", callback_data='setlang_ru')],
        [InlineKeyboardButton(f"{'✅' if current == 'es' else '⬜'} Español", callback_data='setlang_es')],
        [InlineKeyboardButton("🔙 Back", callback_data='b')],
    ])
    await q.message.reply_text("🌐 Select subtitle language:", reply_markup=kb)
    await q.message.delete()

async def _set_language(bot, u, c):
    q = u.callback_query; await q.answer()
    uid = u.effective_user.id
    lang = q.data.split('_')[1]
    bot._user_langs[uid] = lang
    bot.save()
    lang_names = {'en': 'English', 'fa': 'فارسی', 'ar': 'العربية', 'ru': 'Русский', 'es': 'Español'}
    await q.message.reply_text(f"🌐 Language set to {lang_names.get(lang, lang.upper())}", reply_markup=menu(bot, uid))
    await q.message.delete()

async def _change_delivery(bot, u, c):
    q = u.callback_query; uid = u.effective_user.id
    current = bot._user_settings.get(uid, {}).get('default_delivery', 'ask')
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton(f"{'✅' if current == 'ask' else '⬜'} Ask every time", callback_data='setdelivery_ask')],
        [InlineKeyboardButton(f"{'✅' if current == 'telegram' else '⬜'} Send via Telegram", callback_data='setdelivery_telegram')],
        [InlineKeyboardButton(f"{'✅' if current == 'link' else '⬜'} Get Download Link", callback_data='setdelivery_link')],
        [InlineKeyboardButton("🔙 Back", callback_data='b')],
    ])
    await q.message.reply_text("📤 Default delivery method:", reply_markup=kb)
    await q.message.delete()

async def _set_delivery(bot, u, c):
    q = u.callback_query; await q.answer()
    uid = u.effective_user.id
    method = q.data.split('_')[1]
    if uid not in bot._user_settings:
        bot._user_settings[uid] = {}
    bot._user_settings[uid]['default_delivery'] = method
    bot.save()
    labels = {'ask': 'Ask every time', 'telegram': 'Send via Telegram', 'link': 'Get Download Link'}
    await q.message.reply_text(f"📤 Default delivery: {labels.get(method, method)}", reply_markup=menu(bot, uid))
    await q.message.delete()

async def _change_video_quality(bot, u, c):
    q = u.callback_query; uid = u.effective_user.id
    current = bot._user_settings.get(uid, {}).get('video_quality', 'best')
    rows = []
    for opt in VIDEO_QUALITY_OPTIONS:
        marker = '✅' if current == opt else '⬜'
        rows.append([InlineKeyboardButton(f"{marker} {VIDEO_QUALITY_LABELS.get(opt, opt)}", callback_data=f'setvq_{opt}')])
    rows.append([InlineKeyboardButton("🔙 Back", callback_data='b')])
    await q.message.reply_text("🎬 Video quality (default: 🏆 Best):", reply_markup=InlineKeyboardMarkup(rows))
    await q.message.delete()

async def _set_video_quality(bot, u, c):
    q = u.callback_query; await q.answer()
    uid = u.effective_user.id
    qkey = q.data[len('setvq_'):]
    if uid not in bot._user_settings or not isinstance(bot._user_settings.get(uid), dict):
        bot._user_settings[uid] = {}
    bot._user_settings[uid]['video_quality'] = qkey
    bot.save()
    await q.message.reply_text(
        f"🎬 Video quality set to {VIDEO_QUALITY_LABELS.get(qkey, qkey)}",
        reply_markup=menu(bot, uid))
    await q.message.delete()

async def _change_audio_quality(bot, u, c):
    q = u.callback_query; uid = u.effective_user.id
    current = bot._user_settings.get(uid, {}).get('audio_quality', 'best')
    rows = []
    for opt in AUDIO_QUALITY_OPTIONS:
        marker = '✅' if current == opt else '⬜'
        rows.append([InlineKeyboardButton(f"{marker} {AUDIO_QUALITY_LABELS.get(opt, opt)}", callback_data=f'setaq_{opt}')])
    rows.append([InlineKeyboardButton("🔙 Back", callback_data='b')])
    await q.message.reply_text("🎵 Audio quality (default: 🏆 Best):", reply_markup=InlineKeyboardMarkup(rows))
    await q.message.delete()

async def _set_audio_quality(bot, u, c):
    q = u.callback_query; await q.answer()
    uid = u.effective_user.id
    qkey = q.data[len('setaq_'):]
    if uid not in bot._user_settings or not isinstance(bot._user_settings.get(uid), dict):
        bot._user_settings[uid] = {}
    bot._user_settings[uid]['audio_quality'] = qkey
    bot.save()
    await q.message.reply_text(
        f"🎵 Audio quality set to {AUDIO_QUALITY_LABELS.get(qkey, qkey)}",
        reply_markup=menu(bot, uid))
    await q.message.delete()

async def _change_subtitle_mode(bot, u, c):
    q = u.callback_query; uid = u.effective_user.id
    current = bot._user_settings.get(uid, {}).get('subtitle_mode', 'embed')
    rows = []
    for opt in SUBTITLE_MODE_OPTIONS:
        marker = '✅' if current == opt else '⬜'
        label = SUBTITLE_MODE_LABELS.get(opt, opt)
        if opt == 'embed':
            desc = ' (SRT subs embedded in MKV)'
        elif opt == 'separate':
            desc = ' (subs sent as .srt file)'
        else:
            desc = ' (no subs)'
        rows.append([InlineKeyboardButton(f"{marker} {label}{desc}", callback_data=f'setsm_{opt}')])
    rows.append([InlineKeyboardButton("🔙 Back", callback_data='b')])
    await q.message.reply_text("📝 Subtitle mode (default: 🔗 Embed MKV):", reply_markup=InlineKeyboardMarkup(rows))
    await q.message.delete()

async def _set_subtitle_mode(bot, u, c):
    q = u.callback_query; await q.answer()
    uid = u.effective_user.id
    qkey = q.data[len('setsm_'):]
    if qkey not in SUBTITLE_MODE_OPTIONS:
        return
    if uid not in bot._user_settings or not isinstance(bot._user_settings.get(uid), dict):
        bot._user_settings[uid] = {}
    bot._user_settings[uid]['subtitle_mode'] = qkey
    bot.save()
    await q.message.reply_text(
        f"📝 Subtitle mode set to {SUBTITLE_MODE_LABELS.get(qkey, qkey)}",
        reply_markup=menu(bot, uid))
    await q.message.delete()


async def _change_auto_format(bot, u, c):
    """Show the auto-format picker inline-keyboard."""
    q = u.callback_query; uid = u.effective_user.id
    stored = bot._user_settings.get(uid, {}).get('auto_format', 'ask')
    current = stored if stored in AUTO_FORMAT_OPTIONS else 'ask'
    rows = []
    for opt in AUTO_FORMAT_OPTIONS:
        marker = '✅' if current == opt else '⬜'
        if opt == 'ask':
            desc = ' (show video/audio/thumb keyboard on link send)'
        elif opt == 'video':
            desc = ' (auto-download video on link send)'
        elif opt == 'audio':
            desc = ' (auto-download audio on link send)'
        else:
            desc = ' (auto-download thumbnail on link send)'
        rows.append([InlineKeyboardButton(
            f"{marker} {AUTO_FORMAT_LABELS.get(opt, opt)}{desc}",
            callback_data=f'setaf_{opt}')])
    rows.append([InlineKeyboardButton("🔙 Back", callback_data='b')])
    await q.message.reply_text(
        "⚡ Default format when you send a YouTube link (private chat only):",
        reply_markup=InlineKeyboardMarkup(rows))
    await q.message.delete()


async def _set_auto_format(bot, u, c):
    q = u.callback_query; await q.answer()
    uid = u.effective_user.id
    qkey = q.data[len('setaf_'):]
    if qkey not in AUTO_FORMAT_OPTIONS:
        return
    if uid not in bot._user_settings or not isinstance(bot._user_settings.get(uid), dict):
        bot._user_settings[uid] = {}
    bot._user_settings[uid]['auto_format'] = qkey
    bot.save()
    await q.message.reply_text(
        f"⚡ Auto-format set to {AUTO_FORMAT_LABELS.get(qkey, qkey)}",
        reply_markup=menu(bot, uid))
    await q.message.delete()


async def _change_video_container(bot, u, c):
    """Show the per-user video-container picker inline-keyboard."""
    q = u.callback_query; uid = u.effective_user.id
    stored = bot._user_settings.get(uid, {}).get('video_container', 'auto')
    current = stored if stored in VIDEO_CONTAINER_OPTIONS else 'auto'
    rows = []
    for opt in VIDEO_CONTAINER_OPTIONS:
        marker = '✅' if current == opt else '⬜'
        if opt == 'auto':
            desc = ' (best codec match, allows MKV sub embed)'
        else:
            desc = ' (universal compat; subs come as separate .srt)'
        rows.append([InlineKeyboardButton(
            f"{marker} {VIDEO_CONTAINER_LABELS.get(opt, opt)}{desc}",
            callback_data=f'setcn_{opt}')])
    rows.append([InlineKeyboardButton("🔙 Back", callback_data='b')])
    await q.message.reply_text(
        "🎞️ Default video output container:",
        reply_markup=InlineKeyboardMarkup(rows))
    await q.message.delete()


async def _set_video_container(bot, u, c):
    """Persist the user's video_container choice to disk."""
    q = u.callback_query; await q.answer()
    uid = u.effective_user.id
    qkey = q.data[len('setcn_'):]
    if qkey not in VIDEO_CONTAINER_OPTIONS:
        return
    if uid not in bot._user_settings or not isinstance(bot._user_settings.get(uid), dict):
        bot._user_settings[uid] = {}
    bot._user_settings[uid]['video_container'] = qkey
    bot.save()
    # Surface the cascade so the user understands why their `Subs:` button
    # label will *visibly* flip to 'SRT' when they pick MP4 + had embed.
    extra = ''
    sm_stored = bot._user_settings[uid].get('subtitle_mode', 'embed')
    if qkey == 'mp4' and sm_stored == 'embed':
        extra = "\n⚠️ MP4 + embed → subs will come as a separate .srt file."
    await q.message.reply_text(
        f"🎞️ Container set to {VIDEO_CONTAINER_LABELS.get(qkey, qkey)}"
        f"{extra}",
        reply_markup=menu(bot, uid))
    await q.message.delete()

async def _clear_all(bot, u, c):
    q = u.callback_query; uid = u.effective_user.id
    videos = bot.videos.get(uid, []); count = len(videos)
    for v in videos: Path(v.file_path).unlink(missing_ok=True)
    bot.videos.pop(uid, None); bot.save()
    await q.message.reply_text(f"🗑️ {count} files cleared.", reply_markup=menu(bot, uid))

async def _select(bot, u, c):
    q = u.callback_query; uid, idx = u.effective_user.id, int(q.data.split('_')[1])
    # Eagerly prune: a file deleted between when show_recent rendered this
    # menu and when the user tapped it would otherwise be delivered (or
    # blow up downstream show_delivery). After pruning, the indices in
    # bot.videos[uid] may have shifted — if the clicked record was the
    # one that got pruned, idx is out of bounds and we bounce to /recent
    # where the user can pick a fresh entry.
    prune_missing(bot, uid)
    videos = bot.videos.get(uid, [])
    if not videos or idx >= len(videos):
        await show_recent(bot, u, c)
        await q.message.delete()
        return
    nav_push(bot, q.message.chat.id, q.message.message_id, NAV_RECENT)
    from app.handlers.formats import show_delivery
    await show_delivery(bot, q.message, videos[idx])
    await q.message.delete()

async def _delete(bot, u, c):
    q = u.callback_query; uid, idx = u.effective_user.id, int(q.data.split('_')[1])
    videos = bot.videos.get(uid, [])
    if 0 <= idx < len(videos): Path(videos[idx].file_path).unlink(missing_ok=True); videos.pop(idx); bot.save()
    await q.message.reply_text("🗑️ Deleted.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("📹 Videos", callback_data='r'), InlineKeyboardButton("🔙 Menu", callback_data='b')]]))