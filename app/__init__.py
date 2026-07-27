"""YouTube Downloader Telegram Bot package entry point.

This module is responsible for bootstrapping the entire bot:
  1. Configures logging levels for the bot and third-party libraries.
  2. Constructs the shared `YouTubeDownloaderBot` state object.
  3. Wires Telegram command, message, callback, inline and conversation handlers.
  4. Starts the aiohttp file server in the background.
  5. Enters the long-polling loop.

It is imported by `bot.py` and should not be imported casually by other modules
because importing it starts side effects such as logging setup.

AI RULE: If you modify this file, you must also update and fix the comments,
docstrings, and descriptions to keep them accurate and current. Every function
must have a descriptive docstring explaining its purpose, parameters, and
return values. Inline comments should explain WHY, not WHAT.
"""
import asyncio
import logging
import sys

from config import Config
from telegram import Update
from telegram.ext import (
    Application, CommandHandler, MessageHandler, CallbackQueryHandler,
    ConversationHandler, filters, InlineQueryHandler
)
from telegram.constants import ParseMode
from app.fileserver import FileServer
from app.bot import YouTubeDownloaderBot, SSLConfigError
from app.handlers.commands import start_cmd, help_cmd, recent_cmd, status_cmd, cancel_cmd, settings_cmd
from app.handlers.messages import on_msg
from app.handlers.inline import inline_query
from app.handlers.cookies import ask_cookies, recv_cookies

# Root + library logger levels. Root stays at WARNING (caught by
# `logging.basicConfig`) so third-party libraries' DEBUG chatter can't
# accidentally flood journalctl even if the operator dials `yt_bot`
# down to INFO. We DO NOT override the library list here with the
# configured level ? operators troubleshooting Telegram / HTTP issues
# usually want a tight WARNING floor on those dependencies regardless
# of how chatty the bot itself is.
logging.basicConfig(format='%(asctime)s - %(levelname)s - %(message)s', level=logging.WARNING)
for lib in ('httpx', 'httpcore', 'telegram', 'telegram.ext', 'aiohttp'):
    logging.getLogger(lib).setLevel(logging.WARNING)

# Bot's own log level is operator-tunable via the `LOG_LEVEL` env var
# (default: INFO, the historical baseline). Operators on tight VPSes
# historically reported `bot.log` filling their disk; setting
# `LOG_LEVEL=WARNING` in `.env` silences the per-download / per-cookie
# chatter while keeping real warnings / errors visible. Resolved by
# Config.LOG_LEVEL at import time so a bot restart is required for
# changes ? same contract as the other env-driven settings.
logger = logging.getLogger('yt_bot')
logger.setLevel(Config.LOG_LEVEL)
h = logging.StreamHandler()
h.setFormatter(logging.Formatter('%(asctime)s - %(levelname)s - %(message)s'))
logger.addHandler(h)
logger.propagate = False

WAITING_FOR_COOKIES = 1


def main():
    """Build the bot, register handlers, start the file server, and poll."""

    # Catch SSLConfigError BEFORE constructing the Application builder
    # so a mis-configured .env exits cleanly with a single CRITICAL log
    # line, not a Python traceback inside the long-running poller. Exit
    # code 78 (EX_CONFIG from sysexits.h) signals "permanent config
    # error" to systemd; the unit sets RestartPreventExitStatus=78 so
    # this code does NOT trigger the `Restart=always + RestartSec=10`
    # restart loop. Without this, every typo'd cert path would spam
    # the operator's journalctl with one CRITICAL line per 10 seconds.
    try:
        bot = YouTubeDownloaderBot()
    except SSLConfigError as e:
        # The exception message already names the offending env var
        # AND the likely-cause hint (fullchain.pem vs cert.pem;
        # Windows backslashes; etc.) so the operator can fix without
        # context-switching to the docs.
        logger.critical('SSL configuration error ? aborting startup: %s', e)
        # Flush any buffered log records before sys.exit so an operator
        # tail-ing -f bot_error.log sees the message they need to
        # action on, not the next sys.exit shell trace.
        logging.shutdown()
        sys.exit(78)

    # Build the Telegram application using the bot token from environment.
    app = Application.builder().token(bot.config.BOT_TOKEN).build()
    # Keep a reference to the underlying Bot object so inline handlers can
    # resolve usernames and perform file operations without needing an
    # explicit application context.
    bot._bot = app.bot

    # -----------------------------------------------------------------------
    # Handler registration
    # -----------------------------------------------------------------------
    # Each command is wrapped in a small lambda that forwards the shared bot
    # instance to the handler. This avoids global state while keeping the
    # handler signatures simple.
    app.add_handler(CommandHandler('start', lambda u, c: start_cmd(bot, u, c)))
    app.add_handler(CommandHandler('help', lambda u, c: help_cmd(bot, u, c)))
    app.add_handler(CommandHandler('settings', lambda u, c: settings_cmd(bot, u, c)))
    app.add_handler(CommandHandler('recent', lambda u, c: recent_cmd(bot, u, c)))
    app.add_handler(CommandHandler('status', lambda u, c: status_cmd(bot, u, c)))

    # /cookies is a conversation: it asks the user to send a .txt document,
    # then persists the cookie bytes in memory.
    app.add_handler(ConversationHandler(
        entry_points=[CommandHandler('cookies', lambda u, c: ask_cookies(bot, u, c)),
                      CallbackQueryHandler(lambda u, c: ask_cookies(bot, u, c), pattern='^c$')],
        states={WAITING_FOR_COOKIES: [
            MessageHandler(filters.Document.FileExtension("txt"), lambda u, c: recv_cookies(bot, u, c)),
            MessageHandler(filters.TEXT & ~filters.COMMAND, lambda u, c: ask_cookies(bot, u, c))]},
        fallbacks=[CommandHandler('cancel', lambda u, c: cancel_cmd(bot, u, c)),
                   CallbackQueryHandler(bot._router, pattern='^b$')],
        per_message=False))
    # Catch-all callback query router. Registered after the conversation so
    # that more specific patterns take precedence.
    app.add_handler(CallbackQueryHandler(bot._router))

    # Plain-text messages (YouTube links, menu navigation text, etc.).
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, lambda u, c: on_msg(bot, u, c)))

    # Inline query handler for @botname <link> style interactions.
    app.add_handler(InlineQueryHandler(lambda u, c: inline_query(bot, u, c)))

    # Start the aiohttp file server in the same event loop. This server
    # exposes the `downloads/` directory so generated download links work.
    asyncio.get_event_loop().create_task(bot.file_server.start())

    logger.info("Bot starting...")
    # Begin long polling. This call blocks until the process is interrupted.
    app.run_polling(allowed_updates=Update.ALL_TYPES)
