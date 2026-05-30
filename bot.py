#!/usr/bin/env python3
"""
YouTube Downloader Telegram Bot – process‑isolated downloads
"""

import os, sys, logging, json, time, shutil, re, threading, socket, subprocess, asyncio
from pathlib import Path
from datetime import datetime, timedelta
from typing import Dict, Optional, List
from urllib.parse import quote
from concurrent.futures import ProcessPoolExecutor
from http.server import HTTPServer, SimpleHTTPRequestHandler
from socketserver import ThreadingMixIn

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import *
from telegram.constants import ParseMode
import yt_dlp
from yt_dlp.utils import DownloadError

from config import Config

# ---------------------------------------------------------------------------
# Logging (silence third‑party noise)
# ---------------------------------------------------------------------------
logging.basicConfig(level=logging.WARNING)
for lib in ('httpx','httpcore','telegram','telegram.ext'):
    logging.getLogger(lib).setLevel(logging.WARNING)

logger = logging.getLogger('yt_bot')
logger.setLevel(logging.INFO)
logger.addHandler(logging.StreamHandler())
logger.handlers[-1].setFormatter(logging.Formatter('%(asctime)s - %(levelname)s - %(message)s'))

# ---------------------------------------------------------------------------
# Paths & constants
# ---------------------------------------------------------------------------
DATA = Path('data')
COOKIES = DATA / 'cookies'
DOWNLOADS = Path('downloads')
YOUTUBE_RE = re.compile(r'(https?://)?(www\.)?(youtube\.com|youtu\.be)/\S+')
WAITING_COOKIES = 1

# ---------------------------------------------------------------------------
# Threaded file server (unchanged, already fixed)
# ---------------------------------------------------------------------------
class ThreadedHTTPServer(ThreadingMixIn, HTTPServer):
    daemon_threads = True
    allow_reuse_address = True

class BufferedFileHandler(SimpleHTTPRequestHandler):
    bufsize = 1024*1024   # 1 MB
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(DOWNLOADS), **kwargs)
    def handle(self):
        try: super().handle()
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError, OSError): pass
    def copyfile(self, src, dst):
        try:
            while True:
                buf = src.read(self.bufsize)
                if not buf: break
                try: dst.write(buf)
                except (BrokenPipeError, ConnectionResetError): break
        except OSError: pass
    def send_header(self, kw, val):
        if kw == 'Content-Type':
            ext = self.path.rsplit('.',1)[-1].lower() if '.' in self.path else ''
            mime = {'mp4':'video/mp4','webm':'video/webm','mkv':'video/x-matroska',
                    'mp3':'audio/mpeg','m4a':'audio/mp4','opus':'audio/opus',
                    'jpg':'image/jpeg','png':'image/png','webp':'image/webp'}
            val = mime.get(ext, 'application/octet-stream')
        super().send_header(kw, val)
    def end_headers(self):
        self.send_header('Cache-Control','public, max-age=86400')
        self.send_header('Access-Control-Allow-Origin','*')
        self.send_header('Accept-Ranges','bytes')
        super().end_headers()
    def log_message(self, *args): pass

class FileServer:
    def __init__(self, port): self.port = port
    def start(self):
        srv = ThreadedHTTPServer(('0.0.0.0', self.port), BufferedFileHandler)
        srv.request_queue_size = 10
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        logger.info("File server :%d (threaded)", self.port)

# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------
class VideoRecord:
    __slots__ = ('title','url','video_id','file_path','file_size','download_time','telegram_file_id','media_type')
    def __init__(self, title, url, vid, path, size, time, tg_id=None, mtype='video'):
        self.title=title; self.url=url; self.video_id=vid; self.file_path=path
        self.file_size=size; self.download_time=time; self.telegram_file_id=tg_id; self.media_type=mtype
    def to_dict(self): return {k:getattr(self,k) for k in self.__slots__}
    @classmethod
    def from_dict(cls,d): return cls(**d)

# ---------------------------------------------------------------------------
# Download functions (run in separate process)
# ---------------------------------------------------------------------------
def _fetch_info(uid, url, cookie_path):
    opts = {'format':'best','cookiefile':cookie_path,'quiet':True,'no_warnings':True,'socket_timeout':30,'retries':3}
    with yt_dlp.YoutubeDL(opts) as ydl:
        return ydl.extract_info(url, download=False)

def _download_media(uid, url, cookie_path, media_type, has_ffmpeg):
    if media_type == 'video':
        opts = {
            'format':'best[ext=mp4]/best',
            'outtmpl':str(DOWNLOADS/'%(id)s_v.%(ext)s'),
            'cookiefile':cookie_path,'quiet':True,'no_warnings':True,
            'socket_timeout':120,'retries':50,'fragment_retries':50,
            'http_chunk_size':10*1024*1024,'throttled_rate':'500K',
            'no_mtime':True,'merge_output_format':'mp4','concurrent_fragment_downloads':4
        }
    else:
        opts = {
            'format':'bestaudio[ext=m4a]/bestaudio',
            'outtmpl':str(DOWNLOADS/'%(id)s_a.%(ext)s'),
            'cookiefile':cookie_path,'quiet':True,'no_warnings':True,
            'socket_timeout':120,'retries':50,'fragment_retries':50,
            'http_chunk_size':10*1024*1024,'throttled_rate':'500K',
            'no_mtime':True,'concurrent_fragment_downloads':4
        }
        if has_ffmpeg:
            opts['postprocessors'] = [{'key':'FFmpegExtractAudio','preferredcodec':'mp3','preferredquality':'192'}]
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=True)
        title = info.get('title','?')
        vid = info.get('id','')
        fp = ydl.prepare_filename(info)
        if media_type=='audio' and has_ffmpeg:
            fp = str(Path(fp).with_suffix('.mp3'))
        # find actual file
        found = None
        if Path(fp).exists(): found = fp
        else:
            for ext in ('.mp4','.mp3','.m4a','.webm','.mkv','.opus'):
                alt = DOWNLOADS / f'{Path(fp).stem}{ext}'
                if alt.exists(): found = str(alt); break
        if not found:
            for f in DOWNLOADS.iterdir():
                if f.is_file() and f.stem.startswith(vid):
                    found = str(f); break
        if not found: raise FileNotFoundError(title)
        return found, title, vid

def _download_thumb(uid, url, cookie_path):
    opts = {
        'cookiefile':cookie_path,'quiet':True,'no_warnings':True,
        'socket_timeout':30,'retries':3,'skip_download':True,
        'writethumbnail':True,'outtmpl':str(DOWNLOADS/'%(id)s_thumb.%(ext)s')
    }
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=False)
        title = info.get('title','?')
        vid = info.get('id','')
        ydl.download([url])
        found = None
        for ext in ('.jpg','.webp','.png'):
            fp = DOWNLOADS / f'{vid}_thumb{ext}'
            if fp.exists(): found = str(fp); break
        if not found:
            thumb_url = None
            for t in info.get('thumbnails',[]):
                if t.get('preference',0)>=0: thumb_url = t.get('url')
            if thumb_url:
                import urllib.request
                ext = thumb_url.split('?')[0].split('.')[-1] or 'jpg'
                fp = DOWNLOADS / f'{vid}_thumb.{ext}'
                urllib.request.urlretrieve(thumb_url, str(fp))
                found = str(fp)
        if not found: raise FileNotFoundError("No thumbnail")
        return found, title, vid

# ---------------------------------------------------------------------------
# Bot class
# ---------------------------------------------------------------------------
class YouTubeBot:
    def __init__(self):
        self.cfg = Config()
        self.base_url = self.cfg.BASE_DOWNLOAD_LINK.rstrip('/')
        try: port = int(self.base_url.split(':')[-1]) if ':' in self.base_url.split('/')[2] else 8000
        except: port = 8000
        
        for d in (DATA, COOKIES, DOWNLOADS): d.mkdir(parents=True, exist_ok=True)
        
        self.cookies: Dict[int, Path] = {}
        self.videos: Dict[int, List[VideoRecord]] = {}
        self._pending: Dict[int, tuple] = {}
        self.has_ffmpeg = self._chk_ffmpeg()
        
        self._load()
        self._cleanup_worker()
        FileServer(port).start()
        
        # Process pool for downloads (max 2 workers)
        self.pool = ProcessPoolExecutor(max_workers=2)
    
    def _chk_ffmpeg(self):
        try: subprocess.run(['ffmpeg','-version'], capture_output=True, timeout=5); return True
        except: return False
    
    def _load(self):
        for name,fn,attr in [('cookies','user_cookies.json',self.cookies),('videos','user_videos.json',self.videos)]:
            try:
                fp = DATA/fn
                if fp.exists():
                    data = json.loads(fp.read_text())
                    if name=='videos': attr.update({int(k):[VideoRecord.from_dict(v) for v in vs] for k,vs in data.items()})
                    else: attr.update({int(k):v for k,v in data.items()})
            except Exception as e: logger.error("Load %s: %s",name,e)
    
    def _save(self):
        d = {
            DATA/'user_cookies.json':{str(k):str(v) for k,v in self.cookies.items()},
            DATA/'user_videos.json':{str(k):[v.to_dict() for v in vs] for k,vs in self.videos.items()}
        }
        for fp,data in d.items():
            try: fp.write_text(json.dumps(data,indent=2))
            except Exception as e: logger.error("Save %s: %s",fp.name,e)
    
    def _cleanup_worker(self):
        def w():
            while True:
                try: self._cleanup()
                except: pass
                time.sleep(3600)
        threading.Thread(target=w, daemon=True).start()
    
    def _cleanup(self):
        cutoff = datetime.now()-timedelta(days=self.cfg.STORAGE_DAYS)
        for f in DOWNLOADS.iterdir():
            if f.is_file() and datetime.fromtimestamp(f.stat().st_mtime)<cutoff: f.unlink()
        for uid in list(self.videos):
            self.videos[uid]=[v for v in self.videos[uid] if Path(v.file_path).exists()]
            if not self.videos[uid]: del self.videos[uid]
        self._save()
    
    def _cookie_path(self,uid): return COOKIES/f'{uid}.txt'
    def _ok(self,uid): return not self.cfg.get_whitelist() or uid in self.cfg.get_whitelist()
    def _extract_url(self,txt):
        m=YOUTUBE_RE.search(txt)
        if m:
            u=m.group(); 
            if u.startswith('www.'): u='https://'+u
            elif not u.startswith('http'): u='https://'+u
            return u
        return None
    def _extract_id(self,url):
        for p in [r'(?:youtube\.com/watch\?v=|youtu\.be/|youtube\.com/embed/|youtube\.com/v/)([a-zA-Z0-9_-]{11})', r'youtube\.com/shorts/([a-zA-Z0-9_-]{11})']:
            m=re.search(p,url)
            if m: return m.group(1)
        return None
    def _existing_types(self,uid,vid):
        return {v.media_type for v in self.videos.get(uid,[]) if v.video_id==vid and Path(v.file_path).exists()}
    def _find_existing(self,uid,vid,mtype):
        for v in self.videos.get(uid,[]):
            if v.video_id==vid and v.media_type==mtype and Path(v.file_path).exists(): return v
        return None
    @staticmethod
    def _esc(t): 
        for c in '*_`[]': t=t.replace(c,'\\'+c)
        return t
    def _menu(self,uid):
        has=uid in self.cookies; vc=len(self.videos.get(uid,[]))
        return InlineKeyboardMarkup([
            [InlineKeyboardButton("📹 Recent", callback_data='r')],
            [InlineKeyboardButton("🍪 Cookies", callback_data='c')],
            [InlineKeyboardButton(f"🍪 {'✅' if has else '❌'}", callback_data='cs'),
             InlineKeyboardButton(f"📦 {vc}", callback_data='vc')]
        ])
    def _fmt_kb(self,uid,vid):
        ex=self._existing_types(uid,vid)
        kb=[]
        kb.append([InlineKeyboardButton(f"{'✅' if 'video' in ex else ''}🎬 Video", callback_data='fmt_video')])
        kb.append([InlineKeyboardButton(f"{'✅' if 'audio' in ex else ''}🎵 Audio", callback_data='fmt_audio')])
        kb.append([InlineKeyboardButton(f"{'✅' if 'thumb' in ex else ''}🖼️ Thumb", callback_data='fmt_thumb')])
        kb.append([InlineKeyboardButton("🔙 Cancel", callback_data='b')])
        return InlineKeyboardMarkup(kb)
    def _delivery_kb(self,uid,idx=None):
        s=str(idx) if idx is not None else 'new'
        return InlineKeyboardMarkup([
            [InlineKeyboardButton("📤 Telegram", callback_data=f'tg_{s}')],
            [InlineKeyboardButton("📋 Link", callback_data=f'lk_{s}')],
            [InlineKeyboardButton("🔙 Formats", callback_data=f'backfmt_{s}')]
        ])
    
    # --- Telegram handlers ---
    async def start(self,u,c):
        if not self._ok(u.effective_user.id): return
        await u.message.reply_text(f"👋 {u.effective_user.first_name}!\n🎥 YouTube Bot\n/start /cookies /recent /help\nSend a link!",
            parse_mode=ParseMode.MARKDOWN, reply_markup=self._menu(u.effective_user.id))
    async def help(self,u,c):
        await u.message.reply_text("📚 Send YouTube link → choose format.",
            reply_markup=self._menu(u.effective_user.id))
    async def recent(self,u,c): await self._show_recent(u,c)
    async def cancel(self,u,c):
        await u.message.reply_text("❌ Cancelled.", reply_markup=self._menu(u.effective_user.id))
        return ConversationHandler.END
    
    async def on_msg(self,u,c):
        uid=u.effective_user.id
        if not self._ok(uid): return
        url=self._extract_url(u.message.text)
        if not url: return
        if uid not in self.cookies:
            await u.message.reply_text("❌ Upload cookies! /cookies",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🍪 Upload", callback_data='c')]]))
            return
        vid=self._extract_id(url)
        if not vid: await u.message.reply_text("❌ Invalid URL."); return
        await self._show_formats(uid,url,vid,u.message)
    
    async def _show_formats(self,uid,url,vid,msg):
        s=await msg.reply_text("🔍 Fetching info...")
        try:
            info = await asyncio.get_event_loop().run_in_executor(
                self.pool, _fetch_info, uid, url, str(self.cookies[uid]))
            title=info.get('title','?')
            dur=info.get('duration',0)
            self._pending[uid]=(url,vid,title)
            mins,secs=divmod(dur,60) if dur else (0,0)
            ex=self._existing_types(uid,vid)
            dn=""
            if ex:
                nms={'video':'🎬','audio':'🎵','thumb':'🖼️'}
                dn="\n✅ "+" ".join(nms[t] for t in ex)
            await s.edit_text(f"📹 *{self._esc(title[:200])}*\n⏱ {mins}:{secs:02d}{dn}\nChoose:",
                parse_mode=ParseMode.MARKDOWN, reply_markup=self._fmt_kb(uid,vid))
        except Exception as e:
            logger.error("Info %d: %s",uid,str(e)[:100])
            await s.edit_text("❌ Failed.", reply_markup=self._menu(uid))
    
    async def _choose_fmt(self,u,c):
        q=u.callback_query; await q.answer()
        uid=u.effective_user.id; fmt=q.data
        if uid not in self._pending: return
        url,vid,title=self._pending[uid]
        if fmt=='fmt_video':
            ex=self._find_existing(uid,vid,'video')
            if ex: await self._show_delivery(q.message, ex, self.videos[uid].index(ex)); return
            await self._start_dl(uid,url,q.message,'video')
        elif fmt=='fmt_audio':
            ex=self._find_existing(uid,vid,'audio')
            if ex: await self._show_delivery(q.message, ex, self.videos[uid].index(ex)); return
            await self._start_dl(uid,url,q.message,'audio')
        elif fmt=='fmt_thumb':
            ex=self._find_existing(uid,vid,'thumb')
            if ex: await self._show_delivery(q.message, ex, self.videos[uid].index(ex)); return
            await self._start_thumb(uid,url,q.message)
    
    async def _start_dl(self,uid,url,msg,mtype):
        s=await msg.reply_text(f"⏳ Downloading {mtype}...")
        try:
            fp,title,vid = await asyncio.get_event_loop().run_in_executor(
                self.pool, _download_media, uid, url, str(self.cookies[uid]), mtype, self.has_ffmpeg)
            sz=Path(fp).stat().st_size
            rec=VideoRecord(title,url,vid,fp,sz,datetime.now().strftime('%Y-%m-%d %H:%M:%S'),mtype=mtype)
            self.videos.setdefault(uid,[]).insert(0,rec)
            while len(self.videos[uid])>20:
                old=self.videos[uid].pop(); Path(old.file_path).unlink(missing_ok=True)
            self._save()
            await s.delete(); await self._show_delivery(msg,rec,0)
        except Exception as e:
            logger.error("DL %d: %s",uid,str(e)[:100])
            await s.edit_text("❌ Failed.", reply_markup=self._menu(uid))
    
    async def _start_thumb(self,uid,url,msg):
        s=await msg.reply_text("🖼️ Downloading thumbnail...")
        try:
            fp,title,vid = await asyncio.get_event_loop().run_in_executor(
                self.pool, _download_thumb, uid, url, str(self.cookies[uid]))
            sz=Path(fp).stat().st_size
            rec=VideoRecord(title,url,vid,fp,sz,datetime.now().strftime('%Y-%m-%d %H:%M:%S'),mtype='thumb')
            self.videos.setdefault(uid,[]).insert(0,rec)
            while len(self.videos[uid])>20:
                old=self.videos[uid].pop(); Path(old.file_path).unlink(missing_ok=True)
            self._save()
            await s.delete(); await self._show_delivery(msg,rec,0)
        except Exception as e:
            logger.error("Thumb %d: %s",uid,str(e)[:100])
            await s.edit_text("❌ Failed.", reply_markup=self._menu(uid))
    
    async def _back_to_fmts(self,u,c):
        q=u.callback_query; await q.answer()
        uid=u.effective_user.id; data=q.data
        idx = 0 if data=='backfmt_new' else int(data.split('_')[1])
        rec = self.videos.get(uid,[None])[idx]
        if not rec: return
        self._pending[uid]=(rec.url,rec.video_id,rec.title)
        await self._show_formats(uid,rec.url,rec.video_id,q.message)
        await q.message.delete()
    
    async def _show_delivery(self,msg,rec,idx):
        emoji={'video':'🎬','audio':'🎵','thumb':'🖼️'}.get(rec.media_type,'📹')
        mb=rec.file_size/1024/1024
        await msg.reply_text(
            f"{emoji} *{self._esc(rec.title[:200])}*\n📦 {mb:.2f} MB | {rec.media_type}\n🕒 {rec.download_time}\nChoose:",
            parse_mode=ParseMode.MARKDOWN, reply_markup=self._delivery_kb(msg.chat.id,idx))
    
    async def _send_tg(self,u,c):
        q=u.callback_query; await q.answer()
        uid=u.effective_user.id; data=q.data
        idx = 0 if data=='tg_new' else int(data.split('_')[1])
        rec = self.videos.get(uid,[None])[idx]
        if not rec: return
        if rec.telegram_file_id:
            try:
                if rec.media_type=='thumb': await q.message.reply_photo(rec.telegram_file_id,caption=f"🖼️ {rec.title}")
                elif rec.media_type=='audio': await q.message.reply_audio(rec.telegram_file_id,title=rec.title)
                else: await q.message.reply_video(rec.telegram_file_id,caption=f"🎬 {rec.title}",supports_streaming=True)
                await q.message.delete(); return
            except: rec.telegram_file_id=None; self._save()
        fp=rec.file_path
        if not Path(fp).exists(): await q.message.reply_text("❌ File deleted."); return
        mb=Path(fp).stat().st_size/1024/1024
        if mb>self.cfg.MAX_TELEGRAM_FILE_SIZE: await q.message.reply_text(f"⚠️ Too large ({mb:.1f}MB)."); return
        s=await q.message.reply_text("📤 Uploading...")
        try:
            with open(fp,'rb') as f:
                if rec.media_type=='thumb':
                    sent=await q.message.reply_photo(f,caption=f"🖼️ {rec.title}"); rec.telegram_file_id=sent.photo[-1].file_id
                elif rec.media_type=='audio':
                    sent=await q.message.reply_audio(f,title=rec.title,performer="YouTube"); rec.telegram_file_id=sent.audio.file_id
                else:
                    sent=await q.message.reply_video(f,caption=f"🎬 {rec.title}",supports_streaming=True); rec.telegram_file_id=sent.video.file_id
            self._save(); await s.delete(); await q.message.delete()
        except Exception as e:
            logger.error("Upload %d: %s",uid,str(e)[:50]); await s.edit_text("❌ Upload failed.")
    
    async def _send_link(self,u,c):
        q=u.callback_query; await q.answer()
        uid=u.effective_user.id; data=q.data
        idx = 0 if data=='lk_new' else int(data.split('_')[1])
        rec = self.videos.get(uid,[None])[idx]
        if not rec or not Path(rec.file_path).exists(): return
        url = f"{self.base_url}/{quote(Path(rec.file_path).name)}"
        await q.message.reply_text(f"📥 `{url}`\n⚠️ {self.cfg.STORAGE_DAYS}d retention.",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("📥 Download",url=url)],[InlineKeyboardButton("🔙 Menu",callback_data='b')]]))
        await q.message.delete()
    
    async def _show_recent(self,u,c,page=0):
        uid=u.effective_user.id
        msg=u.callback_query.message if u.callback_query else u.message
        vids=self.videos.get(uid,[])
        if not vids:
            await msg.reply_text("📭 No files.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Menu",callback_data='b')]]))
            return
        pp=5; tp=max(1,(len(vids)+pp-1)//pp); page=max(0,min(page,tp-1))
        pv=vids[page*pp:(page+1)*pp]
        emoji={'video':'🎬','audio':'🎵','thumb':'🖼️'}
        txt=f"📹 *Downloads* ({page+1}/{tp})\n\n"
        for i,v in enumerate(pv,page*pp+1):
            ex="✅" if Path(v.file_path).exists() else "🗑️"
            txt+=f"{ex} {emoji.get(v.media_type,'📹')} *{i}.* {self._esc(v.title[:50])}\n   📦 {v.file_size/1024/1024:.2f}MB | {v.download_time}\n\n"
        kb=[]
        for i,v in enumerate(pv,page*pp+1):
            if Path(v.file_path).exists():
                idx=page*pp+(i-page*pp-1)
                kb.append([InlineKeyboardButton(f"{emoji.get(v.media_type,'📹')} {i}. {v.title[:40]}",callback_data=f'sel_{idx}')])
        nav=[]
        if page>0: nav.append(InlineKeyboardButton("⬅️",callback_data=f'p_{page-1}'))
        if page<tp-1: nav.append(InlineKeyboardButton("➡️",callback_data=f'p_{page+1}'))
        if nav: kb.append(nav)
        kb.append([InlineKeyboardButton("🔙 Menu",callback_data='b')])
        await msg.reply_text(txt,parse_mode=ParseMode.MARKDOWN,disable_web_page_preview=True,reply_markup=InlineKeyboardMarkup(kb))
    
    async def _select_video(self,u,c):
        q=u.callback_query; await q.answer()
        uid=u.effective_user.id; idx=int(q.data.split('_')[1])
        vids=self.videos.get(uid,[])
        if 0<=idx<len(vids):
            await self._show_delivery(q.message,vids[idx],idx)
            await q.message.delete()
    
    async def _delete_video(self,u,c):
        q=u.callback_query; await q.answer()
        uid=u.effective_user.id; idx=int(q.data.split('_')[1])
        vids=self.videos.get(uid,[])
        if 0<=idx<len(vids):
            Path(vids[idx].file_path).unlink(missing_ok=True)
            vids.pop(idx); self._save()
            await q.message.reply_text("🗑️ Deleted.", reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("📹 Videos",callback_data='r'),
                InlineKeyboardButton("🔙 Menu",callback_data='b')]]))
    
    async def _ask_cookies(self,u,c):
        if not self._ok(u.effective_user.id): return ConversationHandler.END
        msg=u.callback_query.message if u.callback_query else u.message
        await msg.reply_text("⚠️ *Cookie Warning*\nSend cookies.txt file.",
            parse_mode=ParseMode.MARKDOWN, reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Cancel",callback_data='b')]]))
        return WAITING_COOKIES
    
    async def _recv_cookies(self,u,c):
        uid=u.effective_user.id
        if not self._ok(uid): return ConversationHandler.END
        if not u.message.document:
            await u.message.reply_text("❌ Send .txt file.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Cancel",callback_data='b')]]))
            return WAITING_COOKIES
        try:
            f=await c.bot.get_file(u.message.document.file_id)
            await f.download_to_drive(str(self._cookie_path(uid)))
            self.cookies[uid]=self._cookie_path(uid); self._save()
            logger.info("User %d cookies",uid)
            await u.message.reply_text("✅ Cookies saved!", reply_markup=self._menu(uid))
            return ConversationHandler.END
        except Exception as e:
            logger.error("Cookie %d: %s",uid,e)
            await u.message.reply_text("❌ Failed.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Cancel",callback_data='b')]]))
            return WAITING_COOKIES
    
    async def _router(self,u,c):
        q=u.callback_query; await q.answer()
        d=q.data; uid=u.effective_user.id
        if d=='b': await q.message.reply_text("📋 Menu:", reply_markup=self._menu(uid))
        elif d=='r': await self._show_recent(u,c)
        elif d=='c': await self._ask_cookies(u,c)
        elif d=='cs': await q.message.reply_text("✅ Ready!" if uid in self.cookies else "❌ Use /cookies")
        elif d=='vc': await q.message.reply_text(f"📦 {len(self.videos.get(uid,[]))} files")
        elif d.startswith('fmt_'): await self._choose_fmt(u,c)
        elif d.startswith('backfmt_'): await self._back_to_fmts(u,c)
        elif d.startswith('tg_'): await self._send_tg(u,c)
        elif d.startswith('lk_'): await self._send_link(u,c)
        elif d.startswith('sel_'): await self._select_video(u,c)
        elif d.startswith('d_'): await self._delete_video(u,c)
        elif d.startswith('p_'): await self._show_recent(u,c,int(d.split('_')[1]))
    
    def run(self):
        app = Application.builder().token(self.cfg.BOT_TOKEN).build()
        app.add_handler(CommandHandler('start',self.start))
        app.add_handler(CommandHandler('help',self.help))
        app.add_handler(CommandHandler('recent',self.recent))
        app.add_handler(ConversationHandler(
            entry_points=[CommandHandler('cookies',self._ask_cookies), CallbackQueryHandler(self._ask_cookies,pattern='^c$')],
            states={WAITING_COOKIES:[
                MessageHandler(filters.Document.FileExtension("txt"),self._recv_cookies),
                MessageHandler(filters.TEXT & ~filters.COMMAND, self._ask_cookies)]},
            fallbacks=[CommandHandler('cancel',self.cancel), CallbackQueryHandler(self._router,pattern='^b$')],
            per_message=False))
        app.add_handler(CallbackQueryHandler(self._router))
        app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, self.on_msg))
        logger.info("Bot starting (process pool)...")
        app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__=='__main__':
    YouTubeBot().run()