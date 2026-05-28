#!/usr/bin/env python3
"""
YouTube Downloader Telegram Bot
Automatically detects YouTube links, downloads videos, and manages file storage.
"""

import os
import logging
import json
import time
import shutil
import re
import threading
from pathlib import Path
from datetime import datetime, timedelta
from typing import Dict, Optional, List, Set
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

# Configure root logger to suppress httpx and telegram request details
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.WARNING  # Only show warnings and errors by default
)

# Set specific loggers
logging.getLogger('httpx').setLevel(logging.WARNING)
logging.getLogger('httpcore').setLevel(logging.WARNING)
logging.getLogger('telegram').setLevel(logging.WARNING)
logging.getLogger('telegram.ext').setLevel(logging.WARNING)

# Our application logger
logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

# Add console handler for our logger only
console_handler = logging.StreamHandler()
console_handler.setLevel(logging.INFO)
console_formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')
console_handler.setFormatter(console_formatter)
logger.addHandler(console_handler)
logger.propagate = False

# Conversation states
WAITING_FOR_COOKIES = 1
SETTINGS_MENU = 2

# YouTube URL pattern
YOUTUBE_URL_PATTERN = re.compile(
    r'(https?://)?(www\.)?(youtube\.com|youtu\.be)/\S+'
)

class VideoRecord:
    """Store video download information"""
    def __init__(self, title: str, url: str, file_path: str, file_size: int, download_time: str):
        self.title = title
        self.url = url
        self.file_path = file_path
        self.file_size = file_size
        self.download_time = download_time
    
    def to_dict(self):
        return {
            'title': self.title,
            'url': self.url,
            'file_path': self.file_path,
            'file_size': self.file_size,
            'download_time': self.download_time
        }
    
    @classmethod
    def from_dict(cls, data):
        return cls(
            data['title'],
            data['url'],
            data['file_path'],
            data['file_size'],
            data['download_time']
        )

class YouTubeDownloaderBot:
    def __init__(self):
        self.config = Config()
        self.base_download_link = self.config.BASE_DOWNLOAD_LINK.rstrip('/')
        
        os.makedirs(self.config.DOWNLOAD_DIR, exist_ok=True)
        
        self.user_cookies: Dict[int, str] = {}
        self.user_settings: Dict[int, Dict] = {}
        self.user_videos: Dict[int, List[VideoRecord]] = {}
        self._last_progress_update = 0
        
        self.load_data()
        self.start_cleanup_thread()
    
    def load_data(self):
        """Load saved user data from JSON files"""
        try:
            if os.path.exists('user_cookies.json'):
                with open('user_cookies.json', 'r') as f:
                    data = json.load(f)
                    self.user_cookies = {int(k): v for k, v in data.items()}
                logger.info(f"Loaded cookies for %d users", len(self.user_cookies))
        except Exception as e:
            logger.error("Error loading cookies: %s", e)
        
        try:
            if os.path.exists('user_settings.json'):
                with open('user_settings.json', 'r') as f:
                    data = json.load(f)
                    self.user_settings = {int(k): v for k, v in data.items()}
        except Exception as e:
            logger.error("Error loading settings: %s", e)
        
        try:
            if os.path.exists('user_videos.json'):
                with open('user_videos.json', 'r') as f:
                    data = json.load(f)
                    self.user_videos = {}
                    for uid, videos in data.items():
                        self.user_videos[int(uid)] = [VideoRecord.from_dict(v) for v in videos]
                logger.info("Loaded video records for %d users", len(self.user_videos))
        except Exception as e:
            logger.error("Error loading videos: %s", e)
    
    def save_data(self):
        """Save user data to JSON files"""
        try:
            cookies_data = {str(k): v for k, v in self.user_cookies.items()}
            with open('user_cookies.json', 'w') as f:
                json.dump(cookies_data, f, indent=2)
            
            settings_data = {str(k): v for k, v in self.user_settings.items()}
            with open('user_settings.json', 'w') as f:
                json.dump(settings_data, f, indent=2)
            
            videos_data = {}
            for uid, videos in self.user_videos.items():
                videos_data[str(uid)] = [v.to_dict() for v in videos]
            with open('user_videos.json', 'w') as f:
                json.dump(videos_data, f, indent=2)
        except Exception as e:
            logger.error("Error saving data: %s", e)
    
    def start_cleanup_thread(self):
        """Start background thread to clean old files"""
        def cleanup_worker():
            while True:
                try:
                    self.cleanup_old_files()
                except Exception as e:
                    logger.error("Cleanup error: %s", e)
                time.sleep(3600)
        
        thread = threading.Thread(target=cleanup_worker, daemon=True)
        thread.start()
        logger.info("Cleanup thread started")
    
    def cleanup_old_files(self):
        """Remove files older than configured days"""
        try:
            current_time = datetime.now()
            cutoff_time = current_time - timedelta(days=self.config.STORAGE_DAYS)
            cleaned_count = 0
            
            for filename in os.listdir(self.config.DOWNLOAD_DIR):
                filepath = os.path.join(self.config.DOWNLOAD_DIR, filename)
                if os.path.isfile(filepath):
                    file_mtime = datetime.fromtimestamp(os.path.getmtime(filepath))
                    if file_mtime < cutoff_time:
                        os.remove(filepath)
                        cleaned_count += 1
            
            if cleaned_count > 0:
                logger.info("Cleaned up %d old files", cleaned_count)
            
            for user_id in list(self.user_videos.keys()):
                self.user_videos[user_id] = [
                    v for v in self.user_videos[user_id]
                    if os.path.exists(v.file_path)
                ]
                if not self.user_videos[user_id]:
                    del self.user_videos[user_id]
            
            self.save_data()
            
        except Exception as e:
            logger.error("Error during cleanup: %s", e)
    
    def add_video_record(self, user_id: int, title: str, url: str, file_path: str, file_size: int):
        """Add a video record for a user"""
        if user_id not in self.user_videos:
            self.user_videos[user_id] = []
        
        video_record = VideoRecord(
            title=title,
            url=url,
            file_path=file_path,
            file_size=file_size,
            download_time=datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        )
        
        self.user_videos[user_id].insert(0, video_record)
        
        if len(self.user_videos[user_id]) > 20:
            old_video = self.user_videos[user_id].pop()
            if os.path.exists(old_video.file_path):
                try:
                    os.remove(old_video.file_path)
                except:
                    pass
        
        self.save_data()
    
    def is_whitelisted(self, user_id: int) -> bool:
        """Check if user is whitelisted"""
        whitelist = self.config.get_whitelist()
        if not whitelist:
            return True
        return user_id in whitelist
    
    def get_default_setting(self, user_id: int) -> str:
        """Get user's default sharing method"""
        if user_id in self.user_settings:
            return self.user_settings[user_id].get('default_share', 'link')
        return 'link'
    
    def extract_youtube_url(self, text: str) -> Optional[str]:
        """Extract YouTube URL from text"""
        match = YOUTUBE_URL_PATTERN.search(text)
        if match:
            url = match.group(0)
            if url.startswith('www.'):
                url = 'https://' + url
            elif not url.startswith('http'):
                url = 'https://' + url
            return url
        return None
    
    async def start(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Send welcome message and show main menu"""
        user = update.effective_user
        
        if not self.is_whitelisted(user.id):
            await update.message.reply_text(
                "⛔ You are not authorized to use this bot.\n"
                "Please contact the administrator."
            )
            return ConversationHandler.END
        
        welcome_text = (
            f"👋 Welcome {user.first_name}!\n\n"
            "🎥 *YouTube Video Downloader Bot*\n\n"
            "📋 *Commands:*\n"
            "/start - Show this menu\n"
            "/cookies - Upload YouTube cookies\n"
            "/recent - View recent downloads\n"
            "/settings - Configure bot settings\n"
            "/help - Show help message\n\n"
            "💡 *How to use:*\n"
            "• Just send me a YouTube link and I'll download it!\n"
            "• No need to use any download command\n\n"
            "⚠️ *Important:* You must upload cookies first!\n"
            "🔒 Sharing cookies is dangerous. We're not responsible for any issues.\n\n"
            f"🗑️ *Note:* Files are automatically deleted after {self.config.STORAGE_DAYS} days."
        )
        
        await update.message.reply_text(
            welcome_text,
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=self.get_main_keyboard(user.id)
        )
    
    def get_main_keyboard(self, user_id: int) -> InlineKeyboardMarkup:
        """Get main menu keyboard"""
        has_cookies = user_id in self.user_cookies
        default_share = self.get_default_setting(user_id)
        video_count = len(self.user_videos.get(user_id, []))
        
        keyboard = [
            [InlineKeyboardButton("📹 Recent Videos", callback_data='recent_videos')],
            [InlineKeyboardButton("🍪 Upload Cookies", callback_data='cookies')],
            [InlineKeyboardButton("⚙️ Settings", callback_data='settings')],
        ]
        
        cookie_status = "✅" if has_cookies else "❌"
        share_method = "📎 Link" if default_share == 'link' else "📤 Direct"
        
        keyboard.append([
            InlineKeyboardButton(f"🍪 Cookies: {cookie_status}", callback_data='cookies_status'),
            InlineKeyboardButton(f"📤 Share: {share_method}", callback_data='share_status')
        ])
        
        keyboard.append([
            InlineKeyboardButton(f"📦 Videos: {video_count}", callback_data='video_count')
        ])
        
        return InlineKeyboardMarkup(keyboard)
    
    async def button_handler(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle button clicks"""
        query = update.callback_query
        await query.answer()
        
        if query.data == 'cookies':
            return await self.ask_for_cookies(update, context)
        elif query.data == 'settings':
            return await self.show_settings(update, context)
        elif query.data == 'recent_videos':
            return await self.show_recent_videos(update, context)
        elif query.data == 'back_to_main':
            return await self.show_main_menu(update, context)
        elif query.data == 'cookies_status':
            user_id = update.effective_user.id
            if user_id in self.user_cookies:
                await query.message.reply_text("✅ You have cookies uploaded!")
            else:
                await query.message.reply_text(
                    "❌ No cookies uploaded. Use /cookies to upload."
                )
        elif query.data == 'share_status':
            user_id = update.effective_user.id
            default = self.get_default_setting(user_id)
            await query.message.reply_text(
                f"Current sharing method: {'📎 Download Link' if default == 'link' else '📤 Direct Telegram Upload'}"
            )
        elif query.data == 'video_count':
            user_id = update.effective_user.id
            count = len(self.user_videos.get(user_id, []))
            await query.message.reply_text(
                f"📦 You have {count} videos stored.\n"
                "Use /recent to view them.\n\n"
                f"⚠️ Files older than {self.config.STORAGE_DAYS} days are automatically deleted."
            )
        elif query.data.startswith('delete_video_'):
            await self.delete_video(update, context)
        elif query.data.startswith('video_page_'):
            await self.show_recent_videos(update, context, page=int(query.data.split('_')[2]))
    
    async def handle_message(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle incoming messages and detect YouTube links"""
        user_id = update.effective_user.id
        
        if not self.is_whitelisted(user_id):
            return
        
        text = update.message.text
        youtube_url = self.extract_youtube_url(text)
        
        if youtube_url:
            logger.info("User %d requested download", user_id)
            
            if user_id not in self.user_cookies:
                await update.message.reply_text(
                    "❌ You need to upload cookies first!\n"
                    "Use /cookies command to upload YouTube cookies.",
                    reply_markup=InlineKeyboardMarkup([[
                        InlineKeyboardButton("🍪 Upload Cookies", callback_data='cookies')
                    ]])
                )
                return
            
            await self.process_download(update, youtube_url)
    
    async def process_download(self, update: Update, url: str):
        """Process YouTube video download"""
        user_id = update.effective_user.id
        
        status_message = await update.message.reply_text(
            "⏳ Detected YouTube link!\n"
            "Starting download...\n"
            "This may take a few moments."
        )
        
        try:
            video_path, video_title = await self.download_video(
                url, 
                self.user_cookies[user_id], 
                status_message,
                user_id
            )
            
            if not video_path:
                return
            
            file_size = os.path.getsize(video_path)
            self.add_video_record(user_id, video_title, url, video_path, file_size)
            
            default_share = self.get_default_setting(user_id)
            
            if default_share == 'telegram':
                await self.send_video_telegram(
                    update, status_message, video_path, video_title, user_id
                )
            else:
                await self.send_download_link(
                    update, status_message, video_path, video_title, user_id
                )
            
        except Exception as e:
            logger.error("Error processing download for user %d: %s", user_id, str(e)[:100])
            await status_message.edit_text(
                "❌ An error occurred while processing your request.\n"
                "Please try again or contact support.",
                reply_markup=self.get_main_keyboard(user_id)
            )
    
    async def show_recent_videos(self, update: Update, context: ContextTypes.DEFAULT_TYPE, page: int = 0):
        """Show recent videos menu with pagination"""
        user_id = update.effective_user.id
        
        if update.callback_query:
            message = update.callback_query.message
        else:
            message = update.message
        
        videos = self.user_videos.get(user_id, [])
        
        if not videos:
            await message.reply_text(
                "📭 No videos downloaded yet.\n\n"
                "Send me a YouTube link to download!",
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton("🔙 Main Menu", callback_data='back_to_main')
                ]])
            )
            return
        
        videos_per_page = 5
        total_pages = (len(videos) + videos_per_page - 1) // videos_per_page
        page = max(0, min(page, total_pages - 1))
        
        start_idx = page * videos_per_page
        end_idx = min(start_idx + videos_per_page, len(videos))
        
        page_videos = videos[start_idx:end_idx]
        
        text = f"📹 *Recent Downloads* (Page {page + 1}/{total_pages})\n\n"
        
        for i, video in enumerate(page_videos, start_idx + 1):
            file_exists = os.path.exists(video.file_path)
            status = "✅" if file_exists else "🗑️"
            
            safe_title = video.title[:50].replace('*', '\\*').replace('_', '\\_').replace('`', '\\`').replace('[', '\\[')
            
            text += (
                f"{status} *{i}.* {safe_title}\n"
                f"   📦 {video.file_size / 1024 / 1024:.1f} MB\n"
                f"   🕒 {video.download_time}\n\n"
            )
        
        text += f"⚠️ Files are deleted after {self.config.STORAGE_DAYS} days.\n"
        text += "Click a button below to manage videos."
        
        keyboard = []
        
        for i, video in enumerate(page_videos, start_idx + 1):
            if os.path.exists(video.file_path):
                download_url = f"{self.base_download_link}/{quote(os.path.basename(video.file_path))}"
                safe_title = video.title[:30]
                keyboard.append([
                    InlineKeyboardButton(f"📥 {i}. {safe_title}", url=download_url)
                ])
                keyboard.append([
                    InlineKeyboardButton(f"🗑️ Delete #{i}", callback_data=f'delete_video_{start_idx + i - 1}')
                ])
        
        nav_buttons = []
        if page > 0:
            nav_buttons.append(InlineKeyboardButton("⬅️ Previous", callback_data=f'video_page_{page - 1}'))
        if page < total_pages - 1:
            nav_buttons.append(InlineKeyboardButton("Next ➡️", callback_data=f'video_page_{page + 1}'))
        
        if nav_buttons:
            keyboard.append(nav_buttons)
        
        keyboard.append([InlineKeyboardButton("🔙 Main Menu", callback_data='back_to_main')])
        
        await message.reply_text(
            text,
            parse_mode=ParseMode.MARKDOWN,
            disable_web_page_preview=True,
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
    
    async def delete_video(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Delete a specific video"""
        query = update.callback_query
        await query.answer()
        
        user_id = update.effective_user.id
        video_idx = int(query.data.split('_')[2])
        
        videos = self.user_videos.get(user_id, [])
        
        if 0 <= video_idx < len(videos):
            video = videos[video_idx]
            
            if os.path.exists(video.file_path):
                try:
                    os.remove(video.file_path)
                    logger.info("User %d deleted video", user_id)
                except Exception as e:
                    logger.error("Error deleting file: %s", e)
            
            videos.pop(video_idx)
            self.save_data()
            
            await query.message.reply_text(
                f"🗑️ Deleted: {video.title[:50]}",
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton("📹 View Videos", callback_data='recent_videos'),
                    InlineKeyboardButton("🔙 Main Menu", callback_data='back_to_main')
                ]])
            )
        else:
            await query.message.reply_text(
                "❌ Video not found. It may have been already deleted.",
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton("🔙 Main Menu", callback_data='back_to_main')
                ]])
            )
    
    async def ask_for_cookies(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Ask user to upload cookies file"""
        user_id = update.effective_user.id
        
        if not self.is_whitelisted(user_id):
            if update.callback_query:
                await update.callback_query.message.reply_text("⛔ Unauthorized access.")
            else:
                await update.message.reply_text("⛔ Unauthorized access.")
            return ConversationHandler.END
        
        warning_text = (
            "⚠️ *WARNING: COOKIE SECURITY NOTICE*\n\n"
            "• Cookies contain sensitive login information\n"
            "• Sharing cookies can give others access to your accounts\n"
            "• We store cookies locally and only use them for YouTube downloads\n"
            "• *We are NOT responsible* for any security issues or account compromises\n"
            "• Use at your own risk!\n\n"
            "📤 Please send your cookies file (.txt) now.\n\n"
            "*How to export cookies:*\n"
            "1. Install 'Get cookies.txt LOCALLY' browser extension\n"
            "2. Log into YouTube in your browser\n"
            "3. Click the extension and export cookies as Netscape format (.txt)\n"
            "4. Send the exported .txt file here"
        )
        
        if update.callback_query:
            message = update.callback_query.message
        else:
            message = update.message
        
        await message.reply_text(
            warning_text,
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("🔙 Cancel", callback_data='back_to_main')
            ]])
        )
        return WAITING_FOR_COOKIES
    
    async def handle_cookies(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle received cookies file"""
        user_id = update.effective_user.id
        
        if not self.is_whitelisted(user_id):
            await update.message.reply_text("⛔ Unauthorized access.")
            return ConversationHandler.END
        
        document = update.message.document
        if not document:
            await update.message.reply_text(
                "❌ Please send a valid cookies file (.txt)\n\n"
                "Use the button below to go back or try again.",
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton("🔙 Back", callback_data='back_to_main'),
                    InlineKeyboardButton("🔄 Try Again", callback_data='cookies')
                ]])
            )
            return WAITING_FOR_COOKIES
        
        try:
            file = await context.bot.get_file(document.file_id)
            cookies_path = f"cookies_{user_id}.txt"
            await file.download_to_drive(cookies_path)
            
            self.user_cookies[user_id] = cookies_path
            self.save_data()
            
            logger.info("User %d uploaded cookies", user_id)
            
            await update.message.reply_text(
                "✅ Cookies saved successfully!\n"
                "You can now send me YouTube links to download!",
                reply_markup=self.get_main_keyboard(user_id)
            )
            
            return ConversationHandler.END
            
        except Exception as e:
            logger.error("Error saving cookies for user %d: %s", user_id, e)
            await update.message.reply_text(
                "❌ Failed to save cookies. Please try again.",
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton("🔙 Back", callback_data='back_to_main')
                ]])
            )
            return WAITING_FOR_COOKIES
    
    async def download_video(self, url: str, cookies_file: str, status_message, user_id: int) -> tuple:
        """Download YouTube video using yt-dlp"""
        try:
            output_template = os.path.join(self.config.DOWNLOAD_DIR, '%(title)s.%(ext)s')
            
            ydl_opts = {
                'format': 'bestvideo[vcodec^=avc1]+bestaudio[acodec^=mp4a]/best[ext=mp4]/best',
                'outtmpl': output_template,
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
                'progress_hooks': [lambda d: self.sync_progress_hook(d)],
            }
            
            await status_message.edit_text("📥 Fetching video information...")
            
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                try:
                    info = ydl.extract_info(url, download=True)
                    video_title = info.get('title', 'Unknown Title')
                    
                    filename = ydl.prepare_filename(info)
                    
                    if not os.path.exists(filename):
                        base = os.path.splitext(filename)[0]
                        for ext in ['.mp4', '.webm', '.mkv']:
                            if os.path.exists(base + ext):
                                filename = base + ext
                                break
                    
                    if not os.path.exists(filename):
                        raise FileNotFoundError("Downloaded file not found")
                    
                    file_size = os.path.getsize(filename)
                    logger.info("Download complete for user %d: %.1f MB", user_id, file_size / 1024 / 1024)
                    
                    safe_title = self._escape_markdown(video_title[:100])
                    await status_message.edit_text(
                        f"✅ Download complete!\n"
                        f"📹 *{safe_title}*\n"
                        f"📦 Size: {file_size / 1024 / 1024:.1f} MB",
                        parse_mode=ParseMode.MARKDOWN
                    )
                    
                    return filename, video_title
                    
                except DownloadError as e:
                    logger.error("Download error for user %d", user_id)
                    
                    await status_message.edit_text(
                        "❌ Download failed\n\n"
                        "*Troubleshooting:*\n"
                        "• Make sure cookies are fresh (logged-in YouTube session)\n"
                        "• Try again in a few minutes\n"
                        "• Some videos may have restrictions\n\n"
                        "Update yt-dlp if issue persists:\n"
                        "`pip install --upgrade yt-dlp`",
                        parse_mode=ParseMode.MARKDOWN,
                        reply_markup=InlineKeyboardMarkup([
                            [InlineKeyboardButton("🍪 Upload New Cookies", callback_data='cookies')],
                            [InlineKeyboardButton("🔙 Main Menu", callback_data='back_to_main')]
                        ])
                    )
                    return None, None
                    
        except Exception as e:
            logger.error("Unexpected error for user %d", user_id)
            await status_message.edit_text(
                "❌ An unexpected error occurred.\n"
                "Please try again or contact support.",
                reply_markup=self.get_main_keyboard(user_id)
            )
            return None, None
    
    def _escape_markdown(self, text: str) -> str:
        """Escape special characters for Markdown"""
        escape_chars = ['*', '_', '`', '[', ']']
        for char in escape_chars:
            text = text.replace(char, '\\' + char)
        return text
    
    def sync_progress_hook(self, d):
        """Synchronous progress hook for yt-dlp"""
        if d['status'] == 'downloading':
            try:
                current_time = time.time()
                if current_time - self._last_progress_update > 10:
                    percent = d.get('_percent_str', 'N/A').strip()
                    speed = d.get('_speed_str', 'N/A').strip()
                    logger.info("Download progress: %s at %s", percent, speed)
                    self._last_progress_update = current_time
            except:
                pass
    
    async def send_video_telegram(self, update, status_message, video_path, video_title, user_id):
        """Send video directly via Telegram"""
        try:
            file_size = os.path.getsize(video_path)
            
            if file_size > self.config.MAX_TELEGRAM_FILE_SIZE:
                await status_message.edit_text(
                    f"⚠️ File too large for Telegram ({file_size / 1024 / 1024:.1f} MB)\n"
                    f"Max size: {self.config.MAX_TELEGRAM_FILE_SIZE / 1024 / 1024:.1f} MB\n"
                    "Switching to download link...",
                    reply_markup=self.get_main_keyboard(user_id)
                )
                await self.send_download_link(update, status_message, video_path, video_title, user_id)
                return
            
            await status_message.edit_text("📤 Uploading to Telegram...")
            
            with open(video_path, 'rb') as video_file:
                await update.message.reply_video(
                    video=video_file,
                    caption=f"📹 {video_title}",
                    supports_streaming=True
                )
            
            await status_message.delete()
            
        except Exception as e:
            logger.error("Error sending video for user %d", user_id)
            await status_message.edit_text(
                "❌ Failed to send video. Trying download link...",
                reply_markup=self.get_main_keyboard(user_id)
            )
            await self.send_download_link(update, status_message, video_path, video_title, user_id)
    
    async def send_download_link(self, update, status_message, video_path, video_title, user_id):
        """Provide download link for the video"""
        try:
            safe_title = "".join(c for c in video_title if c.isalnum() or c in (' ', '-', '_')).rstrip()
            perm_filename = f"{safe_title[:50]}_{user_id}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.mp4"
            perm_path = os.path.join(self.config.DOWNLOAD_DIR, perm_filename)
            
            shutil.move(video_path, perm_path)
            
            if user_id in self.user_videos:
                for video in self.user_videos[user_id]:
                    if video.file_path == video_path:
                        video.file_path = perm_path
                        break
            self.save_data()
            
            download_url = f"{self.base_download_link}/{quote(perm_filename)}"
            
            safe_title_md = self._escape_markdown(video_title[:200])
            await status_message.edit_text(
                f"✅ Video downloaded successfully!\n\n"
                f"📹 *Title:* {safe_title_md}\n"
                f"📥 *Download Link:* [Click here]({download_url})\n\n"
                f"⚠️ This file will be deleted after {self.config.STORAGE_DAYS} days.\n"
                f"💾 Save the file locally if you want to keep it.",
                parse_mode=ParseMode.MARKDOWN,
                disable_web_page_preview=True,
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("📥 Download", url=download_url)],
                    [InlineKeyboardButton("📹 Recent Videos", callback_data='recent_videos')],
                    [InlineKeyboardButton("🔙 Main Menu", callback_data='back_to_main')]
                ])
            )
            
        except Exception as e:
            logger.error("Error creating download link for user %d", user_id)
            await status_message.edit_text(
                "❌ Failed to create download link.",
                reply_markup=self.get_main_keyboard(user_id)
            )
    
    async def show_settings(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Show settings menu"""
        user_id = update.effective_user.id
        
        if not self.is_whitelisted(user_id):
            if update.callback_query:
                await update.callback_query.message.reply_text("⛔ Unauthorized access.")
            else:
                await update.message.reply_text("⛔ Unauthorized access.")
            return ConversationHandler.END
        
        current_setting = self.get_default_setting(user_id)
        
        settings_text = (
            "⚙️ *Bot Settings*\n\n"
            "Configure how you receive downloaded videos.\n\n"
            f"📤 *Current method:* {'📎 Download Link' if current_setting == 'link' else '📤 Direct Telegram Upload'}"
        )
        
        keyboard = [
            [
                InlineKeyboardButton(
                    f"{'✅' if current_setting == 'link' else '⬜'} Download Link",
                    callback_data='set_share_link'
                )
            ],
            [
                InlineKeyboardButton(
                    f"{'✅' if current_setting == 'telegram' else '⬜'} Telegram Upload",
                    callback_data='set_share_telegram'
                )
            ],
            [InlineKeyboardButton("🔙 Back to Menu", callback_data='back_to_main')]
        ]
        
        if update.callback_query:
            message = update.callback_query.message
        else:
            message = update.message
        
        await message.reply_text(
            settings_text,
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
        return SETTINGS_MENU
    
    async def handle_settings(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle settings changes"""
        query = update.callback_query
        await query.answer()
        
        user_id = update.effective_user.id
        
        if query.data == 'set_share_link':
            self.user_settings[user_id] = {'default_share': 'link'}
            self.save_data()
            await query.edit_message_text(
                "✅ Default sharing method set to: *Download Link*\n"
                "You'll receive a download link for your videos.",
                parse_mode=ParseMode.MARKDOWN,
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton("🔙 Back to Settings", callback_data='settings')
                ]])
            )
        
        elif query.data == 'set_share_telegram':
            self.user_settings[user_id] = {'default_share': 'telegram'}
            self.save_data()
            await query.edit_message_text(
                "✅ Default sharing method set to: *Telegram Upload*\n"
                "Videos will be sent directly in Telegram (max 50MB).",
                parse_mode=ParseMode.MARKDOWN,
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton("🔙 Back to Settings", callback_data='settings')
                ]])
            )
    
    async def show_main_menu(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Show main menu"""
        if update.callback_query:
            message = update.callback_query.message
            user_id = update.callback_query.from_user.id
        else:
            message = update.message
            user_id = update.message.from_user.id
        
        await message.reply_text(
            "📋 Main Menu:",
            reply_markup=self.get_main_keyboard(user_id)
        )
    
    async def recent_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Show recent videos"""
        return await self.show_recent_videos(update, context)
    
    async def help_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Show help message"""
        help_text = (
            "📚 *Help Guide*\n\n"
            "1️⃣ *Send YouTube Link*: Just send any message with a YouTube URL\n"
            "   • Bot automatically detects and downloads it\n\n"
            "2️⃣ *Upload Cookies*: Required for downloading\n"
            "   • Use /cookies command\n"
            "   • Send cookies.txt file from browser extension\n\n"
            "3️⃣ *Recent Videos*: View your downloads\n"
            "   • Use /recent command or menu button\n"
            "   • Download or delete videos from the menu\n\n"
            "4️⃣ *Settings*: Configure sharing method\n"
            "   • Download Link: Get a direct download link\n"
            "   • Telegram Upload: Receive video directly (up to 50MB)\n\n"
            "⚠️ *Security Notice*: Cookies are stored locally.\n"
            "We are not responsible for any misuse or security issues.\n\n"
            f"🗑️ *Auto-Delete*: Files are removed after {self.config.STORAGE_DAYS} days."
        )
        
        await update.message.reply_text(
            help_text,
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=self.get_main_keyboard(update.effective_user.id)
        )
    
    async def cancel(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Cancel current operation"""
        await update.message.reply_text(
            "❌ Operation cancelled.",
            reply_markup=self.get_main_keyboard(update.effective_user.id)
        )
        return ConversationHandler.END
    
    def run(self):
        """Start the bot"""
        app = Application.builder().token(self.config.BOT_TOKEN).build()
        
        cookies_conv = ConversationHandler(
            entry_points=[
                CommandHandler('cookies', self.ask_for_cookies),
                CallbackQueryHandler(self.ask_for_cookies, pattern='^cookies$')
            ],
            states={
                WAITING_FOR_COOKIES: [
                    MessageHandler(filters.Document.FileExtension("txt"), self.handle_cookies),
                    MessageHandler(filters.TEXT & ~filters.COMMAND, self.ask_for_cookies),
                ]
            },
            fallbacks=[
                CommandHandler('cancel', self.cancel),
                CallbackQueryHandler(self.show_main_menu, pattern='^back_to_main$')
            ],
            per_message=False
        )
        
        app.add_handler(CommandHandler('start', self.start))
        app.add_handler(CommandHandler('help', self.help_command))
        app.add_handler(CommandHandler('recent', self.recent_command))
        app.add_handler(CallbackQueryHandler(self.button_handler, pattern='^(cookies_status|share_status|video_count)$'))
        app.add_handler(CallbackQueryHandler(self.show_main_menu, pattern='^back_to_main$'))
        app.add_handler(CallbackQueryHandler(self.handle_settings, pattern='^set_share_'))
        app.add_handler(CallbackQueryHandler(self.show_settings, pattern='^settings$'))
        app.add_handler(CallbackQueryHandler(self.show_recent_videos, pattern='^recent_videos$'))
        app.add_handler(CallbackQueryHandler(self.delete_video, pattern='^delete_video_'))
        app.add_handler(CallbackQueryHandler(self.show_recent_videos, pattern='^video_page_'))
        app.add_handler(cookies_conv)
        app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, self.handle_message))
        
        logger.info("Bot started successfully")
        app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == '__main__':
    bot = YouTubeDownloaderBot()
    bot.run()