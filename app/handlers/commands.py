"""Command handlers: /start, /help, /recent, /cancel"""
from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from app.handlers.navigation import nav_clear, show_recent

async def start_cmd(bot, u, c):
    uid = u.effective_user.id; args = c.args
    from app.utils import ok
    if not ok(bot, uid): await u.message.reply_text("⛔"); return
    if args and args[0].startswith('dl_'):
        from app.handlers.tokens import handle_token_start
        await handle_token_start(bot, uid, args[0], u.message); return
    from app.handlers.messages import _ensure
    await _ensure(bot, uid)
    nav_clear(bot, uid)
    from app.handlers.navigation import welcome_text, menu
    await u.message.reply_text(await welcome_text(bot), reply_markup=menu(bot, uid))

async def help_cmd(bot, u, c):
    from app.handlers.navigation import menu
    await u.message.reply_text("📚 Send YouTube link.\n📱 Inline: @botname <link>\n/cookies /recent", reply_markup=menu(bot, u.effective_user.id))

async def recent_cmd(bot, u, c):
    nav_clear(bot, u.effective_user.id); await show_recent(bot, u, c)

async def cancel_cmd(bot, u, c):
    nav_clear(bot, u.effective_user.id)
    from app.handlers.navigation import menu
    from telegram.ext import ConversationHandler
    await u.message.reply_text("❌ Cancelled.", reply_markup=menu(bot, u.effective_user.id))
    return ConversationHandler.END