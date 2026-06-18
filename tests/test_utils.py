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
    """find_existing is forgiving if no records match — test it returns None cleanly."""

    def test_returns_none_for_unknown_user(self):
        bot = _make_bot()
        self.assertIsNone(find_existing(bot, 999, 'fakevid', 'video'))


if __name__ == '__main__':
    unittest.main()
