"""Data models for the YouTube Downloader Telegram Bot.

This module defines the in-memory representation of a downloaded video/audio/
thumbnail record. Records are created after a successful download and stored
in `bot.videos`, then persisted to `data/user_videos.json` via `save_data()`.

AI RULE: If you modify this file, you must also update and fix the comments,
docstrings, and descriptions to keep them accurate and current. Every function
must have a descriptive docstring explaining its purpose, parameters, and
return values. Inline comments should explain WHY, not WHAT.
"""


class VideoRecord:
    """Lightweight value object representing a single downloaded media file.

    Attributes (slots):
        title (str): Human-readable title of the YouTube video/playlist item.
        url (str): Original YouTube URL provided by the user.
        video_id (str): YouTube video id (11-character string).
        file_path (str): Absolute/relative path to the downloaded file on disk.
        file_size (int): Size of the file in bytes.
        download_time (str): ISO-ish timestamp string when the record was created.
        telegram_file_id (str|None): Telegram file_id after the file has been
            uploaded via the Bot API. Used to avoid re-uploading the same file
            to Telegram in the future (cached sends).
        media_type (str): One of 'video', 'audio', or 'thumb'.
        _pending_subs (list|None): Transient list of subtitle file paths that
            should be delivered together with this record. Not persisted to disk.
    """

    # __slots__ keeps the object memory-light and documents the public API.
    # Private attributes (prefixed with `_`) are excluded from to_dict() so
    # they don't pollute the persisted JSON.
    __slots__ = (
        'title', 'url', 'video_id', 'file_path', 'file_size',
        'download_time', 'telegram_file_id', 'media_type', '_pending_subs'
    )

    def __init__(self, title, url, video_id, file_path, file_size,
                 download_time, telegram_file_id=None, media_type='video'):
        # Store the core metadata supplied by the downloader.
        self.title = title
        self.url = url
        self.video_id = video_id
        self.file_path = file_path
        self.file_size = file_size
        self.download_time = download_time
        # Telegram file_id may be filled later by send_file() once the bot
        # uploads the file to Telegram's servers.
        self.telegram_file_id = telegram_file_id
        self.media_type = media_type
        # _pending_subs is used only during a single delivery flow and is
        # intentionally not part of __slots__ serialization below.
        self._pending_subs = None

    def to_dict(self):
        """Serialize the record to a plain dict for JSON storage.

        Private attributes (anything starting with `_`) are skipped so the
        JSON file stays clean and does not rely on implementation details.
        """
        # Use getattr to iterate through all public slots defined above.
        d = {k: getattr(self, k) for k in self.__slots__ if not k.startswith('_')}
        return d

    @classmethod
    def from_dict(cls, d):
        """Reconstruct a VideoRecord from a dict read from JSON.

        The keyword expansion relies on the JSON keys matching the constructor
        argument names. Extra keys are ignored by Python's normal function
        call semantics.
        """
        return cls(**d)
