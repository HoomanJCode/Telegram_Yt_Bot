#!/usr/bin/env python3
"""
YouTube Downloader Telegram Bot
Automatically detects YouTube links, downloads videos, and manages file storage.
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
from pathlib import Path
from datetime import datetime, timedelta
from typing import Dict, Optional, List
from urllib.parse import quote

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ConversationHandler,
    filters,
    ContextTypes
)
from telegram.constants import ParseMode
import yt_dlp
from yt_dlp.utils import DownloadError

from config import Config

# ---------------------------------------------------------------------------
# Logging Configuration
# ---------------------------------------------------------------------------
logging.basicConfig(
    format='%(asctime)s - %(levelname)s - %(message)s',
    level=logging.WARNING
)

# Silence noisy third-party loggers
for lib in ('httpx', 'httpcore', 'telegram', 'telegram.ext'):
    logging.getLogger(lib).setLevel(logging.WARNING)

# Application logger
logger = logging.getLogger('yt_bot')
logger.setLevel(logging.INFO)
handler = logging.StreamHandler()
handler.setFormatter(logging.Formatter('%(asctime)s - %(levelname)s - %(message)s'))
logger.addHandler(handler)
logger.propagate = False

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
DATA_DIR = Path('data')
COOKIES_DIR = DATA_DIR / 'cookies'
DOWNLOADS_DIR = Path('downloads')

WAITING_FOR_COOKIES = 1

YOUTUBE_URL_PATTERN = re.compile(
    r'(https?://)?(www\.)?(youtube\.com|youtu\.be)/\S+'
)

# ---------------------------------------------------------------------------
# Data Models
# ---------------------------------------------------------------------------
class VideoRecord:
    """Store video download information"""
    __slots__ = ('title', 'url', 'file_path', 'file_size', 'download_time')
    
    def __init__(self, title: str, url: str, file_path: str, file_size: int, download_time: str):
        self.title = title
        self.url = url
        self.file_path = file_path
        self.file_size = file_size
        self.download_time = download_time
    
    def to_dict(self) -> dict:
        return {
            'title': self.title,
            'url': self.url,
            'file_path': self.file_path,
            'file_size': self.file_size,
            'download_time': self.download_time
        }
    
    @classmethod
    def from_dict(cls, data: dict) -> 'VideoRecord':
        return cls(
            data['title'], data['url'], data['file_path'],
            data['file_size'], data['download_time']
        )

# ---------------------------------------------------------------------------
# Main Bot Class
# ---------------------------------------------------------------------------
class YouTubeDownloaderBot:
    def __init__(self):
        self.config = Config()
        self.base_download_link = self.config.BASE_DOWNLOAD_LINK.rstrip('/')
        
        self._setup_directories()
        self._update_ytdlp()
        
        self.user_cookies: Dict[int, Path] = {}
        self.user_settings: Dict[int, dict] = {}
        self.user_videos: Dict[int, List[VideoRecord]] = {}
        self._last_progress_update = 0
        
        self._load_data()
        self._start_cleanup_thread()
    
    # -----------------------------------------------------------------------
    # Setup Methods
    # -----------------------------------------------------------------------
    def _setup_directories(self) -> None:
        """Create required directory structure"""
        for directory in (DATA_DIR, COOKIES_DIR, DOWNLOADS_DIR):
            directory.mkdir(parents=True, exist_ok=True)
    
    def _update_ytdlp(self) -> None:
        """Update yt-dlp to latest version"""
        try:
            result = subprocess.run(
                [sys.executable, '-m', 'pip', 'install', '--upgrade', 'yt-dlp'],
                capture_output=True, text=True, timeout=30
            )
            if result.returncode == 0:
                logger.info("yt-dlp updated successfully")
            else:
                logger.warning("yt-dlp update may have failed")
        except Exception as e:
            logger.warning("Could not update yt-dlp: %s", e)
    
    # -----------------------------------------------------------------------
    # Data Persistence
    # -----------------------------------------------------------------------
    def _load_data(self) -> None:
        """Load user data from JSON files"""
        files = {
            'cookies': (DATA_DIR / 'user_cookies.json', self.user_cookies),
            'settings': (DATA_DIR / 'user_settings.json', self.user_settings),
            'videos': (DATA_DIR / 'user_videos.json', self.user_videos),
        }
        
        for name, (filepath, storage) in files.items():
            try:
                if filepath.exists():
                    with open(filepath, 'r') as f:
                        data = json.load(f)
                    if name == 'videos':
                        storage.update({
                            int(uid): [VideoRecord.from_dict(v) for v in videos]
                            for uid, videos in data.items()
                        })
                    else:
                        storage.update({int(k): v for k, v in data.items()})
                    logger.info("Loaded %s data for %d users", name, len(storage))
            except Exception as e:
                logger.error("Error loading %s: %s", name, e)
    
    def _save_data(self) -> None:
        """Save user data to JSON files"""
        files = {
            DATA_DIR / 'user_cookies.json': {str(k): str(v) for k, v in self.user_cookies.items()},
            DATA_DIR / 'user_settings.json': {str(k): v for k, v in self.user_settings.items()},
            DATA_DIR / 'user_videos.json': {
                str(uid): [v.to_dict() for v in videos]
                for uid, videos in self.user_videos.items()
            },
        }
        
        for filepath, data in files.items():
            try:
                with open(filepath, 'w') as f:
                    json.dump(data, f, indent=2)
            except Exception as e:
                logger.error("Error saving %s: %s", filepath.name, e)
    
    # -----------------------------------------------------------------------
    # File Management
    # -----------------------------------------------------------------------
    def _start_cleanup_thread(self) -> None:
        """Start background thread for file cleanup"""
        def worker():
            while True:
                try:
                    self._cleanup_old_files()
                except Exception as e:
                    logger.error("Cleanup error: %s", e)
                time.sleep(3600)
        
        thread = threading.Thread(target=worker, daemon=True)
        thread.start()
        logger.info("File cleanup thread started")
    
    def _cleanup_old_files(self) -> None:
        """Remove files older than configured retention period"""
        cutoff = datetime.now() - timedelta(days=self.config.STORAGE_DAYS)
        cleaned = 0
        
        for filepath in DOWNLOADS_DIR.iterdir():
            if filepath.is_file():
                mtime = datetime.fromtimestamp(filepath.stat().st_mtime)
                if mtime < cutoff:
                    filepath.unlink()
                    cleaned += 1
        
        if cleaned:
            logger.info("Cleaned up %d expired files", cleaned)
        
        # Update video records
        for user_id in list(self.user_videos):
            self.user_videos[user_id] = [
                v for v in self.user_videos[user_id]
                if Path(v.file_path).exists()
            ]
            if not self.user_videos[user_id]:
                del self.user_videos[user_id]
        
        self._save_data()
    
    def _get_cookie_path(self, user_id: int) -> Path:
        """Get standardized cookie file path for user"""
        return COOKIES_DIR / f'{user_id}.txt'
    
    def _add_video_record(self, user_id: int, title: str, url: str, 
                          file_path: str, file_size: int) -> None:
        """Add video to user's download history"""
        if user_id not in self.user_videos:
            self.user_videos[user_id] = []
        
        record = VideoRecord(
            title=title, url=url, file_path=file_path,
            file_size=file_size,
            download_time=datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        )
        
        self.user_videos[user_id].insert(0, record)
        
        # Keep max 20 records
        while len(self.user_videos[user_id]) > 20:
            old = self.user_videos[user_id].pop()
            old_path = Path(old.file_path)
            if old_path.exists():
                try:
                    old_path.unlink()
                except OSError:
                    pass
        
        self._save_data()
    
    # -----------------------------------------------------------------------
    # Utility Methods
    # -----------------------------------------------------------------------
    def _is_whitelisted(self, user_id: int) -> bool:
        whitelist = self.config.get_whitelist()
        return not whitelist or user_id in whitelist
    
    def _get_default_share(self, user_id: int) -> str:
        return self.user_settings.get(user_id, {}).get('default_share', 'link')
    
    def _extract_youtube_url(self, text: str) -> Optional[str]:
        match = YOUTUBE_URL_PATTERN.search(text)
        if match:
            url = match.group(0)
            if url.startswith('www.'):
                url = 'https://' + url
            elif not url.startswith('http'):
                url = 'https://' + url
            return url
        return None
    
    @staticmethod
    def _escape_markdown(text: str) -> str:
        for char in ('*', '_', '`', '[', ']'):
            text = text.replace(char, '\\' + char)
        return text
    
    def _build_main_keyboard(self, user_id: int) -> InlineKeyboardMarkup:
        has_cookies = user_id in self.user_cookies
        share_method = self._get_default_share(user_id)
        video_count = len(self.user_videos.get(user_id, []))
        
        return InlineKeyboardMarkup([
            [InlineKeyboardButton("📹 Recent Videos", callback_data='recent_videos')],
            [InlineKeyboardButton("🍪 Upload Cookies", callback_data='cookies')],
            [InlineKeyboardButton("⚙️ Settings", callback_data='settings')],
            [
                InlineKeyboardButton(
                    f"🍪 {'✅' if has_cookies else '❌'}", 
                    callback_data='cookies_status'
                ),
                InlineKeyboardButton(
                    f"📤 {'📎 Link' if share_method == 'link' else '📤 Direct'}", 
                    callback_data='share_status'
                )
            ],
            [InlineKeyboardButton(f"📦 Videos: {video_count}", callback_data='video_count')],
        ])
    
    # -----------------------------------------------------------------------
    # Command Handlers
    # -----------------------------------------------------------------------
    async def cmd_start(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user = update.effective_user
        if not self._is_whitelisted(user.id):
            await update.message.reply_text("⛔ Unauthorized access.")
            return ConversationHandler.END
        
        await update.message.reply_text(
            f"👋 Welcome {user.first_name}!\n\n"
            "🎥 *YouTube Video Downloader Bot*\n\n"
            "📋 *Commands:*\n"
            "/start - Show this menu\n"
            "/cookies - Upload YouTube cookies\n"
            "/recent - View recent downloads\n"
            "/settings - Configure bot settings\n"
            "/help - Show help message\n\n"
            "💡 *How to use:*\n"
            "• Just send me a YouTube link and I'll download it!\n\n"
            "⚠️ *Important:* You must upload cookies first!\n"
            f"🗑️ Files auto-delete after {self.config.STORAGE_DAYS} days.",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=self._build_main_keyboard(user.id)
        )
    
    async def cmd_help(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        await update.message.reply_text(
            "📚 *Help Guide*\n\n"
            "1️⃣ *Send YouTube Link*: Just send any message with a YouTube URL\n"
            "2️⃣ *Upload Cookies*: /cookies - Required for downloading\n"
            "3️⃣ *Recent Videos*: /recent - View and manage downloads\n"
            "4️⃣ *Settings*: /settings - Choose link or direct upload\n\n"
            f"🗑️ Files auto-delete after {self.config.STORAGE_DAYS} days.",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=self._build_main_keyboard(update.effective_user.id)
        )
    
    async def cmd_recent(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        await self._show_recent_videos(update, context)
    
    async def cmd_cancel(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        await update.message.reply_text(
            "❌ Operation cancelled.",
            reply_markup=self._build_main_keyboard(update.effective_user.id)
        )
        return ConversationHandler.END
    
    # -----------------------------------------------------------------------
    # Message Handler (YouTube link detection)
    # -----------------------------------------------------------------------
    async def on_message(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user_id = update.effective_user.id
        if not self._is_whitelisted(user_id):
            return
        
        url = self._extract_youtube_url(update.message.text)
        if not url:
            return
        
        logger.info("User %d requested download", user_id)
        
        if user_id not in self.user_cookies:
            await update.message.reply_text(
                "❌ Upload cookies first! Use /cookies command.",
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton("🍪 Upload Cookies", callback_data='cookies')
                ]])
            )
            return
        
        await self._process_download(update, url)
    
    # -----------------------------------------------------------------------
    # Download Process
    # -----------------------------------------------------------------------
    async def _process_download(self, update: Update, url: str):
        user_id = update.effective_user.id
        status = await update.message.reply_text("⏳ Starting download...")
        
        try:
            video_path, video_title = await self._download_video(
                url, str(self.user_cookies[user_id]), status, user_id
            )
            
            if not video_path:
                return
            
            file_size = Path(video_path).stat().st_size
            self._add_video_record(user_id, video_title, url, video_path, file_size)
            
            if self._get_default_share(user_id) == 'telegram':
                await self._send_video_telegram(update, status, video_path, video_title, user_id)
            else:
                await self._send_download_link(update, status, video_path, video_title, user_id)
                
        except Exception as e:
            logger.error("Download failed for user %d: %s", user_id, str(e)[:100])
            await status.edit_text(
                "❌ Download failed. Please try again.",
                reply_markup=self._build_main_keyboard(user_id)
            )
    
    async def _download_video(self, url: str, cookies_file: str, 
                          status_msg, user_id: int) -> tuple:
    try:
        ydl_opts = {
            'format': 'best[ext=mp4]/best',
            'outtmpl': str(DOWNLOADS_DIR / '%(title)s.%(ext)s'),
            'cookiefile': cookies_file,
            'quiet': True,
            'no_warnings': True,
            'socket_timeout': 120,
            'retries': 50,
            'fragment_retries': 50,
            'http_chunk_size': 5 * 1024 * 1024,
            'throttled_rate': '100K',
            'no_mtime': True,
            'merge_output_format': 'mp4',
            'progress_hooks': [lambda d: self._progress_hook(d)],
        }
        
        await status_msg.edit_text("📥 Downloading video...")
        
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)
            title = info.get('title', 'Unknown')
            filepath = ydl.prepare_filename(info)
            
            # Find the actual downloaded file
            if not Path(filepath).exists():
                base = Path(filepath).stem
                for ext in ('.mp4', '.webm', '.mkv', '.m4a'):
                    alt = DOWNLOADS_DIR / f'{base}{ext}'
                    if alt.exists():
                        filepath = str(alt)
                        break
            
            if not Path(filepath).exists():
                # Search for any file with matching title
                safe_title = "".join(c for c in title if c.isalnum() or c in (' ', '-', '_')).rstrip()[:50]
                for f in DOWNLOADS_DIR.iterdir():
                    if f.is_file() and safe_title in f.stem:
                        filepath = str(f)
                        break
            
            if not Path(filepath).exists():
                raise FileNotFoundError(f"Downloaded file not found for: {title}")
            
            size_mb = Path(filepath).stat().st_size / 1024 / 1024
            logger.info("Downloaded for user %d: %.1f MB", user_id, size_mb)
            
            safe_title = self._escape_markdown(title[:100])
            await status_msg.edit_text(
                f"✅ *{safe_title}*\n📦 {size_mb:.1f} MB",
                parse_mode=ParseMode.MARKDOWN
            )
            
            return filepath, title
            
    except DownloadError as e:
        logger.error("yt-dlp error for user %d: %s", user_id, str(e)[:100])
        
        # Check if it's a format error
        error_msg = str(e)
        if "format is not available" in error_msg.lower():
            await status_msg.edit_text(
                "❌ Video format not available\n\n"
                "This video may have limited formats.\n"
                "Try a different video or quality.",
                parse_mode=ParseMode.MARKDOWN,
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("🔙 Menu", callback_data='back_to_main')]
                ])
            )
        else:
            await status_msg.edit_text(
                "❌ Download failed\n\n"
                "*Common fixes:*\n"
                "• Update yt-dlp: `pip install --upgrade yt-dlp`\n"
                "• Upload fresh cookies\n"
                "• Try a different video",
                parse_mode=ParseMode.MARKDOWN,
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("🍪 New Cookies", callback_data='cookies')],
                    [InlineKeyboardButton("🔙 Menu", callback_data='back_to_main')]
                ])
            )
        return None, None
    
    except Exception as e:
        logger.error("Download error for user %d: %s", user_id, str(e)[:100])
        await status_msg.edit_text(
            "❌ An error occurred. Please try again.",
            reply_markup=self._build_main_keyboard(user_id)
        )
        return None, None
        
    def _progress_hook(self, d: dict) -> None:
        if d['status'] == 'downloading':
            now = time.time()
            if now - self._last_progress_update > 10:
                pct = d.get('_percent_str', 'N/A').strip()
                spd = d.get('_speed_str', 'N/A').strip()
                logger.info("Download: %s at %s", pct, spd)
                self._last_progress_update = now
    
    # -----------------------------------------------------------------------
    # Video Delivery Methods
    # -----------------------------------------------------------------------
    async def _send_video_telegram(self, update, status_msg, path, title, user_id):
        try:
            size_mb = Path(path).stat().st_size / 1024 / 1024
            
            if size_mb > self.config.MAX_TELEGRAM_FILE_SIZE:
                await status_msg.edit_text(
                    f"⚠️ File too large ({size_mb:.1f} MB). Using download link...",
                    reply_markup=self._build_main_keyboard(user_id)
                )
                return await self._send_download_link(update, status_msg, path, title, user_id)
            
            await status_msg.edit_text("📤 Uploading to Telegram...")
            
            with open(path, 'rb') as f:
                await update.message.reply_video(
                    video=f, caption=f"📹 {title}", supports_streaming=True
                )
            
            await status_msg.delete()
            
        except Exception as e:
            logger.error("Failed to send video for user %d", user_id)
            await status_msg.edit_text(
                "❌ Upload failed. Using download link...",
                reply_markup=self._build_main_keyboard(user_id)
            )
            await self._send_download_link(update, status_msg, path, title, user_id)
    
    async def _send_download_link(self, update, status_msg, path, title, user_id):
        try:
            safe_name = "".join(c for c in title if c.isalnum() or c in (' ', '-', '_')).rstrip()
            new_name = f"{safe_name[:50]}_{user_id}_{datetime.now():%Y%m%d_%H%M%S}.mp4"
            new_path = DOWNLOADS_DIR / new_name
            
            shutil.move(path, new_path)
            
            # Update record path
            for video in self.user_videos.get(user_id, []):
                if video.file_path == path:
                    video.file_path = str(new_path)
                    break
            self._save_data()
            
            download_url = f"{self.base_download_link}/{quote(new_name)}"
            safe_title = self._escape_markdown(title[:200])
            
            await status_msg.edit_text(
                f"✅ *{safe_title}*\n\n"
                f"📥 [Download Link]({download_url})\n\n"
                f"⚠️ File deleted after {self.config.STORAGE_DAYS} days.",
                parse_mode=ParseMode.MARKDOWN,
                disable_web_page_preview=True,
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("📥 Download", url=download_url)],
                    [InlineKeyboardButton("📹 Recent", callback_data='recent_videos')],
                    [InlineKeyboardButton("🔙 Menu", callback_data='back_to_main')]
                ])
            )
        except Exception as e:
            logger.error("Failed to create link for user %d", user_id)
            await status_msg.edit_text(
                "❌ Failed to create download link.",
                reply_markup=self._build_main_keyboard(user_id)
            )
    
    # -----------------------------------------------------------------------
    # Recent Videos Menu
    # -----------------------------------------------------------------------
    async def _show_recent_videos(self, update: Update, context: ContextTypes.DEFAULT_TYPE, 
                                  page: int = 0):
        user_id = update.effective_user.id
        msg = update.callback_query.message if update.callback_query else update.message
        videos = self.user_videos.get(user_id, [])
        
        if not videos:
            await msg.reply_text(
                "📭 No videos yet. Send a YouTube link!",
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton("🔙 Menu", callback_data='back_to_main')
                ]])
            )
            return
        
        per_page = 5
        total_pages = max(1, (len(videos) + per_page - 1) // per_page)
        page = max(0, min(page, total_pages - 1))
        
        start = page * per_page
        end = min(start + per_page, len(videos))
        page_videos = videos[start:end]
        
        text = f"📹 *Downloads* ({page + 1}/{total_pages})\n\n"
        
        for i, video in enumerate(page_videos, start + 1):
            exists = "✅" if Path(video.file_path).exists() else "🗑️"
            safe = self._escape_markdown(video.title[:50])
            text += (
                f"{exists} *{i}.* {safe}\n"
                f"   📦 {video.file_size / 1024 / 1024:.1f} MB | 🕒 {video.download_time}\n\n"
            )
        
        text += f"⚠️ Auto-delete after {self.config.STORAGE_DAYS} days."
        
        keyboard = []
        for i, video in enumerate(page_videos, start + 1):
            if Path(video.file_path).exists():
                url = f"{self.base_download_link}/{quote(Path(video.file_path).name)}"
                keyboard.append([
                    InlineKeyboardButton(f"📥 {i}. {video.title[:30]}", url=url)
                ])
                keyboard.append([
                    InlineKeyboardButton(f"🗑️ Delete #{i}", callback_data=f'delete_{start + i - 1}')
                ])
        
        nav = []
        if page > 0:
            nav.append(InlineKeyboardButton("⬅️", callback_data=f'vpage_{page - 1}'))
        if page < total_pages - 1:
            nav.append(InlineKeyboardButton("➡️", callback_data=f'vpage_{page + 1}'))
        if nav:
            keyboard.append(nav)
        
        keyboard.append([InlineKeyboardButton("🔙 Menu", callback_data='back_to_main')])
        
        await msg.reply_text(
            text, parse_mode=ParseMode.MARKDOWN,
            disable_web_page_preview=True,
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
    
    async def _delete_video(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        query = update.callback_query
        await query.answer()
        
        user_id = update.effective_user.id
        idx = int(query.data.split('_')[1])
        videos = self.user_videos.get(user_id, [])
        
        if 0 <= idx < len(videos):
            video = videos[idx]
            path = Path(video.file_path)
            if path.exists():
                try:
                    path.unlink()
                except OSError:
                    pass
            videos.pop(idx)
            self._save_data()
            
            await query.message.reply_text(
                f"🗑️ Deleted: {video.title[:50]}",
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton("📹 Videos", callback_data='recent_videos'),
                    InlineKeyboardButton("🔙 Menu", callback_data='back_to_main')
                ]])
            )
    
    # -----------------------------------------------------------------------
    # Cookies Management
    # -----------------------------------------------------------------------
    async def _ask_cookies(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user_id = update.effective_user.id
        if not self._is_whitelisted(user_id):
            msg = update.callback_query.message if update.callback_query else update.message
            await msg.reply_text("⛔ Unauthorized.")
            return ConversationHandler.END
        
        msg = update.callback_query.message if update.callback_query else update.message
        await msg.reply_text(
            "⚠️ *WARNING: COOKIE SECURITY NOTICE*\n\n"
            "• Cookies contain sensitive login information\n"
            "• We are *NOT responsible* for security issues\n"
            "• Use at your own risk!\n\n"
            "📤 Send your cookies.txt file now.\n\n"
            "*How to export:*\n"
            "1. Install 'Get cookies.txt LOCALLY' extension\n"
            "2. Log into YouTube\n"
            "3. Export cookies (Netscape format)\n"
            "4. Send the file here",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("🔙 Cancel", callback_data='back_to_main')
            ]])
        )
        return WAITING_FOR_COOKIES
    
    async def _receive_cookies(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user_id = update.effective_user.id
        if not self._is_whitelisted(user_id):
            await update.message.reply_text("⛔ Unauthorized.")
            return ConversationHandler.END
        
        doc = update.message.document
        if not doc:
            await update.message.reply_text(
                "❌ Please send a .txt cookies file.",
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton("🔙 Cancel", callback_data='back_to_main')
                ]])
            )
            return WAITING_FOR_COOKIES
        
        try:
            file = await context.bot.get_file(doc.file_id)
            cookie_path = self._get_cookie_path(user_id)
            await file.download_to_drive(str(cookie_path))
            
            self.user_cookies[user_id] = cookie_path
            self._save_data()
            
            logger.info("User %d uploaded cookies", user_id)
            
            await update.message.reply_text(
                "✅ Cookies saved! Send me YouTube links to download.",
                reply_markup=self._build_main_keyboard(user_id)
            )
            return ConversationHandler.END
            
        except Exception as e:
            logger.error("Cookie upload failed for user %d: %s", user_id, e)
            await update.message.reply_text(
                "❌ Failed to save cookies. Try again.",
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton("🔙 Cancel", callback_data='back_to_main')
                ]])
            )
            return WAITING_FOR_COOKIES
    
    # -----------------------------------------------------------------------
    # Settings
    # -----------------------------------------------------------------------
    async def _show_settings(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user_id = update.effective_user.id
        if not self._is_whitelisted(user_id):
            return
        
        current = self._get_default_share(user_id)
        msg = update.callback_query.message if update.callback_query else update.message
        
        await msg.reply_text(
            f"⚙️ *Settings*\n\nCurrent: {'📎 Link' if current == 'link' else '📤 Upload'}",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton(
                    f"{'✅' if current == 'link' else '⬜'} Download Link",
                    callback_data='set_link'
                )],
                [InlineKeyboardButton(
                    f"{'✅' if current == 'telegram' else '⬜'} Telegram Upload",
                    callback_data='set_telegram'
                )],
                [InlineKeyboardButton("🔙 Menu", callback_data='back_to_main')]
            ])
        )
    
    async def _handle_settings(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        query = update.callback_query
        await query.answer()
        
        user_id = update.effective_user.id
        setting = 'link' if query.data == 'set_link' else 'telegram'
        
        self.user_settings[user_id] = {'default_share': setting}
        self._save_data()
        
        label = 'Download Link' if setting == 'link' else 'Telegram Upload'
        await query.edit_message_text(
            f"✅ Default: *{label}*",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("🔙 Settings", callback_data='settings')
            ]])
        )
    
    # -----------------------------------------------------------------------
    # Navigation
    # -----------------------------------------------------------------------
    async def _back_to_menu(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        msg = update.callback_query.message if update.callback_query else update.message
        user_id = update.effective_user.id
        await msg.reply_text("📋 Menu:", reply_markup=self._build_main_keyboard(user_id))
    
    # -----------------------------------------------------------------------
    # Button Router
    # -----------------------------------------------------------------------
    async def _button_router(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        query = update.callback_query
        await query.answer()
        
        routes = {
            'cookies': self._ask_cookies,
            'settings': self._show_settings,
            'recent_videos': self._show_recent_videos,
            'back_to_main': self._back_to_menu,
            'cookies_status': lambda u, c: query.message.reply_text(
                "✅ Cookies ready!" if update.effective_user.id in self.user_cookies 
                else "❌ Use /cookies to upload."
            ),
            'share_status': lambda u, c: query.message.reply_text(
                f"Method: {'📎 Link' if self._get_default_share(update.effective_user.id) == 'link' else '📤 Upload'}"
            ),
            'video_count': lambda u, c: query.message.reply_text(
                f"📦 {len(self.user_videos.get(update.effective_user.id, []))} videos stored."
            ),
        }
        
        if query.data in routes:
            return await routes[query.data](update, context)
        elif query.data.startswith('delete_'):
            return await self._delete_video(update, context)
        elif query.data.startswith('vpage_'):
            return await self._show_recent_videos(update, context, int(query.data.split('_')[1]))
    
    # -----------------------------------------------------------------------
    # Run Bot
    # -----------------------------------------------------------------------
    def run(self) -> None:
        app = Application.builder().token(self.config.BOT_TOKEN).build()
        
        # Conversation handler for cookies
        cookies_conv = ConversationHandler(
            entry_points=[
                CommandHandler('cookies', self._ask_cookies),
                CallbackQueryHandler(self._ask_cookies, pattern='^cookies$')
            ],
            states={
                WAITING_FOR_COOKIES: [
                    MessageHandler(filters.Document.FileExtension("txt"), self._receive_cookies),
                    MessageHandler(filters.TEXT & ~filters.COMMAND, self._ask_cookies),
                ]
            },
            fallbacks=[
                CommandHandler('cancel', self.cmd_cancel),
                CallbackQueryHandler(self._back_to_menu, pattern='^back_to_main$')
            ],
            per_message=False
        )
        
        # Register handlers
        app.add_handler(CommandHandler('start', self.cmd_start))
        app.add_handler(CommandHandler('help', self.cmd_help))
        app.add_handler(CommandHandler('recent', self.cmd_recent))
        app.add_handler(cookies_conv)
        app.add_handler(CallbackQueryHandler(self._button_router))
        app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, self.on_message))
        
        logger.info("Bot starting...")
        app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == '__main__':
    YouTubeDownloaderBot().run()