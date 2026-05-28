#!/usr/bin/env python3
"""
YouTube Downloader Telegram Bot
Downloads YouTube videos and sends them to users or provides download links.
"""

import os
import logging
import json
import tempfile
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
        
        # Load saved data
        self.load_data()
    
    def load_data(self):
        """Load saved user data from JSON files"""
        try:
            if os.path.exists('user_cookies.json'):
                with open('user_cookies.json', 'r') as f:
                    self.user_cookies = json.load(f)
        except Exception as e:
            logger.error(f"Error loading cookies data: {e}")
        
        try:
            if os.path.exists('user_settings.json'):
                with open('user_settings.json', 'r') as f:
                    self.user_settings = json.load(f)
        except Exception as e:
            logger.error(f"Error loading settings data: {e}")
    
    def save_data(self):
        """Save user data to JSON files"""
        try:
            with open('user_cookies.json', 'w') as f:
                json.dump(self.user_cookies, f)
            
            with open('user_settings.json', 'w') as f:
                json.dump(self.user_settings, f)
        except Exception as e:
            logger.error(f"Error saving data: {e}")
    
    def is_whitelisted(self, user_id: int) -> bool:
        """Check if user is whitelisted"""
        whitelist = self.config.get_whitelist()
        if not whitelist:  # Empty whitelist means all users are allowed
            return True
        return user_id in whitelist
    
    def get_default_setting(self, user_id: int) -> str:
        """Get user's default sharing method"""
        if user_id in self.user_settings:
            return self.user_settings[user_id].get('default_share', 'link')
        return 'link'  # Default to link sharing
    
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
            await self.ask_for_url(query.message, context)
        elif query.data == 'cookies':
            await self.ask_for_cookies(query.message, context)
        elif query.data == 'settings':
            await self.show_settings(query.message, context)
        elif query.data == 'back_to_main':
            await self.show_main_menu(query.message, context)
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
    
    async def ask_for_url(self, message, context):
        """Ask user for YouTube URL"""
        if message.from_user and not self.is_whitelisted(message.from_user.id):
            await message.reply_text("⛔ Unauthorized access.")
            return
        
        await message.reply_text(
            "🔗 Please send me the YouTube video URL you want to download:",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("🔙 Back", callback_data='back_to_main')
            ]])
        )
        return WAITING_FOR_URL
    
    async def ask_for_cookies(self, message, context):
        """Ask user to upload cookies file"""
        if message.from_user and not self.is_whitelisted(message.from_user.id):
            await message.reply_text("⛔ Unauthorized access.")
            return
        
        warning_text = (
            "⚠️ *WARNING: COOKIE SECURITY NOTICE*\n\n"
            "• Cookies contain sensitive login information\n"
            "• Sharing cookies can give others access to your accounts\n"
            "• We store cookies locally and only use them for YouTube downloads\n"
            "• *We are NOT responsible* for any security issues or account compromises\n"
            "• Use at your own risk!\n\n"
            "📤 Please send your cookies file (.txt) now.\n"
            "Export from browser extension like 'Get cookies.txt LOCALLY'"
        )
        
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
                "❌ Please send a valid cookies file (.txt)",
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton("🔙 Back", callback_data='back_to_main')
                ]])
            )
            return WAITING_FOR_COOKIES
        
        # Download the file
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
    
    async def handle_url(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle YouTube URL and download video"""
        user_id = update.effective_user.id
        
        if not self.is_whitelisted(user_id):
            await update.message.reply_text("⛔ Unauthorized access.")
            return ConversationHandler.END
        
        # Check for cookies
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
                "❌ Please send a valid YouTube URL.",
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton("🔙 Back", callback_data='back_to_main')
                ]])
            )
            return WAITING_FOR_URL
        
        # Send processing message
        status_message = await update.message.reply_text("⏳ Processing your request...")
        
        try:
            # Download the video
            video_path, video_title = await self.download_video(
                url, 
                self.user_cookies[user_id], 
                status_message
            )
            
            if not video_path:
                await status_message.edit_text(
                    "❌ Failed to download video. Please check the URL and try again.",
                    reply_markup=self.get_main_keyboard(user_id)
                )
                return ConversationHandler.END
            
            # Check default sharing method
            default_share = self.get_default_setting(user_id)
            
            # Send based on preference
            if default_share == 'telegram':
                await self.send_video_telegram(
                    update, status_message, video_path, video_title, user_id
                )
            else:
                await self.send_download_link(
                    update, status_message, video_path, video_title, user_id
                )
            
        except Exception as e:
            logger.error(f"Error downloading video: {e}")
            await status_message.edit_text(
                f"❌ An error occurred: {str(e)}",
                reply_markup=self.get_main_keyboard(user_id)
            )
        
        return ConversationHandler.END
    
    async def download_video(self, url: str, cookies_file: str, status_message) -> tuple:
        """Download YouTube video using yt-dlp"""
        try:
            output_template = os.path.join(self.config.DOWNLOAD_DIR, '%(title)s.%(ext)s')
            
            ydl_opts = {
                'format': 'best[ext=mp4]/best',
                'outtmpl': output_template,
                'cookiefile': cookies_file,
                'quiet': True,
                'no_warnings': True,
                'progress_hooks': [lambda d: self.progress_hook(d, status_message)],
            }
            
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                await status_message.edit_text("📥 Downloading video information...")
                info = ydl.extract_info(url, download=False)
                video_title = info.get('title', 'Unknown Title')
                
                await status_message.edit_text(f"📥 Downloading: {video_title[:50]}...")
                ydl.download([url])
                
                # Find downloaded file
                filename = ydl.prepare_filename(info)
                
                # Check if file exists (might have different extension)
                if not os.path.exists(filename):
                    base = os.path.splitext(filename)[0]
                    for ext in ['.mp4', '.webm', '.mkv']:
                        if os.path.exists(base + ext):
                            filename = base + ext
                            break
                
                return filename, video_title
                
        except Exception as e:
            logger.error(f"Download error: {e}")
            return None, None
    
    def progress_hook(self, d, status_message):
        """Update progress in status message"""
        if d['status'] == 'downloading':
            try:
                percent = d.get('_percent_str', 'N/A')
                speed = d.get('_speed_str', 'N/A')
                eta = d.get('_eta_str', 'N/A')
                
                # Update message periodically (not every hook call to avoid rate limiting)
                if hasattr(self, '_last_progress_update'):
                    import time
                    if time.time() - self._last_progress_update < 5:
                        return
                
                self._last_progress_update = time.time()
                
                # We can't await here, so we'll skip live progress updates
                # The message will be updated at download completion
            except:
                pass
    
    async def send_video_telegram(self, update, status_message, video_path, video_title, user_id):
        """Send video directly via Telegram"""
        try:
            # Check file size
            file_size = os.path.getsize(video_path)
            
            if file_size > self.config.MAX_TELEGRAM_FILE_SIZE:
                await status_message.edit_text(
                    f"⚠️ File is too large for Telegram ({file_size / 1024 / 1024:.1f} MB)\n"
                    f"Max size: {self.config.MAX_TELEGRAM_FILE_SIZE / 1024 / 1024:.1f} MB\n"
                    "Switching to download link...",
                    reply_markup=self.get_main_keyboard(user_id)
                )
                await self.send_download_link(update, status_message, video_path, video_title, user_id)
                return
            
            await status_message.edit_text("📤 Uploading to Telegram...")
            
            # Send video
            with open(video_path, 'rb') as video_file:
                await update.message.reply_video(
                    video=video_file,
                    caption=f"📹 {video_title}",
                    supports_streaming=True
                )
            
            await status_message.delete()
            
            # Clean up
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
            # Create a permanent copy in downloads folder
            perm_filename = f"video_{user_id}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.mp4"
            perm_path = os.path.join(self.config.DOWNLOAD_DIR, perm_filename)
            
            os.rename(video_path, perm_path)
            
            # Generate download link
            download_url = f"{self.base_download_link}/downloads/{quote(perm_filename)}"
            
            await status_message.edit_text(
                f"✅ Video downloaded successfully!\n\n"
                f"📹 *Title:* {video_title}\n"
                f"📥 *Download Link:* [Click here]({download_url})\n\n"
                f"⚠️ Link will expire or file will be removed eventually.",
                parse_mode=ParseMode.MARKDOWN,
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("📥 Download", url=download_url)],
                    [InlineKeyboardButton("🔙 Main Menu", callback_data='back_to_main')]
                ])
            )
            
            # Schedule file cleanup after 24 hours (optional)
            # You can implement a cleanup mechanism here
            
        except Exception as e:
            logger.error(f"Error creating download link: {e}")
            await status_message.edit_text(
                f"❌ Failed to create download link: {str(e)}",
                reply_markup=self.get_main_keyboard(user_id)
            )
    
    async def show_settings(self, message, context):
        """Show settings menu"""
        user_id = message.from_user.id if hasattr(message, 'from_user') else message.chat.id
        
        if not self.is_whitelisted(user_id):
            await message.reply_text("⛔ Unauthorized access.")
            return
        
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
    
    async def show_main_menu(self, message, context):
        """Show main menu"""
        user_id = message.from_user.id if hasattr(message, 'from_user') else message.chat.id
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
    
    def run(self):
        """Start the bot"""
        app = Application.builder().token(self.config.BOT_TOKEN).build()
        
        # Conversation handler for cookies upload
        cookies_conv = ConversationHandler(
            entry_points=[
                CommandHandler('cookies', self.ask_for_cookies),
                CallbackQueryHandler(self.ask_for_cookies, pattern='^cookies$')
            ],
            states={
                WAITING_FOR_COOKIES: [
                    MessageHandler(filters.Document.FileExtension("txt"), self.handle_cookies),
                    MessageHandler(filters.ALL, self.ask_for_cookies)
                ]
            },
            fallbacks=[CommandHandler('cancel', self.start)]
        )
        
        # Conversation handler for URL download
        download_conv = ConversationHandler(
            entry_points=[
                CommandHandler('download', self.ask_for_url),
                CallbackQueryHandler(self.ask_for_url, pattern='^download$')
            ],
            states={
                WAITING_FOR_URL: [
                    MessageHandler(filters.TEXT & ~filters.COMMAND, self.handle_url),
                    MessageHandler(filters.ALL, self.ask_for_url)
                ]
            },
            fallbacks=[CommandHandler('cancel', self.start)]
        )
        
        # Add handlers
        app.add_handler(CommandHandler('start', self.start))
        app.add_handler(CommandHandler('help', self.help_command))
        app.add_handler(CallbackQueryHandler(self.button_handler, pattern='^(cookies_status|share_status)$'))
        app.add_handler(CallbackQueryHandler(self.show_main_menu, pattern='^back_to_main$'))
        app.add_handler(CallbackQueryHandler(self.handle_settings, pattern='^set_share_'))
        app.add_handler(CallbackQueryHandler(self.show_settings, pattern='^settings$'))
        app.add_handler(cookies_conv)
        app.add_handler(download_conv)
        
        # Start polling
        logger.info("Starting bot...")
        app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == '__main__':
    bot = YouTubeDownloaderBot()
    bot.run()