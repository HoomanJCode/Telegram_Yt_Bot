"""Tests for app/utils.py — quality constants, settings getters, and helpers.

Uses only stdlib unittest so the test suite runs in the deployed environment
without requiring extra `pip install` steps.
"""
import unittest
from pathlib import Path
from unittest.mock import MagicMock

from app.utils import (
    esc, _format_comments, _format_description,
    VIDEO_QUALITY_OPTIONS, AUDIO_QUALITY_OPTIONS, SUBTITLE_MODE_OPTIONS,
    AUTO_FORMAT_OPTIONS, AUTO_FORMAT_LABELS, AUTO_FORMAT_SHORT,
    VIDEO_CONTAINER_OPTIONS, VIDEO_CONTAINER_LABELS, VIDEO_CONTAINER_SHORT,
    VIDEO_QUALITY_FMT, AUDIO_QUALITY_FMT,
    VIDEO_QUALITY_LABELS, AUDIO_QUALITY_LABELS, SUBTITLE_MODE_LABELS,
    get_video_quality, get_audio_quality, get_subtitle_mode, get_auto_format,
    get_video_container, get_default_delivery, _ensure_settings,
    classify_yt_error, friendly_error_msg,
    find_existing, prune_missing, _path_on_disk,
)


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

    def test_video_quality_fmt_pins_to_avc_codec(self):
        # Regression pin for the 2026-06-20 MKV / 'video codec:none'
        # TV report. Every height-bound entry must include the
        # `[vcodec^=avc]` prefix-match filter on `bv*` so yt-dlp
        # restricts selection to H.264 streams (avc1.640028,
        # avc1.4d401e, etc.). Without this filter yt-dlp picks the
        # highest-height VP9 / AV1 stream YouTube serves for those
        # tiers and muxes it into MKV with codec id `V_VP9` /
        # `V_AV1` — which older TVs refuse to decode.
        avc_pinned = ('2160p', '1440p', '1080p', '720p', '480p', '360p', 'best')
        for vq in avc_pinned:
            self.assertIn(
                '[vcodec^=avc]', VIDEO_QUALITY_FMT[vq],
                f'VIDEO_QUALITY_FMT[{vq!r}] must pin to AVC so older '
                f'TVs can decode the resulting MKV / MP4 -- without '
                f'the pin yt-dlp picks VP9/AV1 streams which the '
                f"TVs report as 'video codec:none'.")

    def test_worst_video_stays_unpinned(self):
        # Sanity: 'worst' deliberately stays a single-stream
        # `worst` token (no codec pin). Pinning worst to avc1 would
        # break downloads on videos that ONLY have VP9 sources -- the
        # user picked 'worst' specifically to accept any codec.
        self.assertEqual(VIDEO_QUALITY_FMT['worst'], 'worst')
        self.assertNotIn('[vcodec^=avc]', VIDEO_QUALITY_FMT['worst'])

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

    def test_video_container_options_complete(self):
        # Must include 'auto' (default — natural container) AND 'mp4'
        # (the universal-compat option the user added).
        self.assertEqual(set(VIDEO_CONTAINER_OPTIONS), {'auto', 'mp4'})

    def test_video_container_labels_present_for_all_options(self):
        for opt in VIDEO_CONTAINER_OPTIONS:
            self.assertIn(opt, VIDEO_CONTAINER_LABELS)
            self.assertIsInstance(VIDEO_CONTAINER_LABELS[opt], str)
            self.assertGreater(len(VIDEO_CONTAINER_LABELS[opt]), 0)

    def test_video_container_short_single_char_per_option(self):
        # Compact one-char labels so the menu's "Container: M"-style row
        # stays compact — visual parity with "Auto: V" / "Auto: A" /
        # "Auto: T" / "Auto: ?". Mirrors the AUTO_FORMAT_SHORT contract.
        for opt in VIDEO_CONTAINER_OPTIONS:
            self.assertIn(opt, VIDEO_CONTAINER_SHORT)
            self.assertEqual(len(VIDEO_CONTAINER_SHORT[opt]), 1)


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

    def test_default_video_container_is_auto(self):
        bot = _make_bot()
        self.assertEqual(get_video_container(bot, 1), 'auto')

    def test_video_container_returns_set_value(self):
        bot = _make_bot({1: {'video_container': 'mp4'}})
        self.assertEqual(get_video_container(bot, 1), 'mp4')

    def test_video_container_invalid_value_falls_back_to_auto(self):
        # Defensive: garbage stored values must NOT reach downloader.py;
        # fall back to 'auto' since downloader expects 'auto' | 'mp4'.
        bot = _make_bot({1: {'video_container': 'webm'}})
        self.assertEqual(get_video_container(bot, 1), 'auto')

    def test_video_container_invalid_value_does_not_mutate_settings(self):
        # Defensive fallback is read-only — must NOT auto-correct the
        # underlying settings dict (mirrors get_auto_format's contract).
        bot = _make_bot({1: {'video_container': 'garbage'}})
        get_video_container(bot, 1)
        self.assertEqual(bot._user_settings[1]['video_container'], 'garbage')

    def test_video_container_isolates_between_users(self):
        bot = _make_bot()
        _ensure_settings(bot, 1)
        bot._user_settings[1]['video_container'] = 'mp4'
        self.assertEqual(get_video_container(bot, 1), 'mp4')
        # User 2 falls back to 'auto'
        self.assertEqual(get_video_container(bot, 2), 'auto')


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
            'video_container': 'auto',
        })

    def test_ensure_preserves_existing_values(self):
        bot = _make_bot({1: {'video_quality': '720p', 'audio_quality': '320',
                             'subtitle_mode': 'separate', 'default_delivery': 'telegram',
                             'auto_format': 'video', 'video_container': 'mp4'}})
        s = _ensure_settings(bot, 1)
        self.assertEqual(s['video_quality'], '720p')
        self.assertEqual(s['audio_quality'], '320')
        self.assertEqual(s['subtitle_mode'], 'separate')
        self.assertEqual(s['default_delivery'], 'telegram')
        self.assertEqual(s['auto_format'], 'video')
        self.assertEqual(s['video_container'], 'mp4')

    def test_ensure_fills_missing_keys_without_overwriting(self):
        bot = _make_bot({1: {'video_quality': '720p'}})
        s = _ensure_settings(bot, 1)
        self.assertEqual(s['video_quality'], '720p')
        # Defaults still filled in for missing keys
        self.assertEqual(s['audio_quality'], 'best')
        self.assertEqual(s['subtitle_mode'], 'embed')
        self.assertEqual(s['default_delivery'], 'ask')
        self.assertEqual(s['auto_format'], 'ask')
        self.assertEqual(s['video_container'], 'auto')

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
            'video_container': 'auto',
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


class TestPathOnDisk(unittest.TestCase):
    """_path_on_disk is the soft-fail-safe existence check.

    Critical contract (caught in code-review): MUST NOT confuse a
    recoverable OSError (NFS hiccup, mid-write EACCES, EIO, EBUSY on
    Windows) with 'file is gone'. Returning False there would let
    prune_missing permanently drop the user's record from
    user_videos.json on a transient blip. The function must keep the
    record on any non-FileNotFoundError and let the next /recent tap
    retry.
    """

    def test_returns_true_for_real_file(self):
        import tempfile
        with tempfile.NamedTemporaryFile(delete=False) as f:
            p = f.name
        try:
            self.assertTrue(_path_on_disk(p))
        finally:
            Path(p).unlink(missing_ok=True)

    def test_returns_false_for_genuinely_missing(self):
        import tempfile
        self.assertFalse(_path_on_disk(tempfile.gettempdir() + '/definitely-not-here-12345.mp4'))

    def test_returns_true_on_transient_oserror(self):
        # Patch Path.stat to raise a recoverable OSError — must be treated
        # as 'still on disk, skip prune'.
        from unittest.mock import patch
        with patch.object(Path, 'stat', side_effect=OSError(5, 'EIO: simulated input/output error')):
            self.assertTrue(_path_on_disk('/any/path/at/all'))

    def test_returns_false_on_filenotfounderror(self):
        from unittest.mock import patch
        with patch.object(Path, 'stat', side_effect=FileNotFoundError('gone')):
            self.assertFalse(_path_on_disk('/any/path'))

    def test_returns_false_for_empty_path(self):
        self.assertFalse(_path_on_disk(''))

    def test_returns_false_for_none(self):
        # The p=None guard short-circuits before Path(None) — important
        # because Path(None) raises TypeError, which would otherwise be
        # swallowed by the OSError catch and yield the wrong True.
        self.assertFalse(_path_on_disk(None))

    def test_returns_false_on_malformed_path_valueerror(self):
        # Round-3 widening: Path('/\\x00bad') raises ValueError at
        # construction time. Caught and treated as 'gone' (False) so
        # prune_missing can clean up rather than leak a perpetually
        # broken entry.
        self.assertFalse(_path_on_disk('/tmp/\x00bad.mp4'))


class TestPruneMissing(unittest.TestCase):
    """prune_missing drops records whose files were deleted out-of-band.

    Touches real files via tempfile.mkdtemp — Path(v.file_path).exists()
    is the source of truth, and mocking would test the mock, not the
    behavior we actually need.
    """

    def setUp(self):
        import tempfile
        from pathlib import Path as _P
        from app.models import VideoRecord
        self._tmp = tempfile.mkdtemp()
        # Two files that will exist for the lifetime of the test.
        self._kept_path = str(_P(self._tmp) / 'kept.mp4')
        self._kept2_path = str(_P(self._tmp) / 'kept2.mp4')
        _P(self._kept_path).write_bytes(b'k1')
        _P(self._kept2_path).write_bytes(b'k2')
        # One path that never gets created — represents a file that was
        # already gone before the test started (e.g. operator ran `rm`).
        self._missing_path = str(_P(self._tmp) / 'missing.mp4')
        self._kept_video = VideoRecord(
            'K1', 'http://a', 'vid1', self._kept_path, 101,
            '2024-01-01 00:00:00', media_type='video')
        self._missing_video = VideoRecord(
            'M1', 'http://b', 'vid2', self._missing_path, 202,
            '2024-01-01 00:00:00', media_type='video')
        self._kept2_video = VideoRecord(
            'K2', 'http://c', 'vid3', self._kept2_path, 303,
            '2024-01-01 00:00:00', media_type='video')

    def tearDown(self):
        import shutil
        shutil.rmtree(self._tmp, ignore_errors=True)

    def test_returns_zero_when_all_files_exist(self):
        bot = _make_bot(videos={1: [self._kept_video, self._kept2_video]})
        removed = prune_missing(bot, 1)
        self.assertEqual(removed, 0)
        self.assertEqual(len(bot.videos[1]), 2)
        bot.save.assert_not_called()

    def test_returns_count_of_missing_records(self):
        bot = _make_bot(videos={1: [self._kept_video, self._missing_video, self._kept2_video]})
        removed = prune_missing(bot, 1)
        self.assertEqual(removed, 1)
        self.assertEqual(len(bot.videos[1]), 2)
        self.assertIn(self._kept_video, bot.videos[1])
        self.assertIn(self._kept2_video, bot.videos[1])
        self.assertNotIn(self._missing_video, bot.videos[1])
        bot.save.assert_called_once()

    def test_preserves_order_of_kept_records(self):
        # Order matters: show_recent / page-rendering relies on stable
        # indices so the keyboard's sel_<idx> callback_data stays aligned
        # with the post-prune list.
        bot = _make_bot(videos={1: [self._missing_video, self._kept_video, self._missing_video]})
        removed = prune_missing(bot, 1)
        self.assertEqual(removed, 2)
        self.assertEqual(bot.videos[1], [self._kept_video])

    def test_all_missing_returns_zero_length_and_saves(self):
        bot = _make_bot(videos={1: [self._missing_video, self._missing_video]})
        removed = prune_missing(bot, 1)
        self.assertEqual(removed, 2)
        self.assertEqual(bot.videos[1], [])
        bot.save.assert_called_once()

    def test_unknown_user_returns_zero_without_save(self):
        bot = _make_bot(videos={})
        removed = prune_missing(bot, 999)
        self.assertEqual(removed, 0)
        bot.save.assert_not_called()

    def test_empty_list_returns_zero_without_save(self):
        bot = _make_bot(videos={1: []})
        removed = prune_missing(bot, 1)
        self.assertEqual(removed, 0)
        bot.save.assert_not_called()

    def test_mid_session_real_deletion_is_detected(self):
        # Simulate the operator clearing one of the files between two
        # /recent clicks. First click sees both; second click after the
        # delete should prune exactly one.
        bot = _make_bot(videos={1: [self._kept_video, self._kept2_video]})
        self.assertEqual(prune_missing(bot, 1), 0)
        Path(self._kept_path).unlink()
        removed = prune_missing(bot, 1)
        self.assertEqual(removed, 1)
        self.assertEqual(len(bot.videos[1]), 1)
        self.assertIn(self._kept2_video, bot.videos[1])

    def test_save_called_only_when_anything_changes(self):
        # IO-cost guard: ensure the eager-purge call sites (show_recent
        # + _select) don't write user_videos.json needlessly on every
        # /recent tap.
        bot = _make_bot(videos={1: [self._kept_video]})
        prune_missing(bot, 1)
        bot.save.reset_mock()
        prune_missing(bot, 1)
        prune_missing(bot, 1)
        bot.save.assert_not_called()

    def test_prune_missing_keeps_records_on_transient_oserror(self):
        # Integration test for the soft-fail contract (TestPathOnDisk
        # only covers the helper in isolation). The whole point of the
        # _path_on_disk wrapper is that prune_missing — which writes the
        # purged result to user_videos.json — must NOT permanently delete
        # a user's record on a recoverable filesystem blip.
        from unittest.mock import patch
        bot = _make_bot(videos={1: [self._kept_video]})
        with patch.object(Path, 'stat',
                          side_effect=OSError(5, 'EIO: simulated input/output error')):
            removed = prune_missing(bot, 1)
        self.assertEqual(removed, 0)
        self.assertEqual(bot.videos[1], [self._kept_video])
        bot.save.assert_not_called()

    def test_prune_missing_mixed_genuine_missing_and_transient_oserror(self):
        # Realistic mixed scenario: one file was genuinely deleted by
        # the operator; the OS is having an EIO hiccup affecting stat()
        # on the other two. prune_missing must remove ONLY the genuinely
        # missing one and keep both files whose stat() is transiently
        # failing.
        from unittest.mock import patch
        bot = _make_bot(videos={
            1: [self._kept_video, self._missing_video, self._kept2_video]
        })

        def fake_stat(path_self):
            p = str(path_self)
            if p == self._missing_path:
                raise FileNotFoundError('genuinely gone')
            raise OSError(5, 'EIO: simulated input/output error')

        with patch.object(Path, 'stat', new=fake_stat):
            removed = prune_missing(bot, 1)

        self.assertEqual(removed, 1)
        self.assertEqual(len(bot.videos[1]), 2)
        self.assertIn(self._kept_video, bot.videos[1])
        self.assertIn(self._kept2_video, bot.videos[1])
        self.assertNotIn(self._missing_video, bot.videos[1])
        bot.save.assert_called_once()


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


class TestFormatDescription(unittest.TestCase):
    """Drive `_format_description` -- the helper that renders a 4-5 line
    description excerpt from yt-dlp info[description] for the format-choice
    screen. Operations applied in order: strip, collapse paragraph-break
    patterns to single newlines, truncate to 300 chars with U+2026, escape
    via esc(). Returns empty string for None/blank input so callers can
    branch with `if desc_text:` cleanly without occupying vertical chat space.
    """

    NL = chr(10)
    NL2 = NL + NL

    def test_none_returns_empty_string(self):
        # Defensive: yt-dlp can return None for the description field.
        self.assertEqual(_format_description(None), "")

    def test_empty_string_returns_empty_string(self):
        self.assertEqual(_format_description(""), "")

    def test_whitespace_only_returns_empty_string(self):
        # Whitespace-only input renders as empty so the caller does not get
        # a blank line under the emoji header.
        self.assertEqual(_format_description("   "), "")
        self.assertEqual(_format_description("	"), "")
        self.assertEqual(_format_description(self.NL + "   " + self.NL), "")

    def test_short_input_returned_verbatim(self):
        # 50-char descriptions render verbatim (no ellipsis, no collapse
        # since input has no double-newline). esc() still runs.
        self.assertEqual(_format_description("Hello world"), "Hello world")

    def test_long_input_truncates_at_300_chars_with_ellipsis(self):
        # 1000 chars in -> at most 300 chars of body + U+2026 ellipsis suffix.
        long_in = "a" * 1000
        result = _format_description(long_in)
        self.assertTrue(result.endswith("…"),
            "overflow must signal truncation with the U+2026 ellipsis")
        self.assertEqual(len(result), 300 + 1)
        self.assertEqual(result, "a" * 300 + "…")

    def test_exactly_300_chars_no_ellipsis(self):
        # Boundary: 300 chars exact stays 300 chars. Cap is INCLUSIVE.
        text = "b" * 300
        result = _format_description(text)
        self.assertEqual(result, text)
        self.assertNotIn("…", result)

    def test_paragraph_breaks_collapse_to_single_newline(self):
        # YouTube uses double-newline paragraph breaks. Rendering them verbatim
        # would crowd the chat; collapse to single newlines so the excerpt fits
        # in 4-5 visible lines for a typical video. Single newlines pass through.
        result = _format_description("Para 1" + self.NL2 + "Para 2" + self.NL2 + "Para 3")
        self.assertEqual(result, "Para 1" + self.NL + "Para 2" + self.NL + "Para 3")
        self.assertEqual(_format_description("line1" + self.NL + "line2"),
                         "line1" + self.NL + "line2")

    def test_escapes_markdown_special_chars(self):
        # Common markdown breakers in YT-descriptions: underscores in handles,
        # asterisks, brackets. Without esc() the surrounding ParseMode.MARKDOWN
        # message breaks. Assert escape runs AND no bare chars leak through.
        result = _format_description("_italics_ *bold* `[ Broken!")
        self.assertIn(r"\_italics\_", result)
        self.assertIn(r"\*bold\*", result)
        self.assertNotIn(" _italics_", result)

    def test_strip_trailing_whitespace(self):
        # Some descriptions have trailing newlines. Strip defensively.
        result = _format_description("Real content" + self.NL2 + self.NL)
        self.assertEqual(result, "Real content")

    def test_strip_handles_leading_whitespace(self):
        # Some descriptions start with newlines.
        self.assertEqual(_format_description(self.NL + "Hello"), "Hello")

    def test_pass_through_unicode_letters(self):
        # Non-ASCII letters (Persian, Arabic, CJK) pass through unchanged.
        persian = "سلام دنيا"
        self.assertEqual(_format_description(persian), persian)

    def test_combined_collapse_then_truncate(self):
        # Order of ops: strip -> collapse -> truncate -> escape. Long description
        # with double-newline paragraphs: collapse first then truncate (otherwise
        # the slice would cut a multi-byte UTF-8 sequence mid-encoding).
        text = "p1" + self.NL2 + "a" * 350 + self.NL2 + "p2"
        result = _format_description(text)
        self.assertTrue(result.endswith("…"))
        self.assertNotIn(self.NL2, result)




    class TestFormatUploader(unittest.TestCase):
        """Drive _format_uploader -- renders channel name with TV emoji."""

        def test_none_returns_empty_string(self):
                self.assertEqual(_format_uploader(None), "")

        def test_empty_string_returns_empty_string(self):
                self.assertEqual(_format_uploader(""), "")

        def test_whitespace_only_returns_empty_string(self):
                self.assertEqual(_format_uploader("   "), "")
                self.assertEqual(_format_uploader("\t"), "")

        def test_short_renders_with_tv_emoji(self):
                result = _format_uploader("TechChannel")
                self.assertIn(chr(0x1F4FA), result)
                self.assertIn("TechChannel", result)

        def test_strips_surrounding_whitespace(self):
                result = _format_uploader("   TechChannel   ")
                self.assertIn("TechChannel", result)
                self.assertNotIn("   TechChannel", result)

        def test_truncates_at_50_chars(self):
                long_in = "A" * 200
                result = _format_uploader(long_in)
                # Channel name portion must be <= 50.
                _, channel = result.split(" ", 1)
                self.assertEqual(len(channel), 50)

        def test_exactly_50_chars_passes_through(self):
                text = "B" * 50
                result = _format_uploader(text)
                self.assertTrue(result.endswith("B" * 50))

        def test_escapes_markdown_special_chars(self):
                result = _format_uploader("a_b *bold* `[ Broken!")
                self.assertIn(r"\_b", result)
                self.assertIn(r"\*bold\*", result)
                self.assertNotIn("a_b ", result)

    class TestFormatViews(unittest.TestCase):
        """Drive _format_views -- raw int -> compact human-readable with K/M/B."""

        def test_none_returns_empty_string(self):
                self.assertEqual(_format_views(None), "")

        def test_zero_returns_empty_string(self):
                self.assertEqual(_format_views(0), "")

        def test_negative_returns_empty_string(self):
                self.assertEqual(_format_views(-100), "")

        def test_non_int_returns_empty_string(self):
                self.assertEqual(_format_views("lots"), "")
                self.assertEqual(_format_views([]), "")

        def test_small_int_no_suffix(self):
                # < 1000 renders the bare integer.
                result = _format_views(999)
                self.assertIn("999", result)
                self.assertNotIn("K", result)
                self.assertNotIn("M", result)
                self.assertNotIn("B", result)

        def test_one_thousand_uses_k_strips_dot_zero(self):
                # 1000 -> "1K" not "1.0K".
                self.assertIn("1K views", _format_views(1000))
                self.assertNotIn("1.0K", _format_views(1000))

        def test_1500_k_with_one_decimal(self):
                self.assertIn("1.5K views", _format_views(1500))

        def test_just_under_thousand_no_suffix(self):
                self.assertIn("999 views", _format_views(999))

        def test_one_million_uses_m_strips_dot_zero(self):
                self.assertIn("1M views", _format_views(1_000_000))
                self.assertNotIn("1.0M", _format_views(1_000_000))

        def test_3_2_million(self):
                self.assertIn("3.2M views", _format_views(3_200_000))

        def test_one_billion_uses_b(self):
                self.assertIn("1B views", _format_views(1_000_000_000))
                self.assertIn("1.5B views", _format_views(1_500_000_000))

        def test_eye_emoji_always_present(self):
                # Every non-empty result is U+1F441-prefixed.
                EYE = chr(0x1F441)
                for n in (1, 999, 1000, 12345, 1_000_000, 5_000_000_000):
                    result = _format_views(n)
                    self.assertTrue(result.startswith(EYE),
                        f"view={n} result={result!r} must start with eye emoji")

    class TestFormatUploadDate(unittest.TestCase):
        """Drive _format_upload_date -- renders yt-dlp date as ISO with calendar emoji."""

        def test_none_returns_empty_string(self):
                self.assertEqual(_format_upload_date(None), "")

        def test_empty_string_returns_empty_string(self):
                self.assertEqual(_format_upload_date(""), "")

        def test_whitespace_only_returns_empty_string(self):
                self.assertEqual(_format_upload_date("   "), "")

        def test_yyyymmdd_format_with_dashes(self):
                # 20231215 -> 2023-12-15.
                result = _format_upload_date("20231215")
                self.assertIn("2023-12-15", result)
                self.assertIn(chr(0x1F4C5), result)

        def test_already_dashed_passes_through(self):
                # Some non-YouTube extractors use ISO format natively.
                result = _format_upload_date("2023-12-15")
                self.assertIn("2023-12-15", result)

        def test_strips_surrounding_whitespace(self):
                result = _format_upload_date("  20231215  ")
                self.assertIn("2023-12-15", result)

        def test_garbage_falls_back_to_verbatim(self):
                result = _format_upload_date("yesterday")
                self.assertIn("yesterday", result)
                self.assertIn(chr(0x1F4C5), result)

        def test_short_garbage_passed_through(self):
                # 7 digits: not the canonical 8, fall back to verbatim.
                result = _format_upload_date("2023121")
                self.assertIn("2023121", result)

    class TestFormatMeta(unittest.TestCase):
        """Drive _format_meta -- combines uploader/views/date into one block."""

        def test_all_none_returns_empty_string(self):
                self.assertEqual(_format_meta(None, None, None), "")

        def test_all_empty_or_zero_returns_empty_string(self):
                self.assertEqual(_format_meta("", 0, ""), "")

        def test_only_uploader_returns_only_uploader(self):
                result = _format_meta("ChannelName", None, None)
                self.assertIn("ChannelName", result)
                self.assertNotIn("views", result)
                self.assertEqual(result.count(NL), 0)

        def test_only_views_returns_only_views(self):
                result = _format_meta(None, 12345, None)
                self.assertIn("12.3K views", result)
                self.assertEqual(result.count(NL), 0)

        def test_only_date_returns_only_date(self):
                result = _format_meta(None, None, "20231215")
                self.assertIn("2023-12-15", result)
                self.assertEqual(result.count(NL), 0)

        def test_uploader_plus_views_combined(self):
                result = _format_meta("Ch", 1000, None)
                self.assertIn("Ch", result)
                self.assertIn("1K views", result)
                self.assertEqual(result.count(NL), 1)

        def test_all_three_combined_three_lines(self):
                result = _format_meta("Ch", 1000, "20231215")
                self.assertIn("Ch", result)
                self.assertIn("1K views", result)
                self.assertIn("2023-12-15", result)
                self.assertEqual(result.count(NL), 2)

        def test_zero_views_skipped_in_combo(self):
                # 0 view_count skipped even if uploader + date present.
                result = _format_meta("Ch", 0, "20231215")
                self.assertIn("Ch", result)
                self.assertIn("2023-12-15", result)
                self.assertNotIn("views", result)

        def test_meta_escapes_when_present(self):
                # Markdown specials in any input still esc.
                result = _format_meta("a_b", None, None)
                self.assertIn(r"\_b", result)


class TestFormatComments(unittest.TestCase):
    """Drive `_format_comments` — the helper that turns yt-dlp's
    `info['comments']` list into a short, Telegram-friendly excerpt for
    the format-choice screen.

    Operator-toggled via `Config.MAX_COMMENTS`: when it's 0 the helper
    is never called. When the operator opts in, the rendering rules
    below are what the user sees in the chat.

    Designed against yt-dlp's documented comment-dict shape
    {author, text, like_count, ...}, but defensively tolerates partial
    dicts / None values because YouTube sometimes returns malformed
    comment objects mid-fetch (network drop, sign-in challenge, etc.).
    """

    def test_empty_list_returns_empty_string(self):
        # Render-as-empty (not a placeholder) so the caller can use
        # `if comments_block:` to skip the section cleanly.
        self.assertEqual(_format_comments([]), '')

    def test_none_returns_empty_string(self):
        # Defensive default: even if a buggy caller (or a future
        # refactor) pipes `None` through, the rendering path stays safe.
        self.assertEqual(_format_comments(None), '')

    def test_single_comment_renders_author_and_text(self):
        out = _format_comments([{'author': 'alice', 'text': 'hi'}])
        self.assertIn('@alice', out)
        self.assertIn('hi', out)

    def test_long_text_truncates_at_140_chars_with_ellipsis(self):
        # 200-char input must produce a single line whose text-body is
        # ≤ 140 chars + U+2026 ellipsis. The whole line (incl. author
        # badge + ': ' separator) stays under ~200 chars even for the
        # worst-case "{} : " padding.
        long = 'a' * 200
        out = _format_comments([{'author': 'a', 'text': long}])
        # 140 'a's then ellipsis; line total well under 200.
        self.assertIn('a' * 140 + '\u2026', out)
        # And does NOT contain the 200x 'a' (otherwise the truncate is broken).
        self.assertNotIn('a' * 141, out)

    def test_missing_author_renders_as_anon(self):
        out = _format_comments([{'text': 'hi'}])
        self.assertIn('@anon', out)
        self.assertIn('hi', out)

    def test_missing_text_renders_with_empty_body(self):
        # yt-dlp partial-fetch failure (e.g. comment author exposed
        # but text truncated to ''). We still render the author badge
        # so a partial comment is visible vs. dropped silently.
        out = _format_comments([{'author': 'a'}])
        self.assertIn('@a', out)

    def test_escapes_markdown_chars_in_author_and_text(self):
        # YouTube usernames often contain underscores (e.g.
        # `@john_doe`); without esc() that would break the surrounding
        # ParseMode.MARKDOWN message. Same for `*` / `[` / backtick in
        # comment bodies. Verify both author and text pass through esc.
        out = _format_comments([{'author': 'a_b', 'text': '*bold*'}])
        self.assertIn(r'a\_b', out)
        self.assertIn(r'\*bold\*', out)
        # And no bare markdown-special chars leak through.
        self.assertNotIn(': *bold*', out)

    def test_multiple_comments_render_one_per_line(self):
        c1 = {'author': 'a', 'text': 'hi'}
        c2 = {'author': 'b', 'text': 'bye'}
        c3 = {'author': 'c', 'text': 'greet'}
        out = _format_comments([c1, c2, c3])
        # All three lines present, one per line (joined by \n).
        self.assertIn('@a: hi', out)
        self.assertIn('@b: bye', out)
        self.assertIn('@c: greet', out)
        # Format invariant: at least N-1 newlines for N lines.
        self.assertGreaterEqual(out.count('\n'), 2)


if __name__ == '__main__':
    unittest.main()
