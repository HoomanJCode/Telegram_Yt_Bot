"""Tests for app/utils.py — quality constants, settings getters, and helpers.

Uses only stdlib unittest so the test suite runs in the deployed environment
without requiring extra `pip install` steps.
"""
import unittest
from unittest.mock import MagicMock

from app.utils import (
    VIDEO_QUALITY_OPTIONS, AUDIO_QUALITY_OPTIONS, SUBTITLE_MODE_OPTIONS,
    VIDEO_QUALITY_FMT, AUDIO_QUALITY_FMT,
    VIDEO_QUALITY_LABELS, AUDIO_QUALITY_LABELS, SUBTITLE_MODE_LABELS,
    get_video_quality, get_audio_quality, get_subtitle_mode,
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

    def test_getters_isolate_between_users(self):
        bot = _make_bot({1: {'video_quality': '720p'}})
        self.assertEqual(get_video_quality(bot, 1), '720p')
        # User 2 has no entry — should return default
        self.assertEqual(get_video_quality(bot, 2), 'best')


class TestEnsureSettings(unittest.TestCase):
    def test_ensure_populates_all_defaults(self):
        bot = _make_bot()
        s = _ensure_settings(bot, 1)
        self.assertEqual(s, {
            'default_delivery': 'ask',
            'video_quality': 'best',
            'audio_quality': 'best',
            'subtitle_mode': 'embed',
        })

    def test_ensure_preserves_existing_values(self):
        bot = _make_bot({1: {'video_quality': '720p', 'audio_quality': '320',
                             'subtitle_mode': 'separate', 'default_delivery': 'telegram'}})
        s = _ensure_settings(bot, 1)
        self.assertEqual(s['video_quality'], '720p')
        self.assertEqual(s['audio_quality'], '320')
        self.assertEqual(s['subtitle_mode'], 'separate')
        self.assertEqual(s['default_delivery'], 'telegram')

    def test_ensure_fills_missing_keys_without_overwriting(self):
        bot = _make_bot({1: {'video_quality': '720p'}})
        s = _ensure_settings(bot, 1)
        self.assertEqual(s['video_quality'], '720p')
        # Defaults still filled in for missing keys
        self.assertEqual(s['audio_quality'], 'best')
        self.assertEqual(s['subtitle_mode'], 'embed')
        self.assertEqual(s['default_delivery'], 'ask')

    def test_ensure_rebuilds_when_existing_value_is_not_dict(self):
        # Legacy: settings stored as a plain string 'ask'
        bot = _make_bot({1: 'ask'})
        s = _ensure_settings(bot, 1)
        self.assertEqual(s, {
            'default_delivery': 'ask',
            'video_quality': 'best',
            'audio_quality': 'best',
            'subtitle_mode': 'embed',
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


class TestFriendlyErrorMsg(unittest.TestCase):
    """Each category should map to a user-friendly message."""

    def test_categories_all_have_messages(self):
        categories = (
            'live_not_started', 'live_ended', 'unavailable', 'private',
            'age_restricted', 'members_only', 'geo_blocked', 'removed',
            'cookies_required', 'playability', 'unknown',
        )
        for category in categories:
            msg = friendly_error_msg(category)
            self.assertTrue(msg, f'category {category!r} has empty msg')

    def test_unknown_category_returns_unknown_message(self):
        self.assertEqual(friendly_error_msg('garbage'), friendly_error_msg('unknown'))

    def test_live_not_started_message_mentions_delay(self):
        msg = friendly_error_msg('live_not_started').lower()
        self.assertIn('live', msg)
        self.assertTrue('try' in msg or 'wait' in msg)

    def test_geo_blocked_message_mentions_region(self):
        msg = friendly_error_msg('geo_blocked').lower()
        self.assertTrue('region' in msg or 'country' in msg or 'geo' in msg)


if __name__ == '__main__':
    unittest.main()
