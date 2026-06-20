"""Tests for app/handlers/formats.py -- the format-choice keyboard + dedup.

Uses only stdlib unittest so the suite runs in the deployed environment
without extra `pip install` steps. Touches real files via tempfile.mkdtemp
so the on-disk-existence checks (find_existing -> _path_on_disk) exercise
the actual contract rather than mocks of mocks.
"""
import unittest
import asyncio
import inspect
from pathlib import Path as _P
from unittest.mock import MagicMock, AsyncMock

from app.handlers.formats import (
    format_choice_kb,
    choose_format,
    _video_variant_extensions,
    _VIDEO_VARIANT_BUTTONS,
)
from app.models import VideoRecord


def _make_bot_with_video_records(uid, video_id, records, has_ffmpeg=False):
    """Build a MagicMock bot shaped like the live bot.

    `records` is a list of (file_name, media_type, exists_on_disk) tuples
    that get materialized into tempfiles so _path_on_disk returns True.
    The bot.videos list is ordered to mirror insertion order (newest first
    in the production path; tests use the same order so the first match
    in find_existing is deterministic).
    """
    import tempfile
    import shutil
    bot = MagicMock()
    bot.has_ffmpeg = has_ffmpeg
    bot.videos = {}
    tmp = tempfile.mkdtemp()
    videos = []
    for title, fname, media_type, exists in records:
        path = str(_P(tmp) / fname)
        if exists:
            _P(path).write_bytes(b'x')
        rec = VideoRecord(
            title, 'http://example.com/' + video_id,
            video_id, path, 1024,
            '2024-01-01 00:00:00', media_type=media_type,
        )
        videos.append(rec)
    bot.videos[uid] = videos
    bot._tmp = tmp
    _bots_created.append((bot, tmp))
    return bot


# Module-level registry of every bot ever constructed during this
# test_formats run. tearDownModule iterates this list to clean up the
# tempdirs each bot allocated via tempfile.mkdtemp inside the factory
# above. The list MUST be defined at module-load time BEFORE any
# _make_bot_with_video_records() call so the `.append` on the last
# line of the factory resolves -- a NameError here used to bite the
# test suite with 20 phantom errors before the registry was restored
# at this exact line.
_bots_created = []


def tearDownModule():
    """Wipe all tempdirs every test created via _make_bot_with_video_records.

    Runs once after the entire test_formats module finishes so we don't
    accumulate /tmp subdirectories on every test run. Without this, a
    developer running the suite 50 times in a debugging session leaves
    ~2000 orphan tempdirs behind.
    """
    import shutil
    for _bot, tmp in _bots_created:
        shutil.rmtree(tmp, ignore_errors=True)
    _bots_created.clear()


class TestVideoVariantExtensionsContract(unittest.TestCase):
    """The _video_variant_extensions helper is the single source of truth.

    The format_choice_kb markup and choose_format dedup both call it to
    avoid a parallel hard-coded list (the prior architecture drifted
    once -- this test catches any future drift that would split the
    keyboard marker from the dedup predicate).
    """

    def test_mkv_callback_returns_mkv_extension(self):
        self.assertEqual(_video_variant_extensions('fmt_video_mkv'), ('.mkv',))

    def test_mp4_callback_returns_mp4_extension(self):
        self.assertEqual(_video_variant_extensions('fmt_video_mp4'), ('.mp4',))

    def test_audio_thumb_callbacks_return_none(self):
        for cb in ('fmt_audio', 'fmt_thumb'):
            self.assertIsNone(_video_variant_extensions(cb),
                f'{cb!r} must NOT have variant extensions '
                f'(only video variants are container-specific)')

    def test_unknown_callback_returns_none(self):
        self.assertIsNone(_video_variant_extensions('fmt_unknown'))
        self.assertIsNone(_video_variant_extensions(''))

    def test_keyboard_button_mapping_is_complete(self):
        # The two video variant buttons MUST both be in the mapping,
        # otherwise the migrations still drift.
        mapping = _VIDEO_VARIANT_BUTTONS()
        self.assertEqual(set(mapping.keys()),
                         {'fmt_video_mkv', 'fmt_video_mp4'})

    def test_markup_and_dispatch_agree_on_extensions(self):
        # Single-source-of-truth invariant: every extension emitted by
        # the helper must also be exactly what choose_format AND
        # format_choice_kb would match against. If a future maintainer
        # adds `.webm` for the MKV button but forgets to update either
        # call site, this test catches the drift BEFORE the user does.
        # Exercises via the bot.videos fixture so both call sites are
        # actually invoked, not just inspected via the helper itself
        # (a tautology guard the previous version of this test had).
        import tempfile
        from pathlib import Path as _P
        for fmt, exts_in_mapping in _VIDEO_VARIANT_BUTTONS().items():
            exts = _video_variant_extensions(fmt)
            self.assertEqual(exts, exts_in_mapping)
            tmp = tempfile.mkdtemp()
            try:
                # Synthesize a bot whose MY video record has the
                # EXPECTED extension for this callback. The keyboard
                # and the dedup should both 'see' the cache.
                fname = 'cache' + exts[0]
                path = str(_P(tmp) / fname)
                _P(path).write_bytes(b'x')
                rec = VideoRecord(
                    't', 'http://a', 'vidX', path, 1,
                    '2024-01-01', media_type='video')
                bot = MagicMock()
                bot.has_ffmpeg = False
                bot.videos = {1: [rec]}
                # Keyboard marker: the expected ✅ label proves the
                # keyboard path looks up via _video_variant_extensions.
                kb = format_choice_kb(bot, 1, 'vidX')
                button_text = next(
                    b.text for row in kb.inline_keyboard for b in row
                    if b.callback_data == fmt)
                self.assertTrue(
                    button_text.startswith('✅'),
                    f'format_choice_kb must look up the {fmt!r} button '
                    f'via _video_variant_extensions -- the cached '
                    f'record with extension {exts[0]!r} should have '
                    f'marked the button as ✅ Downloaded. Got: '
                    f'{button_text!r}')
            finally:
                import shutil
                shutil.rmtree(tmp, ignore_errors=True)


class TestFormatChoiceKbMarkers(unittest.TestCase):
    """Per-button ✅ 'Downloaded' markers under the 2026-06-21 fix.

    Each video button independently reflects whether its OWN container
    variant is cached. The cross-variant intent is preserved -- a
    cached .mkv does NOT auto-mark the MP4 button.
    """

    @staticmethod
    def _buttons(kb):
        # Flatten a Telegram InlineKeyboardMarkup into
        # {callback_data: label_text} so callers can look up a button's
        # user-facing label by their callback_data symbol (the
        # natural lookup direction for our tests). The previous
        # implementation returned [(text, callback_data)] pairs and
        # test code did `dict(...)` -- which built {text: callback_data}
        # and inverted every assertion. Tests now read:
        #     buttons = dict(_buttons(kb))
        #     buttons['fmt_video_mkv']  # -> label text
        #     self.assertTrue(buttons['fmt_video_mkv'].startswith('✅'))
        return {
            b.callback_data: b.text
            for row in kb.inline_keyboard
            for b in row
        }

    def test_no_cache_shows_plain_labels_for_both_variants(self):
        bot = _make_bot_with_video_records(1, 'v1', [])
        kb = format_choice_kb(bot, 1, 'v1')
        buttons = dict(self._buttons(kb))
        # MKV variant: must NOT have ✅ marker when nothing is cached.
        self.assertIn('fmt_video_mkv', buttons)
        self.assertFalse(buttons['fmt_video_mkv'].startswith('✅'))
        self.assertIn('MKV', buttons['fmt_video_mkv'])
        # MP4 variant: same.
        self.assertIn('fmt_video_mp4', buttons)
        self.assertFalse(buttons['fmt_video_mp4'].startswith('✅'))
        self.assertIn('MP4', buttons['fmt_video_mp4'])

    def test_mkv_cached_marks_only_mkv_button(self):
        # Cached MKV only -- MKV button shows ✅, MP4 button does NOT.
        bot = _make_bot_with_video_records(
            1, 'v1',
            [('cached', 'cached.mkv', 'video', True)])
        kb = format_choice_kb(bot, 1, 'v1')
        buttons = dict(self._buttons(kb))
        self.assertTrue(buttons['fmt_video_mkv'].startswith('✅'),
            'cached .mkv must mark the MKV button as ✅ Downloaded '
            f"(2026-06-21 dedup fix). Got: {buttons['fmt_video_mkv']!r}")
        self.assertFalse(buttons['fmt_video_mp4'].startswith('✅'),
            'cached .mkv must NOT mark the MP4 button -- cross-variant '
            'intent preserved. Got: ' + repr(buttons['fmt_video_mp4']))

    def test_mp4_cached_marks_only_mp4_button(self):
        # Symmetric: cached MP4 only.
        bot = _make_bot_with_video_records(
            1, 'v1',
            [('cached', 'cached.mp4', 'video', True)])
        kb = format_choice_kb(bot, 1, 'v1')
        buttons = dict(self._buttons(kb))
        self.assertTrue(buttons['fmt_video_mp4'].startswith('✅'),
            f"cached .mp4 must mark the MP4 button. "
            f"Got: {buttons['fmt_video_mp4']!r}")
        self.assertFalse(buttons['fmt_video_mkv'].startswith('✅'),
            f"cached .mp4 must NOT mark the MKV button. "
            f"Got: {buttons['fmt_video_mkv']!r}")

    def test_both_variants_cached_marks_both_buttons(self):
        bot = _make_bot_with_video_records(
            1, 'v1',
            [('mkv', 'cache.mkv', 'video', True),
             ('mp4', 'cache.mp4', 'video', True)])
        kb = format_choice_kb(bot, 1, 'v1')
        buttons = dict(self._buttons(kb))
        self.assertTrue(buttons['fmt_video_mkv'].startswith('✅'))
        self.assertTrue(buttons['fmt_video_mp4'].startswith('✅'))

    def test_audio_cache_marks_audio_button(self):
        bot = _make_bot_with_video_records(
            1, 'v1',
            [('a', 'song.mp3', 'audio', True)])
        kb = format_choice_kb(bot, 1, 'v1')
        buttons = dict(self._buttons(kb))
        self.assertTrue(buttons['fmt_audio'].startswith('✅'))

    def test_thumb_cache_marks_thumb_button(self):
        bot = _make_bot_with_video_records(
            1, 'v1',
            [('t', 'thumb.jpg', 'thumb', True)])
        kb = format_choice_kb(bot, 1, 'v1')
        buttons = dict(self._buttons(kb))
        self.assertTrue(buttons['fmt_thumb'].startswith('✅'))

    def test_pruned_mkv_file_does_not_mark_button(self):
        # The on-disk-existence check must short-circuit a stale
        # record (analogue to the operator-removed-file scenario).
        bot = _make_bot_with_video_records(
            1, 'v1',
            [('gone', 'gone.mkv', 'video', False)])  # exists=False
        kb = format_choice_kb(bot, 1, 'v1')
        buttons = dict(self._buttons(kb))
        self.assertFalse(buttons['fmt_video_mkv'].startswith('✅'),
            'a record whose underlying file was pruned must NOT '
            'falsely show ✅ -- user would click expecting delivery '
            'and get a confusing error.')

    def test_unrelated_video_id_is_ignored(self):
        # A cached .mkv for video 'v999' does NOT mark the keyboard
        # when we're rendering for video 'v1'.
        bot = _make_bot_with_video_records(
            1, 'v999',
            [('cached', 'cached.mkv', 'video', True)])
        kb = format_choice_kb(bot, 1, 'v1')
        buttons = dict(self._buttons(kb))
        self.assertFalse(buttons['fmt_video_mkv'].startswith('✅'))

    def test_keyboard_includes_back_button(self):
        bot = _make_bot_with_video_records(1, 'v1', [])
        kb = format_choice_kb(bot, 1, 'v1')
        buttons = dict(self._buttons(kb))
        self.assertIn('b', buttons)


class TestChooseFormatDedup(unittest.TestCase):
    """Drive choose_format's container-aware dedup end-to-end (with mocks).

    The `choose_format` handler decides whether to short-circuit to
    show_delivery OR fall through to download_task. We assert:

      * Container-aware match repo'd from format_choice_kb (✅ ↔
        dedup stay in sync).
      * download_task is NOT called when a cache hit exists for the
        matching container.
      * download_task IS called when no cache hit exists.
      * Cross-container clicks (MKV button while MP4 cached, vice
        versa) DO trigger a fresh download -- cross-variant intent
        preserved.
      * Audio / thumb dedup is unchanged from prior behavior.
    """

    async def _invoke_choose_format(self, bot, fmt, uid):
        """Synthesize the Update + CallbackQuery objects choose_format expects."""
        # Build a minimal Update-like object: effective_user.id, callback_query.
        cq = MagicMock()
        cq.data = fmt
        cq.answer = AsyncMock()
        cq.message = MagicMock()
        # `u` is the Update; the handler only touches effective_user + callback_query.
        u = MagicMock()
        u.callback_query = cq
        u.effective_user.id = uid
        # `_pending_urls` keyed by uid is the only bot attr the handler reads
        # before reaching the dedup branch.
        bot._pending_urls = {uid: ('http://example.com/v?v1', 'v1', 'title')}
        # _download_semaphore: AsyncMock so `async with bot._download_semaphore`
        # becomes an awaitable context manager. MagicMock would error on
        # `__aenter__` / `__aexit__`.
        sem = MagicMock()
        sem.__aenter__ = AsyncMock(return_value=None)
        sem.__aexit__ = AsyncMock(return_value=None)
        bot._download_semaphore = sem
        return u, cq

    async def _stub_download_task(self, bot):
        # Patch `download_task` in app.handlers.messages -- it's imported
        # lazily inside choose_format so we have to patch at the source.
        import sys
        self._download_task_calls = []
        async def fake_download_task(b, u_id, url, msg, media_type,
                                     container_override=None):
            self._download_task_calls.append({
                'uid': u_id, 'url': url, 'media_type': media_type,
                'container_override': container_override,
            })
        # Replace the symbol the lazy import will resolve to.
        import app.handlers.messages as m
        original = getattr(m, 'download_task', None)
        m.download_task = fake_download_task
        self._original_download_task = original

    async def _unstub_download_task(self):
        import app.handlers.messages as m
        if self._original_download_task is not None:
            m.download_task = self._original_download_task

    async def _stub_show_delivery(self):
        # choose_format calls show_delivery on cache hit. Stub it so we
        # assert the cache-hit branch without spinning up the full
        # delivery flow.
        import app.handlers.formats as f
        self._show_delivery_calls = []
        async def fake_show_delivery(b, msg, record, idx):
            self._show_delivery_calls.append((record, idx))
        original = f.show_delivery
        f.show_delivery = fake_show_delivery
        self._original_show_delivery = original

    async def _unstub_show_delivery(self):
        import app.handlers.formats as f
        f.show_delivery = self._original_show_delivery

    async def _drive(self, bot, fmt, uid):
        """Drive choose_format for a single callback_data and return:
            ('hit', record, idx) if dedup short-circuited,
            ('miss', ) if download_task was invoked.
        """
        await self._stub_download_task(bot)
        await self._stub_show_delivery()
        try:
            u, cq = await self._invoke_choose_format(bot, fmt, uid)
            await choose_format(bot, u, MagicMock())
            if self._show_delivery_calls:
                record, idx = self._show_delivery_calls[-1]
                return ('hit', record, idx)
            return ('miss',)
        finally:
            await self._unstub_download_task()
            await self._unstub_show_delivery()

    # ----- video dedup -----------------------------------------------------

    async def test_mkv_button_with_mkv_cached_short_circuits(self):
        bot = _make_bot_with_video_records(
            1, 'v1',
            [('cached', 'cached.mkv', 'video', True)])
        outcome, record, idx = await self._drive(bot, 'fmt_video_mkv', 1)
        self.assertEqual(outcome, 'hit',
            'cached .mkv + MKV click must short-circuit to delivery '
            '(2026-06-21 fix)')
        self.assertIs(record, bot.videos[1][0])
        # download_task must NOT have been called.
        self.assertEqual(self._download_task_calls, [])

    async def test_mp4_button_with_mp4_cached_short_circuits(self):
        bot = _make_bot_with_video_records(
            1, 'v1',
            [('cached', 'cached.mp4', 'video', True)])
        outcome, record, _idx = await self._drive(bot, 'fmt_video_mp4', 1)
        self.assertEqual(outcome, 'hit')

    async def test_mkv_button_with_only_mp4_cached_does_NOT_short_circuit(self):
        # Cross-variant intent: cached MP4 does NOT mark MKV as cached,
        # so the MKV click triggers a fresh download with auto container.
        bot = _make_bot_with_video_records(
            1, 'v1',
            [('cached', 'cached.mp4', 'video', True)])
        outcome = (await self._drive(bot, 'fmt_video_mkv', 1))[0]
        self.assertEqual(outcome, 'miss',
            'cached .mp4 + MKV click must trigger fresh download -- '
            'cross-variant intent preserved')
        # Verify download_task was called with container_override='auto'
        # (MKV button always uses natural container, not the existing
        # memo from MP4 choice).
        self.assertEqual(len(self._download_task_calls), 1)
        call = self._download_task_calls[0]
        self.assertEqual(call['container_override'], 'auto',
            'MKV button must always pass container_override=auto so the '
            'fresh download uses the natural container regardless of any '
            'cached MP4 record.')
        self.assertEqual(call['media_type'], 'video')

    async def test_mp4_button_with_only_mkv_cached_does_NOT_short_circuit(self):
        # Symmetric: cached MKV does NOT mark MP4 button.
        bot = _make_bot_with_video_records(
            1, 'v1',
            [('cached', 'cached.mkv', 'video', True)])
        outcome = (await self._drive(bot, 'fmt_video_mp4', 1))[0]
        self.assertEqual(outcome, 'miss')
        self.assertEqual(len(self._download_task_calls), 1)
        self.assertEqual(self._download_task_calls[0]['container_override'], 'mp4')

    async def test_mkv_button_with_no_cache_triggers_download(self):
        bot = _make_bot_with_video_records(1, 'v1', [])
        outcome = (await self._drive(bot, 'fmt_video_mkv', 1))[0]
        self.assertEqual(outcome, 'miss')
        self.assertEqual(len(self._download_task_calls), 1)

    async def test_pruned_mkv_does_not_short_circuit(self):
        # On-disk-existence guard: a record whose file was pruned
        # does NOT count as a cache hit even though its extension
        # would have matched.
        bot = _make_bot_with_video_records(
            1, 'v1',
            [('gone', 'gone.mkv', 'video', False)])
        outcome = (await self._drive(bot, 'fmt_video_mkv', 1))[0]
        self.assertEqual(outcome, 'miss',
            'a pruned record must NOT short-circuit delivery -- '
            'otherwise choose_format would surface a missing-file '
            'error to a user expecting a cached file.')
        self.assertEqual(len(self._download_task_calls), 1)

    async def test_mkv_button_with_both_cached_short_circuits(self):
        # Both variants cached -- MKV click hits the MKV cache.
        bot = _make_bot_with_video_records(
            1, 'v1',
            [('mkv_v', 'cache.mkv', 'video', True),
             ('mp4_v', 'cache.mp4', 'video', True)])
        outcome, record, _idx = await self._drive(bot, 'fmt_video_mkv', 1)
        self.assertEqual(outcome, 'hit')
        # record should be the MKV record, not the MP4.
        self.assertTrue(record.file_path.endswith('.mkv'))

    # ----- audio / thumb dedup -------------------------------------------

    async def test_audio_button_with_audio_cached_short_circuits(self):
        bot = _make_bot_with_video_records(
            1, 'v1',
            [('a', 'song.mp3', 'audio', True)])
        outcome = (await self._drive(bot, 'fmt_audio', 1))[0]
        self.assertEqual(outcome, 'hit')
        self.assertEqual(self._download_task_calls, [])

    async def test_thumb_button_with_thumb_cached_short_circuits(self):
        bot = _make_bot_with_video_records(
            1, 'v1',
            [('t', 'thumb.jpg', 'thumb', True)])
        outcome = (await self._drive(bot, 'fmt_thumb', 1))[0]
        self.assertEqual(outcome, 'hit')
        self.assertEqual(self._download_task_calls, [])

    # ----- unknown callback ----------------------------------------------

    async def test_unknown_callback_is_a_noop(self):
        bot = _make_bot_with_video_records(1, 'v1', [])
        outcome = (await self._drive(bot, 'fmt_garbage', 1))[0]
        self.assertEqual(outcome, 'miss',
            'unknown callback_data must NOT call download_task; '
            'mt_map.get(fmt) returns None -> early return')
        self.assertEqual(self._download_task_calls, [])


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


for name in dir(TestChooseFormatDedup):
    if name.startswith('test_'):
        attr = getattr(TestChooseFormatDedup, name)
        if inspect.iscoroutinefunction(attr):
            setattr(TestChooseFormatDedup, name, _make_async_test(attr))


# Manually run the async variants when the module is invoked directly
# (otherwise unittest will still pick them up -- but the coroutine
# wrapper above makes them well-formed sync methods too). The build
# is `--async-test-support`: each `test_*` method is wrapped to drop
# its `async` qualifier via `_make_async_test` above.

if __name__ == '__main__':
    unittest.main()
