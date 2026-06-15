"""Command handlers: /start, /help, /recent, /cancel"""
from telegram.constants import ParseMode
from app.handlers.navigation import nav_clear, show_recent

async def start_cmd(bot, u, c):
    uid = u.effective_user.id; args = c.args
    from app.utils import ok
    if not ok(bot, uid): await u.message.reply_text("⛔"); return
    if args and args[0].startswith('dl_'):
        from app.handlers.tokens import handle_token_start
        await handle_token_start(bot, uid, args[0], u.message); return
    nav_clear(bot, uid)
    from app.handlers.navigation import welcome_text
    await u.message.reply_text(await welcome_text(bot), reply_markup=_menu(bot, uid))

async def help_cmd(bot, u, c):
    from app.handlers.navigation import menu
    await u.message.reply_text("📚 Send YouTube link.\n📱 Inline: @botname <link>\n/cookies /recent", reply_markup=menu(bot, u.effective_user.id))

async def recent_cmd(bot, u, c):
    from app.handlers.navigation import nav_clear
    nav_clear(bot, u.effective_user.id); await show_recent(bot, u, c)

async def cancel_cmd(bot, u, c):
    from app.handlers.navigation import nav_clear, menu
    nav_clear(bot, u.effective_user.id)
    await u.message.reply_text("❌ Cancelled.", reply_markup=menu(bot, u.effective_user.id))
    from app import WAITING_FOR_COOKIES
    return WAITING_FOR_COOKIES  # Actually returns ConversationHandler.END equivalent

def _menu(bot, uid):
    from telegram import InlineKeyboardButton, InlineKeyboardMarkup
    has = uid in bot._cookie_data; vc = len(bot.videos.get(uid, []))
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📹 Recent Downloads", callback_data='r')],
        [InlineKeyboardButton("🍪 Upload Cookies", callback_data='c')],
        [InlineKeyboardButton(f"🍪 {'✅' if has else '❌'}", callback_data='cs'),
         InlineKeyboardButton(f"📦 {vc} files", callback_data='vc')],
    ])