"""aiohttp file server"""
import logging
from pathlib import Path
from aiohttp import web

logger = logging.getLogger('yt_bot')
DOWNLOADS_DIR = Path('downloads')

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
        response = web.StreamResponse()
        response.headers['Content-Type'] = _mime(filepath.suffix)
        response.headers['Content-Length'] = str(filepath.stat().st_size)
        response.headers['Cache-Control'] = 'public, max-age=86400'
        await response.prepare(request)
        try:
            with open(filepath, 'rb') as f:
                while chunk := f.read(1024 * 1024):
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
        '.jpg': 'image/jpeg', '.png': 'image/png', '.webp': 'image/webp'
    }.get(ext.lower(), 'application/octet-stream')