"""Log tail adapter — reads chat from Camfrog's log file."""
from __future__ import annotations
import logging
import time
from .base import ChatAdapter, Message, diff_lines, parse_line

log = logging.getLogger("autodj")


class LogtailAdapter(ChatAdapter):
    def __init__(self, cfg):
        self.cfg = cfg
        self.path = cfg.log_path
        self.encoding = cfg.log_encoding
        self.rx = parse_line.__self__.rx if hasattr(parse_line, 'rx') else None
        # We'll set the regex in the first messages() call
        self._rx = None

    def messages(self):
        if not self.path:
            raise RuntimeError("log_path not set in config")
        import re
        self._rx = re.compile(self.cfg.line_regex)
        prev = None
        while True:
            try:
                text = Path(self.path).read_text(self.encoding, errors="replace")
            except Exception as e:
                log.debug("logtail read failed: %s", e)
                time.sleep(1)
                continue
            lines = text.splitlines()[-300:]
            if prev is not None:
                for line in diff_lines(prev, lines):
                    m = parse_line(line, self._rx)
                    if m:
                        yield m
            prev = lines
            time.sleep(self.cfg.poll_interval)

    def send(self, text: str) -> None:
        raise NotImplementedError("logtail adapter cannot send messages")

    def close(self) -> None:
        pass