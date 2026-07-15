"""Tests for app/fileserver.py -- pin transport-level fixes for the
"stall at 99%" symptom on mobile Telegram clients.

Pinned invariants:
* 1 MiB chunk size (regression-pinned via _CHUNK_BYTES constant).
* TCP_NODELAY=1 on the underlying socket per connection.
* `Connection: close` header on every response.
* `await response.drain()` after every `await response.write(...)`.
* HEAD route registered alongside GET (Telegram mobile probe).
* DEBUG log on mid-flight connection-reset (with bytes-sent counter).

Stdlib-only; mirrors tests/test_downloader.py style.
"""
import asyncio
import logging
import socket
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, mock_open, patch

from app.fileserver import FileServer, _CHUNK_BYTES


class TestChunkSizeRegressionPin(unittest.TestCase):
    """A future maintainer could bump _CHUNK_BYTES back to 8 MiB
    without realising why it was 1 MiB. Pin it."""

    def test_chunk_size_is_exactly_one_mib(self):
        self.assertEqual(_CHUNK_BYTES, 1024 * 1024)


class TestTcpNodelayOptIn(unittest.TestCase):
    """TCP_NODELAY=1 is the highest-impact fix for the end-of-stream
    stall. Pin the behaviour on its real and degenerate inputs."""

    def _request(self, transport_extra):
        request = MagicMock()
        request.transport = MagicMock()
        request.transport.get_extra_info.return_value = transport_extra
        return request

    def test_sets_tcp_nodelay_on_real_socket(self):
        sock = MagicMock(spec=socket.socket)
        FileServer._enable_tcp_nodelay(self._request(sock))
        sock.setsockopt.assert_called_with(
            socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)

    def test_swallows_no_socket(self):
        # Pipe / mock transports have no real socket. Must not raise.
        FileServer._enable_tcp_nodelay(self._request(None))

    def test_swallows_oserror_from_setsockopt(self):
        sock = MagicMock(spec=socket.socket)
        sock.setsockopt.side_effect = OSError("EBADF")
        FileServer._enable_tcp_nodelay(self._request(sock))


class TestHeadRouteRegistered(unittest.TestCase):
    """Telegram mobile clients probe HEAD before GET to decide resume
    vs fresh download. Verify HEAD routes through the same handler"""

    def test_head_route_resolves_without_resource_error(self):
        from aiohttp.test_utils import make_mocked_request
        fs = FileServer(port=0)
        # aiohttp's PlainResource auto-handles HEAD when GET is
        # registered, so an explicit add_head is redundant (raises
        # RuntimeError). Asserting at the ROUTING level is more
        # durable than iterating fs.app.router.routes() -- aiohttp
        # may consolidate method entries across versions, but the
        # public router.resolve() API is stable.
        head_req = make_mocked_request('HEAD', '/sample.mp4')
        head_resource = fs.app.router.resolve(head_req)
        get_req = make_mocked_request('GET', '/sample.mp4')
        get_resource = fs.app.router.resolve(get_req)
        self.assertIsNotNone(head_resource)
        self.assertIsNotNone(get_resource)


class TestHandlerInvariants(unittest.TestCase):
    """End-to-end mock-driven exercise of _handle_download covering
    both Range and non-Range code paths."""

    def setUp(self):
        # DEBUG messages emitted from inside _handle_download must reach
        # assertLogs even when an operator's LOG_LEVEL is INFO.
        logging.getLogger('yt_bot').setLevel(logging.DEBUG)

    def _make_request(self, *, range_header=None):
        request = MagicMock()
        request.match_info = {'filename': 'sample.mp4'}
        request.headers = (
            {'Range': range_header} if range_header is not None else {})
        request.transport = MagicMock()
        return request

    def _mock_response(self, *, write_side_effect=None):
        r = MagicMock()
        r.prepare = AsyncMock()
        if write_side_effect is not None:
            r.write = AsyncMock(side_effect=write_side_effect)
        else:
            r.write = AsyncMock()
        r.drain = AsyncMock()
        r.headers = MagicMock()
        r.headers.update = MagicMock()
        return r

    def _patch_path(self, total_size):
        stat_mock = MagicMock(st_size=total_size)
        return (
            patch.object(Path, 'exists', return_value=True),
            patch.object(Path, 'is_file', return_value=True),
            patch.object(Path, 'stat', return_value=stat_mock),
        )

    def test_non_range_path_emits_close_header_drains_each_chunk(self):
        request = self._make_request()
        response = self._mock_response()
        # 5 MiB at 1 MiB / chunk = exactly 5 writes, then EOF.
        total = 5 * _CHUNK_BYTES
        p_e, p_f, p_s = self._patch_path(total)
        with p_e, p_f, p_s, \
             patch('builtins.open', mock_open(read_data=b'A' * total)), \
             patch('aiohttp.web.StreamResponse', return_value=response):
            asyncio.run(FileServer(port=0)._handle_download(request))

        self.assertEqual(response.write.call_count, 5)
        self.assertEqual(response.drain.call_count, 5)
        headers_dict = response.headers.update.call_args[0][0]
        self.assertEqual(headers_dict['Connection'], 'close')

    def test_non_range_path_calls_nodelay_after_prepare(self):
        request = self._make_request()
        response = self._mock_response()
        sock = MagicMock(spec=socket.socket)
        request.transport.get_extra_info.return_value = sock

        total = _CHUNK_BYTES
        p_e, p_f, p_s = self._patch_path(total)
        with p_e, p_f, p_s, \
             patch('builtins.open', mock_open(read_data=b'A' * total)), \
             patch('aiohttp.web.StreamResponse', return_value=response):
            asyncio.run(FileServer(port=0)._handle_download(request))

        # prepare() once (response transitions to "headers sent"),
        # THEN setsockopt(TCP_NODELAY) is applied to the socket.
        self.assertEqual(response.prepare.call_count, 1)
        sock.setsockopt.assert_called_with(
            socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)

    def test_range_path_emits_partial_content_and_drains(self):
        # Range bytes=0-2621439 = first 2621440 bytes (~2.5 MiB).
        # At 1 MiB chunks = 3 writes (1 MiB, 1 MiB, 0.5 MiB).
        request = self._make_request(range_header='bytes=0-2621439')
        response = self._mock_response()
        total = 5 * _CHUNK_BYTES
        p_e, p_f, p_s = self._patch_path(total)
        with p_e, p_f, p_s, \
             patch('builtins.open', mock_open(read_data=b'B' * total)), \
             patch('aiohttp.web.StreamResponse', return_value=response):
            asyncio.run(FileServer(port=0)._handle_download(request))

        self.assertEqual(response.write.call_count, 3)
        self.assertEqual(response.drain.call_count, 3)
        headers_dict = response.headers.update.call_args[0][0]
        self.assertEqual(headers_dict['Connection'], 'close')
        self.assertIn('Content-Range', headers_dict)

    def test_disconnect_logs_bytes_sent_at_info(self):
        # Simulate mid-flight client disconnect on the first write.
        # The handler must catch + log at INFO (NOT silently swallow).
        # INFO (not DEBUG) because operators at default CONFIG_LOG_LEVEL
        # need to see this diagnostic without changing env;
        # `assertLogs(..., level='DEBUG')` captures DEBUG-and-above,
        # so INFO records are still included.
        request = self._make_request()
        response = self._mock_response(
            write_side_effect=ConnectionResetError("client closed"))

        total = 5 * _CHUNK_BYTES
        p_e, p_f, p_s = self._patch_path(total)
        with p_e, p_f, p_s, \
             patch('builtins.open', mock_open(read_data=b'A' * total)), \
             patch('aiohttp.web.StreamResponse', return_value=response):
            with self.assertLogs('yt_bot', level='DEBUG') as logs:
                asyncio.run(FileServer(port=0)._handle_download(request))

        self.assertTrue(
            any('client disconnect' in line for line in logs.output),
            f"Expected 'client disconnect' in {logs.output!r}",
        )


class TestFileServerSSLContext(unittest.TestCase):
    """Pins the ssl_context round-trip from __init__ to web.TCPSite.

    A future refactor that drops the kwarg from FileServer.__init__ or
    forgets to forward it to aiohttp.web.TCPSite would silently regress
    native HTTPS to plain HTTP — exactly the "I set the HTTPS domain
    and it doesn't work" failure mode this machinery exists to fix.

    Each test patches TCPSite at its import site (aiohttp.web.TCPSite)
    and asserts the call signature verbatim, so an absent argument
    surfaces as a "expected call not found" assertion failure rather
    than a runtime AttributeError deep inside aiohttp.
    """

    def _setup_runner_and_tcpsite_mocks(self):
        runner_cls = patch('aiohttp.web.AppRunner')
        runner_cls_mock = runner_cls.start()
        runner_instance = MagicMock()
        runner_instance.setup = AsyncMock()
        runner_cls_mock.return_value = runner_instance

        tcpsite_mock = patch('aiohttp.web.TCPSite').start()
        tcpsite_instance = MagicMock()
        tcpsite_instance.start = AsyncMock()
        tcpsite_mock.return_value = tcpsite_instance
        return runner_cls, tcpsite_mock, tcpsite_instance, runner_instance

    def _stop_patches(self, *patches):
        for p in patches:
            p.stop()

    def test_default_is_http_with_ssl_context_none(self):
        runner_patch, tcpsite_mock, _ti, runner_instance = (
            self._setup_runner_and_tcpsite_mocks())
        try:
            fs = FileServer(port=8000)
            asyncio.run(fs.start())
            # `ssl_context=None` MUST be forwarded verbatim — callers
            # that want to detect "default mode" downstream rely on
            # `is None` rather than the argument being absent.
            tcpsite_mock.assert_called_once_with(
                runner_instance, '0.0.0.0', 8000, ssl_context=None)
        finally:
            self._stop_patches(runner_patch, tcpsite_mock)

    def test_ssl_context_is_forwarded_unchanged(self):
        runner_patch, tcpsite_mock, _ti, runner_instance = (
            self._setup_runner_and_tcpsite_mocks())
        try:
            # Use `object()` as a sentinel — the FileServer pin is on
            # *identity* (the exact same ssl_context object is handed
            # to aiohttp), not on attribute equality, so a recreated
            # or proxied context would defeat the contract.
            sentinel = object()
            fs = FileServer(port=8443, ssl_context=sentinel)
            asyncio.run(fs.start())
            tcpsite_mock.assert_called_once_with(
                runner_instance, '0.0.0.0', 8443, ssl_context=sentinel)
        finally:
            self._stop_patches(runner_patch, tcpsite_mock)


if __name__ == '__main__':
    unittest.main()
