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


if __name__ == '__main__':
    unittest.main()
