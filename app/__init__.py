"""YouTube Downloader Telegram Bot"""
import asyncio
import logging
from telegram import Update
from telegram.ext import (
    Application, CommandHandler, MessageHandler, CallbackQueryHandler,
    ConversationHandler, filters, InlineQueryHandler
)
from telegram.constants import ParseMode
from app.fileserver import FileServer
from app.bot import YouTubeDownloaderBot
from app.handlers.commands import start_cmd, help_cmd, recent_cmd, status_cmd, cancel_cmd, settings_cmd
from app.handlers.messages import on_msg
from app.handlers.inline import inline_query
from app.handlers.cookies import ask_cookies, recv_cookies

logging.basicConfig(format='%(asctime)s - %(levelname)s - %(message)s', level=logging.WARNING)
for lib in ('httpx', 'httpcore', 'telegram', 'telegram.ext', 'aiohttp'):
    logging.getLogger(lib).setLevel(logging.WARNING)

logger = logging.getLogger('yt_bot')
logger.setLevel(logging.INFO)
h = logging.StreamHandler()
h.setFormatter(logging.Formatter('%(asctime)s - %(levelname)s - %(message)s'))
logger.addHandler(h)
logger.propagate = False

WAITING_FOR_COOKIES = 1

def main():
    bot = YouTubeDownloaderBot()
    app = Application.builder().token(bot.config.BOT_TOKEN).build()
    bot._bot = app.bot

    app.add_handler(CommandHandler('start', lambda u, c: start_cmd(bot, u, c)))
    app.add_handler(CommandHandler('help', lambda u, c: help_cmd(bot, u, c)))
    app.add_handler(CommandHandler('settings', lambda u, c: settings_cmd(bot, u, c)))
    app.add_handler(CommandHandler('recent', lambda u, c: recent_cmd(bot, u, c)))
    app.add_handler(CommandHandler('status', lambda u, c: status_cmd(bot, u, c)))
    app.add_handler(ConversationHandler(
        entry_points=[CommandHandler('cookies', lambda u, c: ask_cookies(bot, u, c)),
                      CallbackQueryHandler(lambda u, c: ask_cookies(bot, u, c), pattern='^c$')],
        states={WAITING_FOR_COOKIES: [
            MessageHandler(filters.Document.FileExtension("txt"), lambda u, c: recv_cookies(bot, u, c)),
            MessageHandler(filters.TEXT & ~filters.COMMAND, lambda u, c: ask_cookies(bot, u, c))]},
        fallbacks=[CommandHandler('cancel', lambda u, c: cancel_cmd(bot, u, c)),
                   CallbackQueryHandler(bot._router, pattern='^b$')],
        per_message=False))
    app.add_handler(CallbackQueryHandler(bot._router))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, lambda u, c: on_msg(bot, u, c)))
    app.add_handler(InlineQueryHandler(lambda u, c: inline_query(bot, u, c)))

    asyncio.get_event_loop().create_task(bot.file_server.start())
    logger.info("Bot starting...")
    app.run_polling(allowed_updates=Update.ALL_TYPES)