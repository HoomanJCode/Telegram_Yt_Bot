# Contributing to Telegram YouTube Downloader Bot

> **Thanks for taking the time to contribute! 🎉**

This project is a community-driven, educational codebase. Whether you're fixing a bug, adding a feature, improving documentation, or reporting an issue — your help is appreciated.

---

## Table of Contents

- [Ways to Contribute](#ways-to-contribute)
- [Development Setup](#development-setup)
- [Code Style & Conventions](#code-style--conventions)
- [Before Submitting a PR](#before-submitting-a-pr)
- [Pull Request Process](#pull-request-process)
- [Commit Message Guidelines](#commit-message-guidelines)
- [Getting Help](#getting-help)

---

## Ways to Contribute

- 🐛 **Report bugs** — Open an issue with steps to reproduce, expected vs actual behaviour, and your config (redact secrets!)
- 💡 **Suggest features** — Describe the problem you're solving, not just the solution
- 🔧 **Submit pull requests** — Code fixes, new features, or test improvements
- 📚 **Improve documentation** — Typos, clearer wording, or missing sections in `README.md` / `docs/`
- 🧪 **Add tests** — More coverage helps everyone

> **Security vulnerabilities**: Please **do not** report them publicly in issues. See [SECURITY.md](./SECURITY.md) for the private reporting process.

---

## Development Setup

Follow the **[Development Guide](./docs/DEVELOPMENT.md)** for full local setup instructions. The short version:

```bash
# 1. Fork + clone your fork
git clone https://github.com/<your-username>/Telegram_Yt_Bot.git
cd Telegram_Yt_Bot

# 2. Virtual environment + dependencies
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
pip install --upgrade yt-dlp yt-dlp-ejs

# 3. Configure
cp .env.example .env   # fill in your BOT_TOKEN

# 4. Run
python bot.py
```

Prerequisites: **Python 3.8+**, **FFmpeg** (for MP3 / subtitle embedding), and **Deno** (required for YouTube extraction).

---

## Code Style & Conventions

This project has a strict convention set documented in the **[Development Guide → Code Style & Conventions](./docs/DEVELOPMENT.md#code-style--conventions)**. Highlights:

- **Imports** — stdlib first, then third-party, then internal
- **Docstrings** — every function must have a descriptive docstring (purpose, args, returns)
- **Comments** — explain **WHY**, not **WHAT**
- **Error handling** — specific exception types, never bare `except:`
- **Configuration** — all env vars are parsed in `config.py`; never use `os.getenv()` directly elsewhere

### ⚠️ AI RULE

Every source file contains this header rule — follow it for **any** code change:

> **AI RULE**: If you modify this file, you must also update and fix the comments, docstrings, and descriptions to keep them accurate and current. Every function must have a descriptive docstring explaining its purpose, parameters, and return values. Inline comments should explain **WHY**, not **WHAT**.

---

## Before Submitting a PR

1. **Run the full test suite** — all tests must pass:
   ```bash
   python -m pytest tests/ -v
   ```
2. **Add tests for your change** — new features need happy-path, edge-case, and error-handling coverage (see the [test categories](./docs/DEVELOPMENT.md#test-categories))
3. **Update docs** — README, `docs/`, and docstrings for any changed behaviour
4. **Keep it focused** — one logical change per PR; small PRs get reviewed faster

---

## Pull Request Process

1. **Fork** the repository and create a feature branch from `master`:
   ```bash
   git checkout -b feat/your-feature-name
   ```
2. **Commit** your changes with a clear message (see guidelines below)
3. **Push** and open a PR against the `master` branch
4. **Describe your PR** — what changed, why, and any testing you performed
5. **CI must pass** — the [CI workflow](./.github/workflows/ci.yml) runs the test suite on every push/PR (`python -m unittest discover tests -v`)
6. Address review feedback; the maintainer merges once everything is green

### Branch naming

| Type | Prefix | Example |
|------|--------|---------|
| Feature | `feat/` | `feat/inline-mode` |
| Bug fix | `fix/` | `fix/cookie-expiry` |
| Refactor | `refactor/` | `refactor/downloader` |
| Docs | `docs/` | `docs/usage-guide` |

---

## Commit Message Guidelines

- **Imperative mood**: "Add feature X" not "Added feature X" / "Adds feature X"
- **Concise subject** (≤ 72 chars) describing *what* and *why*
- Reference the issue when applicable: `Fix #42 — handle empty subtitle files`

```bash
git commit -m "Fix callback answer() race on expired queries"
```

---

## Getting Help

- **Usage questions** → [USAGE.md](./docs/USAGE.md) or open a GitHub Discussion
- **Development questions** → [DEVELOPMENT.md](./docs/DEVELOPMENT.md)
- **Architecture questions** → [ARCHITECTURE.md](./docs/ARCHITECTURE.md)

---

**Next:** [Development Guide](./docs/DEVELOPMENT.md) → [Security Policy](./SECURITY.md) → [Back to README](./README.md)