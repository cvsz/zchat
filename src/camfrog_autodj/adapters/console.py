"""Console adapter for testing without Camfrog."""
from __future__ import annotations
import logging
import sys
from .base import ChatAdapter, Message

log = logging.getLogger("autodj")


class ConsoleAdapter(ChatAdapter):
    def __init__(self, cfg):
        self.cfg = cfg

    def messages(self):
        log.info("Console mode: type messages as 'nick: text' (empty line to exit)")
        for line in sys.stdin:
            line = line.rstrip("\n")
            if not line:
                break
            yield Message("You", line, local=True)

    def send(self, text: str) -> None:
        print(f"[{self.cfg.bot_nick}] {text}")

    def close(self) -> None:
        pass