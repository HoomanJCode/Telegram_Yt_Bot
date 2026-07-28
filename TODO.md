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

---

# TODO — Menu & User Flow Redesign (2026-07-28)

> **Status**: Not yet implemented. Proposal documented below.

## Problems with Current Design

| # | Issue | Impact |
|---|-------|--------|
| 1 | **6 rows of buttons** on the main menu — cognitive overload | New users get confused immediately |
| 2 | **Side-by-side tiny buttons** (3 per row) — unreadable on phones | Users tap the wrong button |
| 3 | **Mixed concerns** — Recent, Upload Cookies, and 7 settings all on one screen | No clear "what do I do next?" |
| 4 | **7 separate settings screens** — each returns to main menu, forcing re-navigation | Changing video quality + subs = 4 back-and-forths |
| 5 | **Cryptic abbreviations** — "MKV", "SRT", "~" in tiny buttons | Casual users don't understand |
| 6 | **Duplicate cookie indicator** — Upload Cookies AND ✅/❌ on same screen | Redundant visual noise |
| 7 | **No progressive disclosure** — power-user settings shown on first interaction | Overwhelms first-timers |
| 8 | **"📦 5 files" button** — informational but wastes a button row | Users click it expecting a menu |
| 9 | **Settings scattered** across buttons | Impossible to review all at a glance |
| 10 | **No quick-actions** — must navigate through menus for common tasks | Power users waste time |

## Proposed Redesign

### Main Menu — Streamlined from 6 rows to 4

```
👋 Welcome!

🎥 YouTube Downloader Bot
💡 Send YouTube link → Download!
📱 Inline: @botname <link>

───────────────────

[📹 My Downloads (3)]     ← combines Recent + file count
[⚙️ Quick Settings]       ← consolidated settings summary screen
[🍪 Cookies: ✅ Active]   ← single cookie row (or "❌ Upload")
[❓ Help / Commands]       ← replaces /help command text
```

**Changes:**
- 6 rows → 4 rows
- "My Downloads" = old Recent Downloads + file count merged into label
- "Quick Settings" = NEW consolidated screen (see below)
- Single contextual cookie button (merges Upload + Status)
- Help button for discoverability

### Quick Settings — All-in-One Summary

Replace 7 scattered main-menu buttons with one entry point:

```
⚙️ Your Settings

🎬 Video: Best (1080p)
🎵 Audio: Best (192kbps)
📝 Subtitles: Embed (MKV)
🎞️ Container: Auto
📤 Delivery: Ask each time
⚡ Auto-Format: Ask
🌐 Language: English

───────────────────

[🎬 Quality] [🎵 Audio]
[📝 Subtitles] [🎞️ Container]
[📤 Delivery] [⚡ Auto-Format]
[🌐 Language]
[🔙 Menu]
```

**Behavior:**
- Each button opens the EXISTING `_change_*()` picker (no rewrite needed)
- All `_set_*()` pickers return to this screen instead of main menu
- User can change multiple settings without back-and-forth through main menu
- 7 scattered buttons → 1 entry point

### Cookie Button — Contextual

| State | Button Text | Click Action |
|-------|------------|-------------|
| No cookies | `🍪 Upload Cookies` | Open cookie upload conversation |
| Cookies active | `🍪 Cookies: ✅ Active` | Show info message |
| Cookies expiring | `🍪 Cookies: ⚠️ Expiring soon` | Show warning + upload prompt |

### Format Picker — Clearer Labels

```
[🎬 Video — MKV (best quality)]
[🎬 Video — MP4 (universal)]
[🎵 Audio — MP3]
[🖼️ Thumbnail only]
[🔙 Back]
```

Labels explain WHY to choose each option, not just WHAT it is.

## Before vs After Summary

| Metric | Before | After |
|--------|--------|-------|
| Main menu rows | 6 | 4 |
| Settings screens to change 3 settings | 9 (3 pickers + 6 back-navs) | 5 (1 summary + 3 pickers + 1 back) |
| Cookie-related buttons | 2 (Upload + Status) | 1 (contextual) |
| Informational-only buttons | 1 (📦 5 files) | 0 (merged) |
| First-time user confusion | High | Low (progressive disclosure) |
| Power-user access | 2 taps to any setting | 2 taps (same) |

## Implementation Plan

| Step | Files | Complexity |
|------|-------|------------|
| 1. New main menu layout | `navigation.py` → `menu()` | Low |
| 2. New "Quick Settings" screen | `navigation.py` → new `show_settings_summary()` | Medium |
| 3. Cookie button merge | `navigation.py` → `menu()`, `router()` | Low |
| 4. Settings pickers return to summary | `navigation.py` → all `_set_*()` functions | Low |
| 5. "My Downloads" replaces Recent button | `navigation.py` → `menu()` | Low |
| 6. Remove "📦 5 files" button | `navigation.py` → `menu()` | Trivial |
| 7. Update format picker labels | `formats.py` → `format_choice_kb()` | Low |
| 8. Add router callback for settings summary | `navigation.py` → `router()` | Low |
| 9. Update USER_FLOWS.md | `docs/USER_FLOWS.md` | Medium |
| 10. Update README menu description | `Readme.md` | Low |
| 11. Compile-check + run tests | All | Required |
| 12. Code review | — | Required |

**Estimated effort**: ~3-4 hours. All existing handler logic (pickers, callbacks, delivery) stays unchanged. Only `menu()` and routing changes.

**Risk**: Low — all callback_data values remain backward-compatible. Existing `_change_*` and `_set_*` functions are reused as-is. The only new code is `show_settings_summary()` and the `menu()` rewrite.

---

## Code Review — Implementation Gaps & Edge Cases

> Added 2026-07-28 after deep codebase cross-reference.

### 1. 🔴 Missing `NAV_SETTINGS` Navigation Stack Constant

**File**: `app/handlers/navigation.py`

The proposal says settings pickers should return to Quick Settings (not main menu) when user presses Back. The per-message nav stack currently has:
```python
NAV_MAIN = 'main'
NAV_RECENT = 'recent'
NAV_FORMAT = 'format'
NAV_DELIVERY = 'delivery'
```
**Missing**: `NAV_SETTINGS = 'settings'`

**Also needed**: `handle_back()` needs a new branch:
```python
elif prev == NAV_SETTINGS:
    await show_settings_summary(bot, u, c)  # or equivalent
    await q.message.delete()
```

**Also needed**: When the router dispatches a setting picker button click (e.g., `vq`), it must push `NAV_SETTINGS` onto the nav stack so the picker's "🔙 Back" returns to Quick Settings:
```python
elif d == 'vq':
    nav_push(bot, q.message.chat.id, q.message.message_id, NAV_SETTINGS)
    await _change_video_quality(bot, u, c)
```
This applies to ALL 7 setting buttons: `vq`, `aq`, `sm`, `lang`, `delivery`, `af`, `cn`.

### 2. 🔴 All `_set_*()` Functions Must Return to Quick Settings

**File**: `app/handlers/navigation.py` — 7 functions

Every `_set_*()` function currently ends with:
```python
await q.message.reply_text(f"...", reply_markup=menu(bot, uid))
await q.message.delete()
```

These must all change to return to the new Quick Settings screen:
```python
await q.message.reply_text(f"...", reply_markup=settings_kb(bot, uid))
await q.message.delete()
```

**Affected functions** (all in `navigation.py`):
- `_set_language()`
- `_set_delivery()`
- `_set_video_quality()`
- `_set_audio_quality()`
- `_set_subtitle_mode()`
- `_set_auto_format()`
- `_set_video_container()`

### 3. 🔴 Cookie Button — Dynamic Callback Data

**File**: `app/handlers/navigation.py` → `menu()`

The cookie button needs **different** `callback_data` depending on state:
- Cookies inactive: `callback_data='c'` → intercepted by ConversationHandler (opens upload flow)
- Cookies active: `callback_data='cs'` → intercepted by router (shows info toast)

```python
# In menu():
has = uid in bot._cookie_data
if has:
    cookie_btn = InlineKeyboardButton("🍪 Cookies: ✅ Active", callback_data='cs')
else:
    cookie_btn = InlineKeyboardButton("🍪 Upload Cookies", callback_data='c')
```

### 4. 🟡 `cs` Callback Should Use Toast, Not New Message

**File**: `app/handlers/navigation.py` → `router()`

Currently:
```python
elif d == 'cs': await q.message.reply_text("✅ Cookies active" if uid in bot._cookie_data else "❌ Upload with /cookies")
```
This creates a **new message** in the chat (visual noise). Should use Telegram's `q.answer()` toast popup:
```python
elif d == 'cs':
    await q.answer("✅ Cookies are active and stored in RAM only. They auto-restore on restart.", show_alert=True)
```

### 5. 🟡 Dead Code: Remove `vc` Callback

**File**: `app/handlers/navigation.py` → `router()`

The "📦 {count} files" button is being removed from the main menu. The `vc` callback handler becomes dead code:
```python
elif d == 'vc': await q.message.reply_text(f"📦 {len(bot.videos.get(uid,[]))} files")
```
**Remove this branch** from `router()`. Also remove `vc` from the main menu `menu()` builder.

### 6. 🟡 New Callback Data Values Needed

| New Callback | Handler | Purpose |
|-------------|---------|---------|
| `cfg` (or `settings`) | `show_settings_summary()` | Open Quick Settings screen |
| `help` (or `h`) | New `show_help_screen()` | Show expanded help text |
| `dl` (or reuse `r`) | `show_recent()` | My Downloads button |

The "My Downloads" button can reuse callback `'r'` (existing `show_recent()`) — no handler change needed, just a label change in `menu()`.

### 7. 🟡 `/settings` Command Should Use New Screen

**File**: `app/handlers/commands.py` → `settings_cmd()`

Currently builds its own markdown summary and attaches `menu()` keyboard. Should now call the same `show_settings_summary()` function used by the Quick Settings button to keep UX consistent.

### 8. 🟡 `/help` Command Update

**File**: `app/handlers/commands.py` → `help_cmd()`

Currently shows terse text. Options:
- Keep `/help` as a quick reference (text command), OR
- Make `/help` route to the same Help screen as the "❓ Help / Commands" button
- **Recommendation**: Keep `/help` as a quick-reference text command (power users prefer it), and the Help button shows an expanded version with the main menu keyboard.

### 9. 🟡 `/status` Command Placement

`/status` is not mentioned in the redesign. It's a diagnostic command for proxy health. Options:
- Keep as text-only command (no menu button) — power users only
- Add to the Help screen as a button
- **Recommendation**: Keep as text-only command. Too technical for a menu button.

### 10. 🟡 Welcome Text Update

**File**: `app/handlers/navigation.py` → `welcome_text()`

Current welcome text:
```
👋 Welcome!
🎥 YouTube Downloader Bot
💡 Send YouTube link → Download!
📱 Inline: @botname <link>
👥 Groups: Send link
🗑️ Files: {STORAGE_DAYS}d retention.
🔒 Cookies: RAM only, auto-restore.
```

Suggested update for the new simplified menu:
```
👋 Welcome!

🎥 YouTube Downloader Bot

💡 Just paste a YouTube link to start!
📱 Or use: @botname <link> in any chat
```
The menu buttons now explain the rest. Shorter = better.

### 11. 🟡 First-Time User Experience

When a brand-new user sends `/start` for the first time:
- No cookies uploaded → cookie button shows "❌ Upload Cookies"
- No downloads → "My Downloads (0)"
- All settings at defaults

Consider showing a one-time onboarding hint in the welcome text:
```
💡 Paste a YouTube link to download!
⚠️ First time? Tap "🍪 Upload Cookies" below.
```

### 12. 🟡 `edit_text()` vs `delete()` + `reply_text()` — UI Flicker

The `_set_*()` functions currently do:
```python
await q.message.reply_text(f"✅ Set to {value}", reply_markup=menu(bot, uid))
await q.message.delete()
```
This causes a visual flash (old message deleted, new message appears). For the Quick Settings flow, consider using `edit_text()` instead:
```python
await q.message.edit_text(
    f"✅ {setting} set to {value}\n\n" + _settings_summary_text(bot, uid),
    reply_markup=settings_kb(bot, uid))
```
This would make the transition smooth — the same message is edited in place.

**Trade-off**: `edit_text` is more complex (need to handle both "picker" state and "summary" state in one message) but the UX is significantly smoother.

### 13. 🟡 MP4+Embed Cascade Warning in Quick Settings

The Quick Settings summary text should show the cascade warning inline when the user has `container=mp4` + `subtitle_mode=embed`:
```
📝 Subtitles: Embed (MKV) ⚠️ → Separate (MP4 active)
```
The `settings_cmd()` in `commands.py` already handles this cascade — reuse that logic in `show_settings_summary()`.

### 14. 🟡 Test Updates

**Files**: `tests/test_bot.py`, `tests/test_formats.py`, and any test that:
- Asserts `len(keyboard.inline_keyboard)` on `menu()` output
- Checks for specific callback_data values like `'vc'`, `'cs'`, or specific row counts
- Mocks `menu()` return values

These will break and need updating after the menu restructure.

### 15. 🟢 Presets — Future Enhancement (Not In Scope)

Power-user feature for a future iteration:
```
[🎬 TV Mode]    → 1080p + MKV + embed subs + Telegram delivery
[📱 Share Mode]  → MP4 + link delivery + separate subs
[🎵 Music Mode]  → Best audio + Telegram delivery
```
Noted for future consideration. Do NOT include in this implementation.

### 16. 🟢 Inline Mode Hint

Currently shown in welcome text (`📱 Inline: @botname <link>`). In the new design:
- Keep in the (shortened) welcome text, OR
- Move to the Help screen
- **Recommendation**: Keep in welcome text since it's the #1 feature users don't know about.

---

## Revised Implementation Plan

| Step | Files | Complexity | Notes |
|------|-------|------------|-------|
| 1. Add `NAV_SETTINGS` constant | `navigation.py` | Trivial | One line |
| 2. Add `show_settings_summary()` | `navigation.py` | Medium | Reuse `settings_cmd()` text logic + new keyboard |
| 3. Add `show_help_screen()` | `navigation.py` | Low | Expanded help text + menu keyboard |
| 4. Rewrite `menu()` — 4 rows | `navigation.py` | Low | New layout with conditional cookie button |
| 5. Update `handle_back()` — add NAV_SETTINGS | `navigation.py` | Low | New elif branch |
| 6. Update `router()` — add `cfg`, `help`, remove `vc` | `navigation.py` | Low | 3 changes |
| 7. Update all 7 `_*` router handlers — push NAV_SETTINGS | `navigation.py` | Low | 7 lines changed |
| 8. Update all 7 `_set_*()` — return to settings summary | `navigation.py` | Low | 7 `reply_markup=menu(...)` → `settings_kb(...)` |
| 9. Update `cs` callback — use toast | `navigation.py` | Trivial | `reply_text` → `q.answer()` |
| 10. Update `welcome_text()` | `navigation.py` | Low | Shorter text |
| 11. Update `settings_cmd()` | `commands.py` | Low | Call `show_settings_summary()` |
| 12. Update `help_cmd()` | `commands.py` | Low | Keep as-is or route to help screen |
| 13. Update format picker labels | `formats.py` | Low | Change 4 string literals |
| 14. Update USER_FLOWS.md | `docs/USER_FLOWS.md` | Medium | Rewrite menu + settings diagrams |
| 15. Update README | `Readme.md` | Low | Menu description |
| 16. Update tests | `tests/*.py` | Medium | Menu row counts, callback checks |
| 17. Compile-check all 17 files | All | Required | |
| 18. Run test suite | `tests/` | Required | |
| 19. Code review | — | Required | |

**Revised effort**: ~4-5 hours. The additional review items add ~2 hours but prevent subtle bugs (nav stack wrong, cookie button not intercepting, dead code, UI flicker, test failures).
