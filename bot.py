#!/usr/bin/env python3
"""
Entry point for the YouTube Downloader Telegram Bot.

When executed directly, this module simply calls `main()` from the `app`
package. The actual bootstrapping (logging, handler wiring, polling loop)
lives in `app/__init__.py::main()`. Keeping this file tiny makes it easy for
operators to run `python bot.py` without worrying about internal wiring.

AI RULE: If you modify this file, you must also update and fix the comments,
docstrings, and descriptions to keep them accurate and current.
"""

# Import the main startup function from the app package. Importing it here
# triggers `app/__init__.py`, which in turn configures logging, builds the
# bot instance, and registers all Telegram handlers.
from app import main

# Guard the `main()` call so that importing this module as a library does not
# accidentally start the bot.
if __name__ == '__main__':
    # Kick off the Telegram polling loop and the aiohttp file server.
    main()
