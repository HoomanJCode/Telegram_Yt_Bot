"""aiohttp file server"""
import logging
from pathlib import Path
from aiohttp import web

logger = logging.getLogger('yt_bot')
DOWNLOADS_DIR = Path('downloads')


class _AiohttpNoiseFilter(logging.Filter):
    """Demote aiohttp's BadHttpMessage probe-spam from ERROR to DEBUG.

    aiohttp's HTTP parser raises `BadHttpMessage` for every malformed probe
    (missing Host header, junk bytes, unsupported HTTP version) and emits it
    through the `aiohttp.server` logger with `exc_info=True`.  Because the
    file server is exposed on a public port we get hit by background scanners
    many times per minute, drowning out operator logs.

    Mutating `record.levelno` to DEBUG makes our INFO+ StreamHandler skip the
    record entirely; the traceback never prints.  Real protocol-level errors
    that don't match these fragments keep their original level.
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
        msg = record.getMessage().lower()
        if record.levelno >= logging.ERROR and any(p in msg for p in self.PATTERNS):
            record.levelno = logging.DEBUG
            record.levelname = 'DEBUG'
        return True


# Install once at import time so it survives across all FileServer instances.
logging.getLogger('aiohttp.server').addFilter(_AiohttpNoiseFilter())

class FileServer:
    def __init__(self, port=8000):
        self.port = port
        self.app = web.Application()
        self.app.router.add_get('/{filename}', self._handle_download)
        self._runner = None

    async def _handle_download(self, request):
        filename = request.match_info['filename']
        filepath = DOWNLOADS_DIR / filename
        if not filepath.exists() or not filepath.is_file():
            raise web.HTTPNotFound()
        
        file_size = filepath.stat().st_size
        headers = {
            'Content-Type': _mime(filepath.suffix),
            'Accept-Ranges': 'bytes',
            'Cache-Control': 'public, max-age=86400',
            'Content-Disposition': f'inline; filename="{filename}"',
        }
        
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
                
                with open(filepath, 'rb') as f:
                    f.seek(start)
                    remaining = length
                    while remaining > 0:
                        chunk = f.read(min(8 * 1024 * 1024, remaining))
                        if not chunk: break
                        await response.write(chunk)
                        remaining -= len(chunk)
                return response
            except (ValueError, IndexError):
                pass
        
        response = web.StreamResponse()
        headers['Content-Length'] = str(file_size)
        response.headers.update(headers)
        await response.prepare(request)
        
        try:
            with open(filepath, 'rb') as f:
                while chunk := f.read(8 * 1024 * 1024):
                    await response.write(chunk)
        except (ConnectionResetError, BrokenPipeError, ConnectionAbortedError):
            pass
        return response

    async def start(self):
        self._runner = web.AppRunner(self.app)
        await self._runner.setup()
        await web.TCPSite(self._runner, '0.0.0.0', self.port).start()
        logger.info("File server on port %d", self.port)


def _mime(ext):
    return {
        '.mp4': 'video/mp4', '.webm': 'video/webm', '.mkv': 'video/x-matroska',
        '.mp3': 'audio/mpeg', '.m4a': 'audio/mp4', '.opus': 'audio/opus',
        '.jpg': 'image/jpeg', '.png': 'image/png', '.webp': 'image/webp',
        '.srt': 'text/plain; charset=utf-8', '.vtt': 'text/vtt; charset=utf-8',
    }.get(ext.lower(), 'application/octet-stream')