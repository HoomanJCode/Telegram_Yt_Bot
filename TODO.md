# TODO — Code Review Findings (2026-07-25)

> **Status**: All items below were implemented and committed in `596239f`.  
> Full test suite: 473 passed, 1 skipped.

## Critical — Silent Failures

### 1. ✅ Duplicated URL-building logic in `_reply_link_text`
- **File**: `app/handlers/formats.py`
- **Status**: Fixed
- **Fix**: Extracted `_build_dl_url(bot, file_path)` helper and replaced both duplicate blocks in `_reply_link_text` with calls to it.

### 2. ✅ Diagnostic catch-all only in `send_link`
- **File**: `app/handlers/formats.py`
- **Status**: Fixed
- **Fix**: Added top-level try/except with traceback logging to `send_telegram`, `back_to_formats`, and `also_get_other_format`.

### 3. ✅ Subtitle-sending loop swallows all errors
- **File**: `app/handlers/formats.py` → `show_delivery()`
- **Status**: Fixed
- **Fix**: Replaced bare `except Exception: pass` with a logger.warning call including `exc_info=True`.

## Minor

### 4. ✅ Orphaned backslashes in plain-text fallback
- **File**: `app/handlers/formats.py` → `_reply_link_text`
- **Status**: Fixed
- **Fix**: Changed `text.replace('*', '')` to `text.replace(r'\\*', '').replace('*', '')` so escaped asterisks are removed before the bold markers.

### 5. ✅ Bare `q.answer()` in `handle_back`
- **File**: `app/handlers/navigation.py` → `handle_back()`
- **Status**: Fixed
- **Fix**: Wrapped `await q.answer()` in try/except `BadRequest`, mirroring the router pattern.

### 6. ✅ No None-guard on `q` in router
- **File**: `app/handlers/navigation.py` → `router()`
- **Status**: Fixed
- **Fix**: Added `if q is None: return` before the try block.

### 7. ✅ Bare `except:` in downloader
- **File**: `app/downloader.py` line 969
- **Status**: Fixed
- **Fix**: Replaced bare `except:` with `except (OSError, IOError):`.

### 8. ✅ `open()` without `with` in subtitle delivery
- **File**: `app/handlers/formats.py` line 350
- **Status**: Fixed
- **Fix**: Wrapped the subtitle file open in a `with` context manager so the file handle is closed after sending.
