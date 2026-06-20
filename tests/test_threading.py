"""
Final behavioral + structural regression tests for the video-message
threading feature.

Reviewer feedback applied:

1. Dropped `import pytest` (dead; tests are stdlib-only).
2. Behavioral mock test in TestBehavioralThreadingContract corrected:
   * fetch_info is imported as `from app.downloader import fetch_info`
     at the top of navigation.py, so the mock MUST target
     `app.downloader.fetch_info` (NOT `app.handlers.navigation.fetch_info`,
     which doesn't exist as a module-level binding). Reviewer caught
     this as a silent false-pass.
   * The "status reply" assertion is now a content-match across
     `msg.reply_text.call_args_list`, finding the call whose first
     positional arg contains "Fetching info" before asserting kwarg.
     This is robust to additional reply_text calls added later
     (error paths, etc.) that the test would otherwise have to keep
     re-tuning against.
3. Non-video scope pin list expanded with all _change_* picker
   handlers (the user-facing buttons in /settings menu), _set_language,
   _set_delivery, _select, welcome_text, menu. Without this, a future
   maintainer adding reply_to_message_id to _change_video_quality
   defensively passes silently — defeating the scope pin.
"""
import asyncio
import inspect
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch


# =====================================================================
# STRUCTURAL PINS
# =====================================================================


class TestStructuralPinReplyToMessageId(unittest.TestCase):
    """Documented minimum counts per handler file. A regression
    that drops THREADING at one video-flow site fails this test
    BEFORE deploying.
    """

    MIN_COUNTS = {
        'app/handlers/messages.py': 7,
        'app/handlers/navigation.py': 2,
        'app/handlers/formats.py': 7,
        'app/handlers/tokens.py': 17,
    }

    def _count(self, path: str):
        content = Path(path).read_text(encoding='utf-8')
        return (
            content.count('reply_to_message_id=msg.message_id')
            + content.count('reply_to_message_id=q.message.message_id'),
            content.count('reply_to_message_id=msg.message_id'),
            content.count('reply_to_message_id=q.message.message_id'),
        )

    def test_messages_py_min(self):
        t, m, q = self._count('app/handlers/messages.py')
        self.assertGreaterEqual(
            t, self.MIN_COUNTS['app/handlers/messages.py'],
            f'app/handlers/messages.py threading lost: got {t} '
            f'(msg={m}, q.message={q}); min '
            f'{self.MIN_COUNTS["app/handlers/messages.py"]}.'
        )

    def test_navigation_py_min(self):
        t, m, q = self._count('app/handlers/navigation.py')
        self.assertGreaterEqual(
            t, self.MIN_COUNTS['app/handlers/navigation.py'],
            f'app/handlers/navigation.py threading lost: got {t} '
            f'(msg={m}, q.message={q}); min '
            f'{self.MIN_COUNTS["app/handlers/navigation.py"]}.'
        )

    def test_formats_py_min(self):
        t, m, q = self._count('app/handlers/formats.py')
        self.assertGreaterEqual(
            t, self.MIN_COUNTS['app/handlers/formats.py'],
            f'app/handlers/formats.py threading lost: got {t} '
            f'(msg={m}, q.message={q}); min '
            f'{self.MIN_COUNTS["app/handlers/formats.py"]}.'
        )

    def test_tokens_py_min(self):
        t, m, q = self._count('app/handlers/tokens.py')
        self.assertGreaterEqual(
            t, self.MIN_COUNTS['app/handlers/tokens.py'],
            f'app/handlers/tokens.py threading lost: got {t} '
            f'(msg={m}, q.message={q}); min '
            f'{self.MIN_COUNTS["app/handlers/tokens.py"]}.'
        )


class TestStructuralPinPerFunction(unittest.TestCase):
    """Source-level spot checks on the most critical video-flow
    functions. Mirrors `TestCommentSliceDefensiveness` pattern.
    """

    def test_show_format_choice_status_threads_kwarg(self):
        from app.handlers.navigation import show_format_choice
        self.assertIn(
            'reply_to_message_id=msg.message_id',
            inspect.getsource(show_format_choice),
            'show_format_choice "🔍 Fetching info..." status MUST '
            'pass reply_to_message_id=msg.message_id so the format '
            'choice screen is threaded to the user\'s link.'
        )

    def test_download_task_status_threads_kwarg(self):
        from app.handlers.messages import download_task
        self.assertIn(
            'reply_to_message_id=msg.message_id',
            inspect.getsource(download_task),
            'download_task "⏳ Downloading..." status MUST thread '
            'to the user\'s link.'
        )

    def test_send_file_threads_kwarg(self):
        from app.handlers.tokens import send_file
        self.assertIn(
            'reply_to_message_id=msg.message_id',
            inspect.getsource(send_file),
            'send_file must thread its file-delivery reply to the '
            'preceding bot/user message in the chain.'
        )

    def test_show_delivery_threads_kwarg(self):
        from app.handlers.formats import show_delivery
        self.assertIn(
            'reply_to_message_id=msg.message_id',
            inspect.getsource(show_delivery),
            'show_delivery must thread its terminal delivery kb text '
            '(both group + private chat paths) to the user\'s link.'
        )

    def test_handle_token_start_threads_kwarg(self):
        from app.handlers.tokens import handle_token_start
        self.assertIn(
            'reply_to_message_id=msg.message_id',
            inspect.getsource(handle_token_start),
            'handle_token_start must thread its status + error '
            'replies to the user\'s /start command in the deep-link '
            'chat (no link message present in that chat).'
        )


# =====================================================================
# NON-VIDEO SCOPE PINS
# =====================================================================


class TestNonVideoFlowsNotThreaded(unittest.TestCase):
    """Pin the scope decision: per-user settings menu actions,
    /recent callbacks, and utility helpers are NOT video-specific
    and DELIBERATELY do not thread to a video link.

    Adding reply_to_message_id= to these would be misleading UX:
    threading a /settings reply to the user's prior /settings click
    (not a video link) visually groups unrelated state under one
    bubble. Pin the deliberate non-threading here.
    """

    NON_VIDEO_DOTTED = (
        # Command handlers (NOT video-specific): start, help, settings,
        # recent, status, cancel. Each replies via msg.reply_text
        # without threading — they're per-user commands (not video
        # link surfaces). Pin here so a future maintainer doesn't add
        # reply_to_message_id defensively and break the
        # "video-only" threading scope.
        'app.handlers.commands.start_cmd',
        'app.handlers.commands.help_cmd',
        'app.handlers.commands.settings_cmd',
        'app.handlers.commands.recent_cmd',
        'app.handlers.commands.status_cmd',
        'app.handlers.commands.cancel_cmd',
        # settings-menu pickers (_change_* show a kb, _set_* persist)
        'app.handlers.navigation._change_video_quality',
        'app.handlers.navigation._change_audio_quality',
        'app.handlers.navigation._change_subtitle_mode',
        'app.handlers.navigation._change_auto_format',
        'app.handlers.navigation._change_video_container',
        'app.handlers.navigation._change_language',
        'app.handlers.navigation._change_delivery',
        'app.handlers.navigation._set_video_quality',
        'app.handlers.navigation._set_audio_quality',
        'app.handlers.navigation._set_subtitle_mode',
        'app.handlers.navigation._set_auto_format',
        'app.handlers.navigation._set_video_container',
        'app.handlers.navigation._set_language',
        'app.handlers.navigation._set_delivery',
        # /recent list actions
        'app.handlers.navigation._select',
        'app.handlers.navigation._delete',
        'app.handlers.navigation._clear_all',
        # utility (not message-sending)
        'app.handlers.navigation.handle_back',
    )

    def test_settings_and_recent_handlers_lack_threading_kwarg(self):
        for dotted in self.NON_VIDEO_DOTTED:
            module_path, attr = dotted.rsplit('.', 1)
            module = __import__(module_path, fromlist=[attr])
            fn = getattr(module, attr)
            src = inspect.getsource(fn)
            self.assertNotIn(
                'reply_to_message_id=', src,
                f'{dotted} is a settings-menu/recent action (NOT '
                f'video-specific). Threading it to msg.message_id '
                f'would mislead users into thinking the bot reply '
                f'is threaded to a video. Pin the deliberately-'
                f'implicit threading scope here.'
            )


# =====================================================================
# BEHAVIORAL TESTS (MagicMock-based)
# =====================================================================


class TestBehavioralThreadingContract(unittest.TestCase):
    """Drive representative video-flow handlers with mocked Message
    objects; capture every `msg.reply_text` call and assert the
    status reply (the FIRST one with the literal "Fetching info"
    string) carried `reply_to_message_id=msg.message_id` kwarg.
    """

    def _find_status_call(self, mock_method, content_substr):
        """Locate the call whose first positional arg contains
        `content_substr` in `mock_method.call_args_list`. Returns the
        Call object. Robust to additional replies (error paths,
        follow-ups) the function may emit.
        """
        for call in mock_method.call_args_list:
            args = call.args
            if args and isinstance(args[0], str) and content_substr in args[0]:
                return call
        return None

    def test_show_format_choice_status_threads_to_msg(self):
        """Drive `show_format_choice` and assert the captured
        status-reply call (whose text contains "Fetching info")
        carries `reply_to_message_id=msg.message_id`.
        """
        # Pin the imports on which the test depends. Without these
        # assertions, a future refactor that moves _ensure into a
        # helper module (or inlines format_choice_kb into navigation)
        # would silently no-op all our patches and bypass the
        # behavioral assertion entirely.
        # NOTE: inspect the MODULE source (not just the function),
        # because the `from X import Y` lines are module-level and
        # would not appear in inspect.getsource(show_format_choice).
        import app.handlers.navigation as navigation_module
        from app.handlers.navigation import show_format_choice
        module_src = inspect.getsource(navigation_module)
        self.assertIn('from app.downloader import fetch_info', module_src,
                      'navigation module no longer imports '
                      'fetch_info from app.downloader; the behavioral '
                      'test must be updated to point at the new '
                      'import path.')
        self.assertIn('from app.handlers.messages import _ensure', module_src,
                      'navigation module no longer imports '
                      '_ensure from app.handlers.messages; behavioral '
                      'test needs re-binding.')
        self.assertIn('from app.handlers.formats import format_choice_kb', module_src,
                      'navigation module no longer imports '
                      'format_choice_kb from app.handlers.formats; '
                      'behavioral test needs re-binding.')

        msg = MagicMock()
        msg.message_id = 12345
        msg.chat.id = 999
        # AsyncMock-ify awaited methods on msg.
        for attr in ('reply_text', 'reply_document', 'reply_photo',
                     'reply_video', 'reply_audio', 'edit_text',
                     'edit_media', 'delete'):
            setattr(msg, attr, AsyncMock())

        bot = MagicMock()

        async def fake_fetch_info(*args, **kwargs):
            return {
                'title': 'X', 'duration': 0, 'comments': [],
                'description': '', 'uploader': 'X',
                'view_count': None, 'upload_date': None,
            }

        async def fake_ensure(b, u):
            return True

        # Pin correct module paths: show_format_choice imports
        # `_ensure` from `app.handlers.messages` and `fetch_info` from
        # `app.downloader` at import time, so mocks MUST target
        # those source modules.
        with            patch('app.handlers.messages._ensure', fake_ensure), \
             patch('app.downloader.fetch_info', fake_fetch_info), \
             patch('app.handlers.formats.format_choice_kb',
                   MagicMock(inline_keyboard=[])):
            # Use explicit per-test event-loop construction so multiple
            # async-driven tests in the same TestCase don't collide
            # under Python 3.10+'s "single running loop per process"
            # enforcement (asyncio.run reuses the policy differently
            # across CPython versions; new_event_loop is unambiguous).
            loop = asyncio.new_event_loop()
            try:
                loop.run_until_complete(
                    show_format_choice(bot, 999, 'http://x', 'vid', msg)
                )
            finally:
                loop.close()

        status_call = self._find_status_call(msg.reply_text, 'Fetching info')
        self.assertIsNotNone(
            status_call,
            'show_format_choice did NOT call msg.reply_text with the '
            'expected "🔍 Fetching info..." content; the test cannot '
            'verify the threading contract.'
        )
        self.assertIn(
            'reply_to_message_id', status_call.kwargs,
            f'"🔍 Fetching info..." reply_text kwargs missing '
            f'reply_to_message_id; got keys: {sorted(status_call.kwargs)}.'
        )
        self.assertEqual(
            status_call.kwargs['reply_to_message_id'], 12345,
            f'"🔍 Fetching info..." reply passed reply_to_message_id='
            f'{status_call.kwargs["reply_to_message_id"]!r}; expected '
            f'12345 (msg.message_id).'
        )


if __name__ == '__main__':
    unittest.main()
