# TODO — Code Review Findings (2026-07-25)

## Critical — Silent Failures

### 1. Duplicated URL-building logic in `_reply_link_text`
- **File**: `app/handlers/formats.py`
- **Issue**: The same 6-line URL-sanitization block (strip, empty-check, scheme prepend, quote) appears identically in both the `try` and `except` blocks.
- **Risk**: If one copy is fixed and the other isn't, it silently regresses.
- **Fix**: Extract to a `_build_dl_url(base_url, file_path)` helper.

### 2. Diagnostic catch-all only in `send_link`
- **File**: `app/handlers/formats.py`
- **Issue**: `send_link` has a top-level try/except with traceback logging and user-visible error. `send_telegram`, `back_to_formats`, and `also_get_other_format` do NOT have this catch-all.
- **Risk**: If any of those three crash, the user sees nothing (same silent-failure pattern).
- **Fix**: Add consistent catch-all to all four delivery-cb handlers, or at least to `send_telegram` and `send_link`.

### 3. Subtitle-sending loop swallows all errors
- **File**: `app/handlers/formats.py` → `show_delivery()`
- **Issue**: `except Exception: pass` around the subtitle file delivery loop.
- **Risk**: Corrupted files, disk errors, permission denied — all silent.
- **Fix**: At minimum log the exception type and message.

## Minor

### 4. Orphaned backslashes in plain-text fallback
- **File**: `app/handlers/formats.py` → `_reply_link_text`
- **Issue**: `esc()` converts `*` → `\*`, then `text.replace('*', '')` leaves `\` hanging on whatever follows.
- **Fix**: Use `re.sub(r'\*', '', text)` or strip `\*` sequences explicitly.

### 5. Bare `q.answer()` in `handle_back`
- **File**: `app/handlers/navigation.py` → `handle_back()`
- **Issue**: `await q.answer()` without try/except BadRequest. Old back-button clicks can expire like delivery buttons.
- **Fix**: Wrap in try/except BadRequest (same pattern as router).

### 6. No None-guard on `q` in router
- **File**: `app/handlers/navigation.py` → `router()`
- **Issue**: `q.data` accessed after the `q.answer()` wrapper, but no `if q is None` check.
- **Fix**: Add `if q is None: return` before the try block.

### 7. Bare `except:` in downloader
- **File**: `app/downloader.py` line 969
- **Issue**: Bare `except:` catches even KeyboardInterrupt/SystemExit.
- **Fix**: Specify the expected exception type(s).

### 8. `open()` without `with` in subtitle delivery
- **File**: `app/handlers/formats.py` line 350
- **Issue**: `document=open(sub, 'rb')` leaks file descriptors (no context manager).
- **Fix**: Use `with open(sub, 'rb') as f:` or explicitly close after sending.
