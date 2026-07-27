# Development Guide

> **Setting up a development environment and contributing to the bot**

---

## Table of Contents

- [Prerequisites](#prerequisites)
- [Local Setup](#local-setup)
- [Running Tests](#running-tests)
- [Code Style & Conventions](#code-style--conventions)
- [AI Rule for Code Changes](#ai-rule-for-code-changes)
- [Project Conventions](#project-conventions)
- [Adding a New Feature](#adding-a-new-feature)
- [Debugging Tips](#debugging-tips)

---

## Prerequisites

- Python 3.8+
- FFmpeg (optional, for MP3 audio and subtitle embedding)
- Deno JavaScript runtime (required for yt-dlp YouTube extraction)
- Telegram Bot Token from [@BotFather](https://t.me/BotFather)

```bash
# Install FFmpeg (Ubuntu/Debian)
sudo apt-get install -y ffmpeg

# Install Deno
curl -fsSL https://deno.land/install.sh | sh
export PATH="$HOME/.deno/bin:$PATH"
```

---

## Local Setup

```bash
# 1. Clone the repository
git clone https://github.com/yourusername/Telegram_Yt_Bot.git
cd Telegram_Yt_Bot

# 2. Create and activate virtual environment
python3 -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate

# 3. Install dependencies
pip install --upgrade pip
pip install -r requirements.txt
pip install --upgrade yt-dlp yt-dlp-ejs

# 4. Create .env file from example
cp env.example .env
# Edit .env with your bot token and settings

# 5. Create required directories
mkdir -p data downloads

# 6. Run the bot
python bot.py
```

---

## Running Tests

```bash
# Run all tests
python -m pytest tests/ -v

# Run tests from a specific file
python -m pytest tests/test_utils.py -v

# Run a specific test
python -m pytest tests/test_utils.py::TestClassifyYtError -v

# Run with coverage
pip install pytest-cov
python -m pytest tests/ --cov=app --cov-report=term-missing
```

### Test Categories

| Test File | What It Tests |
|-----------|---------------|
| `test_utils.py` | URL extraction, error classification, formatting helpers |
| `test_downloader.py` | Download functions, subtitle merge, codec probe |
| `test_bot.py` | Bot state management, cleanup logic |
| `test_fileserver.py` | HTTP Range requests, MIME types, chunk-size pin |
| `test_models.py` | VideoRecord serialization/deserialization |
| `test_formats.py` | Format keyboards, delivery logic |
| `test_merge_subs_metadata.py` | MKV subtitle merge + language tags |
| `test_probe_audio_codec.py` | ffprobe audio codec detection |
| `test_aac_transcode.py` | AAC transcode helper |
| `test_threading.py` | Concurrency safety |
| `test_config.py` | Config parsing |

### Adding Tests

When adding a new feature, include tests that cover:

1. **Happy path**: The feature works with valid inputs
2. **Edge cases**: Empty inputs, None values, boundary conditions
3. **Error handling**: Expected exceptions produce the right messages
4. **Regression pins**: Tests that assert behavior doesn't silently change (e.g., `TestChunkSizeRegressionPin`)

---

## Code Style & Conventions

### Imports

```python
# Standard library first
import asyncio
import logging
import os

# Third-party libraries second
from telegram import Update
from telegram.ext import Application

# Internal imports third
from app.models import VideoRecord
from app.utils import extract_url, ok
```

### Function Signatures

All handler functions receive the shared `bot` instance as their first argument:

```python
async def my_handler(bot, u, c):
    # bot = YouTubeDownloaderBot instance
    # u = Update (Telegram update)
    # c = ContextTypes.DEFAULT_TYPE
    ...
```

### Docstrings

Every function must have a descriptive docstring:

```python
def extract_video_id(url):
    """Extract the 11-character YouTube video ID from a URL.

    Handles standard watch URLs, youtu.be short links, embed URLs, /v/ URLs,
    and /shorts/ URLs. Returns None if no 11-char ID is found.

    Args:
        url: A YouTube URL string.

    Returns:
        The 11-character video ID, or None.
    """
```

### Inline Comments

Comments should explain **WHY**, not **WHAT**:

```python
# GOOD — explains why:
# Restrict to AVC (H.264) so old smart TVs can decode the video.
# YouTube serves 4K/1440p only in VP9/AV1, so users who pick those
# will get a format_unavailable error — that's intentional.
opts['format'] = VIDEO_QUALITY_FMT.get(vq, VIDEO_QUALITY_FMT['best'])

# BAD — states the obvious:
# Set the format option
opts['format'] = format_string
```

### Error Handling

```python
# Wrap callback answer() in try/except — callbacks can expire
try:
    await q.answer()
except BadRequest:
    pass

# Use specific exception types, not bare except:
except (ConnectionResetError, BrokenPipeError):
    logger.info('client disconnect')
```

---

## AI Rule for Code Changes

Every source file in this project includes the following rule in its header:

> **AI RULE**: If you modify this file, you must also update and fix the comments, docstrings, and descriptions to keep them accurate and current. Every function must have a descriptive docstring explaining its purpose, parameters, and return values. Inline comments should explain **WHY**, not **WHAT**.

When making changes:

1. ✅ Update function docstrings if signatures or behaviour changed
2. ✅ Add inline comments for non-obvious logic
3. ✅ Update module-level docstrings if the module's responsibility changed
4. ✅ Check that existing comments still match the code
5. ❌ Don't leave stale comments that contradict the code

---

## Project Conventions

### State Management

- **Persistent state**: JSON files in `data/`, loaded at startup via `load_data()`, saved after every mutation via `save_data()`
- **Ephemeral state**: In-memory `OrderedDict` with LRU cap (1024 entries), keyed by `(chat_id, message_id)`
- **Never use** per-user indices for callback data — use per-message keys

### Logging

- Use the `yt_bot` logger: `logger = logging.getLogger('yt_bot')`
- Log levels:
  - **DEBUG**: Detailed diagnostic info (cache hits, probe results)
  - **INFO**: Important events (downloads, cookie changes, cleanup)
  - **WARNING**: Concerning but non-fatal (SSL key permissions, config issues)
  - **ERROR**: Operation failures that don't crash the bot
  - **CRITICAL**: Fatal errors that exit the bot

### Thread Safety

- `yt_dlp` operations run in `asyncio.get_event_loop().run_in_executor(None, ...)`
- `subprocess.run()` calls have hard timeouts to prevent stuck processes
- A global download semaphore (`asyncio.Semaphore(1)`) ensures only one download runs at a time

### Configuration

- All user-facing settings are environment variables parsed in `config.py`
- Helper functions (`_env_bool`, `_env_int`, `_env_log_level`) handle parsing with safe defaults
- Never use `os.getenv()` directly outside `config.py`

---

## Adding a New Feature

1. **Plan the state**: Will the feature need persistent state? Ephemeral state? Add to `YouTubeDownloaderBot` in `app/bot.py`.
2. **Add configuration**: If the feature needs env vars, add them to `config.py` with parsing helpers.
3. **Implement the handler**: Create or extend a handler in `app/handlers/`.
4. **Wire it up**: Register the handler in `app/__init__.py::main()`.
5. **Add tests**: Write tests in `tests/` covering happy path, edge cases, and error handling.
6. **Update docs**: Update `README.md` or `docs/` with the new feature.

### File Checklist

- [ ] State added to `YouTubeDownloaderBot` (`app/bot.py`)
- [ ] Config added to `Config` (`config.py`)
- [ ] Handler logic implemented (`app/handlers/`)
- [ ] Handler registered in `main()` (`app/__init__.py`)
- [ ] Tests written (`tests/`)
- [ ] Docstrings updated for new/changed functions
- [ ] AI RULE header present and accurate
- [ ] Docs updated (`docs/`)

---

## Debugging Tips

### Enable Debug Logging

```bash
# In .env
LOG_LEVEL=DEBUG
```

### Check Bot Logs

```bash
# Live log tail
journalctl -u telegramytbot -f

# Last 100 lines
journalctl -u telegramytbot -n 100 --no-pager

# Filter by module
journalctl -u telegramytbot -n 100 --no-pager | grep "downloader"
```

### Test Without a Real Bot

Many tests use mocked Telegram updates and don't need a bot token:

```bash
python -m pytest tests/test_utils.py -v
python -m pytest tests/test_models.py -v
```

### Common Debug Scenarios

| Symptom | Likely Cause | Debug Step |
|---------|--------------|------------|
| Bot won't start | SSL config error | Check `SSL_CERT_FILE`/`SSL_KEY_FILE` paths |
| Download fails silently | yt-dlp error | Enable `LOG_LEVEL=DEBUG`, check `classify_yt_error()` |
| Callback buttons don't respond | Expired queries | Check for `BadRequest` in logs |
| Memory grows over time | LRU cap not working | Check `_ephemeral_max` is enforced |
| File links return 404 | Port mismatch | Check `BASE_DOWNLOAD_LINK` vs bot's port |

---

**Next:** [Architecture Guide](./ARCHITECTURE.md) → [Deployment Guide](./DEPLOYMENT.md) → [Back to README](../README.md)
