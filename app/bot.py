"""Main bot class with shared state"""
import asyncio
from pathlib import Path
from typing import Dict, List, Tuple
from app.models import VideoRecord
from app.fileserver import FileServer
from app.utils import check_ffmpeg, load_data, save_data

DATA_DIR = Path('data')
DOWNLOADS_DIR = Path('downloads')

class YouTubeDownloaderBot:
    def __init__(self):
        from config import Config
        self.config = Config()
        self.base_url = self.config.BASE_DOWNLOAD_LINK.rstrip('/')
        try: port = int(self.base_url.split(':')[-1]) if ':' in self.base_url.split('/')[2] else 8000
        except: port = 8000
        for d in (DATA_DIR, DOWNLOADS_DIR): d.mkdir(parents=True, exist_ok=True)
        self._cookie_file_ids: Dict[int, str] = {}
        self._cookie_data: Dict[int, bytes] = {}
        self._cookie_tmpfiles: Dict[int, str] = {}
        self._bot = None
        self._bot_username = None
        self.videos: Dict[int, List[VideoRecord]] = {}
        self._pending_urls: Dict[int, tuple] = {}
        self._nav_stack: Dict[int, List[Tuple[str, any]]] = {}
        self._tokens: Dict[str, dict] = {}
        self._group_admins: Dict[int, set] = {}
        self._global_file_ids: Dict[str, str] = {}
        self._download_semaphore = asyncio.Semaphore(1)
        self.has_ffmpeg = check_ffmpeg()
        self.file_server = FileServer(port=port)
        load_data(self)

    def save(self):
        save_data(self)

    async def _router(self, u, c):
        from app.handlers.navigation import router
        await router(self, u, c)