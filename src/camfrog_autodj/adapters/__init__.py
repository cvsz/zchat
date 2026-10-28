"""Adapter factory."""
from __future__ import annotations
from .base import ChatAdapter
from .console import ConsoleAdapter
from .logtail import LogtailAdapter
from .uia import UiaAdapter


def build_adapter(cfg) -> ChatAdapter:
    if cfg.chat_sender == "console":
        return ConsoleAdapter(cfg)
    if cfg.chat_sender == "logtail":
        return LogtailAdapter(cfg)
    if cfg.chat_sender == "uia":
        return UiaAdapter(cfg)
    raise ValueError(f"unknown chat_sender: {cfg.chat_sender}")