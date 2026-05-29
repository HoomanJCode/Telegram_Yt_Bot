#!/usr/bin/env python3
"""
YouTube Downloader Telegram Bot with built-in file server
"""

import os
import sys
import logging
import json
import time
import shutil
import re
import threading
import socket
import subprocess
import asyncio
import concurrent.futures
from pathlib import Path
from datetime import datetime, timedelta
from typing import Dict, Optional, List
from urllib.parse import quote, unquote
from http.server import HTTPServer, SimpleHTTPRequestHandler

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, MessageHandler, CallbackQueryHandler,
    ConversationHandler, filters, ContextTypes
)
from telegram.constants import ParseMode
import yt_dlp
from yt_dlp.utils import DownloadError

from config import Config

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logging.basicConfig(format='%(asctime)s - %(levelname)s - %(message)s', level=logging.WARNING)
for lib in ('httpx', 'httpcore', 'telegram', 'telegram.ext'):
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
COOKIES_DIR = DATA_DIR / 'cookies'
DOWNLOADS_DIR = Path('downloads')
WAITING_FOR_COOKIES = 1
YOUTUBE_RE = re.compile(r'(https?://)?(www\.)?(youtube\.com|youtu\.be)/\S+')

# Thread pool for downloads
DOWNLOAD_EXECUTOR = concurrent.futures.ThreadPoolExecutor(max_workers=2)

# ---------------------------------------------------------------------------
# HTTP File Server
# ---------------------------------------------------------------------------
class FileServerHandler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(DOWNLOADS_DIR), **kwargs)
    
    def handle(self):
        try: super().handle()
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError): pass
        except Exception as e: logger.error("HTTP: %s", str(e)[:100])
    
    def handle_one_request(self):
        try: super().handle_one_request()
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError): pass
    
    def copyfile(self, source, outputfile):
        try: super().copyfile(source, outputfile)
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError): pass
    
    def log_message(self, format, *args):
        if args and len(args) > 1 and '200' in str(args[1]):
            logger.info("File: %s", args[0])

class FileServer:
    def __init__(self, port=8000):
        self.port = port
    
    def start(self):
        try:
            s = HTTPServer(('0.0.0.0', self.port), FileServerHandler)
            s.socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            threading.Thread(target=s.serve_forever, daemon=True).start()
            logger.info("File server on port %d", self.port)
        except Exception as e:
            logger.error("File server: %s", e)

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
        
        for d in (DATA_DIR, COOKIES_DIR, DOWNLOADS_DIR):
            d.mkdir(parents=True, exist_ok=True)
        
        self.cookies: Dict[int, Path] = {}
        self.videos: Dict[int, List[VideoRecord]] = {}
        self._pending_urls: Dict[int, tuple] = {}
        
        # Check FFmpeg
        self.has_ffmpeg = self._check_ffmpeg()
        logger.info("FFmpeg: %s", "available" if self.has_ffmpeg else "NOT FOUND")
        
        self._load()
        self._start_cleanup()
        FileServer(port=port).start()
    
    def _check_ffmpeg(self):
        try:
            subprocess.run(['ffmpeg', '-version'], capture_output=True, timeout=5)
            subprocess.run(['ffprobe', '-version'], capture_output=True, timeout=5)
            return True
        except:
            return False
    
    def _load(self):
        for name, fn, attr in [
            ('cookies', 'user_cookies.json', self.cookies),
            ('videos', 'user_videos.json', self.videos),
        ]:
            try:
                fp = DATA_DIR / fn
                if fp.exists():
                    data = json.loads(fp.read_text())
                    if name == 'videos':
                        attr.update({int(k): [VideoRecord.from_dict(v) for v in vs] for k, vs in data.items()})
                    else:
                        attr.update({int(k): v for k, v in data.items()})
                    logger.info("Loaded %s: %d", name, len(attr))
            except Exception as e:
                logger.error("Load %s: %s", name, e)
    
    def _save(self):
        data = {
            DATA_DIR / 'user_cookies.json': {str(k): str(v) for k, v in self.cookies.items()},
            DATA_DIR / 'user_videos.json': {str(k): [v.to_dict() for v in vs] for k, vs in self.videos.items()},
        }
        for fp, d in data.items():
            try: fp.write_text(json.dumps(d, indent=2))
            except Exception as e: logger.error("Save %s: %s", fp.name, e)
    
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
        self._save()
    
    def _cookie_path(self, uid): return COOKIES_DIR / f'{uid}.txt'
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
        types = set()
        for v in self.videos.get(uid, []):
            if v.video_id == video_id and Path(v.file_path).exists():
                types.add(v.media_type)
        return types
    
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
        has = uid in self.cookies
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
        if 'video' in existing: v_label = "✅ 🎬 Video (MP4) - Downloaded"
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
    
    # --- Sync download (runs in thread pool) ---
    def _sync_fetch_info(self, uid, url):
        """Fetch video info synchronously"""
        opts = {
            'format': 'best',
            'cookiefile': str(self.cookies[uid]),
            'quiet': True, 'no_warnings': True,
            'socket_timeout': 30, 'retries': 3,
        }
        with yt_dlp.YoutubeDL(opts) as ydl:
            return ydl.extract_info(url, download=False)
    
    def _sync_download(self, uid, url, media_type, progress_callback=None):
        """Download video synchronously"""
        if media_type == 'video':
            fmt = 'best[ext=mp4]/best'
            tmpl = str(DOWNLOADS_DIR / '%(id)s_v.%(ext)s')
            opts = {
                'format': fmt, 'outtmpl': tmpl,
                'cookiefile': str(self.cookies[uid]),
                'quiet': True, 'no_warnings': True,
                'socket_timeout': 120, 'retries': 50, 'fragment_retries': 50,
                'http_chunk_size': 5*1024*1024, 'throttled_rate': '100K',
                'no_mtime': True, 'merge_output_format': 'mp4',
            }
        else:
            if self.has_ffmpeg:
                fmt = 'bestaudio[ext=m4a]/bestaudio'
                tmpl = str(DOWNLOADS_DIR / '%(id)s_a.%(ext)s')
                opts = {
                    'format': fmt, 'outtmpl': tmpl,
                    'cookiefile': str(self.cookies[uid]),
                    'quiet': True, 'no_warnings': True,
                    'socket_timeout': 120, 'retries': 50, 'fragment_retries': 50,
                    'http_chunk_size': 5*1024*1024, 'throttled_rate': '100K',
                    'no_mtime': True,
                    'postprocessors': [{'key': 'FFmpegExtractAudio', 'preferredcodec': 'mp3', 'preferredquality': '192'}],
                }
            else:
                fmt = 'bestaudio[ext=m4a]/bestaudio'
                tmpl = str(DOWNLOADS_DIR / '%(id)s_a.%(ext)s')
                opts = {
                    'format': fmt, 'outtmpl': tmpl,
                    'cookiefile': str(self.cookies[uid]),
                    'quiet': True, 'no_warnings': True,
                    'socket_timeout': 120, 'retries': 50, 'fragment_retries': 50,
                    'http_chunk_size': 5*1024*1024, 'throttled_rate': '100K',
                    'no_mtime': True,
                }
        
        if progress_callback:
            opts['progress_hooks'] = [progress_callback]
        
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=True)
            title = info.get('title', 'Unknown')
            vid = info.get('id', '')
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
                    if f.is_file() and f.stem.startswith(vid):
                        found = str(f); break
            
            if not found:
                raise FileNotFoundError(title)
            
            return found, title, vid
    
    def _sync_download_thumb(self, uid, url):
        """Download thumbnail synchronously"""
        opts = {
            'cookiefile': str(self.cookies[uid]),
            'quiet': True, 'no_warnings': True,
            'socket_timeout': 30, 'retries': 3,
            'skip_download': True,
            'writethumbnail': True,
            'outtmpl': str(DOWNLOADS_DIR / '%(id)s_thumb.%(ext)s'),
        }
        
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=False)
            title = info.get('title', 'Unknown')
            vid = info.get('id', '')
            ydl.download([url])
            
            found = None
            for ext in ('.jpg', '.webp', '.png'):
                fp = DOWNLOADS_DIR / f'{vid}_thumb{ext}'
                if fp.exists(): found = str(fp); break
            
            if not found:
                thumb_url = None
                for t in info.get('thumbnails', []):
                    if t.get('preference', 0) >= 0:
                        thumb_url = t.get('url')
                if thumb_url:
                    import urllib.request
                    ext = thumb_url.split('?')[0].split('.')[-1] or 'jpg'
                    fp = DOWNLOADS_DIR / f'{vid}_thumb.{ext}'
                    urllib.request.urlretrieve(thumb_url, str(fp))
                    found = str(fp)
            
            if not found:
                raise FileNotFoundError("No thumbnail found")
            
            return found, title, vid
    
    # --- Commands ---
    async def start(self, u, c):
        if not self._ok(u.effective_user.id): await u.message.reply_text("⛔"); return
        await u.message.reply_text(
            f"👋 Welcome {u.effective_user.first_name}!\n\n"
            "🎥 *YouTube Downloader Bot*\n\n"
            "/start /cookies /recent /help\n\n"
            "💡 Send YouTube link to download!\n"
            f"🎬 Video | 🎵 Audio | 🖼️ Thumbnails\n"
            f"🗑️ Files deleted after {self.config.STORAGE_DAYS}d.",
            parse_mode=ParseMode.MARKDOWN, reply_markup=self._menu(u.effective_user.id))
    
    async def help(self, u, c):
        await u.message.reply_text(
            "📚 *Help*\n\n"
            "Send YouTube link → Choose format → Download!\n"
            f"🎬 Video (MP4) | 🎵 Audio ({'MP3' if self.has_ffmpeg else 'M4A'}) | 🖼️ Thumbnails\n\n"
            "/cookies - Upload cookies\n"
            "/recent - View downloads",
            parse_mode=ParseMode.MARKDOWN, reply_markup=self._menu(u.effective_user.id))
    
    async def recent(self, u, c): await self._show_recent(u, c)
    
    async def cancel(self, u, c):
        await u.message.reply_text("❌ Cancelled.", reply_markup=self._menu(u.effective_user.id))
        return ConversationHandler.END
    
    # --- Message Handler ---
    async def on_msg(self, u, c):
        uid = u.effective_user.id
        if not self._ok(uid): return
        url = self._extract_url(u.message.text)
        if not url: return
        if uid not in self.cookies:
            await u.message.reply_text("❌ Upload cookies first! /cookies",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🍪 Upload", callback_data='c')]]))
            return
        
        video_id = self._extract_video_id(url)
        if not video_id:
            await u.message.reply_text("❌ Invalid YouTube URL.")
            return
        
        await self._show_format_choice(uid, url, video_id, u.message)
    
    async def _show_format_choice(self, uid, url, video_id, msg):
        s = await msg.reply_text("🔍 Fetching video info...")
        try:
            loop = asyncio.get_event_loop()
            info = await loop.run_in_executor(DOWNLOAD_EXECUTOR, self._sync_fetch_info, uid, url)
            
            title = info.get('title', 'Unknown')
            duration = info.get('duration', 0)
            
            self._pending_urls[uid] = (url, video_id, title)
            
            mins = duration // 60
            secs = duration % 60
            duration_str = f"{mins}:{secs:02d}" if duration else "?"
            
            existing = self._get_existing_types(uid, video_id)
            downloaded_info = ""
            if existing:
                type_names = {'video': '🎬', 'audio': '🎵', 'thumb': '🖼️'}
                downloaded_info = "\n✅ Downloaded: " + " ".join(type_names[t] for t in existing)
            
            await s.edit_text(
                f"📹 *{self._esc(title[:200])}*\n"
                f"⏱ Duration: {duration_str}{downloaded_info}\n\n"
                "Choose format to download:",
                parse_mode=ParseMode.MARKDOWN,
                reply_markup=self._format_choice_keyboard(uid, video_id))
        except Exception as e:
            logger.error("Info fetch %d: %s", uid, str(e)[:100])
            await s.edit_text("❌ Failed to get video info.", reply_markup=self._menu(uid))
    
    async def _choose_format(self, u, c):
        q = u.callback_query; await q.answer()
        uid = u.effective_user.id
        fmt = q.data
        
        if uid not in self._pending_urls:
            await q.message.reply_text("❌ Session expired. Send link again.")
            return
        
        url, video_id, title = self._pending_urls[uid]
        
        if fmt == 'fmt_video':
            existing = self._find_existing(uid, video_id, 'video')
            if existing:
                await q.answer("Already downloaded!")
                await self._show_delivery(q.message, existing, self.videos[uid].index(existing))
                return
            await self._start_download(uid, url, q.message, 'video', video_id)
            
        elif fmt == 'fmt_audio':
            existing = self._find_existing(uid, video_id, 'audio')
            if existing:
                await q.answer("Already downloaded!")
                await self._show_delivery(q.message, existing, self.videos[uid].index(existing))
                return
            await self._start_download(uid, url, q.message, 'audio', video_id)
            
        elif fmt == 'fmt_thumb':
            existing = self._find_existing(uid, video_id, 'thumb')
            if existing:
                await q.answer("Already downloaded!")
                await self._show_delivery(q.message, existing, self.videos[uid].index(existing))
                return
            await self._start_thumb_download(uid, url, q.message, video_id)
    
    async def _start_download(self, uid, url, msg, media_type, video_id):
        s = await msg.reply_text(f"⏳ Downloading {media_type}...")
        try:
            loop = asyncio.get_event_loop()
            fp, title, vid = await loop.run_in_executor(
                DOWNLOAD_EXECUTOR, self._sync_download, uid, url, media_type, None
            )
            
            sz = Path(fp).stat().st_size
            record = VideoRecord(title, url, vid, fp, sz,
                                datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                                media_type=media_type)
            self.videos.setdefault(uid, []).insert(0, record)
            while len(self.videos[uid]) > 20:
                old = self.videos[uid].pop()
                Path(old.file_path).unlink(missing_ok=True)
            self._save()
            await s.delete()
            await self._show_delivery(msg, record, 0)
        except Exception as e:
            logger.error("Download fail %d: %s", uid, str(e)[:100])
            await s.edit_text("❌ Download failed.", reply_markup=self._menu(uid))
    
    async def _start_thumb_download(self, uid, url, msg, video_id):
        s = await msg.reply_text("🖼️ Downloading thumbnail...")
        try:
            loop = asyncio.get_event_loop()
            fp, title, vid = await loop.run_in_executor(
                DOWNLOAD_EXECUTOR, self._sync_download_thumb, uid, url
            )
            
            sz = Path(fp).stat().st_size
            record = VideoRecord(title, url, vid, fp, sz,
                                datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                                media_type='thumb')
            self.videos.setdefault(uid, []).insert(0, record)
            while len(self.videos[uid]) > 20:
                old = self.videos[uid].pop()
                Path(old.file_path).unlink(missing_ok=True)
            self._save()
            await s.delete()
            await self._show_delivery(msg, record, 0)
        except Exception as e:
            logger.error("Thumb %d: %s", uid, str(e)[:100])
            await s.edit_text("❌ Failed to get thumbnails.", reply_markup=self._menu(uid))
    
    async def _back_to_formats(self, u, c):
        q = u.callback_query; await q.answer()
        uid = u.effective_user.id
        data = q.data
        
        if data == 'backfmt_new':
            record = self.videos.get(uid, [None])[0]
        else:
            idx = int(data.split('_')[1])
            record = self.videos.get(uid, [None])[idx]
        
        if not record:
            await q.message.reply_text("❌ Not found."); return
        
        self._pending_urls[uid] = (record.url, record.video_id, record.title)
        await self._show_format_choice(uid, record.url, record.video_id, q.message)
        await q.message.delete()
    
    async def _show_delivery(self, msg, record, idx):
        type_emoji = {'video': '🎬', 'audio': '🎵', 'thumb': '🖼️'}
        emoji = type_emoji.get(record.media_type, '📹')
        mb = record.file_size / 1024 / 1024
        txt = (
            f"{emoji} *{self._esc(record.title[:200])}*\n"
            f"📦 {mb:.2f} MB | {record.media_type}\n"
            f"🕒 {record.download_time}\n\n"
            "Choose how to receive\nor go back to download other formats:"
        )
        await msg.reply_text(
            txt,
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=self._delivery_keyboard(msg.chat.id, idx))
    
    async def _send_telegram(self, u, c):
        q = u.callback_query; await q.answer()
        uid = u.effective_user.id
        data = q.data
        
        if data == 'tg_new':
            record = self.videos.get(uid, [None])[0]
        else:
            idx = int(data.split('_')[1])
            record = self.videos.get(uid, [None])[idx]
        
        if not record:
            await q.message.reply_text("❌ Not found."); return
        
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
        if not Path(fp).exists():
            await q.message.reply_text("❌ File deleted."); return
        
        mb = Path(fp).stat().st_size / 1024 / 1024
        if mb > self.config.MAX_TELEGRAM_FILE_SIZE:
            await q.message.reply_text(f"⚠️ Too large ({mb:.1f}MB). Use link."); return
        
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
            await s.edit_text("❌ Upload failed. Use link.")
    
    async def _send_link(self, u, c):
        q = u.callback_query; await q.answer()
        uid = u.effective_user.id
        data = q.data
        
        if data == 'lk_new':
            record = self.videos.get(uid, [None])[0]
        else:
            idx = int(data.split('_')[1])
            record = self.videos.get(uid, [None])[idx]
        
        if not record or not Path(record.file_path).exists():
            await q.message.reply_text("❌ Not found."); return
        
        url = f"{self.base_url}/{quote(Path(record.file_path).name)}"
        await q.message.reply_text(
            f"📥 `{url}`\n\n⚠️ Deleted after {self.config.STORAGE_DAYS}d.",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("📥 Download", url=url)],
                [InlineKeyboardButton("🔙 Menu", callback_data='b')]]))
        await q.message.delete()
    
    async def _show_recent(self, u, c, page=0):
        uid = u.effective_user.id
        msg = u.callback_query.message if u.callback_query else u.message
        videos = self.videos.get(uid, [])
        if not videos:
            await msg.reply_text("📭 No files.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Menu", callback_data='b')]]))
            return
        pp = 5; tp = max(1, (len(videos)+pp-1)//pp); page = max(0, min(page, tp-1))
        pv = videos[page*pp:(page+1)*pp]
        type_emoji = {'video': '🎬', 'audio': '🎵', 'thumb': '🖼️'}
        txt = f"📹 *Downloads* ({page+1}/{tp})\n\n"
        for i, v in enumerate(pv, page*pp+1):
            ex = "✅" if Path(v.file_path).exists() else "🗑️"
            emoji = type_emoji.get(v.media_type, '📹')
            txt += f"{ex} {emoji} *{i}.* {self._esc(v.title[:50])}\n   📦 {v.file_size/1024/1024:.2f}MB | {v.download_time}\n\n"
        kb = []
        for i, v in enumerate(pv, page*pp+1):
            if Path(v.file_path).exists():
                idx = page*pp + (i-page*pp-1)
                emoji = type_emoji.get(v.media_type, '📹')
                kb.append([InlineKeyboardButton(f"{emoji} {i}. {v.title[:40]}", callback_data=f'sel_{idx}')])
        nav = []
        if page > 0: nav.append(InlineKeyboardButton("⬅️", callback_data=f'p_{page-1}'))
        if page < tp-1: nav.append(InlineKeyboardButton("➡️", callback_data=f'p_{page+1}'))
        if nav: kb.append(nav)
        kb.append([InlineKeyboardButton("🔙 Menu", callback_data='b')])
        await msg.reply_text(txt, parse_mode=ParseMode.MARKDOWN, disable_web_page_preview=True, reply_markup=InlineKeyboardMarkup(kb))
    
    async def _select_video(self, u, c):
        q = u.callback_query; await q.answer()
        uid = u.effective_user.id
        idx = int(q.data.split('_')[1])
        videos = self.videos.get(uid, [])
        if 0 <= idx < len(videos):
            await self._show_delivery(q.message, videos[idx], idx)
            await q.message.delete()
    
    async def _del_video(self, u, c):
        q = u.callback_query; await q.answer()
        uid = u.effective_user.id; idx = int(q.data.split('_')[1])
        videos = self.videos.get(uid, [])
        if 0 <= idx < len(videos):
            Path(videos[idx].file_path).unlink(missing_ok=True)
            videos.pop(idx); self._save()
            await q.message.reply_text("🗑️ Deleted.", reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("📹 Videos", callback_data='r'),
                InlineKeyboardButton("🔙 Menu", callback_data='b')]]))
    
    async def _ask_cookies(self, u, c):
        if not self._ok(u.effective_user.id):
            msg = u.callback_query.message if u.callback_query else u.message
            await msg.reply_text("⛔"); return ConversationHandler.END
        msg = u.callback_query.message if u.callback_query else u.message
        await msg.reply_text("⚠️ *Cookie Warning*\n\nSend cookies.txt file.",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Cancel", callback_data='b')]]))
        return WAITING_FOR_COOKIES
    
    async def _recv_cookies(self, u, c):
        uid = u.effective_user.id
        if not self._ok(uid): await u.message.reply_text("⛔"); return ConversationHandler.END
        if not u.message.document:
            await u.message.reply_text("❌ Send .txt file.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Cancel", callback_data='b')]]))
            return WAITING_FOR_COOKIES
        try:
            f = await c.bot.get_file(u.message.document.file_id)
            await f.download_to_drive(str(self._cookie_path(uid)))
            self.cookies[uid] = self._cookie_path(uid); self._save()
            logger.info("User %d cookies", uid)
            await u.message.reply_text("✅ Cookies saved!", reply_markup=self._menu(uid))
            return ConversationHandler.END
        except Exception as e:
            logger.error("Cookie %d: %s", uid, e)
            await u.message.reply_text("❌ Failed.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Cancel", callback_data='b')]]))
            return WAITING_FOR_COOKIES
    
    async def _router(self, u, c):
        q = u.callback_query; await q.answer()
        d = q.data; uid = u.effective_user.id
        
        if d == 'b': await q.message.reply_text("📋 Menu:", reply_markup=self._menu(uid))
        elif d == 'r': await self._show_recent(u, c)
        elif d == 'c': await self._ask_cookies(u, c)
        elif d == 'cs': await q.message.reply_text("✅ Ready!" if uid in self.cookies else "❌ Use /cookies")
        elif d == 'vc': await q.message.reply_text(f"📦 {len(self.videos.get(uid,[]))} files")
        elif d.startswith('fmt_'): await self._choose_format(u, c)
        elif d.startswith('backfmt_'): await self._back_to_formats(u, c)
        elif d.startswith('tg_'): await self._send_telegram(u, c)
        elif d.startswith('lk_'): await self._send_link(u, c)
        elif d.startswith('sel_'): await self._select_video(u, c)
        elif d.startswith('d_'): await self._del_video(u, c)
        elif d.startswith('p_'): await self._show_recent(u, c, int(d.split('_')[1]))
    
    def run(self):
        app = Application.builder().token(self.config.BOT_TOKEN).build()
        app.add_handler(CommandHandler('start', self.start))
        app.add_handler(CommandHandler('help', self.help))
        app.add_handler(CommandHandler('recent', self.recent))
        app.add_handler(ConversationHandler(
            entry_points=[CommandHandler('cookies', self._ask_cookies), CallbackQueryHandler(self._ask_cookies, pattern='^c$')],
            states={WAITING_FOR_COOKIES: [
                MessageHandler(filters.Document.FileExtension("txt"), self._recv_cookies),
                MessageHandler(filters.TEXT & ~filters.COMMAND, self._ask_cookies)]},
            fallbacks=[CommandHandler('cancel', self.cancel), CallbackQueryHandler(self._router, pattern='^b$')],
            per_message=False))
        app.add_handler(CallbackQueryHandler(self._router))
        app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, self.on_msg))
        logger.info("Bot starting...")
        app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == '__main__':
    YouTubeDownloaderBot().run()