"""Tests for app/utils.py — quality constants, settings getters, and helpers.

Uses only stdlib unittest so the test suite runs in the deployed environment
without requiring extra `pip install` steps.
"""
import unittest
from unittest.mock import MagicMock

from app.utils import (
    VIDEO_QUALITY_OPTIONS, AUDIO_QUALITY_OPTIONS, SUBTITLE_MODE_OPTIONS,
    AUTO_FORMAT_OPTIONS, AUTO_FORMAT_LABELS, AUTO_FORMAT_SHORT,
    VIDEO_QUALITY_FMT, AUDIO_QUALITY_FMT,
    VIDEO_QUALITY_LABELS, AUDIO_QUALITY_LABELS, SUBTITLE_MODE_LABELS,
    get_video_quality, get_audio_quality, get_subtitle_mode, get_auto_format,
    get_default_delivery, _ensure_settings,
    classify_yt_error, friendly_error_msg,
)
from app.utils import find_existing


def _make_bot(settings=None, videos=None):
    bot = MagicMock()
    bot._user_settings = settings if settings is not None else {}
    bot.videos = videos or {}
    return bot


class TestQualityConstants(unittest.TestCase):
    def test_video_quality_default_is_first(self):
        self.assertEqual(VIDEO_QUALITY_OPTIONS[0], 'best')

    def test_video_quality_fmt_covers_all_options(self):
        for opt in VIDEO_QUALITY_OPTIONS:
            self.assertIn(opt, VIDEO_QUALITY_FMT)
            self.assertIsInstance(VIDEO_QUALITY_FMT[opt], str)
            self.assertTrue(len(VIDEO_QUALITY_FMT[opt]) > 0)

    def test_audio_quality_default_is_first(self):
        self.assertEqual(AUDIO_QUALITY_OPTIONS[0], 'best')

    def test_audio_quality_fmt_covers_all_options(self):
        for opt in AUDIO_QUALITY_OPTIONS:
            self.assertIn(opt, AUDIO_QUALITY_FMT)
            self.assertIsInstance(AUDIO_QUALITY_FMT[opt], str)
            self.assertTrue(len(AUDIO_QUALITY_FMT[opt]) > 0)

    def test_subtitle_mode_default_is_embed(self):
        self.assertEqual(SUBTITLE_MODE_OPTIONS[0], 'embed')

    def test_quality_labels_present_for_all_options(self):
        for opt in VIDEO_QUALITY_OPTIONS:
            self.assertIn(opt, VIDEO_QUALITY_LABELS)
            self.assertIsInstance(VIDEO_QUALITY_LABELS[opt], str)
        for opt in AUDIO_QUALITY_OPTIONS:
            self.assertIn(opt, AUDIO_QUALITY_LABELS)
            self.assertIsInstance(AUDIO_QUALITY_LABELS[opt], str)
        for opt in SUBTITLE_MODE_OPTIONS:
            self.assertIn(opt, SUBTITLE_MODE_LABELS)
            self.assertIsInstance(SUBTITLE_MODE_LABELS[opt], str)

    def test_video_quality_fmt_uses_height_filters(self):
        for res in ('2160p', '1440p', '1080p', '720p', '480p', '360p'):
            self.assertIn(f'height<={res[:-1]}', VIDEO_QUALITY_FMT[res])

    def test_audio_quality_fmt_uses_bitrate_filters(self):
        for br in ('320', '256', '192', '128', '96'):
            self.assertIn(f'abr<={br}', AUDIO_QUALITY_FMT[br])

    def test_auto_format_options_complete(self):
        # Must include 'ask' (default) plus the three media_types that
        # download_task understands.
        self.assertEqual(set(AUTO_FORMAT_OPTIONS),
                         {'ask', 'video', 'audio', 'thumb'})

    def test_auto_format_labels_present_for_all_options(self):
        for opt in AUTO_FORMAT_OPTIONS:
            self.assertIn(opt, AUTO_FORMAT_LABELS)
            self.assertIsInstance(AUTO_FORMAT_LABELS[opt], str)
            self.assertGreater(len(AUTO_FORMAT_LABELS[opt]), 0)

    def test_auto_format_short_single_char_per_option(self):
        # Compact one-glyph labels so the menu's "⚡ Auto: V"-style
        # row stays compact.
        for opt in AUTO_FORMAT_OPTIONS:
            self.assertIn(opt, AUTO_FORMAT_SHORT)
            self.assertEqual(len(AUTO_FORMAT_SHORT[opt]), 1)


class TestSettingsGetters(unittest.TestCase):
    def test_default_delivery_is_ask(self):
        bot = _make_bot()
        self.assertEqual(get_default_delivery(bot, 1), 'ask')

    def test_default_delivery_returns_set_value(self):
        bot = _make_bot({1: {'default_delivery': 'telegram'}})
        self.assertEqual(get_default_delivery(bot, 1), 'telegram')

    def test_default_video_quality_is_best(self):
        bot = _make_bot()
        self.assertEqual(get_video_quality(bot, 1), 'best')

    def test_default_audio_quality_is_best(self):
        bot = _make_bot()
        self.assertEqual(get_audio_quality(bot, 1), 'best')

    def test_default_subtitle_mode_is_embed(self):
        bot = _make_bot()
        self.assertEqual(get_subtitle_mode(bot, 1), 'embed')

    def test_default_auto_format_is_ask(self):
        bot = _make_bot()
        self.assertEqual(get_auto_format(bot, 1), 'ask')

    def test_video_quality_returns_set_value(self):
        bot = _make_bot({1: {'video_quality': '1080p'}})
        self.assertEqual(get_video_quality(bot, 1), '1080p')

    def test_audio_quality_returns_set_value(self):
        bot = _make_bot({1: {'audio_quality': '192'}})
        self.assertEqual(get_audio_quality(bot, 1), '192')

    def test_subtitle_mode_returns_set_value_separate(self):
        bot = _make_bot({1: {'subtitle_mode': 'separate'}})
        self.assertEqual(get_subtitle_mode(bot, 1), 'separate')

    def test_subtitle_mode_returns_set_value_off(self):
        bot = _make_bot({1: {'subtitle_mode': 'off'}})
        self.assertEqual(get_subtitle_mode(bot, 1), 'off')

    def test_auto_format_returns_set_value_video(self):
        bot = _make_bot({1: {'auto_format': 'video'}})
        self.assertEqual(get_auto_format(bot, 1), 'video')

    def test_auto_format_returns_set_value_audio(self):
        bot = _make_bot({1: {'auto_format': 'audio'}})
        self.assertEqual(get_auto_format(bot, 1), 'audio')

    def test_auto_format_returns_set_value_thumb(self):
        bot = _make_bot({1: {'auto_format': 'thumb'}})
        self.assertEqual(get_auto_format(bot, 1), 'thumb')

    def test_auto_format_invalid_value_falls_back_to_ask(self):
        # Defensive: garbage stored values (legacy data, hand-edited JSON)
        # must NOT reach download_task; fall back to 'ask'.
        bot = _make_bot({1: {'auto_format': 'garbage'}})
        self.assertEqual(get_auto_format(bot, 1), 'ask')

    def test_auto_format_handles_legacy_mp4_value(self):
        bot = _make_bot({1: {'auto_format': 'mp4'}})
        self.assertEqual(get_auto_format(bot, 1), 'ask')

    def test_auto_format_invalid_value_does_not_mutate_persisted_dict(self):
        # Defensive fallback is read-only — must NOT auto-correct the
        # underlying settings dict.
        bot = _make_bot({1: {'auto_format': 'garbage'}})
        get_auto_format(bot, 1)
        self.assertEqual(bot._user_settings[1]['auto_format'], 'garbage')

    def test_getters_isolate_between_users(self):
        bot = _make_bot({1: {'video_quality': '720p'}})
        self.assertEqual(get_video_quality(bot, 1), '720p')
        # User 2 has no entry — should return default
        self.assertEqual(get_video_quality(bot, 2), 'best')

    def test_auto_format_isolates_between_users(self):
        bot = _make_bot()
        # Need to ensure user 1's settings dict exists before mutating it.
        _ensure_settings(bot, 1)
        bot._user_settings[1]['auto_format'] = 'video'
        self.assertEqual(get_auto_format(bot, 1), 'video')
        # User 2 has no settings — falls back to 'ask'
        self.assertEqual(get_auto_format(bot, 2), 'ask')


class TestEnsureSettings(unittest.TestCase):
    def test_ensure_populates_all_defaults(self):
        bot = _make_bot()
        s = _ensure_settings(bot, 1)
        self.assertEqual(s, {
            'default_delivery': 'ask',
            'video_quality': 'best',
            'audio_quality': 'best',
            'subtitle_mode': 'embed',
            'auto_format': 'ask',
        })

    def test_ensure_preserves_existing_values(self):
        bot = _make_bot({1: {'video_quality': '720p', 'audio_quality': '320',
                             'subtitle_mode': 'separate', 'default_delivery': 'telegram',
                             'auto_format': 'video'}})
        s = _ensure_settings(bot, 1)
        self.assertEqual(s['video_quality'], '720p')
        self.assertEqual(s['audio_quality'], '320')
        self.assertEqual(s['subtitle_mode'], 'separate')
        self.assertEqual(s['default_delivery'], 'telegram')
        self.assertEqual(s['auto_format'], 'video')

    def test_ensure_fills_missing_keys_without_overwriting(self):
        bot = _make_bot({1: {'video_quality': '720p'}})
        s = _ensure_settings(bot, 1)
        self.assertEqual(s['video_quality'], '720p')
        # Defaults still filled in for missing keys
        self.assertEqual(s['audio_quality'], 'best')
        self.assertEqual(s['subtitle_mode'], 'embed')
        self.assertEqual(s['default_delivery'], 'ask')
        self.assertEqual(s['auto_format'], 'ask')

    def test_ensure_rebuilds_when_existing_value_is_not_dict(self):
        # Legacy: settings stored as a plain string 'ask'
        bot = _make_bot({1: 'ask'})
        s = _ensure_settings(bot, 1)
        self.assertEqual(s, {
            'default_delivery': 'ask',
            'video_quality': 'best',
            'audio_quality': 'best',
            'subtitle_mode': 'embed',
            'auto_format': 'ask',
        })

    def test_mutations_persist_in_bot_dict(self):
        bot = _make_bot()
        _ensure_settings(bot, 42)
        # Mutate the settings dict via bot (same reference)
        bot._user_settings[42]['video_quality'] = '4k'
        self.assertEqual(get_video_quality(bot, 42), '4k')

    def test_per_user_isolation(self):
        bot = _make_bot()
        _ensure_settings(bot, 1)
        _ensure_settings(bot, 2)
        bot._user_settings[1]['video_quality'] = '2160p'
        self.assertEqual(get_video_quality(bot, 1), '2160p')
        self.assertEqual(get_video_quality(bot, 2), 'best')


class TestFindExistingSignature(unittest.TestCase):
    """find_existing is forgiving if no records match - test it returns None cleanly."""

    def test_returns_none_for_unknown_user(self):
        bot = _make_bot()
        self.assertIsNone(find_existing(bot, 999, 'fakevid', 'video'))


class TestClassifyYtError(unittest.TestCase):
    """Drive classify_yt_error against representative yt-dlp / Telegram error text."""

    def test_live_event_not_started(self):
        msg = 'ERROR: [youtube] N5fjrdyQdQg: This live event will begin in 53 minutes.'
        self.assertEqual(classify_yt_error(msg), 'live_not_started')

    def test_live_event_message_about_live_stream_not_started(self):
        msg = "The live stream hasn't started yet."
        self.assertEqual(classify_yt_error(msg), 'live_not_started')

    def test_live_event_already_ended(self):
        msg = 'ERROR: [youtube] abc: The livestream has ended.'
        self.assertEqual(classify_yt_error(msg), 'live_ended')

    def test_video_unavailable(self):
        self.assertEqual(classify_yt_error('Video unavailable.'), 'unavailable')

    def test_geo_blocked_wins_over_unavailable(self):
        # "This video is not available in your country" must hit geo_blocked,
        # NOT the generic unavailable bucket. Anti-shadowing regression.
        msg = 'This video is not available in your country'
        self.assertEqual(classify_yt_error(msg), 'geo_blocked')

    def test_private_video(self):
        self.assertEqual(
            classify_yt_error("Private video. Sign in if you've been granted access"),
            'private')

    def test_age_restricted_via_age_dash(self):
        self.assertEqual(
            classify_yt_error('This video is age-restricted and can only be viewed on the website.'),
            'age_restricted')

    def test_age_restricted_via_sign_in(self):
        self.assertEqual(
            classify_yt_error('Sign in to confirm your age.'),
            'age_restricted')

    def test_members_only(self):
        # Exact phrase yt-dlp emits.
        self.assertEqual(
            classify_yt_error('Members-only content.'),
            'members_only')

    def test_members_only_apostrophe_variants(self):
        # yt-dlp emits straight apostrophe; we match both ASCII and UTF-8 curly forms.
        self.assertEqual(
            classify_yt_error("This video is for this channel's members only."),
            'members_only')
        # Curly apostrophe (U+2019) — original string literal uses the actual char.
        self.assertEqual(
            classify_yt_error("This video is for this channel\u2019s members only."),
            'members_only')

    def test_members_only_paid_membership(self):
        # Fragment 'paid membership' (singular-form phrasing)
        self.assertEqual(
            classify_yt_error('This video requires paid membership.'),
            'members_only')

    def test_members_only_paid_members(self):
        # Fragment 'paid members' (plural-form phrasing — yt-dlp uses both)
        self.assertEqual(
            classify_yt_error('This video is for paid members only.'),
            'members_only')

    def test_members_only_membership_program(self):
        # Fragment 'membership program' (specific)
        self.assertEqual(
            classify_yt_error('This channel has a membership program.'),
            'members_only')

    def test_members_only_become_a_member(self):
        # Fragment 'become a member of this channel'
        self.assertEqual(
            classify_yt_error('Become a member of this channel to unlock this video.'),
            'members_only')

    def test_members_only_does_not_match_loose_join_phrasing(self):
        # Make sure we did NOT introduce a too-broad 'join this channel' fragment.
        # 'Join this channel for updates' is a community/newsletter suggestion,
        # not a paywall error — should NOT be classified as members_only.
        msg = 'Please join this channel to get notifications about new videos.'
        self.assertNotEqual(classify_yt_error(msg), 'members_only')

    def test_removed_by_uploader(self):
        self.assertEqual(
            classify_yt_error('This video has been removed by the uploader.'),
            'removed')

    def test_removed_for_copyright(self):
        self.assertEqual(
            classify_yt_error('This video has been removed for copyright reasons.'),
            'removed')

    def test_cookies_required_login_required(self):
        self.assertEqual(
            classify_yt_error('Login required: please log in to your account in your web browser'),
            'cookies_required')

    def test_playability_message(self):
        self.assertEqual(
            classify_yt_error('Some playability error happened'),
            'playability')

    def test_unknown_message_classified_as_unknown(self):
        self.assertEqual(
            classify_yt_error('Something specific and unrelated happened'),
            'unknown')

    def test_empty_message_classified_as_unknown(self):
        self.assertEqual(classify_yt_error(''), 'unknown')
        self.assertEqual(classify_yt_error(None), 'unknown')

    def test_case_insensitive(self):
        self.assertEqual(
            classify_yt_error('THIS LIVE EVENT WILL BEGIN in 5 minutes.'),
            'live_not_started')

    def test_subtitle_throttle_canonical(self):
        # The exact phrasing yt-dlp emits when subtitle fetch 429s.
        msg = ("ERROR: Unable to download video subtitles for 'en': "
               "HTTP Error 429: Too Many Requests")
        self.assertEqual(classify_yt_error(msg), 'subtitle_throttled')

    def test_subtitle_throttle_bare_429_is_not_subtitle(self):
        # Regression guard: bare `HTTP Error 429` from a format-fetch or
        # manifest rate-limit must NOT be classified as `subtitle_throttled`,
        # otherwise the friendly message would falsely claim the video was
        # delivered when nothing was. Falls through to `unknown`.
        self.assertEqual(classify_yt_error('HTTP Error 429'), 'unknown')

    def test_subtitle_throttle_bare_too_many_requests_is_not_subtitle(self):
        self.assertEqual(classify_yt_error('Too Many Requests'), 'unknown')

    def test_subtitle_throttle_429_with_subtitle_word_is_unknown(self):
        # Helper-only match: the wrapper will retry, but classifier must not
        # miscategorize this as subtitle-throttled since it lacks the
        # canonical `unable to download video subtitles` phrasing.
        msg = "ERROR: [youtube] abc: subtitle fetch failed: HTTP 429"
        self.assertEqual(classify_yt_error(msg), 'unknown')

    def test_subtitle_throttle_too_many_requests_with_subtitle_is_unknown(self):
        msg = "Subtitle download was rate-limited, too many requests"
        self.assertEqual(classify_yt_error(msg), 'unknown')

    def test_subtitle_throttle_does_not_shadow_unavailable(self):
        # "Video unavailable" must still hit `unavailable`, not `subtitle_throttled`.
        self.assertEqual(classify_yt_error('Video unavailable'), 'unavailable')

    def test_disk_error_from_pre_check_string(self):
        msg = ('Less than 5 GB free on bot storage — refusing to start a '
               'download that would crash mid-flight.')
        self.assertEqual(classify_yt_error(msg), 'disk_error')

    def test_disk_error_from_real_yt_dlp_oserror_28(self):
        # The exact string from the user's incident log:
        # "ERROR: unable to write data: [Errno 28] No space left on device"
        msg = ('ERROR: unable to write data: [Errno 28] No space left on device')
        self.assertEqual(classify_yt_error(msg), 'disk_error')

    def test_disk_error_from_quickjs_oserror(self):
        msg = ("OSError(28, 'No space left on device')")
        self.assertEqual(classify_yt_error(msg), 'disk_error')


class TestFriendlyErrorMsg(unittest.TestCase):
    """Each category should map to a user-friendly message."""

    def test_categories_all_have_messages(self):
        categories = (
            'live_not_started', 'live_ended', 'unavailable', 'private',
            'age_restricted', 'members_only', 'geo_blocked', 'removed',
            'cookies_required', 'playability', 'subtitle_throttled',
            'disk_error', 'unknown',
        )
        for category in categories:
            msg = friendly_error_msg(category)
            self.assertTrue(msg, f'category {category!r} has empty msg')

    def test_subtitle_throttled_message_mentions_subs(self):
        msg = friendly_error_msg('subtitle_throttled').lower()
        self.assertIn('subtitle', msg)

    def test_disk_error_message_mentions_storage(self):
        msg = friendly_error_msg('disk_error').lower()
        self.assertTrue('storage' in msg or 'space' in msg or '💾' in msg)

    def test_unknown_category_returns_unknown_message(self):
        self.assertEqual(friendly_error_msg('garbage'), friendly_error_msg('unknown'))

    def test_live_not_started_message_mentions_delay(self):
        msg = friendly_error_msg('live_not_started').lower()
        self.assertIn('live', msg)
        self.assertTrue('try' in msg or 'wait' in msg)

    def test_geo_blocked_message_mentions_region(self):
        msg = friendly_error_msg('geo_blocked').lower()
        self.assertTrue('region' in msg or 'country' in msg or 'geo' in msg)


class TestConfigAdmin(unittest.TestCase):
    """Config.is_admin + Config.get_admin_set — the cookie-upload gate.

    Tests monkey-patch `Config.ADMIN_USERS` directly (no module reload) so we
    verify the documented "NOT cached at import" contract on `is_admin`.
    """

    def setUp(self):
        # Snapshot and clear so leftover env-side state does not leak between
        # tests. Each test sets the value it wants explicitly.
        from config import Config
        self._saved = Config.ADMIN_USERS

    def tearDown(self):
        from config import Config
        Config.ADMIN_USERS = self._saved

    def _set(self, value):
        from config import Config
        Config.ADMIN_USERS = value

    # ----- get_admin_set ----------------------------------------------

    def test_get_admin_set_unset_returns_empty(self):
        from config import Config
        self._set('')
        self.assertEqual(Config.get_admin_set(), set())

    def test_get_admin_set_whitespace_only_returns_empty(self):
        from config import Config
        self._set('   ')
        self.assertEqual(Config.get_admin_set(), set())

    def test_get_admin_set_single_id(self):
        from config import Config
        self._set('42')
        self.assertEqual(Config.get_admin_set(), {42})

    def test_get_admin_set_multiple_ids(self):
        from config import Config
        self._set('1,2,3')
        self.assertEqual(Config.get_admin_set(), {1, 2, 3})

    def test_get_admin_set_strips_whitespace(self):
        from config import Config
        self._set('  1 ,  2  ,, 3  ')
        self.assertEqual(Config.get_admin_set(), {1, 2, 3})

    def test_get_admin_set_malformed_ignores_bad_tokens(self):
        from config import Config
        # `abc` cannot be parsed as int; the surrounding valid tokens still land.
        self._set('1,abc,2')
        self.assertEqual(Config.get_admin_set(), {1, 2})

    def test_get_admin_set_all_malformed_returns_empty(self):
        from config import Config
        # Whole env var garbage → empty set, NOT a crash. `is_admin` then
        # treats this as "admin gating requested but no valid uids" → deny
        # all (safe default).
        self._set('abc,def,ghi')
        self.assertEqual(Config.get_admin_set(), set())

    # ----- is_admin ---------------------------------------------------

    def test_is_admin_unset_returns_true_for_any_uid(self):
        # ADMIN_USERS unset → permissive (legacy behavior preserved).
        from config import Config
        self._set('')
        self.assertTrue(Config.is_admin(123456))
        self.assertTrue(Config.is_admin(1))
        self.assertTrue(Config.is_admin(999999999))

    def test_is_admin_set_returns_true_only_for_listed(self):
        from config import Config
        self._set('1,2,3')
        self.assertTrue(Config.is_admin(1))
        self.assertTrue(Config.is_admin(2))
        self.assertTrue(Config.is_admin(3))
        self.assertFalse(Config.is_admin(4))
        self.assertFalse(Config.is_admin(123456))

    def test_is_admin_set_with_malformed_admin_set_denies_all(self):
        # All-malformed ADMIN_USERS → empty parse set → deny all. Safer
        # than accidentally letting an unexpected parse succeed and
        # admit the wrong uid.
        from config import Config
        self._set('abc,def')
        self.assertFalse(Config.is_admin(1))
        self.assertFalse(Config.is_admin(2))
        self.assertFalse(Config.is_admin(123456))

    def test_is_admin_re_evaluates_dynamic(self):
        # Critical contract: monkey-patching ADMIN_USERS at runtime must
        # be reflected in is_admin's result WITHOUT reloading the config
        # module. This proves the no-import-time-cache invariant from the
        # docstring.
        from config import Config
        self._set('1')
        self.assertTrue(Config.is_admin(1))
        self.assertFalse(Config.is_admin(2))
        # Flip the gate mid-test.
        self._set('2')
        self.assertFalse(Config.is_admin(1))
        self.assertTrue(Config.is_admin(2))
        # And back.
        self._set('')
        self.assertTrue(Config.is_admin(1))
        self.assertTrue(Config.is_admin(2))

    def test_is_admin_layered_with_ok_helper(self):
        # is_admin is layered on top of `ok(...)` (which gates by
        # WHITELIST_USERS). This test is purely documentary — the helper
        # does not enforce layering itself; the call sites do.
        # We just assert that is_admin cares ONLY about ADMIN_USERS.
        from config import Config
        self._set('100')
        self.assertTrue(Config.is_admin(100))
        self.assertFalse(Config.is_admin(200))
        # Even if there were no WHITELIST_USERS, is_admin should not change.
        self.assertTrue(Config.is_admin(100))


if __name__ == '__main__':
    unittest.main()
