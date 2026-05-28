#!/usr/bin/env python3
"""
YouTube Downloader Telegram Bot
Downloads YouTube videos and sends them to users or provides download links.
"""

import os
import logging
import json
import time
import shutil
import subprocess
import sys
from pathlib import Path
from datetime import datetime
from typing import Dict, Optional, Set
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

# Enable logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Conversation states
WAITING_FOR_COOKIES = 1
WAITING_FOR_URL = 2
SETTINGS_MENU = 3

class YouTubeDownloaderBot:
    def __init__(self):
        self.config = Config()
        self.base_download_link = self.config.BASE_DOWNLOAD_LINK.rstrip('/')
        
        # Create downloads directory
        os.makedirs(self.config.DOWNLOAD_DIR, exist_ok=True)
        
        # User data storage
        self.user_cookies: Dict[int, str] = {}  # user_id -> cookie_file_path
        self.user_settings: Dict[int, Dict] = {}  # user_id -> settings
        self._last_progress_update = 0  # For progress tracking
        
        # Check yt-dlp version
        self.check_ytdlp_version()
        
        # Load saved data
        self.load_data()
    
    def check_ytdlp_version(self):
        """Check if yt-dlp is up to date"""
        try:
            result = subprocess.run([sys.executable, '-m', 'yt_dlp', '--version'], 
                                  capture_output=True, text=True)
            current_version = result.stdout.strip()
            logger.info(f"Current yt-dlp version: {current_version}")
        except Exception as e:
            logger.warning(f"Could not check yt-dlp version: {e}")
    
    def load_data(self):
        """Load saved user data from JSON files"""
        try:
            if os.path.exists('user_cookies.json'):
                with open('user_cookies.json', 'r') as f:
                    data = json.load(f)
                    self.user_cookies = {int(k): v for k, v in data.items()}
        except Exception as e:
            logger.error(f"Error loading cookies data: {e}")
        
        try:
            if os.path.exists('user_settings.json'):
                with open('user_settings.json', 'r') as f:
                    data = json.load(f)
                    self.user_settings = {int(k): v for k, v in data.items()}
        except Exception as e:
            logger.error(f"Error loading settings data: {e}")
    
    def save_data(self):
        """Save user data to JSON files"""
        try:
            cookies_data = {str(k): v for k, v in self.user_cookies.items()}
            with open('user_cookies.json', 'w') as f:
                json.dump(cookies_data, f, indent=2)
            
            settings_data = {str(k): v for k, v in self.user_settings.items()}
            with open('user_settings.json', 'w') as f:
                json.dump(settings_data, f, indent=2)
        except Exception as e:
            logger.error(f"Error saving data: {e}")
    
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
            "/download - Download a YouTube video\n"
            "/cookies - Upload YouTube cookies\n"
            "/settings - Configure bot settings\n"
            "/help - Show help message\n\n"
            "⚠️ *Important:* You must upload cookies to download videos!\n"
            "🔒 Sharing cookies is dangerous. We're not responsible for any issues."
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
        
        keyboard = [
            [InlineKeyboardButton("📥 Download Video", callback_data='download')],
            [InlineKeyboardButton("🍪 Upload Cookies", callback_data='cookies')],
            [InlineKeyboardButton("⚙️ Settings", callback_data='settings')],
        ]
        
        cookie_status = "✅" if has_cookies else "❌"
        share_method = "📎 Link" if default_share == 'link' else "📤 Direct"
        
        keyboard.append([
            InlineKeyboardButton(f"🍪 Cookies: {cookie_status}", callback_data='cookies_status'),
            InlineKeyboardButton(f"📤 Share: {share_method}", callback_data='share_status')
        ])
        
        return InlineKeyboardMarkup(keyboard)
    
    async def button_handler(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle button clicks"""
        query = update.callback_query
        await query.answer()
        
        if query.data == 'download':
            return await self.ask_for_url(update, context)
        elif query.data == 'cookies':
            return await self.ask_for_cookies(update, context)
        elif query.data == 'settings':
            return await self.show_settings(update, context)
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
    
    async def ask_for_url(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Ask user for YouTube URL"""
        user_id = update.effective_user.id
        
        if not self.is_whitelisted(user_id):
            if update.callback_query:
                await update.callback_query.message.reply_text("⛔ Unauthorized access.")
            else:
                await update.message.reply_text("⛔ Unauthorized access.")
            return ConversationHandler.END
        
        if update.callback_query:
            message = update.callback_query.message
        else:
            message = update.message
        
        await message.reply_text(
            "🔗 Please send me the YouTube video URL you want to download:",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("🔙 Back", callback_data='back_to_main')
            ]])
        )
        return WAITING_FOR_URL
    
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
            
            # Store cookie path
            self.user_cookies[user_id] = cookies_path
            self.save_data()
            
            await update.message.reply_text(
                "✅ Cookies saved successfully!\n"
                "You can now download YouTube videos.",
                reply_markup=self.get_main_keyboard(user_id)
            )
            
            return ConversationHandler.END
            
        except Exception as e:
            logger.error(f"Error saving cookies: {e}")
            await update.message.reply_text(
                "❌ Failed to save cookies. Please try again.",
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton("🔙 Back", callback_data='back_to_main')
                ]])
            )
            return WAITING_FOR_COOKIES
    
    async def handle_url(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle YouTube URL and download video"""
        user_id = update.effective_user.id
        
        if not self.is_whitelisted(user_id):
            await update.message.reply_text("⛔ Unauthorized access.")
            return ConversationHandler.END
        
        if user_id not in self.user_cookies:
            await update.message.reply_text(
                "❌ You need to upload cookies first!\n"
                "Use /cookies command to upload.",
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton("🍪 Upload Cookies", callback_data='cookies')
                ]])
            )
            return ConversationHandler.END
        
        url = update.message.text.strip()
        
        if 'youtube.com' not in url and 'youtu.be' not in url:
            await update.message.reply_text(
                "❌ Please send a valid YouTube URL.\n"
                "Example: https://www.youtube.com/watch?v=...",
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton("🔙 Back", callback_data='back_to_main')
                ]])
            )
            return WAITING_FOR_URL
        
        status_message = await update.message.reply_text(
            "⏳ Initializing download...\n"
            "This may take a few moments."
        )
        
        try:
            video_path, video_title = await self.download_video(
                url, 
                self.user_cookies[user_id], 
                status_message,
                update
            )
            
            if not video_path:
                return ConversationHandler.END
            
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
            logger.error(f"Error in handle_url: {e}")
            await status_message.edit_text(
                f"❌ An error occurred: {str(e)[:200]}\n\n"
                "Please try again or contact support.",
                reply_markup=self.get_main_keyboard(user_id)
            )
        
        return ConversationHandler.END
    
    async def download_video(self, url: str, cookies_file: str, status_message, update) -> tuple:
        """Download YouTube video using yt-dlp with proven working configuration"""
        try:
            output_template = os.path.join(self.config.DOWNLOAD_DIR, '%(title)s.%(ext)s')
            
            # Using the same working configuration from the bash script
            ydl_opts = {
                'format': 'bestvideo[vcodec^=avc1]+bestaudio[acodec^=mp4a]/best[ext=mp4]/best',
                'outtmpl': output_template,
                'cookiefile': cookies_file,
                'quiet': True,
                'no_warnings': True,
                'socket_timeout': 120,
                'retries': 50,
                'fragment_retries': 50,
                'http_chunk_size': 5 * 1024 * 1024,  # 5M
                'throttled_rate': '100K',
                'no_mtime': True,
                'merge_output_format': 'mp4',
                'progress_hooks': [lambda d: self.sync_progress_hook(d)],
            }
            
            await status_message.edit_text("📥 Fetching video information...")
            
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                try:
                    # Extract info and download
                    info = ydl.extract_info(url, download=True)
                    video_title = info.get('title', 'Unknown Title')
                    
                    # Find downloaded file
                    filename = ydl.prepare_filename(info)
                    
                    # Check for merged MP4 file
                    if not os.path.exists(filename):
                        base = os.path.splitext(filename)[0]
                        for ext in ['.mp4', '.webm', '.mkv']:
                            if os.path.exists(base + ext):
                                filename = base + ext
                                break
                    
                    if not os.path.exists(filename):
                        raise FileNotFoundError("Downloaded file not found")
                    
                    file_size = os.path.getsize(filename)
                    await status_message.edit_text(
                        f"✅ Download complete!\n"
                        f"📹 *{video_title[:100]}*\n"
                        f"📦 Size: {file_size / 1024 / 1024:.1f} MB",
                        parse_mode=ParseMode.MARKDOWN
                    )
                    
                    return filename, video_title
                    
                except DownloadError as e:
                    error_msg = str(e)
                    logger.error(f"Download error: {error_msg}")
                    
                    await status_message.edit_text(
                        f"❌ Download failed: {error_msg[:200]}\n\n"
                        f"*Troubleshooting:*\n"
                        f"• Make sure cookies are fresh (logged-in YouTube session)\n"
                        f"• Try again in a few minutes\n"
                        f"• Some videos may have restrictions\n\n"
                        f"Update yt-dlp if issue persists:\n"
                        f"`pip install --upgrade yt-dlp`",
                        parse_mode=ParseMode.MARKDOWN,
                        reply_markup=InlineKeyboardMarkup([
                            [InlineKeyboardButton("🍪 Upload New Cookies", callback_data='cookies')],
                            [InlineKeyboardButton("🔙 Main Menu", callback_data='back_to_main')]
                        ])
                    )
                    return None, None
                    
        except Exception as e:
            logger.error(f"Unexpected error in download_video: {e}")
            await status_message.edit_text(
                f"❌ An unexpected error occurred: {str(e)[:200]}",
                reply_markup=self.get_main_keyboard(update.effective_user.id)
            )
            return None, None
    
    def sync_progress_hook(self, d):
        """Synchronous progress hook for yt-dlp"""
        if d['status'] == 'downloading':
            try:
                percent = d.get('_percent_str', 'N/A').strip()
                speed = d.get('_speed_str', 'N/A').strip()
                eta = d.get('_eta_str', 'N/A').strip()
                
                current_time = time.time()
                if current_time - self._last_progress_update > 5:
                    logger.info(f"Download progress: {percent} at {speed}, ETA: {eta}")
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
            os.remove(video_path)
            
        except Exception as e:
            logger.error(f"Error sending video via Telegram: {e}")
            await status_message.edit_text(
                f"❌ Failed to send video. Trying download link...",
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
            
            download_url = f"{self.base_download_link}/{quote(perm_filename)}"
            
            await status_message.edit_text(
                f"✅ Video downloaded successfully!\n\n"
                f"📹 *Title:* {video_title[:200]}\n"
                f"📥 *Download Link:* [Click here]({download_url})\n\n"
                f"⚠️ Link may expire or file will be removed eventually.\n"
                f"💾 Save the file locally if you want to keep it.",
                parse_mode=ParseMode.MARKDOWN,
                disable_web_page_preview=True,
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("📥 Download", url=download_url)],
                    [InlineKeyboardButton("🔙 Main Menu", callback_data='back_to_main')]
                ])
            )
            
        except Exception as e:
            logger.error(f"Error creating download link: {e}")
            await status_message.edit_text(
                f"❌ Failed to create download link: {str(e)[:200]}",
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
    
    async def help_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Show help message"""
        help_text = (
            "📚 *Help Guide*\n\n"
            "1️⃣ *Upload Cookies*: Required for downloading YouTube videos\n"
            "   • Use browser extension to export cookies\n"
            "   • Send the .txt file to the bot\n\n"
            "2️⃣ *Download Video*: Send a YouTube URL\n"
            "   • Bot will download the video\n"
            "   • Choose between download link or direct upload\n\n"
            "3️⃣ *Settings*: Configure default sharing method\n"
            "   • Download Link: Get a direct download link\n"
            "   • Telegram Upload: Receive video directly (up to 50MB)\n\n"
            "⚠️ *Security Notice*: Cookies are stored locally.\n"
            "We are not responsible for any misuse or security issues.\n\n"
            "📞 Contact admin if you need access."
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
        
        download_conv = ConversationHandler(
            entry_points=[
                CommandHandler('download', self.ask_for_url),
                CallbackQueryHandler(self.ask_for_url, pattern='^download$')
            ],
            states={
                WAITING_FOR_URL: [
                    MessageHandler(filters.TEXT & ~filters.COMMAND, self.handle_url),
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
        app.add_handler(CallbackQueryHandler(self.button_handler, pattern='^(cookies_status|share_status)$'))
        app.add_handler(CallbackQueryHandler(self.show_main_menu, pattern='^back_to_main$'))
        app.add_handler(CallbackQueryHandler(self.handle_settings, pattern='^set_share_'))
        app.add_handler(CallbackQueryHandler(self.show_settings, pattern='^settings$'))
        app.add_handler(cookies_conv)
        app.add_handler(download_conv)
        
        logger.info("Starting bot...")
        logger.info("Bot is ready to receive messages!")
        app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == '__main__':
    bot = YouTubeDownloaderBot()
    bot.run()