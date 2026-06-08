#!/usr/bin/env python3
"""
YouTube Downloader Telegram Bot
Inline mode + cookie caching + file server
"""

import os
import sys
import logging
import json
import time
import shutil
import re
import threading
import subprocess
import asyncio
import tempfile
from pathlib import Path
from datetime import datetime, timedelta
from typing import Dict, Optional, List
from urllib.parse import quote
from uuid import uuid4

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, InlineQueryResultArticle, InputTextMessageContent
from telegram.ext import (
    Application, CommandHandler, MessageHandler, CallbackQueryHandler,
    ConversationHandler, filters, ContextTypes, InlineQueryHandler
)
from telegram.constants import ParseMode
import yt_dlp
from yt_dlp.utils import DownloadError
from aiohttp import web

from config import Config

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logging.basicConfig(format='%(asctime)s - %(levelname)s - %(message)s', level=logging.WARNING)
for lib in ('httpx', 'httpcore', 'telegram', 'telegram.ext', 'aiohttp'):
    logging.getLogger(lib).setLevel(logging.WARNING)

logger = logging.getLogger('yt_bot')
logger.setLevel(logging.INFO)
h = logging.StreamHandler()
h.setFormatter(logging.Formatter('%(asctime)s - %(levelname)s - %(message)s'))
logger.addHandler(h)
logger.propagate = False

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
DATA_DIR = Path('data')
DOWNLOADS_DIR = Path('downloads')
WAITING_FOR_COOKIES = 1
YOUTUBE_RE = re.compile(r'(https?://)?(www\.)?(youtube\.com|youtu\.be)/\S+')

# ---------------------------------------------------------------------------
# aiohttp File Server
# ---------------------------------------------------------------------------
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
        response.headers['Content-Type'] = self._get_mime(filepath.suffix)
        response.headers['Content-Length'] = str(filepath.stat().st_size)
        response.headers['Cache-Control'] = 'public, max-age=86400'
        response.headers['Accept-Ranges'] = 'bytes'
        
        range_header = request.headers.get('Range', '')
        if range_header.startswith('bytes='):
            try:
                start, end = range_header[6:].split('-')
                start = int(start) if start else 0
                end = int(end) if end else filepath.stat().st_size - 1
                response.set_status(206)
                response.headers['Content-Range'] = f'bytes {start}-{end}/{filepath.stat().st_size}'
                response.headers['Content-Length'] = str(end - start + 1)
            except:
                start, end = 0, filepath.stat().st_size - 1
        else:
            start, end = 0, filepath.stat().st_size - 1
        
        await response.prepare(request)
        try:
            with open(filepath, 'rb') as f:
                f.seek(start)
                remaining = end - start + 1
                while remaining > 0:
                    chunk = f.read(min(1024*1024, remaining))
                    if not chunk: break
                    await response.write(chunk)
                    remaining -= len(chunk)
        except (ConnectionResetError, BrokenPipeError, ConnectionAbortedError):
            pass
        return response
    
    def _get_mime(self, ext):
        return {
            '.mp4': 'video/mp4', '.webm': 'video/webm', '.mkv': 'video/x-matroska',
            '.mp3': 'audio/mpeg', '.m4a': 'audio/mp4', '.opus': 'audio/opus',
            '.jpg': 'image/jpeg', '.jpeg': 'image/jpeg', '.png': 'image/png',
            '.webp': 'image/webp',
        }.get(ext.lower(), 'application/octet-stream')
    
    async def start(self):
        self._runner = web.AppRunner(self.app)
        await self._runner.setup()
        site = web.TCPSite(self._runner, '0.0.0.0', self.port)
        await site.start()
        logger.info("File server on port %d", self.port)
    
    async def stop(self):
        if self._runner:
            await self._runner.cleanup()

# ---------------------------------------------------------------------------
# Video Record
# ---------------------------------------------------------------------------
class VideoRecord:
    __slots__ = ('title', 'url', 'video_id', 'file_path', 'file_size',
                 'download_time', 'telegram_file_id', 'media_type')
    
    def __init__(self, title, url, video_id, file_path, file_size,
                 download_time, telegram_file_id=None, media_type='video'):
        self.title = title
        self.url = url
        self.video_id = video_id
        self.file_path = file_path
        self.file_size = file_size
        self.download_time = download_time
        self.telegram_file_id = telegram_file_id
        self.media_type = media_type
    
    def to_dict(self):
        return {k: getattr(self, k) for k in self.__slots__}
    
    @classmethod
    def from_dict(cls, d):
        return cls(**d)

# ---------------------------------------------------------------------------
# Bot
# ---------------------------------------------------------------------------
class YouTubeDownloaderBot:
    def __init__(self):
        self.config = Config()
        self.base_url = self.config.BASE_DOWNLOAD_LINK.rstrip('/')
        try: port = int(self.base_url.split(':')[-1]) if ':' in self.base_url.split('/')[2] else 8000
        except: port = 8000
        
        for d in (DATA_DIR, DOWNLOADS_DIR):
            d.mkdir(parents=True, exist_ok=True)
        
        self._cookie_file_ids: Dict[int, str] = {}
        self._cookie_data: Dict[int, bytes] = {}
        self._cookie_tmpfiles: Dict[int, str] = {}
        self._cookie_last_used: Dict[int, float] = {}
        self._bot = None
        
        self.videos: Dict[int, List[VideoRecord]] = {}
        self._pending_urls: Dict[int, tuple] = {}
        self._download_tasks: Dict[int, list] = {}
        
        self.has_ffmpeg = self._check_ffmpeg()
        self.file_server = FileServer(port=port)
        
        self._load()
        self._start_cleanup()
    
    def _check_ffmpeg(self):
        try:
            subprocess.run(['ffmpeg', '-version'], capture_output=True, timeout=5)
            return True
        except:
            return False
    
    def _load(self):
        try:
            fp = DATA_DIR / 'user_videos.json'
            if fp.exists():
                data = json.loads(fp.read_text())
                self.videos = {int(k): [VideoRecord.from_dict(v) for v in vs] for k, vs in data.items()}
                logger.info("Loaded videos: %d users", len(self.videos))
        except Exception as e:
            logger.error("Load videos: %s", e)
        
        try:
            fp = DATA_DIR / 'cookie_file_ids.json'
            if fp.exists():
                self._cookie_file_ids = {int(k): v for k, v in json.loads(fp.read_text()).items()}
                logger.info("Loaded cookie IDs: %d users", len(self._cookie_file_ids))
        except Exception as e:
            logger.error("Load cookie IDs: %s", e)
    
    def _save(self):
        try:
            (DATA_DIR / 'user_videos.json').write_text(
                json.dumps({str(k): [v.to_dict() for v in vs] for k, vs in self.videos.items()}, indent=2))
        except Exception as e:
            logger.error("Save videos: %s", e)
        
        try:
            (DATA_DIR / 'cookie_file_ids.json').write_text(
                json.dumps({str(k): v for k, v in self._cookie_file_ids.items()}, indent=2))
        except Exception as e:
            logger.error("Save cookie IDs: %s", e)
    
    async def _load_cookies_from_telegram(self, uid: int) -> bool:
        if uid not in self._cookie_file_ids or not self._bot:
            return False
        
        try:
            file_id = self._cookie_file_ids[uid]
            file = await self._bot.get_file(file_id)
            cookie_bytes = await file.download_as_bytearray()
            self._cookie_data[uid] = bytes(cookie_bytes)
            self._cookie_last_used[uid] = time.time()
            
            if uid in self._cookie_tmpfiles:
                try: os.unlink(self._cookie_tmpfiles[uid])
                except: pass
                del self._cookie_tmpfiles[uid]
            
            logger.info("Re-downloaded cookies for user %d", uid)
            return True
        except Exception as e:
            logger.warning("Failed to re-download cookies for %d: %s", uid, str(e)[:50])
            del self._cookie_file_ids[uid]
            self._save()
            return False
    
    def _start_cleanup(self):
        def w():
            while True:
                try: self._cleanup()
                except Exception as e: logger.error("Cleanup: %s", e)
                time.sleep(3600)
        threading.Thread(target=w, daemon=True).start()
    
    def _cleanup(self):
        cutoff = datetime.now() - timedelta(days=self.config.STORAGE_DAYS)
        for f in DOWNLOADS_DIR.iterdir():
            if f.is_file() and datetime.fromtimestamp(f.stat().st_mtime) < cutoff:
                f.unlink()
        for uid in list(self.videos):
            self.videos[uid] = [v for v in self.videos[uid] if Path(v.file_path).exists()]
            if not self.videos[uid]: del self.videos[uid]
        
        ttl_seconds = self.config.COOKIE_TTL_HOURS * 3600
        now = time.time()
        expired = [uid for uid, ts in self._cookie_last_used.items() if now - ts >= ttl_seconds]
        for uid in expired:
            self._cookie_data.pop(uid, None)
            self._cookie_last_used.pop(uid, None)
            if uid in self._cookie_tmpfiles:
                try: os.unlink(self._cookie_tmpfiles[uid])
                except: pass
                del self._cookie_tmpfiles[uid]
        
        self._save()
    
    def _has_cookies(self, uid):
        if uid in self._cookie_data:
            last_used = self._cookie_last_used.get(uid, 0)
            ttl_seconds = self.config.COOKIE_TTL_HOURS * 3600
            if time.time() - last_used < ttl_seconds:
                return True
            else:
                del self._cookie_data[uid]
                self._cookie_last_used.pop(uid, None)
                if uid in self._cookie_tmpfiles:
                    try: os.unlink(self._cookie_tmpfiles[uid])
                    except: pass
                    del self._cookie_tmpfiles[uid]
                return False
        return uid in self._cookie_file_ids
    
    async def _ensure_cookies(self, uid: int) -> bool:
        if uid in self._cookie_data:
            last_used = self._cookie_last_used.get(uid, 0)
            ttl_seconds = self.config.COOKIE_TTL_HOURS * 3600
            if time.time() - last_used < ttl_seconds:
                self._cookie_last_used[uid] = time.time()
                return True
            else:
                del self._cookie_data[uid]
                self._cookie_last_used.pop(uid, None)
                if uid in self._cookie_tmpfiles:
                    try: os.unlink(self._cookie_tmpfiles[uid])
                    except: pass
                    del self._cookie_tmpfiles[uid]
        
        if uid in self._cookie_file_ids:
            success = await self._load_cookies_from_telegram(uid)
            if success:
                self._cookie_last_used[uid] = time.time()
            return success
        
        return False
    
    def _get_cookie_file(self, uid):
        if uid not in self._cookie_tmpfiles or not os.path.exists(self._cookie_tmpfiles[uid]):
            tmp = tempfile.NamedTemporaryFile(mode='w', suffix='.txt', delete=False)
            tmp.write(self._cookie_data[uid].decode('utf-8', errors='replace'))
            tmp.close()
            self._cookie_tmpfiles[uid] = tmp.name
        return self._cookie_tmpfiles[uid]
    
    def _ok(self, uid): return not self.config.get_whitelist() or uid in self.config.get_whitelist()
    
    def _extract_url(self, text):
        m = YOUTUBE_RE.search(text)
        if m:
            u = m.group(0)
            if u.startswith('www.'): u = 'https://' + u
            elif not u.startswith('http'): u = 'https://' + u
            return u
        return None
    
    def _extract_video_id(self, url):
        for p in [
            r'(?:youtube\.com/watch\?v=|youtu\.be/|youtube\.com/embed/|youtube\.com/v/)([a-zA-Z0-9_-]{11})',
            r'youtube\.com/shorts/([a-zA-Z0-9_-]{11})',
        ]:
            m = re.search(p, url)
            if m: return m.group(1)
        return None
    
    def _get_existing_types(self, uid, video_id):
        return {v.media_type for v in self.videos.get(uid, []) 
                if v.video_id == video_id and Path(v.file_path).exists()}
    
    def _find_existing(self, uid, video_id, media_type='video'):
        for v in self.videos.get(uid, []):
            if v.video_id == video_id and v.media_type == media_type and Path(v.file_path).exists():
                return v
        return None
    
    @staticmethod
    def _esc(text):
        for c in '*_`[]': text = text.replace(c, '\\' + c)
        return text
    
    def _menu(self, uid):
        has = self._has_cookies(uid)
        vc = len(self.videos.get(uid, []))
        return InlineKeyboardMarkup([
            [InlineKeyboardButton("📹 Recent Downloads", callback_data='r')],
            [InlineKeyboardButton("🍪 Upload Cookies", callback_data='c')],
            [InlineKeyboardButton(f"🍪 {'✅' if has else '❌'}", callback_data='cs'),
             InlineKeyboardButton(f"📦 {vc} files", callback_data='vc')],
        ])
    
    def _format_choice_keyboard(self, uid, video_id):
        existing = self._get_existing_types(uid, video_id)
        kb = []
        v_label = "🎬 Video (MP4)"
        if 'video' in existing: v_label = "✅ 🎬 Video - Downloaded"
        kb.append([InlineKeyboardButton(v_label, callback_data='fmt_video')])
        
        a_label = f"🎵 Audio ({'MP3' if self.has_ffmpeg else 'M4A'})"
        if 'audio' in existing: a_label = "✅ 🎵 Audio - Downloaded"
        kb.append([InlineKeyboardButton(a_label, callback_data='fmt_audio')])
        
        t_label = "🖼️ Thumbnails"
        if 'thumb' in existing: t_label = "✅ 🖼️ Thumbnails - Downloaded"
        kb.append([InlineKeyboardButton(t_label, callback_data='fmt_thumb')])
        
        kb.append([InlineKeyboardButton("🔙 Cancel", callback_data='b')])
        return InlineKeyboardMarkup(kb)
    
    def _delivery_keyboard(self, uid, idx=None):
        idx_str = str(idx) if idx is not None else 'new'
        return InlineKeyboardMarkup([
            [InlineKeyboardButton("📤 Send via Telegram", callback_data=f'tg_{idx_str}')],
            [InlineKeyboardButton("📋 Get Download Link", callback_data=f'lk_{idx_str}')],
            [InlineKeyboardButton("🔙 Back to formats", callback_data=f'backfmt_{idx_str}')],
        ])
    
    def _sync_fetch_info(self, uid, url):
        opts = {
            'format': 'best', 'cookiefile': self._get_cookie_file(uid),
            'quiet': True, 'no_warnings': True, 'socket_timeout': 30, 'retries': 3,
        }
        with yt_dlp.YoutubeDL(opts) as ydl:
            return ydl.extract_info(url, download=False)
    
    def _sync_download(self, uid, url, media_type):
        base_opts = {
            'cookiefile': self._get_cookie_file(uid),
            'quiet': True, 'no_warnings': True,
            'socket_timeout': 120, 'retries': 50, 'fragment_retries': 50,
            'concurrent_fragment_downloads': 8, 'no_mtime': True,
        }
        
        if media_type == 'video':
            opts = {**base_opts, 'format': 'best[ext=mp4]/best',
                    'outtmpl': str(DOWNLOADS_DIR / '%(id)s_v.%(ext)s'), 'merge_output_format': 'mp4'}
        else:
            opts = {**base_opts, 'format': 'bestaudio[ext=m4a]/bestaudio',
                    'outtmpl': str(DOWNLOADS_DIR / '%(id)s_a.%(ext)s')}
            if self.has_ffmpeg:
                opts['postprocessors'] = [{'key': 'FFmpegExtractAudio', 'preferredcodec': 'mp3', 'preferredquality': '192'}]
        
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=True)
            title, vid = info.get('title', 'Unknown'), info.get('id', '')
            fp = ydl.prepare_filename(info)
            if media_type == 'audio' and self.has_ffmpeg:
                fp = str(Path(fp).with_suffix('.mp3'))
            
            found = None
            if Path(fp).exists(): found = fp
            else:
                for ext in ('.mp4', '.mp3', '.m4a', '.webm', '.mkv', '.opus'):
                    alt = DOWNLOADS_DIR / f'{Path(fp).stem}{ext}'
                    if alt.exists(): found = str(alt); break
            if not found:
                for f in DOWNLOADS_DIR.iterdir():
                    if f.is_file() and f.stem.startswith(vid): found = str(f); break
            if not found: raise FileNotFoundError(title)
            return found, title, vid
    
    def _sync_download_thumb(self, uid, url):
        opts = {
            'cookiefile': self._get_cookie_file(uid),
            'quiet': True, 'no_warnings': True,
            'socket_timeout': 30, 'retries': 3,
            'skip_download': True, 'writethumbnail': True,
            'outtmpl': str(DOWNLOADS_DIR / '%(id)s_thumb.%(ext)s'),
        }
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=False)
            title, vid = info.get('title', 'Unknown'), info.get('id', '')
            ydl.download([url])
            
            found = None
            for ext in ('.jpg', '.webp', '.png'):
                fp = DOWNLOADS_DIR / f'{vid}_thumb{ext}'
                if fp.exists(): found = str(fp); break
            if not found:
                for t in info.get('thumbnails', []):
                    if t.get('url'):
                        import urllib.request
                        ext = t['url'].split('?')[0].split('.')[-1] or 'jpg'
                        fp = DOWNLOADS_DIR / f'{vid}_thumb.{ext}'
                        urllib.request.urlretrieve(t['url'], str(fp))
                        found = str(fp); break
            if not found: raise FileNotFoundError("No thumbnail")
            return found, title, vid
    
    # --- Inline Query Handler ---
    async def _inline_query(self, u: Update, c: ContextTypes.DEFAULT_TYPE):
        query = u.inline_query.query.strip()
        if not query: return
        
        url = self._extract_url(query)
        if not url: return
        
        uid = u.effective_user.id
        if not self._ok(uid): return
        
        if not self._has_cookies(uid):
            await u.inline_query.answer([], switch_pm_text="Upload cookies first", switch_pm_parameter="cookies")
            return
        
        results = [
            InlineQueryResultArticle(
                id=str(uuid4()),
                title="🎬 Download Video (MP4)",
                description="Download video in MP4 format",
                input_message_content=InputTextMessageContent(f"{url} #video"),
            ),
            InlineQueryResultArticle(
                id=str(uuid4()),
                title="🎵 Download Audio (MP3/M4A)",
                description="Extract audio only",
                input_message_content=InputTextMessageContent(f"{url} #audio"),
            ),
            InlineQueryResultArticle(
                id=str(uuid4()),
                title="🖼️ Download Thumbnail",
                description="Get video thumbnail image",
                input_message_content=InputTextMessageContent(f"{url} #thumb"),
            ),
        ]
        await u.inline_query.answer(results, cache_time=0)
    
    # --- Commands ---
    async def start_cmd(self, u, c):
        if not self._ok(u.effective_user.id): return
        await u.message.reply_text(
            f"👋 Welcome {u.effective_user.first_name}!\n\n"
            f"🎥 YouTube Downloader Bot\n\n"
            f"💡 Send YouTube link → Choose format → Download!\n"
            f"📱 Inline mode: @botname <link> in any chat\n"
            f"🗑️ Files deleted after {self.config.STORAGE_DAYS}d.\n\n"
            f"🔒 Cookies: Content in RAM, file_id on disk.\n"
            f"Auto-restores after restart.",
            reply_markup=self._menu(u.effective_user.id))
    
    async def help_cmd(self, u, c):
        await u.message.reply_text(
            f"📚 Send YouTube link to download.\n"
            f"📱 Inline: @botname <link> in any chat\n"
            f"/cookies /recent\n\n"
            f"🔒 Cookies auto-restore after restart.",
            reply_markup=self._menu(u.effective_user.id))
    
    async def recent_cmd(self, u, c): await self._show_recent(u, c)
    
    async def cancel_cmd(self, u, c):
        await u.message.reply_text("❌ Cancelled.", reply_markup=self._menu(u.effective_user.id))
        return ConversationHandler.END
    
    async def on_msg(self, u, c):
        uid = u.effective_user.id
        if not self._ok(uid): return
        text = u.message.text
        
        media_type = None
        if text.endswith(' #video'):
            media_type = 'video'
            text = text[:-7]
        elif text.endswith(' #audio'):
            media_type = 'audio'
            text = text[:-7]
        elif text.endswith(' #thumb'):
            media_type = 'thumb'
            text = text[:-7]
        
        url = self._extract_url(text)
        if not url: return
        
        if not await self._ensure_cookies(uid):
            await u.message.reply_text("❌ Cookies expired or missing. /cookies")
            return
        
        video_id = self._extract_video_id(url)
        if not video_id:
            await u.message.reply_text("❌ Invalid URL."); return
        
        if media_type:
            existing = self._find_existing(uid, video_id, media_type)
            if existing:
                await self._show_delivery(u.message, existing, self.videos[uid].index(existing))
            else:
                task = asyncio.create_task(self._download_task(uid, url, u.message, media_type))
                if uid not in self._download_tasks:
                    self._download_tasks[uid] = []
                self._download_tasks[uid].append(task)
        else:
            await self._show_format_choice(uid, url, video_id, u.message)
    
    async def _show_format_choice(self, uid, url, video_id, msg):
        s = await msg.reply_text("🔍 Fetching info...")
        try:
            info = await asyncio.get_event_loop().run_in_executor(None, self._sync_fetch_info, uid, url)
            title, duration = info.get('title', '?'), info.get('duration', 0)
            self._pending_urls[uid] = (url, video_id, title)
            
            mins, secs = divmod(duration, 60) if duration else (0, 0)
            existing = self._get_existing_types(uid, video_id)
            dl = ""
            if existing:
                icons = {'video':'🎬','audio':'🎵','thumb':'🖼️'}
                dl = "\n✅ " + " ".join(icons[t] for t in existing)
            
            safe_title = self._esc(title[:200])
            await s.edit_text(
                f"📹 *{safe_title}*\n⏱ {mins}:{secs:02d}{dl}\n\nChoose format:",
                parse_mode=ParseMode.MARKDOWN, reply_markup=self._format_choice_keyboard(uid, video_id))
        except Exception as e:
            logger.error("Info %d: %s", uid, str(e)[:100])
            await s.edit_text("❌ Failed.", reply_markup=self._menu(uid))
    
    async def _choose_format(self, u, c):
        q = u.callback_query; await q.answer()
        uid, fmt = u.effective_user.id, q.data
        
        if uid not in self._pending_urls: return
        url, video_id, _ = self._pending_urls[uid]
        
        for media_type, fmt_key in [('video', 'fmt_video'), ('audio', 'fmt_audio'), ('thumb', 'fmt_thumb')]:
            if fmt == fmt_key:
                existing = self._find_existing(uid, video_id, media_type)
                if existing:
                    await q.answer("Already downloaded!")
                    await self._show_delivery(q.message, existing, self.videos[uid].index(existing))
                    return
                task = asyncio.create_task(self._download_task(uid, url, q.message, media_type))
                if uid not in self._download_tasks:
                    self._download_tasks[uid] = []
                self._download_tasks[uid].append(task)
    
    async def _download_task(self, uid, url, msg, media_type):
        s = await msg.reply_text(f"⏳ Downloading {media_type}...")
        try:
            if media_type == 'thumb':
                fp, title, vid = await asyncio.get_event_loop().run_in_executor(None, self._sync_download_thumb, uid, url)
            else:
                fp, title, vid = await asyncio.get_event_loop().run_in_executor(None, self._sync_download, uid, url, media_type)
            
            sz = Path(fp).stat().st_size
            record = VideoRecord(title, url, vid, fp, sz, datetime.now().strftime('%Y-%m-%d %H:%M:%S'), media_type=media_type)
            self.videos.setdefault(uid, []).insert(0, record)
            while len(self.videos[uid]) > 20:
                old = self.videos[uid].pop()
                Path(old.file_path).unlink(missing_ok=True)
            self._save()
            await s.delete()
            await self._show_delivery(msg, record, 0)
        except Exception as e:
            logger.error("Download %d: %s", uid, str(e)[:100])
            await s.edit_text("❌ Failed.", reply_markup=self._menu(uid))
        finally:
            if uid in self._download_tasks:
                self._download_tasks[uid] = [t for t in self._download_tasks[uid] if not t.done()]
                if not self._download_tasks[uid]:
                    del self._download_tasks[uid]
    
    async def _back_to_formats(self, u, c):
        q = u.callback_query; await q.answer()
        uid = u.effective_user.id
        data = q.data
        
        if data == 'backfmt_new':
            record = self.videos.get(uid, [None])[0]
        else:
            idx = int(data.split('_')[1])
            record = self.videos.get(uid, [None])[idx]
        
        if not record: return
        self._pending_urls[uid] = (record.url, record.video_id, record.title)
        await self._show_format_choice(uid, record.url, record.video_id, q.message)
        await q.message.delete()
    
    async def _show_delivery(self, msg, record, idx):
        emoji = {'video': '🎬', 'audio': '🎵', 'thumb': '🖼️'}.get(record.media_type, '📹')
        mb = record.file_size / 1024 / 1024
        safe_title = self._esc(record.title[:200])
        await msg.reply_text(
            f"{emoji} *{safe_title}*\n📦 {mb:.2f} MB | {record.media_type}\n🕒 {record.download_time}\n\nChoose delivery:",
            parse_mode=ParseMode.MARKDOWN, reply_markup=self._delivery_keyboard(msg.chat.id, idx))
    
    async def _send_telegram(self, u, c):
        q = u.callback_query; await q.answer()
        uid = u.effective_user.id
        data = q.data
        
        if data == 'tg_new':
            record = self.videos.get(uid, [None])[0]
        else:
            idx = int(data.split('_')[1])
            record = self.videos.get(uid, [None])[idx]
        
        if not record: return
        
        if record.telegram_file_id:
            try:
                if record.media_type == 'thumb':
                    await q.message.reply_photo(photo=record.telegram_file_id, caption=f"🖼️ {record.title}")
                elif record.media_type == 'audio':
                    await q.message.reply_audio(audio=record.telegram_file_id, title=record.title)
                else:
                    await q.message.reply_video(video=record.telegram_file_id, caption=f"🎬 {record.title}", supports_streaming=True)
                await q.message.delete(); return
            except:
                record.telegram_file_id = None; self._save()
        
        fp = record.file_path
        if not Path(fp).exists(): await q.message.reply_text("❌ File deleted."); return
        
        mb = Path(fp).stat().st_size / 1024 / 1024
        if mb > self.config.MAX_TELEGRAM_FILE_SIZE:
            await q.message.reply_text(f"⚠️ Too large ({mb:.1f}MB)."); return
        
        s = await q.message.reply_text("📤 Uploading...")
        try:
            with open(fp, 'rb') as f:
                if record.media_type == 'thumb':
                    sent = await q.message.reply_photo(photo=f, caption=f"🖼️ {record.title}")
                    record.telegram_file_id = sent.photo[-1].file_id
                elif record.media_type == 'audio':
                    sent = await q.message.reply_audio(audio=f, title=record.title, performer="YouTube")
                    record.telegram_file_id = sent.audio.file_id
                else:
                    sent = await q.message.reply_video(video=f, caption=f"🎬 {record.title}", supports_streaming=True)
                    record.telegram_file_id = sent.video.file_id
            self._save()
            await s.delete(); await q.message.delete()
        except Exception as e:
            logger.error("Upload %d: %s", uid, str(e)[:50])
            await s.edit_text("❌ Upload failed.")
    
    async def _send_link(self, u, c):
        q = u.callback_query; await q.answer()
        uid = u.effective_user.id
        data = q.data
        
        if data == 'lk_new':
            record = self.videos.get(uid, [None])[0]
        else:
            idx = int(data.split('_')[1])
            record = self.videos.get(uid, [None])[idx]
        
        if not record or not Path(record.file_path).exists(): return
        
        url = f"{self.base_url}/{quote(Path(record.file_path).name)}"
        safe_title = self._esc(record.title[:200])
        await q.message.reply_text(
            f"✅ *{safe_title}*\n\n📥 `{url}`\n\n⚠️ {self.config.STORAGE_DAYS}d retention.",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("📥 Download", url=url)], [InlineKeyboardButton("🔙 Menu", callback_data='b')]]))
        await q.message.delete()
    
    async def _show_recent(self, u, c, page=0):
        uid = u.effective_user.id
        msg = u.callback_query.message if u.callback_query else u.message
        videos = self.videos.get(uid, [])
        if not videos:
            await msg.reply_text("📭 No files.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Menu", callback_data='b')]])); return
        
        pp, tp = 5, max(1, (len(videos)+4)//5)
        page = max(0, min(page, tp-1))
        pv = videos[page*pp:(page+1)*pp]
        emoji_map = {'video': '🎬', 'audio': '🎵', 'thumb': '🖼️'}
        
        txt = f"📹 Downloads ({page+1}/{tp})\n\n"
        for i, v in enumerate(pv, page*pp+1):
            ex, em = "✅" if Path(v.file_path).exists() else "🗑️", emoji_map.get(v.media_type, '📹')
            safe_title = self._esc(v.title[:50])
            txt += f"{ex} {em} {i}. {safe_title}\n   📦 {v.file_size/1024/1024:.2f}MB | {v.download_time}\n\n"
        txt += f"⚠️ Deleted after {self.config.STORAGE_DAYS}d."
        
        kb = []
        for i, v in enumerate(pv, page*pp+1):
            if Path(v.file_path).exists():
                idx = page*pp + (i-page*pp-1)
                kb.append([InlineKeyboardButton(f"{emoji_map.get(v.media_type,'📹')} {i}. {v.title[:40]}", callback_data=f'sel_{idx}')])
        
        kb.append([InlineKeyboardButton("🗑️ Clear All Files", callback_data='clear_all')])
        
        nav = []
        if page > 0: nav.append(InlineKeyboardButton("⬅️", callback_data=f'p_{page-1}'))
        if page < tp-1: nav.append(InlineKeyboardButton("➡️", callback_data=f'p_{page+1}'))
        if nav: kb.append(nav)
        kb.append([InlineKeyboardButton("🔙 Menu", callback_data='b')])
        await msg.reply_text(txt, disable_web_page_preview=True, reply_markup=InlineKeyboardMarkup(kb))
    
    async def _clear_all(self, u, c):
        q = u.callback_query; await q.answer()
        uid = u.effective_user.id
        videos = self.videos.get(uid, [])
        count = len(videos)
        for v in videos:
            Path(v.file_path).unlink(missing_ok=True)
        self.videos.pop(uid, None)
        self._save()
        await q.message.reply_text(f"🗑️ {count} files cleared.", reply_markup=self._menu(uid))
    
    async def _select_video(self, u, c):
        q = u.callback_query; await q.answer()
        uid, idx = u.effective_user.id, int(q.data.split('_')[1])
        videos = self.videos.get(uid, [])
        if 0 <= idx < len(videos):
            await self._show_delivery(q.message, videos[idx], idx)
            await q.message.delete()
    
    async def _delete_video(self, u, c):
        q = u.callback_query; await q.answer()
        uid, idx = u.effective_user.id, int(q.data.split('_')[1])
        videos = self.videos.get(uid, [])
        if 0 <= idx < len(videos):
            Path(videos[idx].file_path).unlink(missing_ok=True)
            videos.pop(idx); self._save()
            await q.message.reply_text("🗑️ Deleted.", reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("📹 Videos", callback_data='r'), InlineKeyboardButton("🔙 Menu", callback_data='b')]]))
    
    async def _ask_cookies(self, u, c):
        if not self._ok(u.effective_user.id): return ConversationHandler.END
        msg = u.callback_query.message if u.callback_query else u.message
        await msg.reply_text(
            f"🔒 Cookie Info\n\n"
            f"• Cookie content: RAM only\n"
            f"• File ID saved: auto-restore after restart\n"
            f"• Telegram stores the file\n"
            f"• TTL: {self.config.COOKIE_TTL_HOURS}h in RAM\n\n"
            f"📤 Send your cookies.txt file:",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Cancel", callback_data='b')]]))
        return WAITING_FOR_COOKIES
    
    async def _recv_cookies(self, u, c):
        uid = u.effective_user.id
        if not self._ok(uid): return ConversationHandler.END
        if not u.message.document:
            await u.message.reply_text("❌ Send .txt file.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Cancel", callback_data='b')]]))
            return WAITING_FOR_COOKIES
        try:
            doc = u.message.document
            f = await c.bot.get_file(doc.file_id)
            cookie_bytes = await f.download_as_bytearray()
            self._cookie_data[uid] = bytes(cookie_bytes)
            self._cookie_last_used[uid] = time.time()
            self._cookie_file_ids[uid] = doc.file_id
            self._save()
            
            if uid in self._cookie_tmpfiles:
                try: os.unlink(self._cookie_tmpfiles[uid])
                except: pass
                del self._cookie_tmpfiles[uid]
            
            logger.info("User %d cookies saved", uid)
            await u.message.reply_text(
                f"✅ Cookies saved!\n\n"
                f"🔒 Content in RAM, auto-restore enabled.\n"
                f"⏱ TTL: {self.config.COOKIE_TTL_HOURS}h\n"
                f"Survives bot restarts.",
                reply_markup=self._menu(uid))
            return ConversationHandler.END
        except Exception as e:
            logger.error("Cookie %d: %s", uid, e)
            await u.message.reply_text("❌ Failed.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Cancel", callback_data='b')]]))
            return WAITING_FOR_COOKIES
    
    async def _router(self, u, c):
        q = u.callback_query; await q.answer()
        d, uid = q.data, u.effective_user.id
        
        routes = {
            'b': lambda: q.message.reply_text("📋 Menu:", reply_markup=self._menu(uid)),
            'r': lambda: self._show_recent(u, c),
            'c': lambda: self._ask_cookies(u, c),
            'cs': lambda: q.message.reply_text(
                f"✅ Cookies active (TTL: {self.config.COOKIE_TTL_HOURS}h)" if self._has_cookies(uid) 
                else "❌ Upload with /cookies"),
            'vc': lambda: q.message.reply_text(f"📦 {len(self.videos.get(uid,[]))} files"),
            'clear_all': lambda: self._clear_all(u, c),
        }
        
        if d in routes: await routes[d]()
        elif d.startswith('fmt_'): await self._choose_format(u, c)
        elif d.startswith('backfmt_'): await self._back_to_formats(u, c)
        elif d.startswith('tg_'): await self._send_telegram(u, c)
        elif d.startswith('lk_'): await self._send_link(u, c)
        elif d.startswith('sel_'): await self._select_video(u, c)
        elif d.startswith('d_'): await self._delete_video(u, c)
        elif d.startswith('p_'): await self._show_recent(u, c, int(d.split('_')[1]))
    
    async def _start_file_server(self):
        await self.file_server.start()
    
    def run(self):
        app = Application.builder().token(self.config.BOT_TOKEN).build()
        self._bot = app.bot
        
        app.add_handler(CommandHandler('start', self.start_cmd))
        app.add_handler(CommandHandler('help', self.help_cmd))
        app.add_handler(CommandHandler('recent', self.recent_cmd))
        app.add_handler(ConversationHandler(
            entry_points=[CommandHandler('cookies', self._ask_cookies), CallbackQueryHandler(self._ask_cookies, pattern='^c$')],
            states={WAITING_FOR_COOKIES: [
                MessageHandler(filters.Document.FileExtension("txt"), self._recv_cookies),
                MessageHandler(filters.TEXT & ~filters.COMMAND, self._ask_cookies)]},
            fallbacks=[CommandHandler('cancel', self.cancel_cmd), CallbackQueryHandler(self._router, pattern='^b$')],
            per_message=False))
        app.add_handler(CallbackQueryHandler(self._router))
        app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, self.on_msg))
        app.add_handler(InlineQueryHandler(self._inline_query))
        
        loop = asyncio.get_event_loop()
        loop.create_task(self._start_file_server())
        
        logger.info("Bot starting (inline mode, cookie TTL %dh)...", self.config.COOKIE_TTL_HOURS)
        app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == '__main__':
    YouTubeDownloaderBot().run()