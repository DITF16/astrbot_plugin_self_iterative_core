import logging
import threading
from collections import deque
from datetime import datetime

try:
    import zoneinfo

    TZ_SHANGHAI = zoneinfo.ZoneInfo("Asia/Shanghai")
except ImportError:
    TZ_SHANGHAI = None


class ShanghaiFormatter(logging.Formatter):
    def formatTime(self, record, datefmt=None):
        dt = datetime.fromtimestamp(record.created, tz=TZ_SHANGHAI)
        if datefmt:
            return dt.strftime(datefmt)
        return dt.isoformat()


class LogManager(logging.Handler):
    def __init__(self, config=None):
        logging.Handler.__init__(self)

        self.max_history = 3000
        if config:
            self.max_history = getattr(config, 'log_max_history', 3000)

        self.log_buffer = deque(maxlen=self.max_history)
        self._buffer_lock = threading.Lock()

        formatter = ShanghaiFormatter(
            '[%(asctime)s] [%(name)s] [%(levelname)s]: %(message)s',
            datefmt='%H:%M:%S'
        )
        self.setFormatter(formatter)

        self.target_logger_names = ["", "Core", "astrbot", "uvicorn", "aiocqhttp"]
        self._attach_target_loggers()

    def _attach_target_loggers(self):
        """主动挂载到关键 Logger"""
        for name in self.target_logger_names:
            logger = logging.getLogger(name)
            if self not in logger.handlers:
                logger.addHandler(self)

    def emit(self, record: logging.LogRecord):
        try:
            if record.name == "uvicorn.access":
                return

            msg = self.format(record)
            with self._buffer_lock:
                self.log_buffer.append(msg)
        except Exception:
            self.handleError(record)

    def get_logs(self, lines: int = 50) -> str:
        self._ensure_still_attached()
        with self._buffer_lock:
            if not self.log_buffer:
                return "暂无日志记录 (Log buffer is empty)."
            return "\n".join(list(self.log_buffer)[-lines:])

    def _ensure_still_attached(self):
        """保活机制"""
        core_logger = logging.getLogger("Core")
        if self not in core_logger.handlers:
            self._attach_target_loggers()

    def shutdown(self):
        """
        插件卸载时调用。
        必须把自己从所有 Logger 中移除，否则重载插件后会出现双重日志，
        甚至导致持有旧对象的引用泄露。
        """
        for name in self.target_logger_names:
            logger = logging.getLogger(name)
            if self in logger.handlers:
                logger.removeHandler(self)

        self.close()
