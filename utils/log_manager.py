"""Read the unified broker, with a bounded logging fallback."""

import logging
import threading
from collections import deque


class LogManager(logging.Handler):
    def __init__(self, config=None, broker=None):
        super().__init__()
        config = config or {}
        self.limit = max(1, min(int(config.get("log_max_history", 1000)), 10000))
        self.buffer = deque(maxlen=self.limit)
        self.mutex = threading.Lock()
        self.broker = broker
        self.targets = []
        self.closed_for_plugin = False
        self.setFormatter(logging.Formatter("[%(name)s] [%(levelname)s] %(message)s"))
        if self.broker is None:
            self.attach()

    def attach(self):
        if self.closed_for_plugin:
            return
        targets = [logging.getLogger(), logging.getLogger("astrbot")]
        targets.extend(
            obj
            for obj in list(logging.Logger.manager.loggerDict.values())
            if isinstance(obj, logging.Logger) and not obj.propagate
        )
        for target in targets:
            if self not in target.handlers:
                target.addHandler(self)
            if target not in self.targets:
                self.targets.append(target)

    def emit(self, record):
        if self.closed_for_plugin or record.name == "uvicorn.access":
            return
        marker = f"_self_iterative_{id(self)}"
        if getattr(record, marker, False):
            return
        setattr(record, marker, True)
        with self.mutex:
            self.buffer.append(
                {
                    "time": record.created,
                    "level": record.levelname,
                    "data": self.format(record)[:16000],
                    "plugin_name": getattr(record, "plugin_name", ""),
                }
            )

    def get_logs(self, lines=100, plugin="", since=0):
        if self.closed_for_plugin:
            return []
        count = max(1, min(int(lines), self.limit, 500))
        if self.broker is not None:
            records = list(self.broker.log_cache)[-self.limit :]
        else:
            self.attach()
            with self.mutex:
                records = list(self.buffer)
        return [
            dict(row, data=str(row.get("data", ""))[:16000])
            for row in records
            if row.get("time", 0) >= float(since)
            and (
                not plugin
                or row.get("plugin_name") == plugin
                or plugin in str(row.get("data", ""))
            )
        ][-count:]

    def shutdown(self):
        self.closed_for_plugin = True
        for target in self.targets:
            target.removeHandler(self)
        self.targets.clear()
        self.close()
