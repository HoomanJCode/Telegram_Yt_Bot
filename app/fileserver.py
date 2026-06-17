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
        self.app.router.add_head('/{filename}', self._handle_head)
        self._runner = None

    async def _handle_head(self, request):
        """Handle HEAD requests for download managers"""
        filename = request.match_info['filename']
        filepath = DOWNLOADS_DIR / filename
        if not filepath.exists() or not filepath.is_file():
            raise web.HTTPNotFound()
        size = filepath.stat().st_size
        return web.Response(
            status=200,
            headers={
                'Content-Type': _mime(filepath.suffix),
                'Content-Length': str(size),
                'Accept-Ranges': 'bytes',
                'Cache-Control': 'public, max-age=86400',
            }
        )

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
        
        # Handle Range request for resume support
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
                        chunk = f.read(min(1024 * 1024, remaining))
                        if not chunk: break
                        await response.write(chunk)
                        remaining -= len(chunk)
                return response
            except (ValueError, IndexError):
                pass
        
        # Full download
        response = web.StreamResponse()
        headers['Content-Length'] = str(file_size)
        response.headers.update(headers)
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