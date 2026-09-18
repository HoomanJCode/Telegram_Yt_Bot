"""aiohttp file server for serving downloaded files.

This module runs an aiohttp web application that exposes the `downloads/`
directory over HTTP (or HTTPS when an SSL context is provided). It supports
Range requests for resume-capable downloads and returns correct MIME types
for the media formats the bot produces.

The server also serves a landing page at the root URL (``/``) so that users
who visit the download domain directly in their browser see a friendly,
informative page instead of a bare 404. Custom error pages (404, 403, 400, 500)
are rendered using the templates in ``app.templates``.

AI RULE: If you modify this file, you must also update and fix the comments,
docstrings, and descriptions to keep them accurate and current. Every function
must have a descriptive docstring explaining its purpose, parameters, and
return values. Inline comments should explain WHY, not WHAT.
"""

import logging
import socket
from pathlib import Path
from aiohttp import web

from app.templates import (
    landing_page,
    not_found_page,
    forbidden_page,
    server_error_page,
    bad_request_page,
)


logger = logging.getLogger('yt_bot')
DOWNLOADS_DIR = Path('downloads')

# Single source of truth for the read buffer used on BOTH the Range
# path and the full-file path. 1 MiB (NOT 8 MiB): the FINAL fragment
# is at most 1 MiB, so any late ACKs finish within ~250 ms of Nagle's
# worst case instead of dragging past mobile-client idle timers that
# fire at 30-60 s. Throughput on any pipe >= 8 Mbps is identical;
# reliability goes up because each chunk retransmits in ~1 RTT
# instead of an 8-RTT stall on a packet loss at the tail. Pinned
# via tests/test_fileserver.py::TestChunkSizeRegressionPin.
_CHUNK_BYTES = 1024 * 1024


class _AiohttpNoiseFilter(logging.Filter):
    """Demote aiohttp's BadHttpMessage probe-spam from ERROR to DEBUG.

    Background internet scanners constantly hit public ports with malformed
    HTTP requests. Without this filter, aiohttp logs each one as an ERROR with
    a traceback, drowning out legitimate log messages. The filter changes the
    level of known probe patterns to DEBUG so they are silently ignored.
    """

    PATTERNS = (
        'badhttpmessage',
        "missing 'host' header",
        "missing host header",
        'invalid http method',
        'invalid http version',
        'too many headers',
        'invalid header',
        'bad request line',
    )

    def filter(self, record):
        """Mutate known probe records to DEBUG level before logging."""
        # Look at the rendered message text so we can match the actual error
        # text emitted by aiohttp.
        msg = record.getMessage().lower()
        if record.levelno >= logging.ERROR and any(p in msg for p in self.PATTERNS):
            # Mutate the record to DEBUG so default INFO+ handlers skip it.
            record.levelno = logging.DEBUG
            record.levelname = 'DEBUG'
        return True


# Install once at import time so it survives across all FileServer instances.
logging.getLogger('aiohttp.server').addFilter(_AiohttpNoiseFilter())


class FileServer:
    """Async HTTP/HTTPS file server backed by aiohttp.

    The server exposes files from `downloads/` and is started by the main bot
    in the same event loop. Range requests are supported for resumable downloads.
    """

    # `ssl_context=None` opts the server into HTTPS mode when an
    # ssl.SSLContext built from a PEM cert+key is forwarded by
    # YouTubeDownloaderBot.__init__ (path resolved from the SSL_CERT_FILE
    # / SSL_KEY_FILE env vars). Default is None (plain HTTP) so an operator
    # upgrading the binary without setting those vars sees exactly the
    # previous behaviour — no surprise protocol flip.
    def __init__(self, port=8000, ssl_context=None):
        self.port = port
        self.ssl_context = ssl_context
        # Build an aiohttp application.
        self.app = web.Application()

        # --- Middleware: render custom HTML for handled HTTP errors. ---
        # aiohttp's default error responses are plain-text. This middleware
        # replaces them with styled HTML pages for common status codes.
        # IMPORTANT: middleware only wraps requests that match a route, so
        # completely unmatched paths (e.g. /foo/bar when only /{filename}
        # exists) still get aiohttp's default 404. We handle that via a
        # catch-all route instead (see the fallback below).
        @web.middleware
        async def _error_middleware(request, handler):
            try:
                return await handler(request)
            except web.HTTPNotFound:
                return _html_response(not_found_page(request.path), status=404)
            except web.HTTPForbidden:
                return _html_response(forbidden_page(), status=403)
            except web.HTTPBadRequest as exc:
                reason = exc.text or ''
                return _html_response(bad_request_page(reason), status=400)
            except web.HTTPException as exc:
                return _html_response(server_error_page(), status=exc.status)
        self.app.middlewares.append(_error_middleware)

        # --- Routes ---
        # IMPORTANT: registration order matters. aiohttp matches routes in
        # registration order, so catch-all routes must come LAST.
        #
        # 1. Landing page at root so the domain is browsable.
        self.app.router.add_get('/', self._handle_root)
        # 2. File listing (directory index).
        self.app.router.add_get('/files', self._handle_file_list)
        # 3. GET serves the body. HEAD serves only headers -- Telegram
        #    mobile clients probe HEAD before GET to learn Content-Length
        #    and decide resume / no-resume. aiohttp's PlainResource
        #    auto-handles HEAD for any registered GET handler, so a
        #    single `add_get` is enough (an explicit `add_head` for a
        #    `/{filename}` PlainResource raises RuntimeError because
        #    PlainResource.add_route already wired HEAD from the GET).
        self.app.router.add_get('/{filename}', self._handle_download)
        # 4. Catch-all fallback: anything not matched by earlier routes
        #    (e.g. multi-segment paths like /foo/bar) renders the 404 page.
        #    MUST be registered LAST.
        self.app.router.add_get('/{tail:.*}', self._handle_fallback)

        self._runner = None

    @staticmethod
    def _enable_tcp_nodelay(request):
        """Disable Nagle on this connection's underlying socket.

        Nagle's algorithm buffers small writes waiting for an ACK before
        sending the next packet. For the final bytes of a large file transfer
        this can cause a visible "stall at 99 %". Disabling it makes the last
        chunk flush immediately.
        """
        # `transport.get_extra_info('socket')` returns the underlying socket,
        # if any. Some transports (e.g. test fakes) may not expose one, so any
        # error is silently ignored rather than failing the request.
        try:
            sock = request.transport.get_extra_info('socket')
            if sock is not None:
                sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        except (OSError, AttributeError):
            pass

    # ── Landing page, file listing, and catch-all fallback ──────────────

    async def _handle_root(self, request):
        """Render the landing page at ``/``.

        Shows a friendly welcome message with service status and a link to
        browse available files. This makes the download domain browsable
        instead of returning a bare 404.
        """
        host = request.host
        file_count = _count_files()
        return _html_response(landing_page(host, file_count))

    async def _handle_file_list(self, request):
        """Render a simple file listing at ``/files``.

        Lists all files currently in the downloads directory with their sizes.
        """
        files = sorted(DOWNLOADS_DIR.iterdir()) if DOWNLOADS_DIR.is_dir() else []
        items_html = ''
        for f in files:
            if f.is_file():
                size = _human_size(f.stat().st_size)
                items_html += (
                    f'<li>'
                    f'<a href="/{f.name}">{f.name}</a>'
                    f'<span class="file-size">{size}</span>'
                    f'</li>'
                )

        if not items_html:
            items_html = '<p style="color: #64748b;">No files currently available.</p>'

        body = f"""
            <h1>📂 Available Files</h1>
            <p class="subtitle">{len(files)} file{'s' if len(files) != 1 else ''} on this server</p>
            <div class="card">
                <ul class="files">{items_html}</ul>
            </div>
            <a href="/" class="btn btn-secondary">← Back Home</a>
        """
        return _html_response(
            f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>File Listing — Media Server</title>
    <style>{_STYLES_EXT}</style>
</head>
<body>
    <div class="container">
        {body}
    </div>
</body>
</html>"""
        )

    async def _handle_fallback(self, request):
        """Catch-all handler for unmatched paths.

        Returns the styled 404 page. This catches multi-segment paths and
        anything that does not match a known route.
        """
        return _html_response(not_found_page(request.path), status=404)

    # ── Main download handler ───────────────────────────────────────────

    async def _handle_download(self, request):
        """Serve a single file, optionally honouring a Range header."""
        # Resolve the requested filename against the downloads directory.
        filename = request.match_info['filename']
        filepath = DOWNLOADS_DIR / filename

        # Reject requests for missing or directory paths to avoid leaking
        # information about the filesystem outside `downloads/`.
        if not filepath.exists() or not filepath.is_file():
            raise web.HTTPNotFound()

        file_size = filepath.stat().st_size

        # Force `Connection: close` so the client knows the stream ended when
        # the connection closes. This avoids ambiguous EOF on mobile clients.
        # Standard headers for a static file with Range support.
        headers = {
            'Content-Type': _mime(filepath.suffix),
            'Accept-Ranges': 'bytes',
            'Cache-Control': 'public, max-age=86400',
            'Content-Disposition': f'inline; filename="{filename}"',
            'Connection': 'close',
        }

        # Parse the Range header if present (e.g. "bytes=0-1023").
        range_header = request.headers.get('Range', '')
        if range_header.startswith('bytes='):
            try:
                range_str = range_header[6:]
                if '-' in range_str:
                    start_str, end_str = range_str.split('-', 1)
                    start = int(start_str) if start_str else 0
                    end = int(end_str) if end_str else file_size - 1
                else:
                    start = int(range_str)
                    end = file_size - 1

                if start >= file_size:
                    raise web.HTTPRequestRangeNotSatisfiable()

                end = min(end, file_size - 1)
                length = end - start + 1

                response = web.StreamResponse(status=206)
                headers['Content-Range'] = f'bytes {start}-{end}/{file_size}'
                headers['Content-Length'] = str(length)
                response.headers.update(headers)
                await response.prepare(request)
                self._enable_tcp_nodelay(request)

                bytes_sent = 0
                try:
                    with open(filepath, 'rb') as f:
                        f.seek(start)
                        remaining = length
                        while remaining > 0:
                            chunk = f.read(min(_CHUNK_BYTES, remaining))
                            if not chunk:
                                break
                            await response.write(chunk)
                            await response.drain()
                            remaining -= len(chunk)
                            bytes_sent += len(chunk)
                except (ConnectionResetError, BrokenPipeError, ConnectionAbortedError) as e:
                    # Log client disconnects during Range transfers. See the
                    # non-Range path for the rationale behind INFO level.
                    logger.info(
                        'file-serve client disconnect during Range after %d/%d bytes of %s: %s',
                        bytes_sent, length, filename, e)
                return response
            except (ValueError, IndexError):
                pass

        # Non-Range path: serve the entire file in 1 MiB chunks.
        response = web.StreamResponse()
        headers['Content-Length'] = str(file_size)
        response.headers.update(headers)
        await response.prepare(request)
        self._enable_tcp_nodelay(request)

        bytes_sent = 0
        try:
            with open(filepath, 'rb') as f:
                while chunk := f.read(_CHUNK_BYTES):
                    await response.write(chunk)
                    await response.drain()
                    bytes_sent += len(chunk)
        except (ConnectionResetError, BrokenPipeError, ConnectionAbortedError) as e:
            # Mid-flight client disconnect -- log it (INFO) instead of
            # silently swallowing. The bytes_sent counter tells the
            # operator whether the user's "stall at 99%" was mid-stream
            # (real network problem) or at EOF (the end-of-stream race
            # fixed by NODELAY + drain + Connection: close). INFO, not
            # WARNING, because legitimate "user closed Telegram app"
            # disconnects are normal noise on a public port.
            logger.info(
                'file-serve client disconnect after %d/%d bytes of %s: %s',
                bytes_sent, file_size, filename, e)
        return response

    async def start(self):
        """Start the aiohttp server on 0.0.0.0:port."""
        self._runner = web.AppRunner(self.app)
        await self._runner.setup()
        # `ssl_context=self.ssl_context` (default None) is the aiohttp 3.x
        # opt-in for native TLS termination. When non-None the listening
        # socket accepts a TLS handshake instead of a plain-text HTTP
        # request — operators pairing this with SSL_CERT_FILE /
        # SSL_KEY_FILE in .env get HTTPS without a reverse proxy.
        # NOTE: aiohttp's TCPSite raises ValueError if `ssl_context` is
        # passed together with a unix-socket / non-TCP site; we always
        # use TCP ('0.0.0.0', self.port) so that combination is safe.
        await web.TCPSite(self._runner, '0.0.0.0', self.port,
                          ssl_context=self.ssl_context).start()
        scheme = 'HTTPS' if self.ssl_context else 'HTTP'
        logger.info('File server on port %d (%s)', self.port, scheme)


def _mime(ext):
    """Return the correct MIME type for a given file extension."""
    return {
        '.mp4': 'video/mp4', '.webm': 'video/webm', '.mkv': 'video/x-matroska',
        '.mp3': 'audio/mpeg', '.m4a': 'audio/mp4', '.opus': 'audio/opus',
        '.jpg': 'image/jpeg', '.png': 'image/png', '.webp': 'image/webp',
        '.srt': 'text/plain; charset=utf-8', '.vtt': 'text/vtt; charset=utf-8',
    }.get(ext.lower(), 'application/octet-stream')


# ── Helpers ──────────────────────────────────────────────────────────────


def _html_response(html: str, *, status: int = 200) -> web.Response:
    """Build an aiohttp Response with HTML content type.

    Args:
        html: The full HTML document string.
        status: HTTP status code. Defaults to 200.

    Returns:
        An aiohttp ``web.Response`` with ``text/html`` content type.
    """
    return web.Response(
        text=html,
        content_type='text/html',
        status=status,
    )


# Shared minimal styles used by the file listing page. Kept in a separate
# string to avoid duplicating the full _STYLES block from templates.py for
# the one page that has a slightly different layout.
_STYLES_EXT = """
body {{
    margin: 0;
    font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Oxygen,
        Ubuntu, Cantarell, sans-serif;
    background: linear-gradient(135deg, #0f0c29, #1a1a3e, #24243e);
    color: #e0e0e0;
    min-height: 100vh;
    display: flex;
    flex-direction: column;
    align-items: center;
    justify-content: center;
}}
.container {{
    text-align: center;
    padding: 2rem;
    max-width: 700px;
    width: 100%;
}}
h1 {{
    font-size: 2.5rem;
    margin: 0.5rem 0;
    background: linear-gradient(90deg, #60a5fa, #a78bfa);
    -webkit-background-clip: text;
    -webkit-text-fill-color: transparent;
    background-clip: text;
}}
.subtitle {{
    font-size: 1.1rem;
    color: #94a3b8;
    margin-bottom: 2rem;
}}
.card {{
    background: #1e1e3fcc;
    backdrop-filter: blur(12px);
    border: 1px solid #3b3b6e;
    border-radius: 16px;
    padding: 1.5rem;
    margin: 1.5rem 0;
}}
.files {{
    list-style: none;
    padding: 0;
    margin: 1rem 0;
}}
.files li {{
    padding: 0.6rem 1rem;
    margin: 0.3rem 0;
    background: #16163acc;
    border-radius: 8px;
    display: flex;
    align-items: center;
    justify-content: space-between;
}}
.files a {{
    color: #93c5fd;
    text-decoration: none;
}}
.files a:hover {{
    text-decoration: underline;
}}
.file-size {{
    color: #64748b;
    font-size: 0.85rem;
}}
.btn {{
    display: inline-block;
    padding: 0.75rem 1.5rem;
    margin: 0.5rem;
    border-radius: 8px;
    text-decoration: none;
    font-weight: 600;
    transition: all 0.2s ease;
    border: none;
    cursor: pointer;
}}
.btn-secondary {{
    background: #2d2d5e;
    color: #cbd5e1;
}}
.btn-secondary:hover {{
    background: #3b3b6e;
    transform: translateY(-2px);
}}
"""


def _human_size(size_bytes: int) -> str:
    """Format a byte count as a human-readable string.

    Args:
        size_bytes: Size in bytes.

    Returns:
        A string like "12.3 MiB" or "1.5 GiB".
    """
    for unit in ('B', 'KiB', 'MiB', 'GiB', 'TiB'):
        if size_bytes < 1024:
            return f'{size_bytes:.1f} {unit}' if unit != 'B' else f'{size_bytes} B'
        size_bytes /= 1024
    return f'{size_bytes:.1f} PiB'


def _count_files() -> int:
    """Count regular files in the downloads directory (non-recursive).

    Returns:
        The number of files, or 0 if the directory does not exist.
    """
    if not DOWNLOADS_DIR.is_dir():
        return 0
    return sum(1 for f in DOWNLOADS_DIR.iterdir() if f.is_file())
