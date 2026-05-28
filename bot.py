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
YOUTUBE_URL_PATTERN = re.compile(r'(https?://)?(www\.)?(youtube\.com|youtu\.be)/\S+')

# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------
class VideoRecord:
    __slots__ = ('title', 'url', 'file_path', 'file_size', 'download_time')
    
    def __init__(self, title: str, url: str, file_path: str, file_size: int, download_time: str):
        self.title = title
        self.url = url
        self.file_path = file_path
        self.file_size = file_size
        self.download_time = download_time
    
    def to_dict(self) -> dict:
        return {k: getattr(self, k) for k in self.__slots__}
    
    @classmethod
    def from_dict(cls, data: dict) -> 'VideoRecord':
        return cls(**data)

# ---------------------------------------------------------------------------
# Bot
# ---------------------------------------------------------------------------
class YouTubeDownloaderBot:
    def __init__(self):
        self.config = Config()
        self.base_download_link = self.config.BASE_DOWNLOAD_LINK.rstrip('/')
        
        self._setup_dirs()
        self._update_ytdlp()
        
        self.user_cookies: Dict[int, Path] = {}
        self.user_settings: Dict[int, dict] = {}
        self.user_videos: Dict[int, List[VideoRecord]] = {}
        self._last_progress = 0
        
        self._load_data()
        self._start_cleanup()
    
    def _setup_dirs(self):
        for d in (DATA_DIR, COOKIES_DIR, DOWNLOADS_DIR):
            d.mkdir(parents=True, exist_ok=True)
    
    def _update_ytdlp(self):
        try:
            r = subprocess.run([sys.executable, '-m', 'pip', 'install', '--upgrade', 'yt-dlp'],
                             capture_output=True, text=True, timeout=30)
            logger.info("yt-dlp %s", "updated" if r.returncode == 0 else "update failed")
        except Exception as e:
            logger.warning("yt-dlp update error: %s", e)
    
    def _load_data(self):
        for name, path, storage in [
            ('cookies', 'user_cookies.json', self.user_cookies),
            ('settings', 'user_settings.json', self.user_settings),
            ('videos', 'user_videos.json', self.user_videos),
        ]:
            try:
                fp = DATA_DIR / path
                if fp.exists():
                    data = json.loads(fp.read_text())
                    if name == 'videos':
                        storage.update({int(k): [VideoRecord.from_dict(v) for v in vs] for k, vs in data.items()})
                    else:
                        storage.update({int(k): v for k, v in data.items()})
                    logger.info("Loaded %s for %d users", name, len(storage))
            except Exception as e:
                logger.error("Load %s: %s", name, e)
    
    def _save_data(self):
        data = {
            DATA_DIR / 'user_cookies.json': {str(k): str(v) for k, v in self.user_cookies.items()},
            DATA_DIR / 'user_settings.json': {str(k): v for k, v in self.user_settings.items()},
            DATA_DIR / 'user_videos.json': {str(k): [v.to_dict() for v in vs] for k, vs in self.user_videos.items()},
        }
        for fp, d in data.items():
            try:
                fp.write_text(json.dumps(d, indent=2))
            except Exception as e:
                logger.error("Save %s: %s", fp.name, e)
    
    def _start_cleanup(self):
        def w():
            while True:
                try:
                    self._cleanup()
                except Exception as e:
                    logger.error("Cleanup: %s", e)
                time.sleep(3600)
        threading.Thread(target=w, daemon=True).start()
    
    def _cleanup(self):
        cutoff = datetime.now() - timedelta(days=self.config.STORAGE_DAYS)
        cleaned = 0
        for f in DOWNLOADS_DIR.iterdir():
            if f.is_file() and datetime.fromtimestamp(f.stat().st_mtime) < cutoff:
                f.unlink()
                cleaned += 1
        if cleaned:
            logger.info("Cleaned %d files", cleaned)
        for uid in list(self.user_videos):
            self.user_videos[uid] = [v for v in self.user_videos[uid] if Path(v.file_path).exists()]
            if not self.user_videos[uid]:
                del self.user_videos[uid]
        self._save_data()
    
    def _cookie_path(self, uid: int) -> Path:
        return COOKIES_DIR / f'{uid}.txt'
    
    def _add_video(self, uid: int, title: str, url: str, path: str, size: int):
        if uid not in self.user_videos:
            self.user_videos[uid] = []
        self.user_videos[uid].insert(0, VideoRecord(title, url, path, size, datetime.now().strftime('%Y-%m-%d %H:%M:%S')))
        while len(self.user_videos[uid]) > 20:
            old = self.user_videos[uid].pop()
            p = Path(old.file_path)
            if p.exists():
                p.unlink(missing_ok=True)
        self._save_data()
    
    def _whitelisted(self, uid: int) -> bool:
        wl = self.config.get_whitelist()
        return not wl or uid in wl
    
    def _share_method(self, uid: int) -> str:
        return self.user_settings.get(uid, {}).get('default_share', 'link')
    
    def _extract_url(self, text: str) -> Optional[str]:
        m = YOUTUBE_URL_PATTERN.search(text)
        if m:
            url = m.group(0)
            if url.startswith('www.'): url = 'https://' + url
            elif not url.startswith('http'): url = 'https://' + url
            return url
        return None
    
    @staticmethod
    def _esc_md(text: str) -> str:
        for c in '*_`[]': text = text.replace(c, '\\' + c)
        return text
    
    def _menu(self, uid: int) -> InlineKeyboardMarkup:
        has_c = uid in self.user_cookies
        sm = self._share_method(uid)
        vc = len(self.user_videos.get(uid, []))
        return InlineKeyboardMarkup([
            [InlineKeyboardButton("📹 Recent Videos", callback_data='recent')],
            [InlineKeyboardButton("🍪 Upload Cookies", callback_data='cookies')],
            [InlineKeyboardButton("⚙️ Settings", callback_data='settings')],
            [InlineKeyboardButton(f"🍪 {'✅' if has_c else '❌'}", callback_data='cstat'),
             InlineKeyboardButton(f"📤 {'Link' if sm == 'link' else 'Upload'}", callback_data='sstat')],
            [InlineKeyboardButton(f"📦 Videos: {vc}", callback_data='vcnt')],
        ])
    
    # --- Commands ---
    async def start(self, u: Update, c: ContextTypes.DEFAULT_TYPE):
        if not self._whitelisted(u.effective_user.id):
            await u.message.reply_text("⛔ Unauthorized."); return
        await u.message.reply_text(
            f"👋 Welcome {u.effective_user.first_name}!\n\n"
            "🎥 *YouTube Downloader Bot*\n\n"
            "/start /cookies /recent /settings /help\n\n"
            "💡 Send a YouTube link to download!\n"
            f"🗑️ Files auto-delete after {self.config.STORAGE_DAYS} days.",
            parse_mode=ParseMode.MARKDOWN, reply_markup=self._menu(u.effective_user.id)
        )
    
    async def help(self, u: Update, c: ContextTypes.DEFAULT_TYPE):
        await u.message.reply_text(
            "📚 *Help*\n\n"
            "1. Send YouTube link to download\n"
            "2. /cookies - Upload cookies (required)\n"
            "3. /recent - View downloads\n"
            "4. /settings - Link or upload",
            parse_mode=ParseMode.MARKDOWN, reply_markup=self._menu(u.effective_user.id)
        )
    
    async def recent(self, u: Update, c: ContextTypes.DEFAULT_TYPE):
        await self._show_recent(u, c)
    
    async def cancel(self, u: Update, c: ContextTypes.DEFAULT_TYPE):
        await u.message.reply_text("❌ Cancelled.", reply_markup=self._menu(u.effective_user.id))
        return ConversationHandler.END
    
    # --- Message Handler ---
    async def on_msg(self, u: Update, c: ContextTypes.DEFAULT_TYPE):
        uid = u.effective_user.id
        if not self._whitelisted(uid): return
        
        url = self._extract_url(u.message.text)
        if not url: return
        
        logger.info("User %d download request", uid)
        
        if uid not in self.user_cookies:
            await u.message.reply_text("❌ Upload cookies first! /cookies",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🍪 Upload", callback_data='cookies')]]))
            return
        
        await self._download(uid, url, u)
    
    async def _download(self, uid: int, url: str, update: Update):
        status = await update.message.reply_text("⏳ Downloading...")
        
        try:
            path, title = await self._do_download(uid, url, status)
            if not path: return
            
            size = Path(path).stat().st_size
            self._add_video(uid, title, url, path, size)
            
            if self._share_method(uid) == 'telegram':
                await self._send_video(update, status, path, title, uid)
            else:
                await self._send_link(update, status, path, title, uid)
        except Exception as e:
            logger.error("Download fail user %d: %s", uid, str(e)[:100])
            await status.edit_text("❌ Download failed.", reply_markup=self._menu(uid))
    
    async def _do_download(self, uid: int, url: str, status) -> tuple:
        try:
            # Simple format selection - let yt-dlp pick best available
            opts = {
                'format': 'best',  # Let yt-dlp decide, just get the best
                'outtmpl': str(DOWNLOADS_DIR / '%(title)s.%(ext)s'),
                'cookiefile': str(self.user_cookies[uid]),
                'quiet': True,
                'no_warnings': True,
                'socket_timeout': 120,
                'retries': 50,
                'fragment_retries': 50,
                'http_chunk_size': 5 * 1024 * 1024,
                'throttled_rate': '100K',
                'no_mtime': True,
                'merge_output_format': 'mp4',
                'progress_hooks': [lambda d: self._hook(d)],
            }
            
            await status.edit_text("📥 Downloading...")
            
            with yt_dlp.YoutubeDL(opts) as ydl:
                info = ydl.extract_info(url, download=True)
                title = info.get('title', 'Unknown')
                fp = ydl.prepare_filename(info)
                
                # Find the actual file
                found = None
                if Path(fp).exists():
                    found = fp
                else:
                    base = Path(fp).stem
                    for ext in ('.mp4', '.webm', '.mkv', '.m4a', '.3gp', '.flv'):
                        alt = DOWNLOADS_DIR / f'{base}{ext}'
                        if alt.exists():
                            found = str(alt)
                            break
                
                if not found:
                    # Search by title
                    st = "".join(c for c in title if c.isalnum() or c in (' ', '-', '_')).rstrip()[:50]
                    for f in sorted(DOWNLOADS_DIR.iterdir(), key=lambda x: x.stat().st_mtime, reverse=True):
                        if f.is_file() and st.lower() in f.stem.lower():
                            found = str(f)
                            break
                
                if not found:
                    raise FileNotFoundError(f"Downloaded file not found for: {title}")
                
                mb = Path(found).stat().st_size / 1024 / 1024
                logger.info("Downloaded user %d: %.1f MB", uid, mb)
                await status.edit_text(
                    f"✅ *{self._esc_md(title[:100])}*\n📦 {mb:.1f} MB",
                    parse_mode=ParseMode.MARKDOWN
                )
                return found, title
                
        except DownloadError as e:
            logger.error("yt-dlp user %d: %s", uid, str(e)[:100])
            await status.edit_text(
                "❌ Download failed\n\n"
                "Try: `pip install --upgrade yt-dlp`\n"
                "Or upload fresh cookies.",
                parse_mode=ParseMode.MARKDOWN,
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton("🍪 Cookies", callback_data='cookies'),
                    InlineKeyboardButton("🔙 Menu", callback_data='back')
                ]])
            )
            return None, None
        except Exception as e:
            logger.error("Error user %d: %s", uid, str(e)[:100])
            await status.edit_text("❌ Error.", reply_markup=self._menu(uid))
            return None, None
    
    def _hook(self, d):
        if d['status'] == 'downloading':
            now = time.time()
            if now - self._last_progress > 10:
                logger.info("Download: %s at %s", 
                          d.get('_percent_str', '?').strip(),
                          d.get('_speed_str', '?').strip())
                self._last_progress = now
    
    async def _send_video(self, u, s, path, title, uid):
        try:
            mb = Path(path).stat().st_size / 1024 / 1024
            if mb > self.config.MAX_TELEGRAM_FILE_SIZE:
                await s.edit_text(f"⚠️ Too large ({mb:.1f} MB). Link instead.", reply_markup=self._menu(uid))
                return await self._send_link(u, s, path, title, uid)
            await s.edit_text("📤 Uploading...")
            with open(path, 'rb') as f:
                await u.message.reply_video(video=f, caption=f"📹 {title}", supports_streaming=True)
            await s.delete()
        except Exception as e:
            logger.error("Send video user %d: %s", uid, str(e)[:50])
            await s.edit_text("❌ Upload failed. Link instead.", reply_markup=self._menu(uid))
            await self._send_link(u, s, path, title, uid)
    
    async def _send_link(self, u, s, path, title, uid):
        try:
            sn = "".join(c for c in title if c.isalnum() or c in (' ', '-', '_')).rstrip()
            nn = f"{sn[:50]}_{uid}_{datetime.now():%Y%m%d_%H%M%S}.mp4"
            np = DOWNLOADS_DIR / nn
            shutil.move(path, np)
            for v in self.user_videos.get(uid, []):
                if v.file_path == path:
                    v.file_path = str(np)
                    break
            self._save_data()
            url = f"{self.base_download_link}/{quote(nn)}"
            await s.edit_text(
                f"✅ *{self._esc_md(title[:200])}*\n\n"
                f"[📥 Download]({url})\n\n"
                f"⚠️ Deleted after {self.config.STORAGE_DAYS} days.",
                parse_mode=ParseMode.MARKDOWN,
                disable_web_page_preview=True,
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("📥 Download", url=url)],
                    [InlineKeyboardButton("📹 Recent", callback_data='recent'),
                     InlineKeyboardButton("🔙 Menu", callback_data='back')]
                ])
            )
        except Exception as e:
            logger.error("Link user %d: %s", uid, str(e)[:50])
            await s.edit_text("❌ Failed.", reply_markup=self._menu(uid))
    
    # --- Recent Videos ---
    async def _show_recent(self, u: Update, c: ContextTypes.DEFAULT_TYPE, page: int = 0):
        uid = u.effective_user.id
        msg = u.callback_query.message if u.callback_query else u.message
        videos = self.user_videos.get(uid, [])
        
        if not videos:
            await msg.reply_text("📭 No videos yet.",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Menu", callback_data='back')]]))
            return
        
        pp = 5
        tp = max(1, (len(videos) + pp - 1) // pp)
        page = max(0, min(page, tp - 1))
        pv = videos[page*pp:(page+1)*pp]
        
        txt = f"📹 *Downloads* ({page+1}/{tp})\n\n"
        for i, v in enumerate(pv, page*pp+1):
            ex = "✅" if Path(v.file_path).exists() else "🗑️"
            txt += f"{ex} *{i}.* {self._esc_md(v.title[:50])}\n   📦 {v.file_size/1024/1024:.1f} MB | 🕒 {v.download_time}\n\n"
        txt += f"⚠️ Deleted after {self.config.STORAGE_DAYS} days."
        
        kb = []
        for i, v in enumerate(pv, page*pp+1):
            if Path(v.file_path).exists():
                kb.append([InlineKeyboardButton(f"📥 {i}. {v.title[:30]}", 
                          url=f"{self.base_download_link}/{quote(Path(v.file_path).name)}")])
                kb.append([InlineKeyboardButton(f"🗑️ Delete #{i}", 
                          callback_data=f'del_{page*pp + (i-page*pp-1)}')])
        
        nav = []
        if page > 0: nav.append(InlineKeyboardButton("⬅️", callback_data=f'pg_{page-1}'))
        if page < tp-1: nav.append(InlineKeyboardButton("➡️", callback_data=f'pg_{page+1}'))
        if nav: kb.append(nav)
        kb.append([InlineKeyboardButton("🔙 Menu", callback_data='back')])
        
        await msg.reply_text(txt, parse_mode=ParseMode.MARKDOWN, disable_web_page_preview=True,
                            reply_markup=InlineKeyboardMarkup(kb))
    
    async def _del_video(self, u: Update, c: ContextTypes.DEFAULT_TYPE):
        q = u.callback_query; await q.answer()
        uid = u.effective_user.id
        idx = int(q.data.split('_')[1])
        videos = self.user_videos.get(uid, [])
        if 0 <= idx < len(videos):
            v = videos[idx]
            Path(v.file_path).unlink(missing_ok=True)
            videos.pop(idx)
            self._save_data()
            await q.message.reply_text("🗑️ Deleted.",
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton("📹 Videos", callback_data='recent'),
                    InlineKeyboardButton("🔙 Menu", callback_data='back')
                ]]))
    
    # --- Cookies ---
    async def _ask_cookies(self, u: Update, c: ContextTypes.DEFAULT_TYPE):
        if not self._whitelisted(u.effective_user.id):
            msg = u.callback_query.message if u.callback_query else u.message
            await msg.reply_text("⛔ Unauthorized."); return ConversationHandler.END
        msg = u.callback_query.message if u.callback_query else u.message
        await msg.reply_text(
            "⚠️ *Cookie Warning*\n\nSend cookies.txt file.\nExport from browser extension.",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Cancel", callback_data='back')]])
        )
        return WAITING_FOR_COOKIES
    
    async def _recv_cookies(self, u: Update, c: ContextTypes.DEFAULT_TYPE):
        uid = u.effective_user.id
        if not self._whitelisted(uid):
            await u.message.reply_text("⛔ Unauthorized."); return ConversationHandler.END
        if not u.message.document:
            await u.message.reply_text("❌ Send .txt file.",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Cancel", callback_data='back')]]))
            return WAITING_FOR_COOKIES
        try:
            f = await c.bot.get_file(u.message.document.file_id)
            await f.download_to_drive(str(self._cookie_path(uid)))
            self.user_cookies[uid] = self._cookie_path(uid)
            self._save_data()
            logger.info("User %d cookies saved", uid)
            await u.message.reply_text("✅ Cookies saved!", reply_markup=self._menu(uid))
            return ConversationHandler.END
        except Exception as e:
            logger.error("Cookie save user %d: %s", uid, e)
            await u.message.reply_text("❌ Failed.",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Cancel", callback_data='back')]]))
            return WAITING_FOR_COOKIES
    
    # --- Settings ---
    async def _settings(self, u: Update, c: ContextTypes.DEFAULT_TYPE):
        uid = u.effective_user.id
        if not self._whitelisted(uid): return
        cur = self._share_method(uid)
        msg = u.callback_query.message if u.callback_query else u.message
        await msg.reply_text(
            f"⚙️ *Settings*\nCurrent: {'Link' if cur=='link' else 'Upload'}",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton(f"{'✅' if cur=='link' else '⬜'} Link", callback_data='slink')],
                [InlineKeyboardButton(f"{'✅' if cur=='telegram' else '⬜'} Upload", callback_data='stg')],
                [InlineKeyboardButton("🔙 Menu", callback_data='back')]
            ])
        )
    
    async def _set_setting(self, u: Update, c: ContextTypes.DEFAULT_TYPE):
        q = u.callback_query; await q.answer()
        uid = u.effective_user.id
        v = 'link' if q.data == 'slink' else 'telegram'
        self.user_settings[uid] = {'default_share': v}; self._save_data()
        await q.edit_message_text(
            f"✅ Set to *{'Link' if v=='link' else 'Upload'}*",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Settings", callback_data='settings')]])
        )
    
    # --- Router ---
    async def _router(self, u: Update, c: ContextTypes.DEFAULT_TYPE):
        q = u.callback_query; await q.answer()
        d = q.data
        uid = u.effective_user.id
        
        if d == 'back':
            await q.message.reply_text("📋 Menu:", reply_markup=self._menu(uid))
        elif d == 'recent':
            await self._show_recent(u, c)
        elif d == 'cookies':
            await self._ask_cookies(u, c)
        elif d == 'settings':
            await self._settings(u, c)
        elif d == 'cstat':
            await q.message.reply_text("✅ Ready!" if uid in self.user_cookies else "❌ Use /cookies")
        elif d == 'sstat':
            await q.message.reply_text(f"Method: {'Link' if self._share_method(uid)=='link' else 'Upload'}")
        elif d == 'vcnt':
            await q.message.reply_text(f"📦 {len(self.user_videos.get(uid,[]))} videos")
        elif d.startswith('del_'):
            await self._del_video(u, c)
        elif d.startswith('pg_'):
            await self._show_recent(u, c, int(d.split('_')[1]))
        elif d in ('slink', 'stg'):
            await self._set_setting(u, c)
    
    # --- Run ---
    def run(self):
        app = Application.builder().token(self.config.BOT_TOKEN).build()
        
        cookies_conv = ConversationHandler(
            entry_points=[
                CommandHandler('cookies', self._ask_cookies),
                CallbackQueryHandler(self._ask_cookies, pattern='^cookies$')
            ],
            states={WAITING_FOR_COOKIES: [
                MessageHandler(filters.Document.FileExtension("txt"), self._recv_cookies),
                MessageHandler(filters.TEXT & ~filters.COMMAND, self._ask_cookies),
            ]},
            fallbacks=[
                CommandHandler('cancel', self.cancel),
                CallbackQueryHandler(self._router, pattern='^back$')
            ],
            per_message=False
        )
        
        app.add_handler(CommandHandler('start', self.start))
        app.add_handler(CommandHandler('help', self.help))
        app.add_handler(CommandHandler('recent', self.recent))
        app.add_handler(cookies_conv)
        app.add_handler(CallbackQueryHandler(self._router))
        app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, self.on_msg))
        
        logger.info("Bot starting...")
        app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == '__main__':
    YouTubeDownloaderBot().run()