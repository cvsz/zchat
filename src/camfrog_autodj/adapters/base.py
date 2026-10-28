"""Base adapter interface and shared utilities."""
from __future__ import annotations
import logging
from abc import ABC, abstractmethod
from typing import Generator

log = logging.getLogger("autodj")


class Message:
    def __init__(self, user: str, text: str, local: bool = False):
        self.user = user
        self.text = text
        self.local = local


class ChatAdapter(ABC):
    @abstractmethod
    def messages(self) -> Generator[Message, None, None]:
        """Yield incoming chat messages."""
        ...

    @abstractmethod
    def send(self, text: str) -> None:
        """Send a chat message."""
        ...

    @abstractmethod
    def close(self) -> None:
        """Clean up resources."""
        ...


def diff_lines(old: list[str], new: list[str]) -> list[str]:
    """Return lines in `new` that were not in `old` (by index)."""
    if not old:
        return new
    i = 0
    while i < len(old) and i < len(new) and old[i] == new[i]:
        i += 1
    return new[i:]


def parse_line(line: str, rx) -> Message | None:
    m = rx.match(line)
    if not m:
        return None
    return Message(m.group("user"), m.group("text"))