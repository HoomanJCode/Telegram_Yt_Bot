#!/usr/bin/env python3
"""
YouTube Downloader Telegram Bot
- Short unique tokens for shared links
- Inline downloads only start on user click
- Cookies auto-restore from Telegram on demand
"""

import os, sys, logging, json, time, shutil, re, subprocess, asyncio, tempfile, secrets
from pathlib import Path
from datetime import datetime, timedelta
from typing import Dict, Optional, List, Tuple
from urllib.parse import quote
from uuid import uuid4

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram import InlineQueryResultArticle, InputTextMessageContent
from telegram import InlineQueryResultCachedVideo, InlineQueryResultCachedAudio, InlineQueryResultCachedPhoto
from telegram.ext import (
    Application, CommandHandler, MessageHandler, CallbackQueryHandler,
    ConversationHandler, filters, ContextTypes, InlineQueryHandler
)
from telegram.constants import ParseMode, ChatType
import yt_dlp
from aiohttp import web

from config import Config

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
DATA_DIR = Path('data')
DOWNLOADS_DIR = Path('downloads')
WAITING_FOR_COOKIES = 1
YOUTUBE_RE = re.compile(r'(https?://)?(www\.)?(youtube\.com|youtu\.be)/\S+')
NAV_MAIN = 'main'
NAV_RECENT = 'recent'
NAV_FORMAT = 'format'
NAV_DELIVERY = 'delivery'

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
        await response.prepare(request)
        try:
            with open(filepath, 'rb') as f:
                while chunk := f.read(1024 * 1024):
                    await response.write(chunk)
        except (ConnectionResetError, BrokenPipeError, ConnectionAbortedError):
            pass
        return response

    def _get_mime(self, ext):
        return {
            '.mp4': 'video/mp4', '.webm': 'video/webm', '.mkv': 'video/x-matroska',
            '.mp3': 'audio/mpeg', '.m4a': 'audio/mp4', '.opus': 'audio/opus',
            '.jpg': 'image/jpeg', '.png': 'image/png', '.webp': 'image/webp'
        }.get(ext.lower(), 'application/octet-stream')

    async def start(self):
        self._runner = web.AppRunner(self.app)
        await self._runner.setup()
        await web.TCPSite(self._runner, '0.0.0.0', self.port).start()
        logger.info("File server on port %d", self.port)

# ---------------------------------------------------------------------------
class VideoRecord:
    __slots__ = ('title', 'url', 'video_id', 'file_path', 'file_size', 'download_time', 'telegram_file_id', 'media_type')
    def __init__(self, title, url, video_id, file_path, file_size, download_time, telegram_file_id=None, media_type='video'):
        self.title = title; self.url = url; self.video_id = video_id; self.file_path = file_path
        self.file_size = file_size; self.download_time = download_time
        self.telegram_file_id = telegram_file_id; self.media_type = media_type
    def to_dict(self): return {k: getattr(self, k) for k in self.__slots__}
    @classmethod
    def from_dict(cls, d): return cls(**d)

# ---------------------------------------------------------------------------
class YouTubeDownloaderBot:
    def __init__(self):
        self.config = Config()
        self.base_url = self.config.BASE_DOWNLOAD_LINK.rstrip('/')
        try: port = int(self.base_url.split(':')[-1]) if ':' in self.base_url.split('/')[2] else 8000
        except: port = 8000
        for d in (DATA_DIR, DOWNLOADS_DIR): d.mkdir(parents=True, exist_ok=True)
        self._cookie_file_ids: Dict[int, str] = {}
        self._cookie_data: Dict[int, bytes] = {}
        self._cookie_tmpfiles: Dict[int, str] = {}
        self._bot = None; self._bot_username = None
        self.videos: Dict[int, List[VideoRecord]] = {}
        self._pending_urls: Dict[int, tuple] = {}
        self._nav_stack: Dict[int, List[Tuple[str, any]]] = {}
        self._tokens: Dict[str, dict] = {}          # short_token -> download info
        self._group_admins: Dict[int, set] = {}
        self._download_semaphore = asyncio.Semaphore(1)
        self.has_ffmpeg = self._check_ffmpeg()
        self.file_server = FileServer(port=port)
        self._load()

    def _check_ffmpeg(self):
        try: subprocess.run(['ffmpeg', '-version'], capture_output=True, timeout=5); return True
        except: return False

    def _load(self):
        try:
            fp = DATA_DIR / 'user_videos.json'
            if fp.exists(): self.videos = {int(k): [VideoRecord.from_dict(v) for v in vs] for k, vs in json.loads(fp.read_text()).items()}
        except Exception as e: logger.error("Load videos: %s", e)
        try:
            fp = DATA_DIR / 'cookie_file_ids.json'
            if fp.exists(): self._cookie_file_ids = {int(k): v for k, v in json.loads(fp.read_text()).items()}
        except Exception as e: logger.error("Load cookie IDs: %s", e)

    def _save(self):
        try: (DATA_DIR / 'user_videos.json').write_text(json.dumps({str(k): [v.to_dict() for v in vs] for k, vs in self.videos.items()}, indent=2))
        except Exception as e: logger.error("Save videos: %s", e)
        try: (DATA_DIR / 'cookie_file_ids.json').write_text(json.dumps({str(k): v for k, v in self._cookie_file_ids.items()}, indent=2))
        except Exception as e: logger.error("Save cookie IDs: %s", e)

    async def _get_bot_username(self):
        if not self._bot_username and self._bot: me = await self._bot.get_me(); self._bot_username = me.username
        return self._bot_username

    async def _load_cookies_from_telegram(self, uid: int) -> bool:
        if uid not in self._cookie_file_ids or not self._bot: return False
        try:
            file = await self._bot.get_file(self._cookie_file_ids[uid])
            cookie_bytes = await file.download_as_bytearray()
            self._cookie_data[uid] = bytes(cookie_bytes)
            if uid in self._cookie_tmpfiles:
                try: os.unlink(self._cookie_tmpfiles[uid]); del self._cookie_tmpfiles[uid]
                except: pass
            logger.info("Cookies restored for user %d", uid)
            return True
        except Exception as e:
            logger.warning("Cookie restore fail %d: %s", uid, str(e)[:50])
            del self._cookie_file_ids[uid]; self._save()
            return False

    async def _ensure_cookies(self, uid: int) -> bool:
        if uid in self._cookie_data: return True
        if uid in self._cookie_file_ids: return await self._load_cookies_from_telegram(uid)
        return False

    def _get_cookie_file(self, uid):
        if uid not in self._cookie_tmpfiles or not os.path.exists(self._cookie_tmpfiles[uid]):
            tmp = tempfile.NamedTemporaryFile(mode='w', suffix='.txt', delete=False)
            tmp.write(self._cookie_data[uid].decode('utf-8', errors='replace')); tmp.close()
            self._cookie_tmpfiles[uid] = tmp.name
        return self._cookie_tmpfiles[uid]

    def _is_private(self, msg): return msg.chat.type == ChatType.PRIVATE
    def _is_group(self, msg): return msg.chat.type in (ChatType.GROUP, ChatType.SUPERGROUP)
    def _ok(self, uid): return not self.config.get_whitelist() or uid in self.config.get_whitelist()

    async def _check_group_allowed(self, chat_id, bot) -> bool:
        if chat_id in self._group_admins and self._group_admins[chat_id]: return True
        try:
            admins = await bot.get_chat_administrators(chat_id)
            self._group_admins[chat_id] = {a.user.id for a in admins if self._ok(a.user.id)}
            return bool(self._group_admins[chat_id])
        except: return False

    def _extract_url(self, text):
        m = YOUTUBE_RE.search(text)
        if m:
            u = m.group(0)
            if u.startswith('www.'): u = 'https://' + u
            elif not u.startswith('http'): u = 'https://' + u
            return u
        return None

    def _extract_video_id(self, url):
        for p in [r'(?:youtube\.com/watch\?v=|youtu\.be/|youtube\.com/embed/|youtube\.com/v/)([a-zA-Z0-9_-]{11})', r'youtube\.com/shorts/([a-zA-Z0-9_-]{11})']:
            m = re.search(p, url)
            if m: return m.group(1)
        return None

    def _find_existing(self, uid, video_id, media_type='video'):
        for v in self.videos.get(uid, []):
            if v.video_id == video_id and v.media_type == media_type and Path(v.file_path).exists(): return v
        return None

    @staticmethod
    def _esc(text):
        for c in '*_`[]': text = text.replace(c, '\\' + c)
        return text

    def _nav_push(self, uid, action, data=None):
        if uid not in self._nav_stack: self._nav_stack[uid] = []
        self._nav_stack[uid].append((action, data))

    def _nav_pop(self, uid):
        if uid in self._nav_stack and self._nav_stack[uid]: return self._nav_stack[uid].pop()
        return (NAV_MAIN, None)

    async def _welcome_text(self):
        username = await self._get_bot_username() or "botname"
        return (f"👋 Welcome!\n\n🎥 YouTube Downloader Bot\n\n"
                f"💡 Send YouTube link → Choose format → Download!\n"
                f"📱 Inline: @{username} <link>\n"
                f"👥 Groups: Send link, get video\n"
                f"🗑️ Files deleted after {self.config.STORAGE_DAYS}d.\n\n"
                f"🔒 Cookies: RAM only, auto-restore.")

    def _menu(self, uid):
        has = uid in self._cookie_data
        vc = len(self.videos.get(uid, []))
        return InlineKeyboardMarkup([
            [InlineKeyboardButton("📹 Recent Downloads", callback_data='r')],
            [InlineKeyboardButton("🍪 Upload Cookies", callback_data='c')],
            [InlineKeyboardButton(f"🍪 {'✅' if has else '❌'}", callback_data='cs'),
             InlineKeyboardButton(f"📦 {vc} files", callback_data='vc')],
        ])

    def _format_choice_keyboard(self, uid, video_id):
        existing = {v.media_type for v in self.videos.get(uid, []) if v.video_id == video_id and Path(v.file_path).exists()}
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
        kb.append([InlineKeyboardButton("🔙 Back", callback_data='b')])
        return InlineKeyboardMarkup(kb)

    def _delivery_keyboard(self, uid, idx=None):
        idx_str = str(idx) if idx is not None else 'new'
        return InlineKeyboardMarkup([
            [InlineKeyboardButton("📤 Send via Telegram", callback_data=f'tg_{idx_str}')],
            [InlineKeyboardButton("📋 Get Download Link", callback_data=f'lk_{idx_str}')],
            [InlineKeyboardButton("🔙 Back to formats", callback_data=f'backfmt_{idx_str}')],
        ])

    def _group_delivery_keyboard(self, uid, idx=None):
        idx_str = str(idx) if idx is not None else 'new'
        return InlineKeyboardMarkup([
            [InlineKeyboardButton("📤 Send via Telegram", callback_data=f'tg_{idx_str}')],
            [InlineKeyboardButton("📋 Get Download Link", callback_data=f'lk_{idx_str}')],
        ])

    # --- Sync helpers ---
    def _sync_fetch_info(self, uid, url):
        opts = {'format': 'best', 'cookiefile': self._get_cookie_file(uid), 'quiet': True, 'no_warnings': True, 'socket_timeout': 30, 'retries': 3}
        with yt_dlp.YoutubeDL(opts) as ydl: return ydl.extract_info(url, download=False)

    def _sync_download(self, uid, url, media_type):
        base_opts = {'cookiefile': self._get_cookie_file(uid), 'quiet': True, 'no_warnings': True, 'socket_timeout': 120, 'retries': 50, 'fragment_retries': 50, 'concurrent_fragment_downloads': 2, 'no_mtime': True}
        if media_type == 'video':
            opts = {**base_opts, 'format': 'best[ext=mp4]/best', 'outtmpl': str(DOWNLOADS_DIR / '%(id)s_v.%(ext)s'), 'merge_output_format': 'mp4'}
        else:
            opts = {**base_opts, 'format': 'bestaudio[ext=m4a]/bestaudio', 'outtmpl': str(DOWNLOADS_DIR / '%(id)s_a.%(ext)s')}
            if self.has_ffmpeg: opts['postprocessors'] = [{'key': 'FFmpegExtractAudio', 'preferredcodec': 'mp3', 'preferredquality': '192'}]
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=True); title, vid = info.get('title', 'Unknown'), info.get('id', '')
            fp = ydl.prepare_filename(info)
            if media_type == 'audio' and self.has_ffmpeg: fp = str(Path(fp).with_suffix('.mp3'))
            if Path(fp).exists(): return fp, title, vid
            for ext in ('.mp4', '.mp3', '.m4a', '.webm', '.mkv', '.opus'):
                alt = DOWNLOADS_DIR / f'{Path(fp).stem}{ext}'
                if alt.exists(): return str(alt), title, vid
            for f in DOWNLOADS_DIR.iterdir():
                if f.is_file() and f.stem.startswith(vid): return str(f), title, vid
            raise FileNotFoundError(title)

    def _sync_download_thumb(self, uid, url):
        opts = {'cookiefile': self._get_cookie_file(uid), 'quiet': True, 'no_warnings': True, 'socket_timeout': 30, 'retries': 3, 'skip_download': True, 'writethumbnail': True, 'outtmpl': str(DOWNLOADS_DIR / '%(id)s_thumb.%(ext)s')}
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=False); title, vid = info.get('title', 'Unknown'), info.get('id', ''); ydl.download([url])
            for ext in ('.jpg', '.webp', '.png'):
                fp = DOWNLOADS_DIR / f'{vid}_thumb{ext}'
                if fp.exists(): return str(fp), title, vid
            for t in info.get('thumbnails', []):
                if t.get('url'):
                    import urllib.request
                    ext = t['url'].split('?')[0].split('.')[-1] or 'jpg'
                    fp = DOWNLOADS_DIR / f'{vid}_thumb.{ext}'; urllib.request.urlretrieve(t['url'], str(fp)); return str(fp), title, vid
            raise FileNotFoundError("No thumbnail")

    # --- Token-based shared download ---
    async def _do_shared_download(self, token: str):
        """Perform download for a token, limited by semaphore"""
        async with self._download_semaphore:
            req = self._tokens.get(token)
            if not req: return
            req['status'] = 'downloading'
            uid, url, media_type = req['uid'], req['url'], req['media_type']
            try:
                if media_type == 'thumb':
                    fp, title, vid = await asyncio.get_event_loop().run_in_executor(None, self._sync_download_thumb, uid, url)
                else:
                    fp, title, vid = await asyncio.get_event_loop().run_in_executor(None, self._sync_download, uid, url, media_type)
                req['status'] = 'completed'; req['file_path'] = fp; req['title'] = title
                sz = Path(fp).stat().st_size
                record = VideoRecord(title, url, vid, fp, sz, datetime.now().strftime('%Y-%m-%d %H:%M:%S'), media_type=media_type)
                self.videos.setdefault(uid, []).insert(0, record)
                while len(self.videos.get(uid, [])) > 20: old = self.videos[uid].pop(); Path(old.file_path).unlink(missing_ok=True)
                self._save()
            except Exception as e:
                logger.error("Shared dl %s: %s", token, str(e)[:100])
                req['status'] = 'failed'; req['error'] = str(e)[:200]

    def _make_token(self, uid, url, video_id, media_type, existing=None):
        """Create a short token for a download request"""
        token = secrets.token_hex(4)  # 8 chars
        self._tokens[token] = {
            'uid': uid, 'url': url, 'media_type': media_type,
            'status': 'completed' if existing and Path(existing.file_path).exists() else 'pending',
            'file_path': existing.file_path if existing and Path(existing.file_path).exists() else None,
            'title': existing.title if existing else None,
            'created_at': time.time()
        }
        return token

    # --- Inline Query ---
    async def _inline_query(self, u: Update, c: ContextTypes.DEFAULT_TYPE):
        try:
            query = u.inline_query.query.strip()
            if not query: return
            url = self._extract_url(query)
            if not url: return
            uid = u.effective_user.id
            if not self._ok(uid): return
            if not await self._ensure_cookies(uid):
                await u.inline_query.answer([], switch_pm_text="Upload cookies first", switch_pm_parameter="cookies")
                return

            video_id = self._extract_video_id(url)
            bot_username = await self._get_bot_username()
            results = []

            for media_type, emoji, label in [
                ('video', '🎬', 'Video (MP4)'), ('audio', '🎵', f"Audio ({'MP3' if self.has_ffmpeg else 'M4A'})"), ('thumb', '🖼️', 'Thumbnail')
            ]:
                existing = self._find_existing(uid, video_id, media_type)

                # Already cached with file_id -> instant inline
                if existing and existing.telegram_file_id:
                    try:
                        if media_type == 'video': results.append(InlineQueryResultCachedVideo(id=str(uuid4()), video_file_id=existing.telegram_file_id, title=existing.title, description=f"📦 {existing.file_size/1024/1024:.1f} MB"))
                        elif media_type == 'audio': results.append(InlineQueryResultCachedAudio(id=str(uuid4()), audio_file_id=existing.telegram_file_id, title=existing.title))
                        else: results.append(InlineQueryResultCachedPhoto(id=str(uuid4()), photo_file_id=existing.telegram_file_id, title=existing.title))
                        continue
                    except: pass

                # Create token for this format (cached or new)
                token = self._make_token(uid, url, video_id, media_type, existing)

                if existing and Path(existing.file_path).exists():
                    results.append(InlineQueryResultArticle(
                        id=str(uuid4()), title=f"{emoji} {label} - Ready",
                        description=f"Click to receive: {existing.title[:50]}",
                        input_message_content=InputTextMessageContent(f"{emoji} {existing.title}"),
                        reply_markup=InlineKeyboardMarkup([[
                            InlineKeyboardButton("📥 Get File", url=f"https://t.me/{bot_username}?start=dl_{token}")
                        ]])
                    ))
                else:
                    results.append(InlineQueryResultArticle(
                        id=str(uuid4()),
                        title=f"{emoji} Download {label}",
                        description="Click to start download & receive",
                        input_message_content=InputTextMessageContent(
                            f"⏳ Click the button below to start downloading {label}.\n"
                            f"🔄 The file will be sent when ready."
                        ),
                        reply_markup=InlineKeyboardMarkup([[
                            InlineKeyboardButton("📥 Start Download", url=f"https://t.me/{bot_username}?start=dl_{token}")
                        ]])
                    ))

            await u.inline_query.answer(results, cache_time=0)
        except Exception as e: logger.warning("Inline: %s", str(e)[:100])

    # --- Handle /start dl_TOKEN ---
    async def _handle_token_start(self, uid, param, msg):
        bot_username = await self._get_bot_username()
        token = param[3:] if param.startswith('dl_') else param
        req = self._tokens.get(token)

        if not req:
            # Try searching all videos by video_id if token looks like one (11 chars)
            if len(token) == 11:
                for user_videos in self.videos.values():
                    for v in user_videos:
                        if v.video_id == token and Path(v.file_path).exists():
                            await self._send_file_direct(msg, v)
                            return
            await msg.reply_text("❌ Download request expired.\nPlease try again from inline mode.")
            return

        if req['status'] == 'completed' and req['file_path'] and Path(req['file_path']).exists():
            await self._send_file_direct(msg, req)
        elif req['status'] == 'pending':
            await msg.reply_text("⏳ Starting download...",
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton("🔄 Check Progress", url=f"https://t.me/{bot_username}?start=dl_{token}")
                ]]))
            asyncio.create_task(self._do_shared_download(token))
        elif req['status'] == 'downloading':
            await msg.reply_text("⏳ Still downloading...",
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton("🔄 Try Again", url=f"https://t.me/{bot_username}?start=dl_{token}")
                ]]))
        elif req['status'] == 'failed':
            await msg.reply_text("❌ Download failed.\nPlease try again from inline mode.")

    async def _send_file_direct(self, msg, record_or_req):
        if isinstance(record_or_req, VideoRecord):
            fp, title, media_type = record_or_req.file_path, record_or_req.title, record_or_req.media_type
        else:
            fp = record_or_req.get('file_path')
            title = record_or_req.get('title', 'Unknown')
            media_type = record_or_req.get('media_type', 'video')
        if not fp or not Path(fp).exists():
            await msg.reply_text("❌ File deleted from server."); return
        mb = Path(fp).stat().st_size / 1024 / 1024
        if mb > self.config.MAX_TELEGRAM_FILE_SIZE:
            await msg.reply_text(f"⚠️ Too large ({mb:.1f}MB)\n📥 `{self.base_url}/{quote(Path(fp).name)}`", parse_mode=ParseMode.MARKDOWN); return
        s = await msg.reply_text("📤 Sending...")
        try:
            with open(fp, 'rb') as f:
                if media_type == 'thumb': await msg.reply_photo(photo=f, caption=f"🖼️ {title}")
                elif media_type == 'audio': await msg.reply_audio(audio=f, title=title, performer="YouTube")
                else: await msg.reply_video(video=f, caption=f"🎬 {title}", supports_streaming=True)
            await s.delete()
        except Exception as e: logger.error("Send: %s", str(e)[:50]); await s.edit_text("❌ Failed.")

    # --- Commands ---
    async def start_cmd(self, u, c):
        uid = u.effective_user.id; args = c.args
        if args and args[0].startswith('dl_'): await self._handle_token_start(uid, args[0], u.message); return
        if not self._ok(uid): await u.message.reply_text("⛔"); return
        self._nav_stack.pop(uid, None)
        await u.message.reply_text(await self._welcome_text(), reply_markup=self._menu(uid))

    async def help_cmd(self, u, c):
        await u.message.reply_text("📚 Send YouTube link.\n📱 Inline: @botname <link>\n/cookies /recent", reply_markup=self._menu(u.effective_user.id))

    async def recent_cmd(self, u, c):
        self._nav_stack.pop(u.effective_user.id, None); await self._show_recent(u, c)

    async def cancel_cmd(self, u, c):
        self._nav_stack.pop(u.effective_user.id, None)
        await u.message.reply_text("❌ Cancelled.", reply_markup=self._menu(u.effective_user.id))
        return ConversationHandler.END

    # --- Message Handler ---
    async def on_msg(self, u, c):
        uid = u.effective_user.id; msg = u.message
        is_private = self._is_private(msg); is_group = self._is_group(msg)
        if is_private and not self._ok(uid): return
        url = self._extract_url(msg.text)
        if not url: return
        video_id = self._extract_video_id(url)
        if not video_id:
            if is_private: await msg.reply_text("❌ Invalid URL."); return

        if is_group:
            if not await self._check_group_allowed(msg.chat_id, c.bot): return
            if not await self._ensure_cookies(uid): return
            async with self._download_semaphore:
                await self._group_download(uid, url, msg, 'video', video_id)
            return

        if not await self._ensure_cookies(uid):
            await msg.reply_text("❌ Upload cookies first! /cookies"); return
        self._nav_stack.pop(uid, None); await self._show_format_choice(uid, url, video_id, msg)

    async def _group_download(self, uid, url, msg, media_type, video_id):
        try:
            existing = self._find_existing(uid, video_id, media_type)
            if existing: fp, title = existing.file_path, existing.title
            else:
                fp, title, vid = await asyncio.get_event_loop().run_in_executor(None, self._sync_download, uid, url, media_type)
                sz = Path(fp).stat().st_size
                record = VideoRecord(title, url, vid, fp, sz, datetime.now().strftime('%Y-%m-%d %H:%M:%S'), media_type=media_type)
                self.videos.setdefault(uid, []).insert(0, record)
                while len(self.videos.get(uid, [])) > 20: old = self.videos[uid].pop(); Path(old.file_path).unlink(missing_ok=True)
                self._save()
            mb = Path(fp).stat().st_size / 1024 / 1024
            await msg.reply_text(f"✅ *{self._esc(title[:200])}*\n📦 {mb:.2f} MB\n\nChoose delivery:", parse_mode=ParseMode.MARKDOWN, reply_markup=self._group_delivery_keyboard(uid, 0), reply_to_message_id=msg.message_id)
        except Exception as e: logger.error("Group dl %d: %s", uid, str(e)[:100])

    async def _show_format_choice(self, uid, url, video_id, msg):
        s = await msg.reply_text("🔍 Fetching info...")
        try:
            info = await asyncio.get_event_loop().run_in_executor(None, self._sync_fetch_info, uid, url)
            title, duration = info.get('title', '?'), info.get('duration', 0)
            self._pending_urls[uid] = (url, video_id, title)
            mins, secs = divmod(duration, 60) if duration else (0, 0)
            await s.edit_text(f"📹 *{self._esc(title[:200])}*\n⏱ {mins}:{secs:02d}\n\nChoose format:", parse_mode=ParseMode.MARKDOWN, reply_markup=self._format_choice_keyboard(uid, video_id))
        except Exception as e: logger.error("Info %d: %s", uid, str(e)[:100]); await s.edit_text("❌ Failed.", reply_markup=self._menu(uid))

    async def _choose_format(self, u, c):
        q = u.callback_query; await q.answer(); uid, fmt = u.effective_user.id, q.data
        if uid not in self._pending_urls: return
        url, video_id, _ = self._pending_urls[uid]
        for media_type, fmt_key in [('video', 'fmt_video'), ('audio', 'fmt_audio'), ('thumb', 'fmt_thumb')]:
            if fmt == fmt_key:
                existing = self._find_existing(uid, video_id, media_type)
                if existing: await q.answer("Already downloaded!"); await self._show_delivery(q.message, existing, self.videos[uid].index(existing)); return
                async with self._download_semaphore:
                    await self._download_task(uid, url, q.message, media_type)

    async def _download_task(self, uid, url, msg, media_type):
        s = await msg.reply_text(f"⏳ Downloading {media_type}...")
        try:
            if media_type == 'thumb': fp, title, vid = await asyncio.get_event_loop().run_in_executor(None, self._sync_download_thumb, uid, url)
            else: fp, title, vid = await asyncio.get_event_loop().run_in_executor(None, self._sync_download, uid, url, media_type)
            sz = Path(fp).stat().st_size
            record = VideoRecord(title, url, vid, fp, sz, datetime.now().strftime('%Y-%m-%d %H:%M:%S'), media_type=media_type)
            self.videos.setdefault(uid, []).insert(0, record)
            while len(self.videos.get(uid, [])) > 20: old = self.videos[uid].pop(); Path(old.file_path).unlink(missing_ok=True)
            self._save(); await s.delete(); await self._show_delivery(msg, record, 0)
        except Exception as e: logger.error("Download %d: %s", uid, str(e)[:100]); await s.edit_text("❌ Failed.", reply_markup=self._menu(uid))

    async def _back_to_formats(self, u, c):
        q = u.callback_query; await q.answer(); uid = u.effective_user.id; data = q.data
        record = self.videos[uid][0] if 'new' in data else self.videos.get(uid, [None])[int(data.split('_')[1])]
        if not record: return
        self._pending_urls[uid] = (record.url, record.video_id, record.title)
        await self._show_format_choice(uid, record.url, record.video_id, q.message); await q.message.delete()

    async def _show_delivery(self, msg, record, idx):
        emoji = {'video': '🎬', 'audio': '🎵', 'thumb': '🖼️'}.get(record.media_type, '📹')
        mb = record.file_size / 1024 / 1024
        self._nav_push(msg.chat.id, NAV_FORMAT, (record.url, record.video_id))
        kb = self._group_delivery_keyboard(msg.chat.id, idx) if self._is_group(msg) else self._delivery_keyboard(msg.chat.id, idx)
        await msg.reply_text(f"{emoji} *{self._esc(record.title[:200])}*\n📦 {mb:.2f} MB | {record.media_type}\n🕒 {record.download_time}\n\nChoose delivery:", parse_mode=ParseMode.MARKDOWN, reply_markup=kb)

    async def _send_telegram(self, u, c):
        q = u.callback_query; await q.answer(); uid = u.effective_user.id; data = q.data
        record = self.videos[uid][0] if 'new' in data else self.videos.get(uid, [None])[int(data.split('_')[1])]
        if not record: return
        if record.telegram_file_id:
            try:
                if record.media_type == 'thumb': await q.message.reply_photo(photo=record.telegram_file_id, caption=f"🖼️ {record.title}")
                elif record.media_type == 'audio': await q.message.reply_audio(audio=record.telegram_file_id, title=record.title)
                else: await q.message.reply_video(video=record.telegram_file_id, caption=f"🎬 {record.title}", supports_streaming=True)
                await q.message.delete(); return
            except: record.telegram_file_id = None; self._save()
        fp = record.file_path
        if not Path(fp).exists(): await q.message.reply_text("❌ File deleted."); return
        if Path(fp).stat().st_size / 1024 / 1024 > self.config.MAX_TELEGRAM_FILE_SIZE: await q.message.reply_text("⚠️ Too large."); return
        s = await q.message.reply_text("📤 Uploading...")
        try:
            with open(fp, 'rb') as f:
                if record.media_type == 'thumb': sent = await q.message.reply_photo(photo=f, caption=f"🖼️ {record.title}"); record.telegram_file_id = sent.photo[-1].file_id
                elif record.media_type == 'audio': sent = await q.message.reply_audio(audio=f, title=record.title, performer="YouTube"); record.telegram_file_id = sent.audio.file_id
                else: sent = await q.message.reply_video(video=f, caption=f"🎬 {record.title}", supports_streaming=True); record.telegram_file_id = sent.video.file_id
            self._save(); await s.delete(); await q.message.delete()
        except Exception as e: logger.error("Upload %d: %s", uid, str(e)[:50]); await s.edit_text("❌ Upload failed.")

    async def _send_link(self, u, c):
        q = u.callback_query; await q.answer(); uid = u.effective_user.id; data = q.data
        record = self.videos[uid][0] if 'new' in data else self.videos.get(uid, [None])[int(data.split('_')[1])]
        if not record or not Path(record.file_path).exists(): return
        url = f"{self.base_url}/{quote(Path(record.file_path).name)}"
        await q.message.reply_text(f"✅ *{self._esc(record.title[:200])}*\n\n📥 `{url}`\n\n⚠️ {self.config.STORAGE_DAYS}d retention.", parse_mode=ParseMode.MARKDOWN, reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("📥 Download", url=url)], [InlineKeyboardButton("🔙 Menu", callback_data='b')]]))
        await q.message.delete()

    async def _show_recent(self, u, c, page=0):
        uid = u.effective_user.id; msg = u.callback_query.message if u.callback_query else u.message
        videos = self.videos.get(uid, [])
        if not videos: await msg.reply_text("📭 No files.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Menu", callback_data='b')]])); return
        pp, tp = 5, max(1, (len(videos) + 4) // 5); page = max(0, min(page, tp - 1)); pv = videos[page * pp:(page + 1) * pp]
        emoji_map = {'video': '🎬', 'audio': '🎵', 'thumb': '🖼️'}
        txt = f"📹 Downloads ({page + 1}/{tp})\n\n"
        for i, v in enumerate(pv, page * pp + 1):
            ex = "✅" if Path(v.file_path).exists() else "🗑️"
            txt += f"{ex} {emoji_map.get(v.media_type, '📹')} {i}. {self._esc(v.title[:50])}\n   📦 {v.file_size / 1024 / 1024:.2f}MB | {v.download_time}\n\n"
        txt += f"⚠️ Deleted after {self.config.STORAGE_DAYS}d."
        kb = []
        for i, v in enumerate(pv, page * pp + 1):
            if Path(v.file_path).exists(): kb.append([InlineKeyboardButton(f"{emoji_map.get(v.media_type, '📹')} {i}. {v.title[:40]}", callback_data=f'sel_{page * pp + (i - page * pp - 1)}')])
        kb.append([InlineKeyboardButton("🗑️ Clear All Files", callback_data='clear_all')])
        nav = []
        if page > 0: nav.append(InlineKeyboardButton("⬅️", callback_data=f'p_{page - 1}'))
        if page < tp - 1: nav.append(InlineKeyboardButton("➡️", callback_data=f'p_{page + 1}'))
        if nav: kb.append(nav)
        kb.append([InlineKeyboardButton("🔙 Menu", callback_data='b')])
        await msg.reply_text(txt, disable_web_page_preview=True, reply_markup=InlineKeyboardMarkup(kb))

    async def _clear_all(self, u, c):
        q = u.callback_query; await q.answer(); uid = u.effective_user.id
        videos = self.videos.get(uid, []); count = len(videos)
        for v in videos: Path(v.file_path).unlink(missing_ok=True)
        self.videos.pop(uid, None); self._save()
        await q.message.reply_text(f"🗑️ {count} files cleared.", reply_markup=self._menu(uid))

    async def _select_video(self, u, c):
        q = u.callback_query; await q.answer(); uid, idx = u.effective_user.id, int(q.data.split('_')[1])
        videos = self.videos.get(uid, [])
        if 0 <= idx < len(videos): self._nav_push(uid, NAV_RECENT); await self._show_delivery(q.message, videos[idx], idx); await q.message.delete()

    async def _delete_video(self, u, c):
        q = u.callback_query; await q.answer(); uid, idx = u.effective_user.id, int(q.data.split('_')[1])
        videos = self.videos.get(uid, [])
        if 0 <= idx < len(videos): Path(videos[idx].file_path).unlink(missing_ok=True); videos.pop(idx); self._save()
        await q.message.reply_text("🗑️ Deleted.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("📹 Videos", callback_data='r'), InlineKeyboardButton("🔙 Menu", callback_data='b')]]))

    async def _handle_back(self, u, c):
        q = u.callback_query; uid = u.effective_user.id; prev_action, prev_data = self._nav_pop(uid); await q.answer()
        if prev_action == NAV_MAIN: await q.message.reply_text(await self._welcome_text(), reply_markup=self._menu(uid)); await q.message.delete()
        elif prev_action == NAV_RECENT: await self._show_recent(u, c); await q.message.delete()
        elif prev_action == NAV_FORMAT:
            url, video_id = prev_data; self._pending_urls[uid] = (url, video_id, '')
            await self._show_format_choice(uid, url, video_id, q.message); await q.message.delete()
        else: await q.message.reply_text(await self._welcome_text(), reply_markup=self._menu(uid)); await q.message.delete()

    async def _ask_cookies(self, u, c):
        if not self._ok(u.effective_user.id): return ConversationHandler.END
        msg = u.callback_query.message if u.callback_query else u.message
        await msg.reply_text("🔒 Cookie Info\n\n• RAM only - never saved to disk\n• File ID saved for auto-restore\n\n📤 Send your cookies.txt file:", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Cancel", callback_data='b')]]))
        return WAITING_FOR_COOKIES

    async def _recv_cookies(self, u, c):
        uid = u.effective_user.id
        if not self._ok(uid): return ConversationHandler.END
        if not u.message.document: await u.message.reply_text("❌ Send .txt file.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Cancel", callback_data='b')]])); return WAITING_FOR_COOKIES
        try:
            doc = u.message.document; f = await c.bot.get_file(doc.file_id)
            cookie_bytes = await f.download_as_bytearray()
            self._cookie_data[uid] = bytes(cookie_bytes)
            self._cookie_file_ids[uid] = doc.file_id; self._save()
            if uid in self._cookie_tmpfiles:
                try: os.unlink(self._cookie_tmpfiles[uid]); del self._cookie_tmpfiles[uid]
                except: pass
            await u.message.reply_text("✅ Cookies saved!\n\n🔒 RAM only, auto-restore enabled.", reply_markup=self._menu(uid))
            return ConversationHandler.END
        except Exception as e: logger.error("Cookie %d: %s", uid, e); return WAITING_FOR_COOKIES

    async def _router(self, u, c):
        q = u.callback_query; await q.answer(); d, uid = q.data, u.effective_user.id
        if d == 'b': await self._handle_back(u, c)
        elif d == 'r': self._nav_push(uid, NAV_MAIN); await self._show_recent(u, c)
        elif d == 'c': await self._ask_cookies(u, c)
        elif d == 'cs': await q.message.reply_text("✅ Cookies active" if uid in self._cookie_data else "❌ Upload with /cookies")
        elif d == 'vc': await q.message.reply_text(f"📦 {len(self.videos.get(uid, []))} files")
        elif d == 'clear_all': await self._clear_all(u, c)
        elif d.startswith('fmt_'): await self._choose_format(u, c)
        elif d.startswith('backfmt_'): await self._back_to_formats(u, c)
        elif d.startswith('tg_'): await self._send_telegram(u, c)
        elif d.startswith('lk_'): await self._send_link(u, c)
        elif d.startswith('sel_'): await self._select_video(u, c)
        elif d.startswith('d_'): await self._delete_video(u, c)
        elif d.startswith('p_'): await self._show_recent(u, c, int(d.split('_')[1]))

    def run(self):
        app = Application.builder().token(self.config.BOT_TOKEN).build()
        self._bot = app.bot
        app.add_handler(CommandHandler('start', self.start_cmd))
        app.add_handler(CommandHandler('help', self.help_cmd))
        app.add_handler(CommandHandler('recent', self.recent_cmd))
        app.add_handler(ConversationHandler(
            entry_points=[CommandHandler('cookies', self._ask_cookies), CallbackQueryHandler(self._ask_cookies, pattern='^c$')],
            states={WAITING_FOR_COOKIES: [MessageHandler(filters.Document.FileExtension("txt"), self._recv_cookies), MessageHandler(filters.TEXT & ~filters.COMMAND, self._ask_cookies)]},
            fallbacks=[CommandHandler('cancel', self.cancel_cmd), CallbackQueryHandler(self._router, pattern='^b$')], per_message=False))
        app.add_handler(CallbackQueryHandler(self._router))
        app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, self.on_msg))
        app.add_handler(InlineQueryHandler(self._inline_query))
        asyncio.get_event_loop().create_task(self.file_server.start())
        logger.info("Bot starting..."); app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == '__main__':
    YouTubeDownloaderBot().run()