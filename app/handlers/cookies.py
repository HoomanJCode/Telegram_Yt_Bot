"""Cookie upload conversation handler"""
import os
from telegram import InlineKeyboardButton, InlineKeyboardMarkup

async def ask_cookies(bot, u, c):
    from app.utils import ok
    if not ok(bot, u.effective_user.id): return ConversationHandler.END
    msg = u.callback_query.message if u.callback_query else u.message
    await msg.reply_text("🔒 Cookie Info\n\n• RAM only\n• File ID saved for auto-restore\n\n📤 Send cookies.txt:", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Cancel", callback_data='b')]]))
    from app import WAITING_FOR_COOKIES
    return WAITING_FOR_COOKIES

async def recv_cookies(bot, u, c):
    from app.utils import ok
    from app.handlers.navigation import menu
    uid = u.effective_user.id
    if not ok(bot, uid): return ConversationHandler.END
    if not u.message.document: await u.message.reply_text("❌ Send .txt file.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Cancel", callback_data='b')]])); return WAITING_FOR_COOKIES
    try:
        doc = u.message.document; f = await c.bot.get_file(doc.file_id)
        cookie_bytes = await f.download_as_bytearray()
        bot._cookie_data[uid] = bytes(cookie_bytes)
        bot._cookie_file_ids[uid] = doc.file_id; bot.save()
        if uid in bot._cookie_tmpfiles:
            try: os.unlink(bot._cookie_tmpfiles[uid]); del bot._cookie_tmpfiles[uid]
            except: pass
        await u.message.reply_text("✅ Cookies saved!\n\n🔒 RAM only, auto-restore.", reply_markup=menu(bot, uid))
        return ConversationHandler.END
    except: return WAITING_FOR_COOKIES