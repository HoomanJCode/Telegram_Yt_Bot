"""Cookie upload conversation handler"""
import logging
import os
from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ConversationHandler

logger = logging.getLogger('yt_bot')


async def ask_cookies(bot, u, c):
    from app import WAITING_FOR_COOKIES
    from app.utils import ok
    from app.handlers.navigation import menu
    uid = u.effective_user.id
    msg = u.callback_query.message if u.callback_query else u.message

    # Layer 1: WHITELIST gate. Non-whitelisted → end the conversation
    # cleanly so the next text message isn't swallowed. Same semantics as
    # before; explicit ConversationHandler.END is correct here.
    if not ok(bot, uid):
        return ConversationHandler.END

    # Layer 2: ADMIN_USERS gate. When admin gating is configured, only
    # listed uids can upload cookies. Reject non-admins with a clear
    # message + return ConversationHandler.END so the next YouTube link
    # they paste goes to the normal download handler, NOT back into
    # `ask_cookies` (which would re-emit the rejection on every text
    # message until the user manually /cancel's).
    from config import Config
    if not Config.is_admin(uid):
        logger.info('cookies upload rejected for non-admin uid=%d', uid)
        await msg.reply_text(
            '🔒 Cookie uploads are admin-only. Ask the bot admin '
            'to upload the cookie file.',
            reply_markup=menu(bot, uid),
        )
        return ConversationHandler.END

    await msg.reply_text(
        "🔒 Cookie Info\n\n• RAM only\n• File ID saved for auto-restore\n\n📤 Send cookies.txt:",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Cancel", callback_data='b')]]))
    return WAITING_FOR_COOKIES


async def recv_cookies(bot, u, c):
    from app import WAITING_FOR_COOKIES
    from app.utils import ok
    from app.handlers.navigation import menu
    uid = u.effective_user.id

    # Defensive checks at every state boundary. `ok()` and `is_admin()`
    # are cheap; re-checking on each Document.message is cheap defense
    # against state mid-conversation (e.g. ADMIN_USERS env var edited
    # via redeploy while a non-admin upload was already in flight).
    if not ok(bot, uid):
        return ConversationHandler.END
    from config import Config
    if not Config.is_admin(uid):
        logger.info('cookies upload rejected at recv for non-admin uid=%d', uid)
        await u.message.reply_text(
            '🔒 Cookie uploads are admin-only. Ask the bot admin '
            'to upload the cookie file.',
            reply_markup=menu(bot, uid),
        )
        return ConversationHandler.END

    if not u.message.document:
        await u.message.reply_text(
            "❌ Send .txt file.",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Cancel", callback_data='b')]]))
        return WAITING_FOR_COOKIES

    try:
        doc = u.message.document
        f = await c.bot.get_file(doc.file_id)
        cookie_bytes = await f.download_as_bytearray()
        bot._cookie_data[uid] = bytes(cookie_bytes)
        bot._cookie_file_ids[uid] = doc.file_id
        bot.save()
        if uid in bot._cookie_tmpfiles:
            try: os.unlink(bot._cookie_tmpfiles[uid]); del bot._cookie_tmpfiles[uid]
            except: pass
        await u.message.reply_text(
            "✅ Cookies saved!\n\n🔒 RAM only, auto-restore.",
            reply_markup=menu(bot, uid))
        return ConversationHandler.END
    except Exception:
        return WAITING_FOR_COOKIES