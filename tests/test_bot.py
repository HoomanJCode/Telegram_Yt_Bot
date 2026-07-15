"""Tests for app/bot.py -- pin the SSL env-var parsing contract.

Validates _build_ssl_context() in isolation:

* Both env vars empty (legacy mode) returns None.
* Only SSL_CERT_FILE set -> raises SSLConfigError.
* Only SSL_KEY_FILE set -> raises SSLConfigError.
* Both set but file paths missing -> raises SSLConfigError.
* Both set + a real (self-signed) PEM pair -> returns a non-None
  ssl.SSLContext that wraps cleanly through `load_cert_chain`.

The function is module-level (not embedded in YouTubeDownloaderBot.__init__)
precisely so we can test it without spinning up the full bot class. The
test contract IS the env-var -> context contract that app/__init__.py
relies on; a regression here surfaces immediately.

Stdlib-only; mirrors tests/test_fileserver.py style.

NOTE: every test uses os.environ.setdefault / explicit pop in setUp
and tearDown so SSL_CERT_FILE / SSL_KEY_FILE inherited from the host
environment (or set by a prior test) cannot leak between cases.
"""
import os
import ssl
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


class TestBuildSSLContext(unittest.TestCase):
    """Pins the SSL_CERT_FILE / SSL_KEY_FILE env-var contract."""

    _ENV_KEYS = ('SSL_CERT_FILE', 'SSL_KEY_FILE')

    def setUp(self):
        # Save + clear SSL env vars so host environment cannot leak in.
        self._saved = {}
        for k in self._ENV_KEYS:
            if k in os.environ:
                self._saved[k] = os.environ.pop(k)

    def tearDown(self):
        # Restore the host environment even if the test failed mid-way.
        for k in self._ENV_KEYS:
            os.environ.pop(k, None)
        for k, v in self._saved.items():
            os.environ[k] = v

    def test_returns_none_when_env_vars_unset(self):
        # Both keys absent from os.environ entirely (setUp already
        # popped them; this test confirms `_build_ssl_context` doesn't
        # re-poke the environment for missing keys).
        from app.bot import _build_ssl_context
        self.assertIsNone(_build_ssl_context())

    def test_returns_none_when_env_vars_set_to_empty_or_whitespace(self):
        # Pin the operator-experience contract: stray empty / whitespace
        # values for EITHER side must round-trip to None (legacy HTTP
        # mode), never SSLConfigError. Operators frequently leave
        # placeholders or have copy-pasted whitespace from multi-line
        # `cat <<EOF` heredocs. Asymmetric: each side is tested in
        # isolation (cert-blank / key-blank / both-blank) across both
        # empty-literal AND whitespace forms so a partial regression
        # in strip-handling surfaces immediately.
        from app.bot import _build_ssl_context
        blanks = ('', '   ', '\t', '\n')
        for cert_blank in blanks:
            for key_blank in blanks:
                os.environ['SSL_CERT_FILE'] = cert_blank
                os.environ['SSL_KEY_FILE'] = key_blank
                with self.subTest(cert=repr(cert_blank),
                                  key=repr(key_blank)):
                    self.assertIsNone(_build_ssl_context())

    def test_raises_on_partial_config_cert_only(self):
        from app.bot import _build_ssl_context, SSLConfigError
        os.environ['SSL_CERT_FILE'] = '/tmp/cert.pem'
        # SSL_KEY_FILE intentionally absent.
        with self.assertRaises(SSLConfigError) as ctx:
            _build_ssl_context()
        # The error message must name BOTH vars so an operator reading
        # journalctl knows exactly which pair is misconfigured.
        msg = str(ctx.exception)
        self.assertIn('SSL_CERT_FILE', msg)
        self.assertIn('SSL_KEY_FILE', msg)

    def test_raises_on_partial_config_key_only(self):
        from app.bot import _build_ssl_context, SSLConfigError
        os.environ['SSL_KEY_FILE'] = '/tmp/key.pem'
        with self.assertRaises(SSLConfigError) as ctx:
            _build_ssl_context()
        self.assertIn('SSL_CERT_FILE', str(ctx.exception))
        self.assertIn('SSL_KEY_FILE', str(ctx.exception))

    def test_raises_when_cert_path_missing(self):
        from app.bot import _build_ssl_context, SSLConfigError
        os.environ['SSL_CERT_FILE'] = '/nonexistent/cert.pem'
        os.environ['SSL_KEY_FILE'] = '/nonexistent/key.pem'
        # is_file() defaults to False for a non-existent Path; no mock
        # needed. We don't need a stat() patch because the validation
        # raises BEFORE reaching the stat() call.
        with self.assertRaises(SSLConfigError) as ctx:
            _build_ssl_context()
        msg = str(ctx.exception)
        self.assertIn('SSL_CERT_FILE', msg)
        self.assertIn('does not exist', msg)

    def test_raises_when_key_path_missing(self):
        # Cert path: real (touch'd temp file). Key path: non-existent.
        # We deliberately do NOT use `Path(__file__)` here because that
        # depends on the test being executed from the project root and
        # would silently re-wire under any future repo-layout move.
        from app.bot import _build_ssl_context, SSLConfigError
        with tempfile.NamedTemporaryFile(suffix='.pem') as good_cert:
            os.environ['SSL_CERT_FILE'] = good_cert.name
            os.environ['SSL_KEY_FILE'] = '/nonexistent/key.pem'
            with self.assertRaises(SSLConfigError) as ctx:
                _build_ssl_context()
            self.assertIn('SSL_KEY_FILE', str(ctx.exception))

    def test_raises_when_keyfile_load_fails(self):
        # Both paths exist but the key file isn't a valid PEM. The
        # load_cert_chain call inside _build_ssl_context should raise
        # and we re-wrap as SSLConfigError. Use a non-PEM text file
        # so load_cert_chain deterministically refuses it.
        from app.bot import _build_ssl_context, SSLConfigError
        with tempfile.TemporaryDirectory() as d:
            cert_path = os.path.join(d, 'cert.pem')
            key_path = os.path.join(d, 'key.pem')
            Path(cert_path).write_text('not a real pem')
            Path(key_path).write_text('also not a real pem')
            os.environ['SSL_CERT_FILE'] = cert_path
            os.environ['SSL_KEY_FILE'] = key_path
            with self.assertRaises(SSLConfigError) as ctx:
                _build_ssl_context()
            # The wrapped message ends with 'something: <ssl error>'
            # from `load_cert_chain`. Lowercase BOTH sides for a
            # case-insensitive contains-against-substring match; the
            # previous assertion lowercased only the message side,
            # which silently failed when the search string was
            # exact-case 'SSL context' (Python 3.12 ssl module emits
            # `"[ssl] pem lib"` lowercase).
            self.assertIn('ssl context', str(ctx.exception).lower())

    def test_builds_ssl_context_with_cert_and_key_paths(self):
        """Pin the EXACT contract: `ssl.create_default_context` is
        called with `Purpose.CLIENT_AUTH` and the resulting context
        has `load_cert_chain` invoked with the operator's cert+key
        paths.

        Mock-based rather than end-to-end (no openssl subprocess) so
        the assertion can directly verify WHAT was called with WHICH
        args — not just the resulting state. Surface-level assertions
        like `verify_mode == CERT_NONE` or `minimum_version >=
        TLSv1_2` would all be the *default* of a freshly-created
        SSLContext regardless of whether `load_cert_chain` ran, so a
        regression where `_build_ssl_context` silently returned the
        bare context without binding the operator's cert would still
        pass. This test catches that exact regression.

        Hermetic: the cert/key files are bare `touch`'d (no real
        PEM content); `ssl.create_default_context` is patched so the
        'real' load_cert_chain never tries to parse them.
        """
        from unittest.mock import MagicMock, patch
        from app.bot import _build_ssl_context
        with tempfile.TemporaryDirectory() as d:
            cert_path = os.path.join(d, 'cert.pem')
            key_path = os.path.join(d, 'key.pem')
            Path(cert_path).touch()
            Path(key_path).touch()
            os.environ['SSL_CERT_FILE'] = cert_path
            os.environ['SSL_KEY_FILE'] = key_path
            fake_ctx = MagicMock(spec=ssl.SSLContext)
            # Patch ssl.create_default_context at its source module
            # (the bare `ssl` module) since `_build_ssl_context` calls
            # `ssl.create_default_context(...)` via module-level
            # lookup, not a `from ssl import create_default_context`
            # bound name.
            with patch('ssl.create_default_context',
                       return_value=fake_ctx) as create_mock:
                returned = _build_ssl_context()
                create_mock.assert_called_once_with(ssl.Purpose.CLIENT_AUTH)
                fake_ctx.load_cert_chain.assert_called_once_with(
                    certfile=cert_path, keyfile=key_path)
                # Caller (YouTubeDownloaderBot.__init__) stores the
                # exact returned context on FileServer.ssl_context;
                # a regression that returned a different object
                # would silently break the aiohttp wiring.
                self.assertIs(returned, fake_ctx)


class TestCrossValidationWarnsOnHTTP(unittest.TestCase):
    """Pins the http:// + HTTPS_enabled scenario: a WARNING (not
    SSLConfigError) so an operator mid-migration isn't blocked."""

    def test_http_with_self_signed_pair_logs_warning_not_raises(self):
        # The cross-validation lives in YouTubeDownloaderBot.__init__,
        # not in _build_ssl_context, so we drive the whole bot init
        # with a heavily-mocked environment. The goal is to confirm:
        # 1. The construction does NOT raise SSLConfigError when the
        #    cert+key are valid but the URL is http://.
        # 2. A WARNING mentioning BASE_DOWNLOAD_LINK / http -> https
        #    is emitted.
        # We isolate the cross-validation from the rest of the init by
        # patching all the noisy class-level construction steps.
        from app.bot import SSLConfigError, YouTubeDownloaderBot
        # Provide a self-signed pair so _build_ssl_context returns
        # a real ssl.SSLContext instead of None -- the warning only
        # fires when ssl_context is NOT None.
        openssl = __import__('shutil').which('openssl')
        if openssl is None:
            self.skipTest('openssl not on PATH; cannot mint a cert')
        with tempfile.TemporaryDirectory() as d:
            cert_path = os.path.join(d, 'cert.pem')
            key_path = os.path.join(d, 'key.pem')
            subprocess.run([
                openssl, 'req', '-x509', '-newkey', 'rsa:2048',
                '-nodes', '-keyout', key_path, '-out', cert_path,
                '-days', '1', '-subj', '/CN=test.local',
            ], check=True, capture_output=True)
            # `clear=True` on patch.dict is essential: config.py
            # calls `load_dotenv()` at import time which side-loads
            # the developer host's .env into os.environ. Without
            # `clear`, a developer's local BASE_DOWNLOAD_LINK would
            # leak in and silently flip the cross-validation outcome.
            # `clear=False` previously let the mutation succeed but
            # masked an env-leak bug.
            env_overrides = {
                'SSL_CERT_FILE': cert_path,
                'SSL_KEY_FILE': key_path,
                'BASE_DOWNLOAD_LINK': 'http://example.com:8000',
            }
            with patch.dict(os.environ, env_overrides, clear=True), \
                 patch.object(Path, 'mkdir'), \
                 patch('app.bot.check_ffmpeg', return_value=False), \
                 patch('app.bot.FileServer') as fs_mock, \
                 patch('app.bot.load_data'), \
                 patch('app.bot.YouTubeDownloaderBot._cleanup_orphans'):
                fs_mock.return_value = object()
                with self.assertLogs('yt_bot', level='DEBUG') as logs:
                    YouTubeDownloaderBot()
                self.assertTrue(
                    any('http://' in line and 'BASE_DOWNLOAD_LINK' in line
                        for line in logs.output),
                    f'Expected http://+SSL warning in {logs.output!r}')


if __name__ == '__main__':
    unittest.main()
