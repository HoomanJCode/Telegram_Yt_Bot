# app/handlers/navigation.py
"""Navigation stack, menus, back button"""
import asyncio
from pathlib import Path
from datetime import datetime
from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ParseMode
from app.utils import (
    esc, find_existing, prune_missing,
    VIDEO_QUALITY_OPTIONS, AUDIO_QUALITY_OPTIONS, SUBTITLE_MODE_OPTIONS,
    AUTO_FORMAT_OPTIONS, AUTO_FORMAT_LABELS, AUTO_FORMAT_SHORT,
    VIDEO_QUALITY_OPTIONS, VIDEO_QUALITY_LABELS, AUDIO_QUALITY_LABELS, SUBTITLE_MODE_LABELS,
    VIDEO_CONTAINER_OPTIONS, VIDEO_CONTAINER_LABELS, VIDEO_CONTAINER_SHORT,
    classify_yt_error, friendly_error_msg, _format_comments,
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

def nav_push(bot, uid, action, data=None):
    if uid not in bot._nav_stack: bot._nav_stack[uid] = []
    bot._nav_stack[uid].append((action, data))

def nav_pop(bot, uid):
    if uid in bot._nav_stack and bot._nav_stack[uid]: return bot._nav_stack[uid].pop()
    return (NAV_MAIN, None)

def nav_clear(bot, uid): bot._nav_stack.pop(uid, None)

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
        await msg.reply_text("❌ Cookies expired. Upload with /cookies")
        return
    s = await msg.reply_text("🔍 Fetching info...")
    try:
        info = await asyncio.get_event_loop().run_in_executor(None, fetch_info, bot, uid, url)
        title, duration = info.get('title', '?'), info.get('duration', 0)
        bot._pending_urls[uid] = (url, video_id, title)
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
        extras = f"\n\n\U0001F4AC Top comments:\n{comments_block}" if comments_block else ''
        from app.handlers.formats import format_choice_kb
        # Telegram's edit_text caps the rendered text at TELEGRAM_TEXT_MAX
        # (4096) bytes; with MAX_COMMENTS=20 + a 200-char title (after
        # esc() potentially doubling — chars like * _ ` [ ] each grow by 1
        # byte) + the format-picker headline, our worst-case crosses 4096
        # and Telegram rejects edit_text with "Bad Request: message is
        # too long". Rather than truncate mid-comment (which would
        # render half a line that looks typed-broken to the user), we
        # DROP the comments block entirely when overflow is detected.
        # The format picker is what the user actually needs to choose a
        # download — losing the comments excerpt on the rare worst-case
        # title is strictly better than losing the kb.
        headline = f"📹 *{esc(title[:200])}*\n⏱ {mins}:{secs:02d}"
        text = f"{headline}{extras}\n\nChoose format:"
        if len(text) > SAFE_TEXT_MAX:
            text = f"{headline}\n\nChoose format:"
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
    q = u.callback_query; uid = u.effective_user.id; prev, data = nav_pop(bot, uid); await q.answer()
    if prev == NAV_MAIN: await q.message.reply_text(await welcome_text(bot), reply_markup=menu(bot, uid)); await q.message.delete()
    elif prev == NAV_RECENT: await show_recent(bot, u, c); await q.message.delete()
    elif prev == NAV_FORMAT:
        url, video_id = data; bot._pending_urls[uid] = (url, video_id, '')
        await show_format_choice(bot, uid, url, video_id, q.message); await q.message.delete()
    else: await q.message.reply_text(await welcome_text(bot), reply_markup=menu(bot, uid)); await q.message.delete()

async def router(bot, u, c):
    q = u.callback_query; await q.answer(); d, uid = q.data, u.effective_user.id
    if d == 'b': await handle_back(bot, u, c)
    elif d == 'r': nav_push(bot, uid, NAV_MAIN); await show_recent(bot, u, c)
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
    elif d.startswith('backfmt_'): from app.handlers.formats import back_to_formats; await back_to_formats(bot, u, c)
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
    nav_push(bot, uid, NAV_RECENT)
    from app.handlers.formats import show_delivery
    await show_delivery(bot, q.message, videos[idx], idx)
    await q.message.delete()

async def _delete(bot, u, c):
    q = u.callback_query; uid, idx = u.effective_user.id, int(q.data.split('_')[1])
    videos = bot.videos.get(uid, [])
    if 0 <= idx < len(videos): Path(videos[idx].file_path).unlink(missing_ok=True); videos.pop(idx); bot.save()
    await q.message.reply_text("🗑️ Deleted.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("📹 Videos", callback_data='r'), InlineKeyboardButton("🔙 Menu", callback_data='b')]]))