"""Data models"""
class VideoRecord:
    __slots__ = ('title', 'url', 'video_id', 'file_path', 'file_size', 'download_time', 'telegram_file_id', 'media_type', '_pending_subs')
    def __init__(self, title, url, video_id, file_path, file_size, download_time, telegram_file_id=None, media_type='video'):
        self.title = title; self.url = url; self.video_id = video_id; self.file_path = file_path
        self.file_size = file_size; self.download_time = download_time
        self.telegram_file_id = telegram_file_id; self.media_type = media_type
        self._pending_subs = None
    def to_dict(self):
        d = {k: getattr(self, k) for k in self.__slots__ if not k.startswith('_')}
        return d
    @classmethod
    def from_dict(cls, d): return cls(**d)