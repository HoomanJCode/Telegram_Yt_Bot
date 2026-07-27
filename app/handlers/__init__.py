"""Handlers package for the YouTube Downloader Telegram Bot.

This package contains all Telegram update handlers. Each module groups a
related set of interactions:

  * commands.py   - slash commands such as /start, /help, /status, /recent.
  * cookies.py    - the /cookies conversation for uploading Netscape cookies.
  * formats.py    - format-choice keyboards and delivery method dispatch.
  * inline.py     - inline query support via @botname <link>.
  * messages.py   - plain-text link processing and the main download task.
  * navigation.py - menu keyboards, back-stack navigation, and settings.
  * tokens.py     - shared /start dl_<token> deep links and file delivery.

All handlers receive the shared `bot` instance as their first argument so
that per-user state (cookies, settings, recent downloads) is available without
using global variables.

AI RULE: If you modify this file, you must also update and fix the comments,
docstrings, and descriptions to keep them accurate and current. Every function
must have a descriptive docstring explaining its purpose, parameters, and
return values. Inline comments should explain WHY, not WHAT.
"""
