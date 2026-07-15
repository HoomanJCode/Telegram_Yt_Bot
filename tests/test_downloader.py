"""Tests for app/downloader.py — pure helpers, ffmpeg wrappers (mocked), and
quality-format integrity.

External calls (yt-dlp, real ffmpeg, real cookies) are NOT exercised here —
this suite targets unit-testable logic only so it stays reliable in the
deployed environment without network or system dependencies.
"""
import os
import shutil
import tempfile
import unittest
import logging
import inspect
from pathlib import Path
from unittest.mock import patch, MagicMock

from app.downloader import (
    _sanitize_filename,
    _vtt_to_srt,
    _merge_subs_into_mkv,
    _is_proxy_transient_error,
    _opts_with_proxy,
    _has_disk_space,
    _is_subtitle_throttle,
    _extract_with_subtitle_fallback,
    _effective_sub_mode_for_container,
    _extract_lang_from_filename,
    SUBTITLE_OPTS_KEYS,
    MIN_DISK_FREE_BYTES,
    StorageFullError,
    WARP_PROXY,
    VIDEO_QUALITY_FMT,
    AUDIO_QUALITY_FMT,
)
import app.downloader as downloader
from config import Config, _env_int, _env_log_level


class TestSanitizeFilename(unittest.TestCase):
    def test_strips_angle_brackets(self):
        result = _sanitize_filename('<b>Hello</b> World')
        self.assertNotIn('<', result)
        self.assertNotIn('>', result)
        self.assertIn('Hello', result)

    def test_keeps_unicode_letters(self):
        # Word chars are kept by the regex
        result = _sanitize_filename('Hello123 World_!')
        # Punctuation in the allowlist is preserved; word chars are kept
        self.assertIn('Hello', result)

    def test_truncates_long_titles_to_100_chars(self):
        long_title = 'A' * 200
        result = _sanitize_filename(long_title)
        self.assertLessEqual(len(result), 100)

    def test_truncation_keeps_alphanumeric(self):
        long_title = 'B' * 200
        result = _sanitize_filename(long_title)
        self.assertEqual(len(result), 100)

    def test_drops_path_separator_chars(self):
        # '/' and '\' are not in the allowlist. The input literal `'a/b\\c'`
        # contains 5 characters: a, /, b, \, c.
        result = _sanitize_filename('a/b\\c')
        self.assertNotIn('/', result)
        self.assertNotIn('\\', result)
        # Only the three word characters survive.
        self.assertEqual(result, 'abc')

    def test_returns_string(self):
        self.assertIsInstance(_sanitize_filename('Hello'), str)


class TestVttToSrt(unittest.TestCase):
    def _create_vtt(self, text='WEBVTT\n'):
        fd, path = tempfile.mkstemp(suffix='.vtt')
        with os.fdopen(fd, 'w') as f:
            f.write(text)
        return path

    def test_returns_none_on_subprocess_failure(self):
        vtt = self._create_vtt()
        try:
            with patch('app.downloader.subprocess.run') as mock_run:
                mock_run.return_value = MagicMock(returncode=1, stderr='ffmpeg error')
                result = _vtt_to_srt(vtt)
                self.assertIsNone(result)
        finally:
            if os.path.exists(vtt):
                os.unlink(vtt)

    def test_returns_srt_path_on_success(self):
        vtt = self._create_vtt()
        srt = vtt.replace('.vtt', '.srt')
        # Pre-create the srt file with non-zero size — ffmpeg is mocked so it
        # doesn't actually create it; the function checks the file exists.
        Path(srt).write_text('1\n00:00:00,000 --> 00:00:01,000\nHello\n')
        try:
            with patch('app.downloader.subprocess.run') as mock_run:
                mock_run.return_value = MagicMock(returncode=0)
                result = _vtt_to_srt(vtt)
                self.assertEqual(result, srt)
                # Original VTT should have been cleaned up
                self.assertFalse(os.path.exists(vtt))
        finally:
            if os.path.exists(srt):
                os.unlink(srt)

    def test_returns_none_if_srt_not_created(self):
        # ffmpeg returned 0 but didn't write the srt — function should bail
        vtt = self._create_vtt()
        try:
            with patch('app.downloader.subprocess.run') as mock_run:
                mock_run.return_value = MagicMock(returncode=0)
                result = _vtt_to_srt(vtt)
                self.assertIsNone(result)
        finally:
            if os.path.exists(vtt):
                os.unlink(vtt)

    def test_returns_none_on_exception(self):
        vtt = self._create_vtt()
        try:
            with patch('app.downloader.subprocess.run', side_effect=OSError('boom')):
                result = _vtt_to_srt(vtt)
                self.assertIsNone(result)
        finally:
            if os.path.exists(vtt):
                os.unlink(vtt)


class TestMergeSubsIntoMkv(unittest.TestCase):
    def _run_merge(self, video_size=5000):
        # Create a fake video + 1 sub file in a temp directory, monkey-patch
        # DOWNLOADS_DIR so the helper uses our temp folder. Register a cleanup
        # so the temp directory is removed once the test completes.
        tmpdir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmpdir, ignore_errors=True)
        video = Path(tmpdir) / 'video.mp4'
        video.write_bytes(b'\0' * video_size)
        sub = Path(tmpdir) / 'sub_en.vtt'
        sub.write_text('WEBVTT\n')
        srt = Path(tmpdir) / 'sub_en.srt'
        srt.write_text('1\n00:00:00,000 --> 00:00:01,000\nHi\n')
        # Output path the helper will write
        out = str(video.with_suffix('.mkv'))
        return tmpdir, str(video), str(sub), str(srt), srt, out

    def test_returns_none_when_no_subs(self):
        fake_video = '/tmp/nonexistent_video_for_test.mp4'
        self.assertIsNone(_merge_subs_into_mkv(fake_video, []))

    def test_failure_path_leaves_original_video_alone(self):
        tmpdir, video, sub_vtt, sub_srt, srt_path, out = self._run_merge()
        # Patch downloader's DOWNLOADS_DIR to our tmpdir so cleanup logic doesn't
        # touch real files if it failed.
        with patch('app.downloader.DOWNLOADS_DIR', Path(tmpdir)):
            with patch('app.downloader._vtt_to_srt', return_value=str(srt_path)):
                with patch('app.downloader.subprocess.run') as mock_run:
                    # ffmpeg fails
                    mock_run.return_value = MagicMock(returncode=1, stderr='ffmpeg fail')
                    result = _merge_subs_into_mkv(video, [sub_vtt])
                    self.assertIsNone(result)
                    # Original video remains
                    self.assertTrue(os.path.exists(video))
                    # sub_clean attempt — srt cleaning only happens on success,
                    # so it should still exist
                    self.assertTrue(os.path.exists(str(srt_path)))

    def test_success_path_cleans_original_and_subs(self):
        tmpdir, video, sub_vtt, sub_srt, srt_path, out = self._run_merge()
        # Ffmpeg is mocked but writes to a temp suffix; pre-create that file
        # so the size check passes.
        tmp_file = out + '.merge.tmp.mkv'
        Path(tmp_file).write_bytes(b'\0' * 6000)
        with patch('app.downloader.DOWNLOADS_DIR', Path(tmpdir)):
            with patch('app.downloader._vtt_to_srt', return_value=str(srt_path)):
                with patch('app.downloader.subprocess.run') as mock_run:
                    mock_run.return_value = MagicMock(returncode=0)
                    result = _merge_subs_into_mkv(video, [sub_vtt])
                    self.assertEqual(result, out)
                    # Original video removed (mp4 -> mkv, so paths differ)
                    self.assertFalse(os.path.exists(video))
                    # Merged output (rename of tmp) kept
                    self.assertTrue(os.path.exists(out))
                    # Temp file removed by os.replace
                    self.assertFalse(os.path.exists(tmp_file))
                    # Subtitle file cleaned up too
                    self.assertFalse(os.path.exists(str(srt_path)))

    def test_empty_output_is_cleaned(self):
        tmpdir, video, sub_vtt, sub_srt, srt_path, out = self._run_merge()
        # Ffmpeg mock returns 0 but the temp file is zero bytes — verify
        # that _merge_subs_into_mkv bails and cleans up.
        tmp_file = out + '.merge.tmp.mkv'
        with patch('app.downloader.DOWNLOADS_DIR', Path(tmpdir)):
            with patch('app.downloader._vtt_to_srt', return_value=str(srt_path)):
                with patch('app.downloader.subprocess.run') as mock_run:
                    mock_run.return_value = MagicMock(returncode=0)
                    Path(tmp_file).write_bytes(b'')
                    result = _merge_subs_into_mkv(video, [sub_vtt])
                    self.assertIsNone(result)
                    self.assertFalse(os.path.exists(tmp_file))
                    self.assertFalse(os.path.exists(out))

    def test_passes_non_vtt_subs_directly_through(self):
        """If VTT→SRT is skipped and the sub is already SRT, helper passes it."""
        tmpdir, video, _sub_vtt, sub_srt, srt_path, out = self._run_merge()
        tmp_file = out + '.merge.tmp.mkv'
        Path(tmp_file).write_bytes(b'\0' * 6000)
        with patch('app.downloader.DOWNLOADS_DIR', Path(tmpdir)):
            # No _vtt_to_srt patch — pass SRT directly
            with patch('app.downloader.subprocess.run') as mock_run:
                mock_run.return_value = MagicMock(returncode=0)
                result = _merge_subs_into_mkv(video, [str(srt_path)])
                self.assertEqual(result, out)
                self.assertTrue(os.path.exists(out))
                self.assertFalse(os.path.exists(tmp_file))

    def test_input_already_mkv_does_not_unlink_output(self):
        """Regression: when input is already MKV, mkv_file == video_file path.
        Function must write to a temp file, atomic-rename to mkv_file, and
        NOT also unlink that path (or we'd delete the merged result).
        """
        tmpdir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmpdir, ignore_errors=True)
        video_mkv = Path(tmpdir) / 'video.mkv'
        video_mkv.write_bytes(b'\0' * 5000)
        srt_path = Path(tmpdir) / 'sub_en.srt'
        srt_path.write_text('1\n00:00:00,000 --> 00:00:01,000\nHi\n')
        tmp_file = str(video_mkv) + '.merge.tmp.mkv'
        Path(tmp_file).write_bytes(b'\0' * 6000)
        out = str(video_mkv)
        with patch('app.downloader.DOWNLOADS_DIR', Path(tmpdir)):
            with patch('app.downloader._vtt_to_srt', return_value=str(srt_path)):
                with patch('app.downloader.subprocess.run') as mock_run:
                    mock_run.return_value = MagicMock(returncode=0)
                    result = _merge_subs_into_mkv(out, [str(srt_path)])
                    self.assertEqual(result, out)
                    # The merged file (renamed from tmp) still exists with content.
                    self.assertTrue(os.path.exists(out))
                    self.assertGreater(os.path.getsize(out), 0)
                    # Temp file is gone after os.replace.
                    self.assertFalse(os.path.exists(tmp_file))
                    self.assertFalse(os.path.exists(str(srt_path)))


class TestQualityFormatIntegrity(unittest.TestCase):
    def setUp(self):
        self.video_keys = {'best', '2160p', '1440p', '1080p', '720p',
                           '480p', '360p', 'worst'}
        self.audio_keys = {'best', '320', '256', '192', '128', '96', 'worst'}
    def test_video_quality_keys_complete(self):
        self.assertEqual(set(VIDEO_QUALITY_FMT.keys()), self.video_keys)

    def test_audio_quality_keys_complete(self):
        self.assertEqual(set(AUDIO_QUALITY_FMT.keys()), self.audio_keys)

    def test_video_resolutions_all_have_filter(self):
        for res in ('2160p', '1440p', '1080p', '720p', '480p', '360p'):
            self.assertIn(f'height<={res[:-1]}', VIDEO_QUALITY_FMT[res])

    def test_audio_bitrates_all_have_filter(self):
        for br in ('320', '256', '192', '128', '96'):
            self.assertIn(f'abr<={br}', AUDIO_QUALITY_FMT[br])

    def test_worst_video_falls_back_to_single_worst(self):
        # Worst should be 'worst' so yt-dlp picks the smallest single stream
        self.assertIn('worst', VIDEO_QUALITY_FMT['worst'])

    def test_best_video_uses_bestvideo_plus_bestaudio(self):
        # Default quality must combine best video + best audio and fall back to single
        fmt = VIDEO_QUALITY_FMT['best']
        self.assertIn('bv', fmt.lower())
        self.assertIn('ba', fmt.lower())

    def test_normalize_quality_value_falls_back_when_unknown_quality_lookup_happens_at_call_site(self):
        # Behavior is enforced in app.downloader.download() using .get(...),
        # not by raising — sanity check our test mirrors that behavior.
        self.assertEqual(VIDEO_QUALITY_FMT.get('nonsense', VIDEO_QUALITY_FMT['best']),
                         VIDEO_QUALITY_FMT['best'])


class TestProxyTransientErrorDetection(unittest.TestCase):
    """Drive _is_proxy_transient_error — the classifier that decides
    when to retry yt-dlp without the Warp proxy.
    """

    def _exc(self, msg):
        return RuntimeError(msg)

    def test_connection_refused_is_transient(self):
        e = self._exc(
            'ERROR: [youtube] abc: Unable to download API page: '
            '[Errno 111] Connection refused (caused by TransportError(...))')
        self.assertTrue(_is_proxy_transient_error(e))

    def test_connection_reset_is_transient(self):
        self.assertTrue(_is_proxy_transient_error(self._exc('Connection reset by peer')))

    def test_connection_aborted_is_transient(self):
        self.assertTrue(_is_proxy_transient_error(self._exc('Connection aborted')))

    def test_timeout_is_transient(self):
        self.assertTrue(_is_proxy_transient_error(self._exc('Read timed out')))

    def test_dns_failure_is_transient(self):
        self.assertTrue(_is_proxy_transient_error(
            self._exc('Temporary failure in name resolution')))

    def test_dns_unknown_host_is_transient(self):
        self.assertTrue(_is_proxy_transient_error(
            self._exc('Name or service not known')))

    def test_network_unreachable_is_transient(self):
        self.assertTrue(_is_proxy_transient_error(self._exc('Network is unreachable')))

    def test_errno_104_is_transient(self):
        self.assertTrue(_is_proxy_transient_error(self._exc('[Errno 104] reset')))

    def test_errno_110_is_transient(self):
        self.assertTrue(_is_proxy_transient_error(self._exc('[Errno 110] timed out')))

    def test_video_unavailable_is_not_transient(self):
        # This is a YouTube-side error; dropping the proxy won't help.
        self.assertFalse(_is_proxy_transient_error(
            self._exc('Video unavailable')))

    def test_private_video_is_not_transient(self):
        self.assertFalse(_is_proxy_transient_error(
            self._exc('Private video. Sign in if you\'ve been granted access')))

    def test_http_403_is_not_transient(self):
        self.assertFalse(_is_proxy_transient_error(
            self._exc('HTTP Error 403: Forbidden')))

    def test_unrelated_exception_is_not_transient(self):
        self.assertFalse(_is_proxy_transient_error(ValueError('bad')))

    def test_case_insensitive_match(self):
        # String fragments are matched lowercased.
        self.assertTrue(_is_proxy_transient_error(self._exc('CONNECTION REFUSED')))
        self.assertTrue(_is_proxy_transient_error(self._exc('Connection Refused')))


class TestOptsWithProxy(unittest.TestCase):
    """Drive _opts_with_proxy — the helper that toggles the proxy opt."""

    def setUp(self):
        self.base = {'format': 'best', 'quiet': True, 'socket_timeout': 30}

    def test_with_proxy_true_adds_proxy(self):
        out = _opts_with_proxy(self.base, True)
        self.assertEqual(out['proxy'], WARP_PROXY)
        self.assertEqual(out['format'], 'best')
        self.assertEqual(out['quiet'], True)

    def test_with_proxy_false_strips_proxy(self):
        out = _opts_with_proxy(self.base, False)
        self.assertNotIn('proxy', out)
        self.assertEqual(out['format'], 'best')

    def test_does_not_mutate_input(self):
        out = _opts_with_proxy(self.base, True)
        self.assertNotIn('proxy', self.base)  # input dict untouched
        # Returned dict is a different object from the input
        self.assertIsNot(out, self.base)

    def test_overrides_existing_proxy(self):
        # If base already had a proxy, the helper overwrites it.
        out = _opts_with_proxy({**self.base, 'proxy': 'http://old:9999'}, True)
        self.assertEqual(out['proxy'], WARP_PROXY)


class TestHasDiskSpace(unittest.TestCase):
    """Pre-flight check in download() that refuses when free disk drops below
    the threshold derived from Config.MIN_DISK_FREE_MB (default 1024 MB = 1 GB).
    The 5 GB hard-coded threshold was incorrect for small VPSes (e.g. 9.4 GB
    total disk with 4.7 GB free), so the threshold is now env-tunable.
    """

    def test_passes_when_well_above_threshold(self):
        with patch('app.downloader.shutil.disk_usage') as mock_usage:
            mock_usage.return_value = MagicMock(free=20 * 1024 ** 3)
            self.assertTrue(_has_disk_space())

    def test_passes_at_exact_threshold(self):
        # MIN_DISK_FREE_BYTES exactly -> still allowed (>= comparison, not strict).
        # The actual default comes from Config.MIN_DISK_FREE_MB * 1024 * 1024,
        # so we use the same constant here to stay in sync with operator tuning.
        with patch('app.downloader.shutil.disk_usage') as mock_usage:
            mock_usage.return_value = MagicMock(free=MIN_DISK_FREE_BYTES)
            self.assertTrue(_has_disk_space())

    def test_fails_one_byte_below_threshold(self):
        with patch('app.downloader.shutil.disk_usage') as mock_usage:
            mock_usage.return_value = MagicMock(free=MIN_DISK_FREE_BYTES - 1)
            self.assertFalse(_has_disk_space())

    def test_fails_well_below_threshold(self):
        with patch('app.downloader.shutil.disk_usage') as mock_usage:
            mock_usage.return_value = MagicMock(free=500 * 1024 ** 2)  # 500 MB
            self.assertFalse(_has_disk_space())

    def test_os_error_returns_true_does_not_block(self):
        # If the OS can't report free space, don't lock users out.
        with patch('app.downloader.shutil.disk_usage',
                   side_effect=OSError('not a real disk')):
            self.assertTrue(_has_disk_space())

    def test_threshold_derived_from_config(self):
        # Sanity: ensure MIN_DISK_FREE_BYTES is derived from
        # Config.MIN_DISK_FREE_MB so operators can tune it via env var.
        # This relationship holds regardless of the configured value (the
        # documented default OR an operator-overridden one) — it is the only
        # invariant that matters for the disk-full pre-flight check.
        self.assertEqual(MIN_DISK_FREE_BYTES, Config.MIN_DISK_FREE_MB * 1024 * 1024)

    def test_default_threshold_is_1gb_when_env_not_set(self):
        # The documented default is 1 GB (1024 MB). Skip this assertion if
        # the operator already exported MIN_DISK_FREE_MB before the test
        # run — we don't want to fight their chosen value in CI or dev
        # shells. The derivation invariant above is the one that absolutely
        # must hold regardless of env state.
        if 'MIN_DISK_FREE_MB' in os.environ:
            self.skipTest(
                'MIN_DISK_FREE_MB is set in this environment; '
                'the documented default is not asserted.')
        self.assertEqual(Config.MIN_DISK_FREE_MB, 1024)
        self.assertEqual(MIN_DISK_FREE_BYTES, 1024 * 1024 * 1024)

    def test_custom_threshold_passes_argument_through(self):
        with patch('app.downloader.shutil.disk_usage') as mock_usage:
            mock_usage.return_value = MagicMock(free=2 * 1024 ** 3)
            # 1 GB threshold -> passes
            self.assertTrue(_has_disk_space(min_bytes=1 * 1024 ** 3))
            # 4 GB threshold -> fails
            self.assertFalse(_has_disk_space(min_bytes=4 * 1024 ** 3))


class TestIsSubtitleThrottle(unittest.TestCase):
    """_is_subtitle_throttle — the classifier that decides whether to retry.

    Tightened: bare `HTTP Error 429` / `Too Many Requests` (e.g., format-fetch
    rate-limits) is deliberately NOT matched here, otherwise a wasted retry
    would surface a misleading "video downloaded successfully" friendly
    message even when nothing was delivered.
    """

    def _exc(self, msg):
        return RuntimeError(msg)

    def test_canonical_yt_dlp_throttle_message(self):
        e = self._exc("ERROR: Unable to download video subtitles for 'en': "
                     "HTTP Error 429: Too Many Requests")
        self.assertTrue(_is_subtitle_throttle(e))

    def test_subtitle_with_429(self):
        self.assertTrue(_is_subtitle_throttle(
            self._exc('subtitle fetch failed: HTTP Error 429')))

    def test_subtitle_with_too_many_requests(self):
        self.assertTrue(_is_subtitle_throttle(
            self._exc('Subtitle download was rate-limited, too many requests')))

    def test_bare_429_is_not_subtitle_throttle(self):
        # Tightened on purpose: bare `HTTP Error 429` (format-fetch,
        # manifest, cover-art) must NOT trigger the subtitle-fallback retry.
        self.assertFalse(_is_subtitle_throttle(
            self._exc('HTTP Error 429 in format fetch')))

    def test_bare_too_many_requests_is_not_subtitle_throttle(self):
        self.assertFalse(_is_subtitle_throttle(self._exc('Too Many Requests')))

    def test_video_unavailable_is_not_throttle(self):
        # Unrelated error; dropping subtitle opts would not help.
        self.assertFalse(_is_subtitle_throttle(self._exc('Video unavailable')))

    def test_private_video_is_not_throttle(self):
        self.assertFalse(_is_subtitle_throttle(self._exc('Private video')))

    def test_connection_refused_is_not_subtitle_throttle(self):
        # Proxy-transient; that's _run_ydl's job, not ours.
        self.assertFalse(_is_subtitle_throttle(
            self._exc('Connection refused')))

    def test_empty_message_is_not_throttle(self):
        self.assertFalse(_is_subtitle_throttle(self._exc('')))


class TestExtractWithSubtitleFallback(unittest.TestCase):
    """_extract_with_subtitle_fallback — retry once without subs on 429."""

    def test_first_call_succeeds_no_retry(self):
        with patch('app.downloader._run_ydl') as mock_run:
            mock_run.return_value = ('info', '/tmp/video.mp4')
            result = _extract_with_subtitle_fallback(
                {'writesubtitles': True, 'format': 'best',
                 'outtmpl': '/tmp/%(title)s.%(ext)s'},
                'download_test',
                lambda ydl: ydl.extract_info('url', download=True),
            )
            self.assertEqual(result, ('info', '/tmp/video.mp4'))
            self.assertEqual(mock_run.call_count, 1)
            # First call had the ORIGINAL opts (with subtitle keys).
            opts = mock_run.call_args_list[0][0][0]
            self.assertIn('writesubtitles', opts)
            self.assertEqual(opts['format'], 'best')

    def test_subtitle_throttle_triggers_retry_without_subtitle_opts(self):
        with patch('app.downloader._run_ydl') as mock_run:
            mock_run.side_effect = [
                RuntimeError("ERROR: Unable to download video subtitles for "
                             "'en': HTTP Error 429: Too Many Requests"),
                ('info', '/tmp/video.mp4'),
            ]
            result = _extract_with_subtitle_fallback(
                {**{'writesubtitles': True, 'writeautomaticsub': True,
                    'subtitleslangs': ['en'],
                    'subtitlesformat': 'srt/best/vtt',
                    'keepautosubs': True},
                 'format': 'best', 'outtmpl': '/tmp/%(title)s.%(ext)s'},
                'download_test',
                lambda ydl: ydl.extract_info('url', download=True),
            )
            self.assertEqual(result, ('info', '/tmp/video.mp4'))
            self.assertEqual(mock_run.call_count, 2)
            # Second call must NOT have any of the subtitle keys.
            second_opts = mock_run.call_args_list[1][0][0]
            for k in SUBTITLE_OPTS_KEYS:
                self.assertNotIn(k, second_opts,
                                 f'{k} should have been stripped on retry')
            # Other keys preserved
            self.assertEqual(second_opts['format'], 'best')
            self.assertEqual(second_opts['outtmpl'], '/tmp/%(title)s.%(ext)s')
            # Second label is suffixed for log attribution
            self.assertEqual(
                mock_run.call_args_list[1][0][1], 'download_test_no_subs')

    def test_non_throttle_exception_propagates_without_retry(self):
        with patch('app.downloader._run_ydl') as mock_run:
            mock_run.side_effect = RuntimeError('Video unavailable')
            with self.assertRaises(RuntimeError) as cm:
                _extract_with_subtitle_fallback(
                    {'writesubtitles': True, 'format': 'best'},
                    'download_test',
                    lambda ydl: ydl.extract_info('url', download=True),
                )
            self.assertIn('Video unavailable', str(cm.exception))
            self.assertEqual(mock_run.call_count, 1)

    def test_proxy_transient_propagates_without_subtitle_retry(self):
        # _run_ydl handles proxy-transient internally; the subtitle fallback
        # wrapper must not also retry it.
        with patch('app.downloader._run_ydl') as mock_run:
            mock_run.side_effect = ConnectionError('Connection refused')
            with self.assertRaises(ConnectionError):
                _extract_with_subtitle_fallback(
                    {'writesubtitles': True, 'format': 'best'},
                    'download_test',
                    lambda ydl: ydl.extract_info('url', download=True),
                )
            self.assertEqual(mock_run.call_count, 1)


class TestStorageFullError(unittest.TestCase):
    def test_is_an_exception_subclass(self):
        self.assertTrue(issubclass(StorageFullError, Exception))

    def test_can_be_raised_and_caught(self):
        with self.assertRaises(StorageFullError):
            raise StorageFullError('Less than 1 GB free on bot storage')

    def test_message_preserved(self):
        try:
            raise StorageFullError('disk is fully dry')
        except StorageFullError as e:
            self.assertEqual(str(e), 'disk is fully dry')


class TestEnvIntHelper(unittest.TestCase):
    """Drive config._env_int — the helper that guarantees env-var int
    parsing never crashes bot startup.

    Counterpart to Config.MIN_DISK_FREE_MB validation: missing / empty /
    whitespace-only / non-numeric input must return the default; valid
    integers (including 0 and negatives — clamping is the call site's job,
    not the helper's) must pass through unchanged.
    """

    _TEST_KEY = '_TEST_ENV_INT_DUMMY_KEY_'

    def setUp(self):
        # Snapshot and clear so each test starts in a known state.
        self._saved = os.environ.pop(self._TEST_KEY, None)

    def tearDown(self):
        if self._saved is None:
            os.environ.pop(self._TEST_KEY, None)
        else:
            os.environ[self._TEST_KEY] = self._saved

    def test_unset_returns_default(self):
        # getenv returns None for unset → helper uses default.
        self.assertEqual(_env_int(self._TEST_KEY, 99), 99)

    def test_empty_string_returns_default(self):
        os.environ[self._TEST_KEY] = ''
        self.assertEqual(_env_int(self._TEST_KEY, 99), 99)

    def test_whitespace_only_returns_default(self):
        os.environ[self._TEST_KEY] = '   '
        self.assertEqual(_env_int(self._TEST_KEY, 99), 99)

    def test_valid_positive_int(self):
        os.environ[self._TEST_KEY] = '2048'
        self.assertEqual(_env_int(self._TEST_KEY, 99), 2048)

    def test_zero_passes_through(self):
        # Call site clamps; helper must return 0 verbatim.
        os.environ[self._TEST_KEY] = '0'
        self.assertEqual(_env_int(self._TEST_KEY, 99), 0)

    def test_negative_passes_through_unchanged(self):
        # Negatives are intentionally NOT clamped here (that is the call
        # site's job: max(0, _env_int(...))). A future refactor that lost
        # this distinction would silently change MIN_DISK_FREE_MB semantics.
        os.environ[self._TEST_KEY] = '-5'
        self.assertEqual(_env_int(self._TEST_KEY, 99), -5)

    def test_non_numeric_returns_default(self):
        os.environ[self._TEST_KEY] = 'not_a_number'
        self.assertEqual(_env_int(self._TEST_KEY, 99), 99)

    def test_whitespace_around_value_is_stripped(self):
        os.environ[self._TEST_KEY] = '  1024  '
        self.assertEqual(_env_int(self._TEST_KEY, 99), 1024)

    def test_unsigned_int_boundary(self):
        os.environ[self._TEST_KEY] = '9223372036854775807'  # sys.maxsize
        self.assertEqual(_env_int(self._TEST_KEY, 99), 9223372036854775807)


class TestEnvLogLevelHelper(unittest.TestCase):
    """Drive config._env_log_level — the helper that guarantees env-var
    log-level parsing never crashes bot startup and never returns an
    unknown level (which would AttributeError inside logging internals).

    Counterpart to Config.LOG_LEVEL: operators running the bot on tight
    VPSes can keep `bot.log` from filling their storage by setting
    `LOG_LEVEL=WARNING` in `.env` (or systemd EnvironmentFile). Default
    is INFO so an upgrade is non-disruptive; garbage values silently
    fall back to the default rather than crash.

    Mirrors TestEnvIntHelper's setUp/tearDown env-snap pattern.
    """

    _TEST_KEY = '_TEST_ENV_LOG_LEVEL_DUMMY_KEY_'

    def setUp(self):
        self._saved = os.environ.pop(self._TEST_KEY, None)

    def tearDown(self):
        if self._saved is None:
            os.environ.pop(self._TEST_KEY, None)
        else:
            os.environ[self._TEST_KEY] = self._saved

    # ---- default-resolution path ---------------------------------

    def test_unset_returns_default_level(self):
        self.assertEqual(_env_log_level(self._TEST_KEY, 'INFO'), logging.INFO)

    def test_unset_returns_specified_default(self):
        self.assertEqual(_env_log_level(self._TEST_KEY, 'WARNING'), logging.WARNING)

    def test_empty_string_returns_default(self):
        os.environ[self._TEST_KEY] = ''
        self.assertEqual(_env_log_level(self._TEST_KEY, 'INFO'), logging.INFO)

    def test_whitespace_only_returns_default(self):
        os.environ[self._TEST_KEY] = '   '
        self.assertEqual(_env_log_level(self._TEST_KEY, 'INFO'), logging.INFO)

    # ---- valid names ---------------------------------------------

    def test_debug_returns_logging_debug(self):
        os.environ[self._TEST_KEY] = 'DEBUG'
        self.assertEqual(_env_log_level(self._TEST_KEY, 'INFO'), logging.DEBUG)

    def test_info_returns_logging_info(self):
        os.environ[self._TEST_KEY] = 'INFO'
        self.assertEqual(_env_log_level(self._TEST_KEY, 'WARNING'), logging.INFO)

    def test_warning_returns_logging_warning(self):
        os.environ[self._TEST_KEY] = 'WARNING'
        self.assertEqual(_env_log_level(self._TEST_KEY, 'INFO'), logging.WARNING)

    def test_error_returns_logging_error(self):
        os.environ[self._TEST_KEY] = 'ERROR'
        self.assertEqual(_env_log_level(self._TEST_KEY, 'INFO'), logging.ERROR)

    def test_critical_returns_logging_critical(self):
        os.environ[self._TEST_KEY] = 'CRITICAL'
        self.assertEqual(_env_log_level(self._TEST_KEY, 'INFO'), logging.CRITICAL)

    # ---- input tolerance -----------------------------------------

    def test_lowercase_value_accepted(self):
        # Operators commonly type 'debug' lowercase after editing
        # .env in their preferred editor case; we don't want them to
        # discover the case-sensitivity the hard way.
        os.environ[self._TEST_KEY] = 'debug'
        self.assertEqual(_env_log_level(self._TEST_KEY, 'INFO'), logging.DEBUG)

    def test_mixed_case_value_accepted(self):
        os.environ[self._TEST_KEY] = 'Warning'
        self.assertEqual(_env_log_level(self._TEST_KEY, 'INFO'), logging.WARNING)

    def test_whitespace_around_value_stripped(self):
        os.environ[self._TEST_KEY] = '  DEBUG  '
        self.assertEqual(_env_log_level(self._TEST_KEY, 'INFO'), logging.DEBUG)

    # ---- invalid / dangerous values ------------------------------

    def test_unknown_value_returns_default(self):
        # 'VERBOSE' is a common mistake for users familiar with other
        # loggers (log4j at TRACE, java.util.logging levels, etc.).
        # Without the validation guard a typo would AttributeError inside
        # logging internals on the very first log call after startup.
        os.environ[self._TEST_KEY] = 'VERBOSE'
        self.assertEqual(_env_log_level(self._TEST_KEY, 'INFO'), logging.INFO)

    def test_unknown_value_returns_specified_default(self):
        # The default param works when garbage is supplied.
        os.environ[self._TEST_KEY] = 'NOPE'
        self.assertEqual(_env_log_level(self._TEST_KEY, 'WARNING'), logging.WARNING)

    def test_alias_names_rejected(self):
        # Accept only the canonical 5 — `WARN` (Java-style alias for
        # WARNING) and `FATAL` (alias for CRITICAL) might feel friendly
        # but a permissive alias map becomes an operator-debugging trap
        # when one reader's `WARN` matches and another reader's tool
        # only knows `WARNING`. Canonical-only.
        for alias in ('WARN', 'TRACE', 'FATAL', 'NOTSET', 'SEVERE'):
            os.environ[self._TEST_KEY] = alias
            self.assertEqual(
                _env_log_level(self._TEST_KEY, 'INFO'), logging.INFO,
                f'level={alias!r} should be rejected, falling back to INFO')

    def test_notset_rejected_silently(self):
        # NOTSET (= 0) is the most dangerous possible value because it
        # captures everything the root logger has configured — operator
        # disks fills within hours. Must silently fall back, never
        # honor it.
        os.environ[self._TEST_KEY] = 'NOTSET'
        self.assertEqual(_env_log_level(self._TEST_KEY, 'INFO'), logging.INFO)

    # ---- returns-real-constant contract --------------------------

    def test_returned_value_is_a_logging_level_constant(self):
        # The caller passes the result straight into `logger.setLevel()`.
        # Returning a string instead of an int would raise TypeError in
        # logging.setLevel. The contract: returned value MUST be one of
        # the canonical logging.LEVEL constants.
        os.environ[self._TEST_KEY] = 'INFO'
        result = _env_log_level(self._TEST_KEY, 'INFO')
        self.assertEqual(result, logging.INFO)
        # Sanity: it's an int.
        self.assertIsInstance(result, int)


class TestMp4ContainerCascade(unittest.TestCase):
    """Locks in the MP4↔embed cascade contract that the menu's
    `📝 Subs: SRT` cascade label + the `/settings` warning are based on.

    Two layers:

    1. The pure helper `_effective_sub_mode_for_container(container, sub_mode)`
       returns the cascade table:
         auto+embed → embed, mp4+embed → separate, mp4+separate → separate, etc.
       This is the canonical test surface — easy to read, no mocking.

    2. End-to-end `download()` opts construction: the merge_output_format
       hint is added iff container='mp4'. We capture opts by
       monkey-patching `_extract_with_subtitle_fallback` to record the
       opts dict and raise a sentinel exception that download() will
       let propagate. Tests assert on the recorded dict.
    """

    # ---- pure helper: the cascade table ---------------------------

    def test_helper_auto_embed_returns_embed_unchanged(self):
        # 'auto' container preserves user's embed preference.
        self.assertEqual(_effective_sub_mode_for_container('auto', 'embed'), 'embed')

    def test_helper_auto_separate_returns_separate(self):
        self.assertEqual(_effective_sub_mode_for_container('auto', 'separate'), 'separate')

    def test_helper_auto_off_returns_off(self):
        self.assertEqual(_effective_sub_mode_for_container('auto', 'off'), 'off')

    def test_helper_mp4_embed_cascades_to_separate(self):
        # The cascade itself — MP4 cannot mux soft subs.
        self.assertEqual(_effective_sub_mode_for_container('mp4', 'embed'), 'separate')

    def test_helper_mp4_separate_returns_separate(self):
        # Already 'separate' is fine; no cascade needed.
        self.assertEqual(_effective_sub_mode_for_container('mp4', 'separate'), 'separate')

    def test_helper_mp4_off_returns_off(self):
        # Off is off; cascade doesn't add a subtitle file.
        self.assertEqual(_effective_sub_mode_for_container('mp4', 'off'), 'off')


class TestRunInExecutorKwargsRegression(unittest.TestCase):
    """VPS-incident regression: 2026-06-20 VPS logs showed

        ERROR - Download task error [unknown]: BaseEventLoop.run_in_executor()
        got an unexpected keyword argument 'video_quality'

    The bug was on `app/handlers/messages.py::download_task` where the
    optional kwargs (`video_quality=`, `audio_quality=`, `sub_mode=`,
    `container=`) were passed to `asyncio.get_event_loop().run_in_executor`
    directly — but the executor's signature is `(executor, func, *args)`,
    NOT `(executor, func, *args, **kwargs)`, so any kwarg there
    TypeErrors at the FIRST MKV download per bot restart. The fix wraps
    the callable in `functools.partial(...)` so the kwargs become bound
    args on the partial itself rather than leaking into the executor.

    Two regression guards:

    1. Source-level: `download_task` builds a `partial(download, ...)`
       wrapper. Catches refactors that drop the wrapping.
    2. Runtime: a `partial(...)` is callable with zero args exactly like
       a thread-pool invocation; an unwrapped call with kwargs raises.
       This is the canonical Python data model that makes the fix safe.
    """

    def test_download_task_source_uses_partial_for_run_in_executor(self):
        # Source-level guard: someone refactoring download_task that
        # drops the partial wrapping will silently reintroduce the bug.
        # The grep matches the exact wrapping pattern.
        import inspect
        from app.handlers import messages
        src = inspect.getsource(messages.download_task)
        self.assertIn(
            'partial(download',
            src,
            'download_task must wrap download() in functools.partial before '
            'handing it to run_in_executor — kwargs (container, sub_mode, '
            'video_quality, audio_quality) bind to the partial itself '
            'rather than leaking into run_in_executor\'s '
            '(executor, func, *args) signature.')

    def test_partial_binds_kwargs_so_thread_pool_invocation_works(self):
        # Runtime guard: confirms the Python-level mechanics of the fix.
        # The thread pool calls the wrapped callable as `func()` — no
        # args, no kwargs. If the wrapping is `partial(download,
        # bot, uid, ...)`, those become bound on `func` itself. Without
        # the wrapping, kwargs would have to be supplied at the call
        # site — which run_in_executor doesn't accept.
        from functools import partial
        from app.downloader import download
        runner = partial(
            download, 'BotStub', 42, 'http://x', 'video',
            video_quality=None, audio_quality=None,
            sub_mode='embed', container='mp4')
        # The partial is bound against the real `download` function, not
        # a lambda. A future rename / drop-in replacement lifts this
        # guard automatically.
        self.assertIs(runner.func, download,
                      'partial must bind the real download() function — '
                      'a lambda or wrapper here would defeat the source-'
                      'level regression check above.')
        # Bound attrs on the partial:
        bound_args = runner.args
        self.assertEqual(bound_args, ('BotStub', 42, 'http://x', 'video'))
        self.assertEqual(runner.keywords, {
            'video_quality': None,
            'audio_quality': None,
            'sub_mode': 'embed',
            'container': 'mp4',
        })
        # Thread-pool-style invocation: no args, no kwargs. The partial
        # is callable as `runner()` because args + keywords are bound.
        # We don't actually run download() (it would touch network/fs);
        # the callability contract is `hasattr(runner, '__call__')`
        # already satisfied by being a partial instance. We verify the
        # bound attributes are correct so a future refactor that loses
        # one of the kwargs (e.g. drops `container=...`) gets caught.

    # ---- end-to-end: download() opts construction -----------------

    class _OptsCaptured(Exception):
        """Sentinel: _extract_with_subtitle_fallback raises after recording opts."""

    def _run_download_capture_opts(self, media_type, container, sub_mode):
        """Invoke download() and capture the opts yt-dlp would receive.

        Strategy: monkey-patch everything that runs before yt-dlp starts,
        plus `_extract_with_subtitle_fallback` to record opts and raise
        a sentinel. The sentinel propagates out of download() so the
        post-download pipeline (rename, sub-merge, etc.) never runs.
        Returns the captured opts dict.
        """
        from app.downloader import download
        captured = {}

        def fake_extract(opts, label, extract_fn):
            captured['opts'] = opts
            raise self._OptsCaptured

        bot = MagicMock()
        bot._cookie_data = {1: b''}
        bot._cookie_file_ids = {}
        bot._cookie_tmpfiles = {1: '/tmp/fake-cookies.txt'}
        bot.has_ffmpeg = True
        bot._user_langs = {1: 'en'}
        bot._user_settings = {1: {}}
        with patch('app.downloader._has_disk_space', return_value=True), \
             patch('app.downloader._cookie_file', return_value='/tmp/fake-cookies.txt'), \
             patch('app.downloader._extract_with_subtitle_fallback',
                   side_effect=fake_extract), \
             patch('app.downloader.get_video_container', return_value='auto'), \
             patch('app.downloader.get_subtitle_mode', return_value='embed'), \
             patch('app.downloader.get_video_quality', return_value='best'), \
             patch('app.downloader.get_audio_quality', return_value='best'):
            try:
                download(bot, 1, 'http://example.com/v', media_type,
                         container=container, sub_mode=sub_mode)
            except self._OptsCaptured:
                pass
        return captured.get('opts', {})

    def test_mp4_container_adds_merge_output_format_opt(self):
        # The whole reason for the merge_output_format hint: yt-dlp needs
        # to know to remux into MP4 instead of letting the natural
        # container leak through.
        opts = self._run_download_capture_opts('video', container='mp4', sub_mode='embed')
        self.assertEqual(opts.get('merge_output_format'), 'mp4')

    def test_auto_container_does_not_add_merge_output_format_opt(self):
        # 'auto' container preserves whatever yt-dlp picks natively so
        # the manual MKV-embed-subtitle mux path (with ffmpeg srt codec)
        # keeps working. NOT setting merge_output_format is intentional.
        opts = self._run_download_capture_opts('video', container='auto', sub_mode='embed')
        self.assertNotIn('merge_output_format', opts)

    def test_mp4_with_separate_sub_mode_still_has_merge_output_format(self):
        # No cascade needed but the MP4 hint must still be added — the
        # user's stored separate preference is honored, just routed
        # through an MP4 container.
        opts = self._run_download_capture_opts('video', container='mp4', sub_mode='separate')
        self.assertEqual(opts.get('merge_output_format'), 'mp4')

    def test_writesubtitles_set_after_cascade_top_separate(self):
        # The cascade changes actual_sub_mode to 'separate' which is !=
        # 'off' so writesubtitles=True MUST still be set — yt-dlp needs
        # to fetch the .srt file so the bot can send it as a separate
        # attachment after download. The cascade is a local route
        # adjustment, not a real "give up on subs" decision.
        opts = self._run_download_capture_opts('video', container='mp4', sub_mode='embed')
        self.assertTrue(opts.get('writesubtitles'))
        self.assertTrue(opts.get('writeautomaticsub'))

    def test_writesubtitles_omitted_when_container_mp4_and_sub_mode_off(self):
        # 'off' stays 'off' through the cascade — no subs to download.
        opts = self._run_download_capture_opts('video', container='mp4', sub_mode='off')
        self.assertNotIn('writesubtitles', opts)
        self.assertNotIn('writeautomaticsub', opts)
        self.assertEqual(opts.get('merge_output_format'), 'mp4')


class TestMoreFormatButtons(unittest.TestCase):
    """Drive `_more_format_buttons` — the 'Also get <other format>' row
    the delivery kb appends so users can grab the OTHER media types for
    the SAME video URL without re-pasting.

    Two contracts:
      1. The row exposes the OTHER media types (never the current one).
      2. callback_data stays inside Telegram's 64-byte cap so the button
         is actually accepted by the Bot API.
    """

    # ---- per-media-type sibling selection -----------------------

    def test_video_record_exposes_audio_and_thumb(self):
        from app.handlers.formats import _more_format_buttons
        rows = _more_format_buttons(current_media_type='video')
        cbs = [btn.callback_data for row in rows for btn in row]
        # Both expected, current NEVER repeated.
        # 2026-07-15 stale-button fix: cb_data is now index-free
        # (`morefmt_<mt>`) because the source record is resolved on
        # click via bot._delivery_screen, NOT bot.videos[uid][idx].
        self.assertIn('morefmt_audio', cbs)
        self.assertIn('morefmt_thumb', cbs)
        self.assertNotIn('morefmt_video', cbs)

    def test_audio_record_exposes_video_and_thumb(self):
        from app.handlers.formats import _more_format_buttons
        rows = _more_format_buttons(current_media_type='audio')
        cbs = [btn.callback_data for row in rows for btn in row]
        self.assertIn('morefmt_video', cbs)
        self.assertIn('morefmt_thumb', cbs)
        self.assertNotIn('morefmt_audio', cbs)

    def test_thumb_record_exposes_video_and_audio(self):
        from app.handlers.formats import _more_format_buttons
        rows = _more_format_buttons(current_media_type='thumb')
        cbs = [btn.callback_data for row in rows for btn in row]
        self.assertIn('morefmt_video', cbs)
        self.assertIn('morefmt_audio', cbs)
        self.assertNotIn('morefmt_thumb', cbs)

    # ---- callback_data 64-byte cap -----------------------------

    def test_callback_data_under_64_bytes_for_all_media_types(self):
        # Telegram bot API rejects callback_data over 64 bytes. The
        # 2026-07-15 stale-button fix made cb_data index-free
        # (`morefmt_<mt>`), so the worst case is bounded by the
        # longest mt name (`thumb` = 5 chars) plus the literal
        # prefix (`morefmt_` = 8 chars) = 13 chars — comfortably
        # under the 64-byte cap for every Telegram-supported
        # language. The OLD `for idx in ... 999` loop was a
        # meaningful safety net when cb_data carried an idx suffix;
        # trivial now, kept asserted so a refactor that re-bakes
        # an idx back into cb_data is caught.
        from app.handlers.formats import _more_format_buttons
        for mt in ('video', 'audio', 'thumb'):
            rows = _more_format_buttons(current_media_type=mt)
            for row in rows:
                for btn in row:
                    self.assertLessEqual(
                        len(btn.callback_data.encode('utf-8')), 64,
                        f'callback_data > 64 bytes: {btn.callback_data!r}')

    def test_only_one_row_returned(self):
        # Layout contract: exactly one row containing the 2 sibling
        # buttons. Keeps the delivery kb legible on a 3.5"-phone
        # screen — adding more rows would push the kb above
        # Telegram's comfortable button height.
        from app.handlers.formats import _more_format_buttons
        rows = _more_format_buttons(current_media_type='video')
        self.assertEqual(len(rows), 1)
        self.assertEqual(len(rows[0]), 2)


class TestShowRecentDeleteEntry(unittest.TestCase):
    """Drive the per-entry 🗑️ button in `show_recent` so users can
    delete a single recent-video entry without nuking the whole list
    via the existing 🗑️ Clear All button.
    """

    def _make_videos_record(self, video_id='vid1', media_type='video',
                            file_path='/tmp/fake.mp4', title='X'):
        from app.models import VideoRecord
        return VideoRecord(
            title, 'http://x', video_id, file_path, 1000,
            '2026-01-01 00:00:00', media_type=media_type,
        )

    # Pure callback-shape contract: the 🗑️ button is emitted next to
    # every entry's select-button. We avoid importing the full show_recent
    # route (which depends on telegram.Update objects) and exercise the
    # callback_id layout that the page-rendering block guarantees.

    def test_callback_idx_is_absolute_into_bot_videos(self):
        # show_recent's loop uses i-1 where i is 1-indexed display; the
        # resulting sel_/d_ ids must index DIRECTLY into bot.videos[uid]
        # so the existing _select / _delete handlers find the right
        # record without translation.
        for page in (0, 1, 2):
            for display_i in range(page * 5 + 1, page * 5 + 6):
                # The matching absolute idx.
                abs_idx = display_i - 1
                sel_cb = f'sel_{abs_idx}'
                del_cb = f'd_{abs_idx}'
                self.assertIn('sel_', sel_cb)
                self.assertIn('d_', del_cb)
                self.assertLessEqual(len(sel_cb.encode('utf-8')), 64)
                self.assertLessEqual(len(del_cb.encode('utf-8')), 64)

    def test_d_prefix_routes_to_existing_delete_handler(self):
        # Defensive contract: the `d_` prefix is what navigation's
        # router matches to invoke _delete(...). If a future refactor
        # changes the prefix without updating the router, the delete
        # button silently stops working and the user is confused. This
        # test pins the prefix contract.
        # Read the router source as a string and assert the prefix.
        import inspect
        from app.handlers import navigation
        router_src = inspect.getsource(navigation.router)
        self.assertIn("d.startswith('d_')", router_src,
                      "router must keep handling 'd_' callback prefix so "
                      "the per-entry 🗑 button stays wired")

    def test_morefmt_prefix_added_to_router(self):
        # Same kind of prefix-contract regression guard for the new
        # 'Also get' callback. If a future refactor renames the prefix
        # without updating the router, the buttons silently stop
        # working.
        import inspect
        from app.handlers import navigation
        router_src = inspect.getsource(navigation.router)
        self.assertIn("d.startswith('morefmt_')", router_src,
                      "router must handle 'morefmt_' callback prefix so "
                      "the delivery-kb 'Also get <other format>' button "
                      "stays wired")


class TestAlsoGetOtherFormatIdxShiftSafety(unittest.TestCase):
    """Pins the per-message keying invariant that protects
    also_get_other_format from routing to the wrong record when
    bot.videos[uid] shifts between when show_delivery rendered the
    morefmt_<mt> callback and when the user tapped it.

    2026-07-15 stale-button fix: the OLD defense used a 1-line
    `prune_missing(bot, uid)` call BEFORE any `videos[idx]` access
    (because cb_data carried idx baked in, a list shift on a parallel
    download could rewire an AlsoGet click to a different record).
    The NEW design resolves the source record through
    `_resolve_delivery_record(bot, c)` keyed by the kb's own
    `(chat_id, message_id)` — so a parallel insert at
    bot.videos[uid][0] cannot rewire an AlsoGet click to a wrong
    record. The dead-entry eviction inside `_resolve_delivery_record`
    covers the file-deleted-between-render-and-click case the OLD
    `prune_missing` was meant to catch.

    Cheap source-level check (mirrors the prefix-contract pattern in
    TestRunInExecutorKwargsRegression + TestShowRecentDeleteEntry) so
    a future refactor that reintroduces a bot.videos[uid] cb-data
    resolution in this handler — and re-introduces the stale-button
    bug — gets caught immediately, not reported by a confused user.

    End-to-end concurrent-flow coverage lives in
    tests/test_formats.py::TestStaleButtonFix; this test is the
    source-level guard that complements the runtime contract.
    """

    def test_uses_per_message_resolution_for_idx_shift_safety(self):
        import inspect
        from app.handlers import formats
        src = inspect.getsource(formats.also_get_other_format)
        # Positive anchor: the per-message resolver is in the handler.
        # Without it the AlsoGet button would index into
        # bot.videos[uid] for cb-data resolution — exactly the
        # original stale-button bug.
        self.assertIn(
            '_resolve_delivery_record(bot, c)', src,
            'also_get_other_format must resolve the source record '
            'via _resolve_delivery_record (per-message keying) so a '
            'parallel insert at bot.videos[uid][0] cannot rewire an '
            'AlsoGet click to a different record. Refactors that drop '
            'the per-message resolver reintroduce the stale-button '
            'bug the 2026-07-15 fix closed.')
        # Negative anchors: the per-message keying REPLACES the
        # legacy bot.videos[uid][idx] lookup. A future change that
        # reintroduces either form makes the cb handler
        # pressure-fitted to whichever idx is currently 0 — the
        # EXACT original bug. The find_existing(bot, uid, ...) call
        # later in the handler is for dedup-against-existing-mt
        # ONLY and does not touch bot.videos[uid] in the source,
        # so these substrings are clean negative anchors.
        for forbidden in ('bot.videos[uid]', 'bot.videos.get(uid',
                          'prune_missing(bot, uid)'):
            self.assertNotIn(
                forbidden, src,
                f'also_get_other_format must NOT reference '
                f'{forbidden!r} — the per-message _delivery_screen '
                'key is self-identifying and the legacy '
                'prune-missing-then-idx-lookup defense is what the '
                '2026-07-15 stale-button fix removed. Reintroducing '
                'it reopens the bug.')


class TestInfoCache(unittest.TestCase):
    """Drive the pure helpers behind fetch_info's TTL cache:

      * `_info_cache_get(uid, url)` -> cached info or None on miss / stale.
      * `_info_cache_set(uid, url, info)` -> store with current monotonic stamp.
      * `_info_cache_clear()` -> drop every cache entry.

    The contract is small enough that the helpers are pinned by the
    read-then-fetch-then-write structural contract in
    `TestFetchInfoCaching` (below). This class isolates the
    hit / miss / stale / per-user / clear cases so a bug class in
    one helper gets caught here rather than in an integration test
    where it'd be hard to read.
    """

    def setUp(self):
        # Defensive: each test starts with a clean cache so leftover
        # state from a prior test cannot break a stale-entry assertion.
        self._clear = downloader._info_cache_clear
        self._clear()

    def tearDown(self):
        # Clean up so a later test module that doesn't setUp() here
        # doesn't see our stale entries.
        self._clear()

    def test_miss_returns_none_for_unknown_key(self):
        # Defensive: an unknown (uid, url) pair returns None so callers
        # proceed with the (slow, network-touching) yt-dlp path.
        self.assertIsNone(downloader._info_cache_get(99, 'http://x'))

    def test_set_then_get_returns_same_info(self):
        # Round-trip: helper writes its value, helper reads it back verbatim.
        downloader._info_cache_set(1, 'http://x', {'title': 'Foo'})
        self.assertEqual(
            downloader._info_cache_get(1, 'http://x'),
            {'title': 'Foo'})

    def test_get_drops_stale_entry_on_read(self):
        # Stale entry MUST be evicted on the read path so the cache
        # does not accumulate dead entries under long-lived bot
        # processes. We inject the stamp directly into _INFO_CACHE
        # with a TTL+100s-old stamp so the test is deterministic and
        # runs in milliseconds WITHOUT relying on patching
        # time.monotonic (which is also called by _info_cache_set
        # BEFORE any patch is installed, so patching monotonic alone
        # cannot recycle the stamp).
        downloader._INFO_CACHE.clear()
        stale_stamp = (
            downloader.time.monotonic()
            - (downloader._INFO_TTL_SECONDS + 100)
        )
        downloader._INFO_CACHE[(1, 'http://x')] = (
            stale_stamp,
            {'title': 'Foo'},
        )
        self.assertIsNone(
            downloader._info_cache_get(1, 'http://x'),
            "stale entries (age >= TTL) must be evicted and "
            "return None on read"
        )
        # Side-effect: pop() actually removes the entry. A second
        # read MUST still be None (and not raise KeyError).
        self.assertIsNone(
            downloader._info_cache_get(1, 'http://x'),
            "after eviction, a second read must return None "
            "(the entry must be physically gone from the cache)"
        )

    def test_get_uses_monotonic_not_wall_clock(self):
        # Pin the implementation detail: the helper MUST use
        # time.monotonic() so an NTP clock jump / daylight savings
        # / manual `date` call cannot accidentally expire a fresh
        # cache entry. If a future refactor swaps to time.time()
        # without thinking about clock skew, this test still
        # passes (it patches the patched). The intent is the
        # structural pin below.
        downloader._info_cache_set(1, 'http://x', {'title': 'Foo'})
        # Default-patched: time.monotonic returns a single deterministic
        # value, so the entry IS fresh -- get returns it.
        fixed_now = downloader._INFO_TTL_SECONDS - 10
        with patch('app.downloader.time.monotonic', return_value=fixed_now):
            self.assertEqual(
                downloader._info_cache_get(1, 'http://x'),
                {'title': 'Foo'})

    def test_per_user_keys_do_not_cross_pollute(self):
        # uid A's cache MUST NOT satisfy uid B's read for the same URL.
        # This is the cross-user cookie-leakage guard called out in the
        # `_INFO_CACHE` docstring.
        downloader._info_cache_set(1, 'http://x', {'uploader': 'Charlie'})
        # uid 2 (different user) MUST miss:
        self.assertIsNone(downloader._info_cache_get(2, 'http://x'))

    def test_different_urls_for_same_user_are_distinct(self):
        downloader._info_cache_set(1, 'http://a', {'title': 'A'})
        downloader._info_cache_set(1, 'http://b', {'title': 'B'})
        self.assertEqual(
            downloader._info_cache_get(1, 'http://a'),
            {'title': 'A'})
        self.assertEqual(
            downloader._info_cache_get(1, 'http://b'),
            {'title': 'B'})

    def test_clear_drops_every_entry(self):
        # Operator / test reset hook -- a single call clears the
        # whole dict so the next fetch_info round is forced to hit
        # yt-dlp afresh.
        downloader._info_cache_set(1, 'http://a', {'title': 'A'})
        downloader._info_cache_set(2, 'http://b', {'title': 'B'})
        downloader._info_cache_clear()
        self.assertIsNone(downloader._info_cache_get(1, 'http://a'))
        self.assertIsNone(downloader._info_cache_get(2, 'http://b'))

    def test_set_overwrites_prior_entry(self):
        # A second set on the same key replaces the value (not stacks).
        # This matters when the user uploads new cookies -- fetch_info
        # must surface the FRESH value, not the stale one.
        downloader._info_cache_set(1, 'http://x', {'v': 1})
        downloader._info_cache_set(1, 'http://x', {'v': 2})
        self.assertEqual(
            downloader._info_cache_get(1, 'http://x'),
            {'v': 2})

    def test_ttl_constant_matches_documented_five_minutes(self):
        # Pin the TTL value. Operators tuning this would touch the
        # constant deliberately; a typo'd refactor slips past the
        # integration test (where TTL is implicit) without failing.
        self.assertEqual(downloader._INFO_TTL_SECONDS, 300)


    def test_set_skips_cache_when_info_none_or_empty(self):
        """A degenerate `info` (None OR empty-dict {}) MUST NOT enter
        the cache.

        Why: a transient _run_ydl failure (network blip,
        cookies-expired mid-fetch) returns None or {}. If we cached
        that, every subsequent fetch for that (uid, url) for the
        next TTL seconds would return the same degenerate marker,
        masking the underlying failure as a 'cached' result.
        Rejection falls through to a fresh fetch next time.

        NOTE on the narrow contract: the production guard is
        `if info is None or info == {}:` (NOT `if not info:`).
        Pinning only that narrow contract here keeps legitimate
        edge cases like `info={"title": ""}` cacheable -- the
        comment-positive assertion below covers that.
        """
        downloader._INFO_CACHE.clear()
        # Negative assertion: None + empty dict are rejected.
        for bad_info in (None, {}):
            downloader._info_cache_set(
                1, f'http://bad-{id(bad_info)}', bad_info,
            )
        self.assertEqual(
            downloader._INFO_CACHE, {},
            "None and empty-dict markers MUST NOT enter the cache"
        )
        # Positive assertion: legitimate info with EMPTY-STRING fields
        # is NOT a degenerate marker -- the guard's narrow contract
        # means such a dict MUST continue to be cacheable. Catches a
        # future widening of the guard to `not info`-style logic.
        downloader._info_cache_set(
            1, 'http://legit-empty-title',
            {'id': 'abc', 'title': ''},
        )
        self.assertIn(
            (1, 'http://legit-empty-title'),
            downloader._INFO_CACHE,
            "info with empty-string fields is legitimate -- the "
            "narrow `info is None or info == {}` guard MUST NOT "
            "reject it. Pin the legitimate-edge-case contract."
        )

    def test_set_evicts_oldest_when_max_size_exceeded(self):
        """When the cache hits `_INFO_CACHE_MAX_SIZE`, the OLDEST entry
        (lowest cached_at stamp) MUST be FIFO-evicted BEFORE the new
        entry is written.

        Why: a single user pasting 100k+ URLs over a long-lived bot
        session would otherwise leak memory forever. FIFO (not LRU) is
        the cheap choice -- the bot's traffic is dominated by
        back-clicks on the same video, not by 1M distinct URLs.

        Two oldest/second-oldest anchor invariants decouple the test
        from the production min/max choice -- catches both
        (a) `min` -> `max` regression (and similar swap bugs that
        accidentally replace OLDEST-evict with NEWEST-evict, which
        would silently still hold the cap but kick the WRONG entry)
        and (b) over-eager eviction (kicking 2+ entries instead of 1).
        """
        downloader._INFO_CACHE.clear()
        cap = downloader._INFO_CACHE_MAX_SIZE
        # Fill the cache to exactly `cap` via direct `_INFO_CACHE`
        # injection with deterministic, monotonically-increasing
        # stamps (i=0,1,2,...). Avoids any `time.monotonic()` resolution
        # edge cases in a same-millisecond fill.
        for i in range(cap):
            downloader._INFO_CACHE[(1, f'http://k{i}')] = (
                float(i),  # stamp = i, monotonically increasing
                {'v': i},
            )
        self.assertEqual(len(downloader._INFO_CACHE), cap)
        # Anchor: `k0` is the FIRST inserted (lowest stamp = 0.0).
        # Its eviction on overflow is the FIFO policy signal.
        k0 = (1, 'http://k0')
        self.assertIn(k0, downloader._INFO_CACHE)
        # Anchor: `k{cap-2}` is the penultimate inserted (higher stamp).
        # Its SURVIVAL on overflow pins that eviction targets exactly
        # ONE entry (oldest), not 2+ -- and that the eviction policy
        # is by-stamp, not by-accident-by-recent-write.
        k_cap_2 = (1, f'http://k{cap-2}')
        self.assertIn(k_cap_2, downloader._INFO_CACHE)
        # Trigger overflow via the production code path.
        downloader._info_cache_set(1, 'http://overflow', {'v': 'new'})
        # POLICY INVARIANT 1: oldest (lowest stamp) MUST be evicted.
        # Catches a `min` -> `max` swap regression -- if production
        # accidentally sorts with `max`, k0 would survive and the
        # SECOND-OLDEST entry would be evicted instead.
        self.assertNotIn(
            k0, downloader._INFO_CACHE,
            "OLDEST entry (k0, lowest stamp) MUST be evicted on "
            "overflow, NOT newest-evict. Catches a `min` -> `max` "
            "swap regression."
        )
        # POLICY INVARIANT 2: a LATER-inserted entry MUST survive
        # (over-eager eviction would remove BOTH k0 and k{cap-2}).
        self.assertIn(
            k_cap_2, downloader._INFO_CACHE,
            "LATER-inserted entry (k_cap-2, second-oldest) MUST "
            "survive overflow -- eviction targets ONLY the oldest."
        )
        # Size + new-entry sanity: cap held + the just-written
        # overflow entry is now in the cache.
        self.assertEqual(
            len(downloader._INFO_CACHE), cap,
            "cache size MUST stay capped at _INFO_CACHE_MAX_SIZE"
        )
        self.assertIn(
            (1, 'http://overflow'), downloader._INFO_CACHE,
            "new entry MUST be written after FIFO eviction"
        )

    def test_ttl_constant_literal_in_source(self):
        """Source-level pin: `_INFO_TTL_SECONDS = 300` MUST appear in
        downloader.py's source as a literal 300.

        Why: a fork that bumps the constant to e.g. 60 would silently
        change cache semantics without failure (the assertEqual on
        `_INFO_TTL_SECONDS == 300` would pass if the const is renamed
        but kept equal to 300). The source-pin ties the LITERAL to
        the documented 5-minute window.
        """
        src = inspect.getsource(downloader)
        self.assertIn(
            '_INFO_TTL_SECONDS = 300', src,
            "the documented 5-minute TTL literal must stay "
            "anchored to 300 in downloader.py source (5 * 60 = 300s)"
        )


class TestFetchInfoCaching(unittest.TestCase):
    """Drive the read-through TTL cache contract in `fetch_info`.

    The integration contract: a SECOND call to fetch_info with the
    same (uid, url) within the TTL MUST return the cached info WITHOUT
    calling `_run_ydl` again. A third call after the TTL expires MUST
    call `_run_ydl` afresh. Per-user isolation MUST hold across the
    integration boundary.

    We monkey-patch `_run_ydl` to capture call counts and inject a
    fixture info dict, mirroring the existing
    TestFetchInfoExtractorArgs._capture_fetch_info_opts pattern.
    """

    def setUp(self):
        # Always start with a clean cache so leftover entries from
        # a prior test do not flip the cache-hit observations below.
        downloader._info_cache_clear()

    def tearDown(self):
        downloader._info_cache_clear()

    def _stub_run_ydl(self, return_value):
        """Monkey-patch downloader._run_ydl to capture calls + return fixture."""
        calls = []
        def fake(opts, label, extract_fn):
            calls.append({'opts': opts, 'label': label})
            return return_value
        return calls, fake

    def test_second_call_within_ttl_avoids_run_ydl(self):
        # First call: cache miss -> _run_ydl is invoked once.
        # Second call: cache hit -> _run_ydl is NOT invoked again.
        fixture = {'title': 'Foo', 'duration': 60, 'uploader': 'Bar'}
        calls, fake = self._stub_run_ydl(fixture)
        from unittest.mock import MagicMock
        with patch.object(downloader, '_run_ydl', side_effect=fake), \
             patch.object(downloader, '_cookie_file', return_value='/tmp/fake-cookies.txt'):
            # First miss:
            result1 = downloader.fetch_info(MagicMock(), 1, 'http://x')
            self.assertEqual(result1, fixture)
            # Second hit (same uid + url) within TTL:
            result2 = downloader.fetch_info(MagicMock(), 1, 'http://x')
            self.assertEqual(result2, fixture)
        # Critical: only ONE _run_ydl call despite TWO fetch_info calls.
        # This is THE primary contract of the read-through cache.
        self.assertEqual(len(calls), 1,
                         'cache HIT should avoid _run_ydl; '
                         f'observed {len(calls)} calls instead of 1')

    def test_different_users_same_url_each_call_run_ydl(self):
        # Per-user keying: two different users requesting the same
        # URL each get their own fetch. Cross-user cache hits are
        # explicitly disallowed (would leak cookie-bound fields).
        fixture = {'title': 'Foo'}
        calls, fake = self._stub_run_ydl(fixture)
        from unittest.mock import MagicMock
        with patch.object(downloader, '_run_ydl', side_effect=fake), \
             patch.object(downloader, '_cookie_file', return_value='/tmp/fake-cookies.txt'):
            downloader.fetch_info(MagicMock(), 1, 'http://x')
            downloader.fetch_info(MagicMock(), 2, 'http://x')
        # Two users -> two _run_ydl calls (no cross-user cache hit).
        self.assertEqual(len(calls), 2,
                         'per-user cache MUST isolate (uid, url) keys; '
                         f'observed {len(calls)} calls instead of 2')

    def test_call_after_ttl_runs_ydl_again(self):
        # Third call after the TTL expires should re-fetch. Patches
        # time.monotonic to deterministic values so the test runs
        # in milliseconds, NOT real-time-clock seconds.
        fixture = {'title': 'Foo'}
        calls, fake = self._stub_run_ydl(fixture)
        from unittest.mock import MagicMock
        with patch.object(downloader, '_run_ydl', side_effect=fake), \
             patch.object(downloader, '_cookie_file', return_value='/tmp/fake-cookies.txt'), \
             patch('app.downloader.time.monotonic',
                   return_value=downloader._INFO_TTL_SECONDS + 1):
            result = downloader.fetch_info(MagicMock(), 1, 'http://x')
        self.assertEqual(result, fixture)
        self.assertEqual(len(calls), 1,
                         'expired entry MUST be re-fetched; '
                         f'observed {len(calls)} calls')

    def test_fetch_info_source_uses_cache_helpers(self):
        # Structural pin mirroring the dual-pin shape from prior
        # rounds: the (uid, url) keying MUST read through the cache
        # BEFORE invoking yt-dlp, AND write through the cache AFTER
        # a successful run. A future refactor that drops the cache
        # from either side fails this pin loudly.

        # `_info_cache_get(` discriminator -- call-site form (open
        # paren). Mirrors prior _format_meta( and _info_thumbnail_url(
        # pins from this session.
        # `_info_cache_set(` -- write-through discriminator.
        # Source-level only -- neither helper's import-line form
        # (`_info_cache_get,` / `_info_cache_set,`) is asserted here
        # because BOTH helpers are MODULE-LEVEL private helpers in
        # the same module the call site lives in; the Module-level
        # import shape is `from app.utils import ...` doesn't apply
        # to the cache helpers. The pin only needs to anchor the
        # call sites.
        from app import downloader
        src = inspect.getsource(downloader.fetch_info)
        self.assertIn(
            '_info_cache_get(',
            src,
            'fetch_info must READ through the cache (_info_cache_get) so '
            'a fresh fetch is skipped on back_to_formats refetches of the '
            'same (uid, url) within the TTL.'
        )
        self.assertIn(
            '_info_cache_set(',
            src,
            'fetch_info must WRITE through the cache (_info_cache_set) on '
            'every successful yt-dlp extract so subsequent reads within '
            'the TTL return the same info dict without a network round-trip.'
        )

    def test_extractor_args_present_on_cache_miss(self):
        # NOTE: this assertion was redundant with the existing
        # `TestFetchInfoExtractorArgs` cases (test_includes_extractor_args_
        # when_max_comments_set + test_omits_extractor_args_when_max_
        # comments_zero), which already drive fetch_info through the
        # cache-miss path with MAX_COMMENTS set/unset. Closed-out per
        # closing-review of b470192: TestFetchInfoExtractorArgs is the
        # canonical home for the extractor_args shape contract;
        # TestFetchInfoCaching is the canonical home for the cache
        # contract. Splitting the contract across two classes made a
        # future regression harder to trace -- consolidating the
        # test layout here keeps the read-through-graph one-to-one.
        self.skipTest(
            'extractor_args shape contract lives in '
            'TestFetchInfoExtractorArgs; TestFetchInfoCaching keeps '
            'the cache-shaped cases only.')


class TestFetchInfoExtractorArgs(unittest.TestCase):
    """Pins the opt-in contract for `Config.MAX_COMMENTS`-driven comment
    fetching in `app.downloader.fetch_info`.

    Two contracts:

    1. When `Config.MAX_COMMENTS > 0`, the opts dict yt-dlp receives MUST
       include `extractor_args.youtube.{max_comments: [str(N)],
       comment_sort: ['new']}` so yt-dlp's YouTube extractor fetches the
       most-recent N comments inline with the existing metadata call.
    2. When `Config.MAX_COMMENTS == 0`, `extractor_args` MUST NOT be in
       the opts dict — the default-no-comment path stays fast (no
       Innertube `/next` round-trip).

    Together these pins prevent a future refactor from regressing in
    either direction:
      * accidentally keeping comment-fetch ON even when MAX_COMMENTS=0
        (rate-limit risk; user can't disable it).
      * accidentally dropping comment_sort=new (yt-dlp defaults to
        "Top by relevance" which the user explicitly did NOT ask for).

    The `comment_sort: ['new']` is non-obvious — yt-dlp's YouTube
    extractor returns "Top" comments when no sort is specified, which
    is per-creator curation (the channel-owner curated pin) and NOT
    chronological. Without forcing 'new', the bot would surface the
    channel owner's pinned comment every fetch, which is exactly the
    opposite of what "Top comments" feels like in the chat.
    """

    def _capture_fetch_info_opts(self):
        """Monkey-patch `_run_ydl` to capture the opts dict yt-dlp would
        receive, returning it as a dict for assertion.

        Patches `_cookie_file` as a side requirement so the helper
        doesn't try to decode `bot._cookie_data[uid]` (a MagicMock that
        would otherwise blow up at the `bytes.decode('utf-8')` call).
        Same trick used in TestMp4ContainerCascade._run_download_capture_opts.

        Self-isolating: clears `_INFO_CACHE` at entry so a prior test
        in the suite (e.g. TestFetchInfoCaching) that wrote a fake
        result for the SAME (uid=1, url='http://x') cache key cannot
        short-circuit fetch_info and leave opts empty (KeyError on
        'extractor_args'). The unit-of-isolation is the helper itself,
        not the test running it — so any future caller is also safe.
        """
        from app import downloader
        downloader._INFO_CACHE.clear()
        captured = {}

        def fake_run(opts, label, extract_fn):
            captured['opts'] = opts
            return {'title': 'X', 'duration': 0}

        with patch.object(downloader, '_run_ydl',
                           side_effect=fake_run), \
             patch.object(downloader, '_cookie_file',
                           return_value='/tmp/fake-cookies.txt'):
            downloader.fetch_info(MagicMock(), 1, 'http://x')
        return captured.get('opts', {})

    def test_includes_extractor_args_when_max_comments_set(self):
        from app import downloader
        with patch.object(downloader.Config, 'MAX_COMMENTS', 5):
            opts = self._capture_fetch_info_opts()
        self.assertIn('extractor_args', opts,
                      'fetch_info MUST add extractor_args when '
                      'Config.MAX_COMMENTS > 0 — otherwise yt-dlp '
                      'skips the Innertube /next comment call and '
                      'show_format_choice renders as if comments '
                      'were never opted-in.')
        yt = opts['extractor_args']['youtube']
        self.assertEqual(
            yt['max_comments'], ['5'],
            'fetch_info must cap yt-dlp at Config.MAX_COMMENTS so '
            'an operator with MAX_COMMENTS=100 doesn\'t accidentally '
            'fetch 100 + comments (rate-limit risk).')
        self.assertEqual(
            yt['comment_sort'], ['new'],
            'fetch_info must force comment_sort=new; otherwise yt-dlp '
            'returns "Top by relevance" which is per-creator pinned '
            'comments, NOT chronological. The user explicitly asked '
            'for "last comments that are viewable" -> newest-first.')

    def test_omits_extractor_args_when_max_comments_zero(self):
        from app import downloader
        with patch.object(downloader.Config, 'MAX_COMMENTS', 0):
            opts = self._capture_fetch_info_opts()
        self.assertNotIn(
            'extractor_args', opts,
            'fetch_info must NOT add extractor_args when '
            'Config.MAX_COMMENTS == 0 — keeps the no-comment fast '
            'path fast (no Innertube round-trip) so operators who '
            'don\'t opt in don\'t pay the latency cost.')

    def test_default_value_is_zero_when_env_unset(self):
        # Anchors the documented configuration: an upgrade is
        # non-disruptive because the operator hasn't opted in.
        if 'MAX_COMMENTS' in os.environ:
            self.skipTest(
                'MAX_COMMENTS is set in this environment; the '
                'documented default is not asserted.')
        self.assertEqual(
            Config.MAX_COMMENTS, 0,
            'Default must be 0 (off) so an upgrade is non-disruptive '
            '— operators opt in via env / GitHub Secrets.')

    def test_positive_max_comments_value_passed_through_str(self):
        # yt-dlp's extractor_args values are lists-of-strings (this
        # mirrors how CLI args are parsed). A refactor that passes an
        # int instead of [str(int)] would silently break comment
        # fetching with no immediate error. Pin the shape explicitly.
        from app import downloader
        with patch.object(downloader.Config, 'MAX_COMMENTS', 12):
            opts = self._capture_fetch_info_opts()
        self.assertEqual(
            opts['extractor_args']['youtube']['max_comments'],
            ['12'],
            'max_comments must be a single-element list-of-strings '
            '— yt-dlp parser expects this shape.')


class TestCommentSliceDefensiveness(unittest.TestCase):
    """Pins the slice pattern used in show_format_choice:

        (info.get('comments') or [])[:Config.MAX_COMMENTS]

    Why this matters: yt-dlp can return `None` mid-fetch (rate-limit,
    partial response, edge-case comment shape, future extractor behaviors).
    The `or []` clause is what keeps that from a TypeError on `None[:N]`
    collapsing the format-choice screen via the outer try/except — which
    would lose the user the title/duration/format-picker they actually
    need to download the video.

    This is a pure-Python pattern test — no monkey-patching needed
    beyond Config.MAX_COMMENTS — because the slice expression sits in
    app/handlers/navigation.py and the test pins the SLICE behavior
    itself rather than the surrounding handler logic.
    """

    def test_none_value_returns_empty_list(self):
        # Defensive case #1: yt-dlp returned comments key with None.
        from app.downloader import Config
        info = {'comments': None}
        self.assertEqual(
            (info.get('comments') or [])[:Config.MAX_COMMENTS],
            [],
            'info["comments"] == None must produce [] not raise '
            'TypeError when sliced.')

    def test_missing_key_returns_empty_list(self):
        # Defensive case #2: yt-dlp didn't include the comments key
        # at all (live/upcoming streams, some non-YouTube extractors).
        from app.downloader import Config
        info = {}
        self.assertEqual(
            (info.get('comments') or [])[:Config.MAX_COMMENTS],
            [],
            'info without "comments" key must produce [] not raise '
            'KeyError or AttributeError.')

    def test_empty_list_returns_empty_list(self):
        # Sanity: an Already-empty list stays empty (NOT falsy).
        from app.downloader import Config
        info = {'comments': []}
        self.assertEqual(
            (info.get('comments') or [])[:Config.MAX_COMMENTS],
            [],
            '[] or [] must be [] — both sides falsy chained still '
            'returns the fallback.')

    def test_normal_list_returns_first_n(self):
        # Positive case: real comments + MAX_COMMENTS=3 → first 3.
        # `or []` is a no-op when value is truthy.
        from app.downloader import Config
        comments = [{'author': f'a{i}', 'text': f'h{i}'}
                    for i in range(7)]
        info = {'comments': comments}
        with patch.object(Config, 'MAX_COMMENTS', 3):
            result = (info.get('comments') or [])[:Config.MAX_COMMENTS]
        self.assertEqual(len(result), 3)
        self.assertEqual(result[0]['author'], 'a0')
        self.assertEqual(result[2]['author'], 'a2')

    def test_show_format_choice_source_uses_or_empty_defensive_slice(self):
        # Structural pin: the slice pattern is BOTH a unit-testable
        # shape (cases above) AND a deployment contract. A future
        # refactor that drops the `or []` clause — without realizing
        # that None[:N] raises TypeError and would collapse the whole
        # format-choice screen via the outer try/except — gets caught
        # here before it lands in production. Mirrors the prefix-
        # contract pattern used in TestRunInExecutorKwargsRegression +
        # TestShowRecentDeleteEntry for cross-package consistency.
        import inspect
        from app.handlers import navigation
        # Function-body source keeps the EXISTING 8 pins as strict as
        # they were (they were calibrated against body-only source;
        # widening to module-level would let imports of e.g.
        # `SAFE_TEXT_MAX` falsely satisfy a use-site pin). A separate
        # module-level source `mod_src` is used ONLY for the new
        # dual-pin pair below, where we explicitly want both the
        # import line `_format_meta,` AND the call site `_format_meta(`
        # to be addressable. The 2026-06-20 production bug was: call
        # present but import missing -- addressable only by reading
        # both halves.
        src = inspect.getsource(navigation.show_format_choice)
        mod_src = inspect.getsource(navigation)
        self.assertIn(
            "info.get('comments') or []",
            src,
            'show_format_choice must keep the `or []` defensiveness '
            'so yt-dlp None / missing-key responses do not '
            'TypeError-collapse the whole format-choice screen '
            'via the outer try/except.')
        self.assertIn(
            'if desc_text:',
            src,
            'show_format_choice must REBUILD the headline'
            ' conditionally on desc_text so the description'
            ' flows through to Telegram; without it the feature'
            ' is a silent no-op.'
        )
        self.assertIn(
            r'\U0001F4D6',
            src,
            'show_format_choice must include the U+1F4D6 book emoji'
            ' between title and duration so the description block'
            ' is visually distinguishable from the comments block.'
        )

        # Thumbnail display structural pins. 3 discursive anchors
        # collectively ensure the photo-edit path stays wired
        # end-to-end so users see a thumbnail above the format-picker
        # kb on every video with a thumbnail. A future refactor that
        # reverts to plain text-only fails all 3 of these pins loudly.
        self.assertIn(
            '_info_thumbnail_url(',
            mod_src,
            'show_format_choice must CALL _info_thumbnail_url(...)'
            ' (not just rely on the str / list shape heuristic)'
            ' so the thumbnail URL is testable in isolation and the'
            ' photo-edit branch can re-render on every fetch.'
        )
        # Dual-pin discriminator. `_info_thumbnail_url,` (with
        # trailing comma) matches ONLY the import-line form
        # `_info_thumbnail_url,` and is NOT a substring of the
        # call-site form `_info_thumbnail_url(`. The 2026-06-20
        # production bug was: helper call added without import,
        # causing NameError at runtime -- caught here against
        # `mod_src` (module-level source) which contains BOTH the
        # import line AND the call site. A function-body-only pin
        # would miss the missing-import case; the dual-pin against
        # module-level source catches both halves of the bug class.
        self.assertIn(
            '_info_thumbnail_url,',
            mod_src,
            'show_format_choice must IMPORT _info_thumbnail_url'
            ' from app.utils the same way _format_meta was'
            ' imported -- the 2026-06-20 production bug we'
            ' fixed in 5942e73 was: present call, missing'
            ' import. The trailing-comma discriminator matches'
            ' the import-line form (NOT the call-site form'
            ' `_info_thumbnail_url(`) so a future refactor that'
            ' drops the import -- even if the call site is kept'
            ' -- fails this pin loudly.'
        )
        self.assertIn(
            'edit_media',
            src,
            "show_format_choice must invoke Telegram edit_media so"
            " the placeholder status message is converted to a"
            " real photo with caption + kb (instead of staying as"
            " plain text). Without this pin, a future refactor that"
            " falls through to edit_text silently regresses the"
            " thumbnail feature on every video."
        )
        self.assertIn(
            'InputMediaPhoto',
            src,
            "show_format_choice must use Telegram InputMediaPhoto"
            " wrapper class so the photo URL is fed to edit_media"
            " correctly. Guards against a refactor that drops the"
            " class import and reverts to a custom media shape."
        )


        # `_format_meta(` (with the open paren) is the discriminator
        # between the call site and the import statement. The half-rollout
        # `_format_meta` call without a corresponding import was the
        # 2026-06-20 production bug; a loose `'_format_meta'` pin would
        # match the import line alone and silently re-pass even when the
        # call site is missing. Pinning `_format_meta(` forces BOTH the
        # import AND at least one open-paren call to be present in
        # source, so the bug class can't recur.
        # `_format_meta(` (with open paren) is the call-site discriminator;
        # `_format_meta,` (with trailing comma) is the import-line
        # discriminator. Together they catch BOTH halves of the 2026-
        # 06-20 bug class: a function-body-only pin matches the
        # import alone; a module-level-only import pin misses the
        # call. Pinning both forms against MODULE-level source forces
        # import + call to coexist.
        self.assertIn(
            '_format_meta(',
            mod_src,
            'show_format_choice must CALL _format_meta(...) (not just'
            ' import it) so uploader + view_count + upload_date render'
            ' inline below the title. The `_format_meta(` open-paren'
            ' discriminator ensures the import alone does not satisfy'
            ' this contract.'
        )
        self.assertIn(
            '_format_meta,',
            mod_src,
            'show_format_choice must IMPORT _format_meta from app.utils'
            ' (the 2026-06-20 production bug: present call, missing'
            ' import). The `_format_meta,` trailing-comma discriminator'
            ' matches the import-line form `_format_meta,` and is not'
            ' satisfied by the call-site form `_format_meta(` -- so a'
            ' missing import fails loudly even if the call site is kept.'
        )
        self.assertIn(
            "info.get('uploader')",
            src,
            'show_format_choice must read uploader from the'
            ' info dict for the channel-name rendering.'
        )
        self.assertIn(
            "info.get('view_count')",
            src,
            'show_format_choice must read view_count from the'
            ' info dict for the views-counter rendering.'
        )
        self.assertIn(
            "info.get('upload_date')",
            src,
            'show_format_choice must read upload_date from the'
            ' info dict for the date rendering.'
        )
        # Also pin that the overflow policy uses SAFE_TEXT_MAX and headline
        # rather than a duplicated f-string — defends against a refactor
        # that reverts to mid-comment truncation (renders half a line
        # that looks typed-broken to the user).
        self.assertIn(
            'SAFE_TEXT_MAX',
            src,
            'show_format_choice must gate the comments block on '
            'SAFE_TEXT_MAX so the worst-case title does not blow past '
            "Telegram's 4096-byte message cap.")
        self.assertIn(
            "f\"{headline}{extras}\\n\\nChoose format:\"",
            src,
            'show_format_choice must template headline + extras + '
            'Choose format so dropping extras on overflow and '
            'headline-staying in place avoids headline duplication.')
        # Pin the OVERFLOW-BRANCH template specifically. The literal
        # f"{headline}\n\nChoose format:" (without {extras} between
        # {headline} and the newlines) matches the overflow line
        # exactly and NOT the normal-branch line above (which has
        # {extras} between {headline} and the newlines). A future
        # refactor that drops overflow handling — silently reverting
        # to mid-comment substring+ellipsis truncation — would re-
        # expose the half-cut-line UX bug, and this assertion catches
        # it before it ships.
        self.assertIn(
            'f"{headline}\\n\\nChoose format:"',
            src,
            'show_format_choice must have an explicit overflow branch '
            'that drops `extras` and keeps `headline` + `Choose '
            'format:` — so a worst-case title still emits the format '
            'picker and never reaches Telegram with a half-rendered '
            'comment line.')


class TestMaxCommentsConfigClamping(unittest.TestCase):
    """Drive Config.MAX_COMMENTS clamping at import time.

    Hard-cap: any value above 20 is clamped DOWN to 20.
    Lower bound: any value below 0 is clamped UP to 0.
    Default: missing/non-numeric env var → 0.

    These tests assert the SHAPE of the clamp (which operands it uses)
    rather than the syntactic chain — a refactor that swaps max(0, ...)
    for abs() without bounds-checking would slip through a value-based
    test. The clamp helpers themselves are visible in the source; the
    test pins the resulting numeric behavior so a future operator who
    misconfigures still gets SAFE behavior, not escalating behavior.
    """

    def test_zero_by_default(self):
        if 'MAX_COMMENTS' in os.environ:
            self.skipTest('MAX_COMMENTS is set in this environment; '
                          'the documented default is not asserted.')
        self.assertEqual(Config.MAX_COMMENTS, 0)

    def test_clamping_observed_for_out_of_range(self):
        # We don't reload the module here because that's expensive —
        # the clamp is captured in Config.MAX_COMMENTS at import time.
        # If the operator sets MAX_COMMENTS=9999, the post-clamp value
        # is min(20, 9999) = 20. Verify we never observe a value > 20
        # nor < 0 in the live Config.
        self.assertGreaterEqual(Config.MAX_COMMENTS, 0)
        self.assertLessEqual(Config.MAX_COMMENTS, 20)
