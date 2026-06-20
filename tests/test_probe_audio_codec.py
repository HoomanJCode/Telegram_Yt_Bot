"""Standalone regression tests for the ffprobe-driven smart-skip helpers.

Single-core-VPS smart-skip (2026-06-21 response to user feedback
"ffmpeg processing is high for my single-core VPS"): an ffprobe probe
after yt-dlp's natural merge tells us whether the post-merge audio
stream a:0 is already in a universal-codec set (currently just `aac`).
If so, the AAC re-encode would be pure wasted CPU -- skip the helper
call entirely. Net savings on a single-core VPS: ~30-90s of CPU per
AAC-source download becomes ~1-2s of probe cost.

This file is SEPARATE from tests/test_aac_transcode.py (where the
TestAacTranscodeIntegration source-level pin for the 6th gate conjunct
lives) because the bigger test file's CRLF-line totals put str_replace
diff rendering at risk on Windows. Same isolation pattern used for the
original split between tests/test_* and tests/test_downloader.py.

Pinned contracts:

  * `_probe_audio_codec(video_file)`: ffprobe the first audio stream's
    codec_name via `-of csv=p=0 -select_streams a:0
    -show_entries stream=codec_name -v error`. Returns lowercased
    codec name on success, '' on any failure mode.
  * `_is_already_universal_codec(video_file)`: boolean wrapper.
    True ONLY when the probe returns a codec in `_AAC_SKIP_CODECS`.
    False on probe failure (fall through to ffmpeg so operators with
    AAC_TRANSCODE=true still get the TV fix on probe blips).
  * `_AAC_SKIP_CODECS`: pinned to `frozenset({'aac'})` -- widening is
    a deliberate operator decision, not a refactor accident.
"""
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch, MagicMock

import app.downloader as downloader
from app.downloader import (
    _probe_audio_codec,
    _is_already_universal_codec,
)


class TestProbeAudioCodec(unittest.TestCase):
    """Drive `_probe_audio_codec` + `_is_already_universal_codec` -- the
    ffprobe-driven smart-skip helpers that gate `download()`'s AAC
    transcode.

    Strategy: monkey-patch `subprocess.run` to a stub returning fake
    ffprobe outputs. Capture the cmd list to assert POSITIONAL pinning
    of `-of csv=p=0` (avoids verbose-text parsing), `-v error`
    (suppresses ffprobe's chatty default), `-select_streams a:0` (pin
    to first audio stream), and `-show_entries stream=codec_name`
    (request only the codec field). Same pattern as the existing
    `_transcode_audio_to_aac` cmd-shape tests in test_aac_transcode.py.
    """

    def _setup_video(self, ext='.mkv', size=5000):
        tmpdir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmpdir, ignore_errors=True)
        video = Path(tmpdir) / f'video{ext}'
        video.write_bytes(b'\0' * size)
        return str(video)

    # ---- _probe_audio_codec: success paths ---------------------

    def test_probe_returns_aac_for_aac_audio_file(self):
        # The probe returns a codec name string on success.
        video = self._setup_video()
        with patch('app.downloader.subprocess.run') as mock_run:
            mock_run.return_value = MagicMock(
                returncode=0, stdout='aac\n')
            result = _probe_audio_codec(video)
        self.assertEqual(
            result, 'aac',
            'ffprobe success with stdout="aac" MUST return `aac` '
            '(lowercased). The downstream smart-skip relies on '
            'this exact value being in `_AAC_SKIP_CODECS`.')

    def test_probe_returns_opus_for_opus_audio_file(self):
        # Symmetric: opus codec name passes through.
        video = self._setup_video()
        with patch('app.downloader.subprocess.run') as mock_run:
            mock_run.return_value = MagicMock(
                returncode=0, stdout='opus\n')
            result = _probe_audio_codec(video)
        self.assertEqual(
            result, 'opus',
            'ffprobe success with stdout="opus" MUST return '
            '`opus` -- the smart-skip relies on this NOT being '
            'in `_AAC_SKIP_CODECS` so the ffmpeg transcode fires.')

    def test_probe_normalizes_uppercase_to_lowercase(self):
        # Some ffprobe builds emit uppercase; the smart-skip
        # lookup is against the lowercase frozenset. Pin the
        # normalization.
        video = self._setup_video()
        with patch('app.downloader.subprocess.run') as mock_run:
            mock_run.return_value = MagicMock(
                returncode=0, stdout='AAC\n')
            result = _probe_audio_codec(video)
        self.assertEqual(
            result, 'aac',
            'uppercase "AAC\n" stdout MUST normalize to "aac" so '
            '`_AAC_SKIP_CODECS` lookup matches. A bug here would '
            'have the smart-skip silently miss AAC sources.')

    def test_probe_strips_trailing_whitespace(self):
        # ffprobe's csv output may or may not have trailing
        # whitespace depending on version. Strip it so the
        # frozenset lookup is robust.
        video = self._setup_video()
        with patch('app.downloader.subprocess.run') as mock_run:
            mock_run.return_value = MagicMock(
                returncode=0, stdout='  aac  \n')
            self.assertEqual(_probe_audio_codec(video), 'aac')

    # ---- _probe_audio_codec: failure paths --------------------

    def test_probe_returns_empty_string_when_file_missing(self):
        # Defensive: missing-file -> '' (not raised). The probe
        # helper itself doesn't run subprocess on missing files;
        # callers can rely on that.
        self.assertEqual(
            _probe_audio_codec('/nonexistent/not_a_real_file.mkv'),
            '',
            'missing-file input MUST return "" so the smart-skip '
            'gracefully falls through to the active transcode '
            '(OR to delivery if the gate\'s other conjuncts '
            'short-circuit).')

    def test_probe_returns_empty_string_when_path_is_empty(self):
        self.assertEqual(_probe_audio_codec(''), '')

    def test_probe_returns_empty_string_on_non_zero_exit(self):
        # ffprobe returns non-zero (e.g. file not found by
        # ffprobe itself even though Path says it exists --
        # partial-download truncation, lock contention, etc.)
        # -> ''.
        video = self._setup_video()
        with patch('app.downloader.subprocess.run') as mock_run:
            mock_run.return_value = MagicMock(
                returncode=1, stderr='ffprobe error', stdout='')
            self.assertEqual(_probe_audio_codec(video), '')

    def test_probe_returns_empty_string_on_empty_stdout(self):
        # ffprobe exited 0 but no audio stream selected ->
        # empty stdout -> ''. Without this pin, an empty
        # string would falsely match `_AAC_SKIP_CODECS` if a
        # future maintainer accidentally widens the skip set
        # to include ''.
        video = self._setup_video()
        with patch('app.downloader.subprocess.run') as mock_run:
            mock_run.return_value = MagicMock(
                returncode=0, stdout='')
            self.assertEqual(_probe_audio_codec(video), '')

    def test_probe_returns_empty_string_on_subprocess_timeout(self):
        # ffprobe hung past `timeout=10` -> TimeoutExpired
        # (subclass of OSError). MUST be swallowed so the
        # bot doesn't 500 the user mid-download.
        video = self._setup_video()
        with patch('app.downloader.subprocess.run',
                   side_effect=subprocess.TimeoutExpired(
                       cmd='ffprobe', timeout=10)):
            result = _probe_audio_codec(video)
        self.assertEqual(result, '')

    def test_probe_returns_empty_string_on_subprocess_oserror(self):
        # ffprobe missing entirely -> FileNotFoundError via
        # subprocess.run. MUST be swallowed.
        video = self._setup_video()
        with patch('app.downloader.subprocess.run',
                   side_effect=OSError('ffprobe not on PATH')):
            result = _probe_audio_codec(video)
        self.assertEqual(result, '')

    # ---- _probe_audio_codec: command-shape pins ---------------

    def test_ffprobe_uses_csv_output_format(self):
        # Positional pin on `-of csv=p=0`. Without this, ffprobe
        # emits verbose text and the helper would have to parse
        # it -- fragile across ffprobe versions.
        #
        # A refactor that swaps to `-of json` or `-of default`
        # would emit a JSON object or verbose text header and
        # silently break the smart-skip.
        video = self._setup_video()
        recorded = {}
        def fake_run(cmd, **kwargs):
            recorded['cmd'] = cmd
            return MagicMock(returncode=0, stdout='aac\n')
        with patch('app.downloader.subprocess.run',
                   side_effect=fake_run):
            _probe_audio_codec(video)
        cmd = recorded['cmd']
        self.assertEqual(
            cmd[cmd.index('-of') + 1], 'csv=p=0',
            'ffprobe output format MUST be csv=p=0 (positional '
            'pin). Any other `-of` value would emit verbose '
            'text or JSON requiring the helper to parse '
            'ffprobe-version-specific output -- fragile.')

    def test_ffprobe_selects_first_audio_stream(self):
        # Pin `-select_streams a:0` (positional). Without this,
        # ffprobe might pick a different stream (cover art,
        # secondary language track) and the smart-skip would
        # reflect the wrong codec.
        video = self._setup_video()
        recorded = {}
        def fake_run(cmd, **kwargs):
            recorded['cmd'] = cmd
            return MagicMock(returncode=0, stdout='aac\n')
        with patch('app.downloader.subprocess.run',
                   side_effect=fake_run):
            _probe_audio_codec(video)
        cmd = recorded['cmd']
        self.assertEqual(
            cmd[cmd.index('-select_streams') + 1], 'a:0',
            'ffprobe MUST select the first audio stream '
            '(`-select_streams a:0`); the transcode helper '
            're-encodes stream a:0 so the probe and the '
            'transcode MUST agree on the same stream.')

    def test_ffprobe_request_only_codec_name_field(self):
        # Pin `-show_entries stream=codec_name` (positional).
        # Without this, ffprobe emits every stream metadata
        # field and stdout would be multi-line -- the helper
        # does strip+lower a single line of stdout, so a wider
        # `stream=all` would silently break parsing.
        video = self._setup_video()
        recorded = {}
        def fake_run(cmd, **kwargs):
            recorded['cmd'] = cmd
            return MagicMock(returncode=0, stdout='aac\n')
        with patch('app.downloader.subprocess.run',
                   side_effect=fake_run):
            _probe_audio_codec(video)
        cmd = recorded['cmd']
        self.assertEqual(
            cmd[cmd.index('-show_entries') + 1],
            'stream=codec_name',
            'ffprobe MUST request only the codec_name field '
            '(`-show_entries stream=codec_name`); a wider '
            '`stream=all` would emit multi-line output that '
            'the helper cannot parse.')

    def test_ffprobe_uses_error_log_level(self):
        # Pin `-v error` (positional). Default ffprobe log level
        # prints info messages to stderr that pollute
        # journalctl if capture_output fails to suppress. With
        # capture_output=True they go to stderr but a future
        # refactor that drops capture_output would leak.
        video = self._setup_video()
        recorded = {}
        def fake_run(cmd, **kwargs):
            recorded['cmd'] = cmd
            return MagicMock(returncode=0, stdout='aac\n')
        with patch('app.downloader.subprocess.run',
                   side_effect=fake_run):
            _probe_audio_codec(video)
        cmd = recorded['cmd']
        self.assertEqual(
            cmd[cmd.index('-v') + 1], 'error',
            'ffprobe MUST run with `-v error` to suppress '
            'info-level chatter. A bug here floods journalctl '
            'on every probe (and probes run on every video).')

    def test_ffprobe_has_timeout(self):
        # Pin that subprocess.run is called with a `timeout=`
        # kwarg -- without it, a hung ffprobe would block the
        # single-core VPS indefinitely.
        video = self._setup_video()
        recorded = {}
        def fake_run(cmd, **kwargs):
            recorded.update(kwargs)
            return MagicMock(returncode=0, stdout='aac\n')
        with patch('app.downloader.subprocess.run',
                   side_effect=fake_run):
            _probe_audio_codec(video)
        self.assertIn(
            'timeout', recorded,
            'ffprobe subprocess.run MUST be called with a '
            'timeout kwarg so a hung ffprobe cannot block '
            'the bot indefinitely.')
        self.assertIsNotNone(
            recorded['timeout'],
            'ffprobe timeout kwarg MUST be non-None so it '
            'actually applies. Default subprocess.run timeout '
            'is None (no timeout) which would be catastrophic '
            'on a hung ffprobe.')

    # ---- _is_already_universal_codec: boolean wrapper ----------

    def test_is_universal_returns_true_for_aac(self):
        # Positive skip signal: probe returns 'aac' (in
        # _AAC_SKIP_CODECS) -> True -> ffmpeg transcode is
        # SKIPPED. This is the headline single-core-VPS win.
        video = self._setup_video()
        with patch('app.downloader.subprocess.run') as mock_run:
            mock_run.return_value = MagicMock(
                returncode=0, stdout='aac\n')
            self.assertTrue(
                _is_already_universal_codec(video),
                'AAC-source files MUST return True so the '
                'download() gate short-circuits the 30-90s '
                'ffmpeg re-encode on a single-core VPS.')

    def test_is_universal_returns_true_for_uppercase_aac(self):
        # End-to-end pin (probe + wrapper chain): the smart-skip
        # MUST return True when the probe returns uppercase 'AAC'
        # because the probe internally lowercases the codec name
        # and the wrapper's frozenset membership check relies on
        # that normalization. A refactor that decouples the two
        # helpers (e.g., a redundant `lower()` in the wrapper
        # while dropping it from the probe, or replacing the
        # frozenset with a regular string comparison in the
        # wrapper) would still pass either the probe-only or
        # the wrapper-only tests in isolation, but this combined
        # chain test catches that decoupling.
        video = self._setup_video()
        with patch('app.downloader.subprocess.run') as mock_run:
            mock_run.return_value = MagicMock(
                returncode=0, stdout='AAC\n')
            self.assertTrue(
                _is_already_universal_codec(video),
                '_is_already_universal_codec MUST return True '
                'for uppercase "AAC" stdout (probe-internal '
                'lowercase normalization + wrapper membership '
                'check as a single chain). A regression that '
                'decouples probe normalization from wrapper '
                'membership would silently break the smart-skip '
                'on some ffprobe version outputs.')

    def test_is_universal_returns_false_for_opus(self):
        # Negative (proceed to ffmpeg): opus is the TV-fix
        # case we WANT to transcode.
        video = self._setup_video()
        with patch('app.downloader.subprocess.run') as mock_run:
            mock_run.return_value = MagicMock(
                returncode=0, stdout='opus\n')
            self.assertFalse(
                _is_already_universal_codec(video),
                'Opus-source files MUST return False so the '
                'TV-fix transcode still fires (this is the '
                'exact file the user reported "audio codec: '
                'none" on).')

    def test_is_universal_returns_false_on_probe_failure(self):
        # Probe failure -> False -> fall through to ffmpeg
        # (preserves the user-facing TV fix even when the
        # probe itself is broken). NOT True silently skipping.
        video = self._setup_video()
        with patch('app.downloader.subprocess.run') as mock_run:
            mock_run.return_value = MagicMock(
                returncode=1, stderr='ffprobe crash', stdout='')
            self.assertFalse(
                _is_already_universal_codec(video),
                'probe failure MUST return False (NOT True '
                'silently skipping the transcode). Operators '
                'with AAC_TRANSCODE=true want the fix even '
                'when the probe blips; ffprobe failure '
                'should fall through, not skip silently.')

    def test_is_universal_returns_false_on_subprocess_timeout(self):
        video = self._setup_video()
        with patch('app.downloader.subprocess.run',
                   side_effect=subprocess.TimeoutExpired(
                       cmd='ffprobe', timeout=10)):
            self.assertFalse(_is_already_universal_codec(video))

    def test_is_universal_does_not_throw_on_any_subprocess_failure(self):
        # Defensive: the wrapper MUST swallow ANY subprocess
        # failure mode -- a buggy refactor that lets, e.g., a
        # CalledProcessError propagate would 500 the user's
        # download mid-flight. Pin via a RuntimeError side-
        # effect on subprocess.run.
        video = self._setup_video()
        with patch('app.downloader.subprocess.run',
                   side_effect=RuntimeError('arbitrary '
                                            'exception')):
            result = _is_already_universal_codec(video)
        self.assertFalse(
            result,
            '_is_already_universal_codec MUST swallow any '
            'subprocess exception and return False. Otherwise '
            'an unrelated subprocess bug 500s the user '
            'mid-download.')

    # ---- _AAC_SKIP_CODECS value pin ---------------------------

    def test_skip_set_constant_pinned_to_aac_only(self):
        # Pin the _AAC_SKIP_CODECS value. Adding e.g. 'mp3' or
        # 'flac' to the skip set is an operationally significant
        # decision (preserves CPU but skips the TV-fix for
        # videos with that source codec). Future maintainers
        # widening the set should do it deliberately.
        self.assertEqual(
            downloader._AAC_SKIP_CODECS,
            frozenset({'aac'}),
            '`_AAC_SKIP_CODECS` MUST be exactly '
            '`frozenset({"aac"})`. Widening the set (to include '
            '"mp3" / "flac" / etc.) would silently skip the '
            'TV-fix for those source codecs; that change is '
            'operationally significant and should be a '
            'deliberate PR-time decision, not a silent test '
            'failure.')
