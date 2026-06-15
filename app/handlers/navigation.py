"""Navigation stack, menus, back button"""
import asyncio
from pathlib import Path
from datetime import datetime
from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ParseMode
from app.utils import esc, find_existing
from app.models import VideoRecord
from app.downloader import fetch_info

NAV_MAIN = 'main'
NAV_RECENT = 'recent'
NAV_FORMAT = 'format'
NAV_DELIVERY = 'delivery'

def nav_push(bot, uid, action, data=None):
    if uid not in bot._nav_stack: bot._nav_stack[uid] = []
    bot._nav_stack[uid].append((action, data))

def nav_pop(bot, uid):
    if uid in bot._nav_stack and bot._nav_stack[uid]: return bot._nav_stack[uid].pop()
    return (NAV_MAIN, None)

def nav_clear(bot, uid): bot._nav_stack.pop(uid, None)

def menu(bot, uid):
    has = uid in bot._cookie_data; vc = len(bot.videos.get(uid, []))
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📹 Recent Downloads", callback_data='r')],
        [InlineKeyboardButton("🍪 Upload Cookies", callback_data='c')],
        [InlineKeyboardButton(f"🍪 {'✅' if has else '❌'}", callback_data='cs'), InlineKeyboardButton(f"📦 {vc} files", callback_data='vc')],
    ])

async def welcome_text(bot):
    username = await _username(bot)
    return f"👋 Welcome!\n\n🎥 YouTube Downloader Bot\n\n💡 Send YouTube link → Download!\n📱 Inline: @{username} <link>\n👥 Groups: Send link\n🗑️ Files: {bot.config.STORAGE_DAYS}d retention.\n\n🔒 Cookies: RAM only, auto-restore."

async def _username(bot):
    if not bot._bot_username and bot._bot: me = await bot._bot.get_me(); bot._bot_username = me.username
    return bot._bot_username or "botname"

async def show_format_choice(bot, uid, url, video_id, msg):
    s = await msg.reply_text("🔍 Fetching info...")
    try:
        info = await asyncio.get_event_loop().run_in_executor(None, fetch_info, bot, uid, url)
        title, duration = info.get('title', '?'), info.get('duration', 0)
        bot._pending_urls[uid] = (url, video_id, title)
        mins, secs = divmod(duration, 60) if duration else (0, 0)
        from app.handlers.formats import format_choice_kb
        await s.edit_text(f"📹 *{esc(title[:200])}*\n⏱ {mins}:{secs:02d}\n\nChoose format:", parse_mode=ParseMode.MARKDOWN, reply_markup=format_choice_kb(bot, uid, video_id))
    except: await s.edit_text("❌ Failed.", reply_markup=menu(bot, uid))

async def show_recent(bot, u, c, page=0):
    uid = u.effective_user.id; msg = u.callback_query.message if u.callback_query else u.message
    videos = bot.videos.get(uid, [])
    if not videos: await msg.reply_text("📭 No files.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Menu", callback_data='b')]])); return
    pp, tp = 5, max(1, (len(videos)+4)//5); page = max(0, min(page, tp-1)); pv = videos[page*pp:(page+1)*pp]
    emoji_map = {'video': '🎬', 'audio': '🎵', 'thumb': '🖼️'}
    txt = f"📹 Downloads ({page+1}/{tp})\n\n"
    for i, v in enumerate(pv, page*pp+1):
        ex = "✅" if Path(v.file_path).exists() else "🗑️"
        txt += f"{ex} {emoji_map.get(v.media_type, '📹')} {i}. {esc(v.title[:50])}\n   📦 {v.file_size/1024/1024:.2f}MB | {v.download_time}\n\n"
    txt += f"⚠️ {bot.config.STORAGE_DAYS}d retention."
    kb = []
    for i, v in enumerate(pv, page*pp+1):
        if Path(v.file_path).exists(): kb.append([InlineKeyboardButton(f"{emoji_map.get(v.media_type,'📹')} {i}. {v.title[:40]}", callback_data=f'sel_{page*pp+(i-page*pp-1)}')])
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
    elif d == 'cs': await q.message.reply_text("✅ Cookies active" if uid in bot._cookie_data else "❌ Upload with /cookies")
    elif d == 'vc': await q.message.reply_text(f"📦 {len(bot.videos.get(uid,[]))} files")
    elif d == 'clear_all': await _clear_all(bot, u, c)
    elif d.startswith('fmt_'): from app.handlers.formats import choose_format; await choose_format(bot, u, c)
    elif d.startswith('backfmt_'): from app.handlers.formats import back_to_formats; await back_to_formats(bot, u, c)
    elif d.startswith('tg_'): from app.handlers.formats import send_telegram; await send_telegram(bot, u, c)
    elif d.startswith('lk_'): from app.handlers.formats import send_link; await send_link(bot, u, c)
    elif d.startswith('sel_'): await _select(bot, u, c)
    elif d.startswith('d_'): await _delete(bot, u, c)
    elif d.startswith('p_'): await show_recent(bot, u, c, int(d.split('_')[1]))

async def _clear_all(bot, u, c):
    q = u.callback_query; uid = u.effective_user.id
    videos = bot.videos.get(uid, []); count = len(videos)
    for v in videos: Path(v.file_path).unlink(missing_ok=True)
    bot.videos.pop(uid, None); bot.save()
    await q.message.reply_text(f"🗑️ {count} files cleared.", reply_markup=menu(bot, uid))

async def _select(bot, u, c):
    q = u.callback_query; uid, idx = u.effective_user.id, int(q.data.split('_')[1])
    videos = bot.videos.get(uid, [])
    if 0 <= idx < len(videos): nav_push(bot, uid, NAV_RECENT); from app.handlers.formats import show_delivery; await show_delivery(bot, q.message, videos[idx], idx); await q.message.delete()

async def _delete(bot, u, c):
    q = u.callback_query; uid, idx = u.effective_user.id, int(q.data.split('_')[1])
    videos = bot.videos.get(uid, [])
    if 0 <= idx < len(videos): Path(videos[idx].file_path).unlink(missing_ok=True); videos.pop(idx); bot.save()
    await q.message.reply_text("🗑️ Deleted.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("📹 Videos", callback_data='r'), InlineKeyboardButton("🔙 Menu", callback_data='b')]]))