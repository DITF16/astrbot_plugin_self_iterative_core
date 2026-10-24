"""Read AstrBot's host LogBroker for the developer log tool."""


class LogManager:
    """Bounded view over the host LogBroker cache."""

    def __init__(self, config=None, broker=None):
        config = config or {}
        self.limit = max(1, min(int(config.get("log_max_history", 1000)), 10000))
        self.broker = broker
        self.closed_for_plugin = False

    def get_logs(self, lines=100, plugin="", since=0):
        if self.closed_for_plugin or self.broker is None:
            return []
        count = max(1, min(int(lines), self.limit, 500))
        records = list(getattr(self.broker, "log_cache", ())) [-self.limit :]
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
        self.broker = None
