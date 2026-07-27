"""Inline query handler.

This module implements the bot's inline mode: users can type
`@botname <youtube_url>` in any chat and see a set of results that let them
download video, audio, or thumbnail. It also creates short-lived deep-link
tokens (`dl_<token>`) so users can share a download with others or revisit it.

AI RULE: If you modify this file, you must also update and fix the comments,
docstrings, and descriptions to keep them accurate and current. Every function
must have a descriptive docstring explaining its purpose, parameters, and
return values. Inline comments should explain WHY, not WHAT.
"""

import secrets            # Used to generate opaque deep-link tokens.
import time               # Used to record when a token was created.
from uuid import uuid4    # Used to generate unique result ids.
from pathlib import Path  # Used to verify cached files still exist.

from telegram import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    InlineQueryResultArticle,
    InputTextMessageContent,
)
from telegram import (
    InlineQueryResultCachedVideo,
    InlineQueryResultCachedAudio,
    InlineQueryResultCachedPhoto,
)

# Internal utilities and message handler.
from app.utils import extract_url, extract_video_id, find_existing, ok
from app.handlers.messages import _ensure


async def inline_query(bot, u, c):
    """Answer an inline query from a user who typed @botname <link>."""
    try:
        # Get the raw query text and ignore empty queries.
        query = u.inline_query.query.strip()
        if not query:
            return

        # Extract a valid YouTube URL from the query.
        url = extract_url(query)
        if not url:
            return

        uid = u.effective_user.id
        # Whitelist check: only allowed users may use the bot.
        if not ok(bot, uid):
            return

        # Ensure the user has uploaded cookies. If not, direct them to PM.
        if not await _ensure(bot, uid):
            await u.inline_query.answer(
                [],
                switch_pm_text="Upload cookies first",
                switch_pm_parameter="cookies",
            )
            return

        # Identify the video so we can deduplicate cached downloads.
        video_id = extract_video_id(url)

        # Resolve the bot's username once. This is used to build deep links.
        bot_username = await _get_username(bot)

        results = []

        # Iterate over the three supported media types. For each, try to
        # return a cached Telegram file_id result, a ready result, or a
        # pending-download token result.
        for media_type, emoji, label in [
            ('video', '🎬', 'Video (MP4)'),
            ('audio', '🎵', f"Audio ({'MP3' if bot.has_ffmpeg else 'M4A'})"),
            ('thumb', '🖼️', 'Thumbnail'),
        ]:
            cache_key = f"{video_id}:{media_type}"

            # 1) Global file_id cache: if we have previously uploaded this
            # exact video+media_type to Telegram, reuse the cached file_id.
            if cache_key in bot._global_file_ids:
                try:
                    fid = bot._global_file_ids[cache_key]
                    if media_type == 'video':
                        results.append(
                            InlineQueryResultCachedVideo(
                                id=str(uuid4()),
                                video_file_id=fid,
                                title=f"Cached {label}",
                                description="Instant",
                            )
                        )
                    elif media_type == 'audio':
                        results.append(
                            InlineQueryResultCachedAudio(
                                id=str(uuid4()),
                                audio_file_id=fid,
                                title=f"Cached {label}",
                            )
                        )
                    else:
                        results.append(
                            InlineQueryResultCachedPhoto(
                                id=str(uuid4()),
                                photo_file_id=fid,
                                title=f"Cached {label}",
                            )
                        )
                    continue
                except Exception:
                    # If a cached file_id is no longer valid, fall through
                    # to regenerate a fresh token/result.
                    pass

            # 2) Per-user record cache: if this user already downloaded the
            # same media type for this video, offer a ready result.
            existing = find_existing(bot, uid, video_id, media_type)
            if existing and existing.telegram_file_id:
                try:
                    if media_type == 'video':
                        results.append(
                            InlineQueryResultCachedVideo(
                                id=str(uuid4()),
                                video_file_id=existing.telegram_file_id,
                                title=existing.title,
                                description=f"📦 {existing.file_size/1024/1024:.1f} MB",
                            )
                        )
                    elif media_type == 'audio':
                        results.append(
                            InlineQueryResultCachedAudio(
                                id=str(uuid4()),
                                audio_file_id=existing.telegram_file_id,
                                title=existing.title,
                            )
                        )
                    else:
                        results.append(
                            InlineQueryResultCachedPhoto(
                                id=str(uuid4()),
                                photo_file_id=existing.telegram_file_id,
                                title=existing.title,
                            )
                        )
                    # Promote this per-user file_id to the global cache so
                    # other users can reuse it.
                    bot._global_file_ids[cache_key] = existing.telegram_file_id
                    bot.save()
                    continue
                except Exception:
                    pass

            # 3) No cached file: generate a deep-link token. When the user
            # clicks the result, they open a private chat with the bot using
            # a start parameter, which triggers handle_token_start().
            token = secrets.token_hex(4)
            bot._tokens[token] = {
                'uid': uid,
                'url': url,
                'video_id': video_id,
                'media_type': media_type,
                # If the file already exists on disk, mark it completed.
                'status': 'completed' if (existing and Path(existing.file_path).exists()) else 'pending',
                'file_path': existing.file_path if (existing and Path(existing.file_path).exists()) else None,
                'title': existing.title if existing else None,
                'created_at': time.time(),
            }

            if existing and Path(existing.file_path).exists():
                # File is ready: show a "Ready" result with a get-file button.
                results.append(
                    InlineQueryResultArticle(
                        id=str(uuid4()),
                        title=f"{emoji} {label} - Ready",
                        description=f"Click: {existing.title[:50]}",
                        input_message_content=InputTextMessageContent(
                            f"{emoji} {existing.title}"
                        ),
                        reply_markup=InlineKeyboardMarkup([
                            [InlineKeyboardButton(
                                "📥 Get File",
                                url=f"https://t.me/{bot_username}?start=dl_{token}",
                            )]
                        ]),
                    )
                )
            else:
                # File not yet downloaded: show a download button.
                results.append(
                    InlineQueryResultArticle(
                        id=str(uuid4()),
                        title=f"{emoji} Download {label}",
                        description="Click to start download & receive",
                        input_message_content=InputTextMessageContent(
                            f"⏳ Click to start downloading {label}."
                        ),
                        reply_markup=InlineKeyboardMarkup([
                            [InlineKeyboardButton(
                                "📥 Start Download",
                                url=f"https://t.me/{bot_username}?start=dl_{token}",
                            )]
                        ]),
                    )
                )

        # cache_time=0 tells Telegram not to cache the inline results, so
        # subsequent taps always get fresh state (ready vs pending).
        await u.inline_query.answer(results, cache_time=0)
    except Exception:
        # Inline queries must be answered quickly. Swallow any unexpected
        # errors to avoid crashing the bot; the user simply sees no results.
        pass


async def _get_username(bot):
    """Return the bot's Telegram username, caching it on the bot instance."""
    if not bot._bot_username and bot._bot:
        # Fetch from Telegram once and cache for future inline queries.
        me = await bot._bot.get_me()
        bot._bot_username = me.username
    return bot._bot_username or "botname"
