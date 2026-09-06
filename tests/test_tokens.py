"""Tests for app/handlers/tokens.py -- file delivery via Telegram.

Uses only stdlib unittest so the suite runs in the deployed environment
without extra `pip install` steps. Touches real files via tempfile so the
on-disk size guard (send_file -> Path.stat) exercises the actual contract
rather than mocks of mocks.

2026-09-06 regression: the size guard compared `mb` (a MiB float) against
`MAX_TELEGRAM_FILE_SIZE` (stored in BYTES), so it never fired and every
>50 MB video attempted a Telegram upload, was rejected by the API, and
surfaced as a bare "❌ Failed." instead of the download-link fallback.
"""
import asyncio
import inspect
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, AsyncMock

from app.models import VideoRecord
from app.handlers.tokens import send_file


def _make_async_test(coro_fn):
    """Wrap an async test method so unittest can run it synchronously.

    stdlib unittest does NOT natively run `async def` test methods;
    without this wrapper, every such method returns a never-awaited
    coroutine object to unittest's result-collector, which silently
    treats it as None -- the test reports OK without ever executing
    the assertions. Wrapping in `asyncio.run(...)` actually drives
    the coroutine to completion so the asserts inside actually fire.
    """
    def wrapped(self):
        return asyncio.run(coro_fn(self))
    return wrapped


def _build_bot(limit_bytes):
    """Minimal bot shaped like the live YouTubeDownloaderBot.

    `_global_file_ids` must be a real dict (send_file writes to it after
    a successful upload); `config.MAX_TELEGRAM_FILE_SIZE` is the only
    config attr the delivery path reads before the Telegram API call.
    """
    bot = MagicMock()
    bot._global_file_ids = {}
    bot.config = MagicMock()
    bot.config.MAX_TELEGRAM_FILE_SIZE = limit_bytes
    bot.base_url = 'http://example.com:8000'
    bot.save = MagicMock()
    return bot


def _build_msg():
    """Message stub with AsyncMock media/reply methods.

    reply_video returns an object whose .video.file_id is used to seed
    the file_id caches, mirroring the real Telegram response shape.
    """
    msg = MagicMock()
    msg.message_id = 100
    msg.reply_text = AsyncMock()
    sent = MagicMock()
    sent.video.file_id = 'FAKE_VIDEO_FILE_ID'
    msg.reply_video = AsyncMock(return_value=sent)
    msg.reply_audio = AsyncMock()
    msg.reply_photo = AsyncMock()
    return msg


class TestSendFileSizeGuard(unittest.TestCase):
    """Regression: oversized files must NOT attempt the Telegram upload.

    Before the 2026-09-06 fix the guard compared `mb > MAX_TELEGRAM_FILE_SIZE`
    (MiB float vs byte count), which never fired. The oversized path is now
    bytes-vs-bytes so a file over the limit is surfaced as a download-link
    fallback instead of a rejected upload.
    """

    async def test_oversized_file_gets_download_link_not_upload(self):
        with tempfile.TemporaryDirectory() as tmp:
            fp = str(Path(tmp) / 'big.mp4')
            Path(fp).write_bytes(b'x' * 1024 * 1024)  # 1 MiB file
            rec = VideoRecord(
                'Big', 'http://example.com/v?x', 'vid1', fp, 1024 * 1024,
                '2024-01-01 00:00:00', media_type='video')
            bot = _build_bot(limit_bytes=1024)  # 1 KiB limit < 1 MiB file
            msg = _build_msg()
            await send_file(bot, msg, rec)
            # Must NOT attempt the Telegram upload...
            msg.reply_video.assert_not_called()
            # ...and must surface the download-link fallback instead.
            self.assertTrue(msg.reply_text.called)
            text = msg.reply_text.call_args[0][0]
            self.assertIn('Too large', text)
            self.assertIn('http://example.com:8000', text)

    async def test_undersized_file_is_uploaded(self):
        with tempfile.TemporaryDirectory() as tmp:
            fp = str(Path(tmp) / 'small.mp4')
            Path(fp).write_bytes(b'x' * 100)  # 100-byte file
            rec = VideoRecord(
                'Small', 'http://example.com/v?y', 'vid2', fp, 100,
                '2024-01-01 00:00:00', media_type='video')
            bot = _build_bot(limit_bytes=1024 * 1024)  # 1 MiB limit > file
            msg = _build_msg()
            await send_file(bot, msg, rec)
            msg.reply_video.assert_called_once()
            # Successful upload seeds the global file_id cache.
            self.assertEqual(
                bot._global_file_ids.get('vid2:video'), 'FAKE_VIDEO_FILE_ID')

    async def test_missing_file_reports_deleted_not_upload(self):
        with tempfile.TemporaryDirectory() as tmp:
            fp = str(Path(tmp) / 'gone.mp4')  # never created
            rec = VideoRecord(
                'Gone', 'http://example.com/v?z', 'vid3', fp, 0,
                '2024-01-01 00:00:00', media_type='video')
            bot = _build_bot(limit_bytes=1024 * 1024)
            msg = _build_msg()
            await send_file(bot, msg, rec)
            msg.reply_video.assert_not_called()
            self.assertTrue(msg.reply_text.called)
            text = msg.reply_text.call_args[0][0]
            self.assertIn('deleted', text.lower())


for name in dir(TestSendFileSizeGuard):
    if name.startswith('test_'):
        attr = getattr(TestSendFileSizeGuard, name)
        if inspect.iscoroutinefunction(attr):
            setattr(TestSendFileSizeGuard, name, _make_async_test(attr))


if __name__ == '__main__':
    unittest.main()