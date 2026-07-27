"""Cookie upload conversation handler.

This module implements the `/cookies` command, which lets a user upload a
Netscape-format cookies.txt file. The file contents are kept in RAM (not on
disk) for security. A temporary file is written only when yt-dlp needs a path
to read cookies from, and that temp file is recreated on demand.

AI RULE: If you modify this file, you must also update and fix the comments,
docstrings, and descriptions to keep them accurate and current. Every function
must have a descriptive docstring explaining its purpose, parameters, and
return values. Inline comments should explain WHY, not WHAT.
"""

import logging
import os
from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ConversationHandler

logger = logging.getLogger('yt_bot')


async def ask_cookies(bot, u, c):
    """Start the /cookies conversation.

    If the user is allowed, the bot prompts them to send a .txt file.
    If not allowed, the conversation is ended cleanly so further messages
    are routed to other handlers instead of being swallowed.
    """
    from app import WAITING_FOR_COOKIES
    from app.utils import ok
    from app.handlers.navigation import menu

    uid = u.effective_user.id
    # Prefer the message from a callback query if present; otherwise use
    # the direct command message.
    msg = u.callback_query.message if u.callback_query else u.message

    # Layer 1: WHITELIST gate. Non-whitelisted users are not allowed to
    # upload cookies. Returning ConversationHandler.END lets the next text
    # message escape this conversation.
    if not ok(bot, uid):
        return ConversationHandler.END

    # Layer 2: ADMIN_USERS gate. When admin gating is configured, only
    # listed uids can upload cookies. Reject non-admins with a clear
    # message and return ConversationHandler.END so the next YouTube link
    # they paste goes to the normal download handler, not back into
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

    # Prompt the user to upload their cookies.txt.
    await msg.reply_text(
        " Cookie Info\n\n• RAM only\n• File ID saved for auto-restore\n\n📤 Send cookies.txt:",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("🔙 Cancel", callback_data='b')]
        ]),
    )
    return WAITING_FOR_COOKIES


async def recv_cookies(bot, u, c):
    """Receive and store the uploaded cookie file.

    Validates that the update is from an allowed user, that it contains a
    .txt document, and then stores the bytes in memory for later use by
    yt-dlp.
    """
    from app import WAITING_FOR_COOKIES
    from app.utils import ok
    from app.handlers.navigation import menu

    uid = u.effective_user.id

    # Re-check authorization at the state boundary in case settings changed
    # mid-conversation (e.g. a redeploy with updated ADMIN_USERS).
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

    # Only accept documents. Re-prompt if the user sent text instead.
    if not u.message.document:
        await u.message.reply_text(
            "❌ Send .txt file.",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton(" Cancel", callback_data='b')]
            ]),
        )
        return WAITING_FOR_COOKIES

    try:
        doc = u.message.document
        # Download the document bytes via Telegram's Bot API.
        f = await c.bot.get_file(doc.file_id)
        cookie_bytes = await f.download_as_bytearray()

        # Store cookies in memory. The raw bytes are used by
        # `downloader._cookie_file()` to write a temp file on demand.
        bot._cookie_data[uid] = bytes(cookie_bytes)
        bot._cookie_file_ids[uid] = doc.file_id
        bot.save()

        # Clean up any previously created temp file so the new cookies are
        # used on the next download.
        if uid in bot._cookie_tmpfiles:
            try:
                os.unlink(bot._cookie_tmpfiles[uid])
                del bot._cookie_tmpfiles[uid]
            except Exception:
                pass

        await u.message.reply_text(
            " Cookies saved!\n\n🔒 RAM only, auto-restore.",
            reply_markup=menu(bot, uid),
        )
        return ConversationHandler.END
    except Exception:
        # On any failure, stay in the conversation and let the user retry.
        return WAITING_FOR_COOKIES
