"""Configuration for AutoDJ — TOML with validation."""
from __future__ import annotations
import difflib
import re
import tomllib
from dataclasses import dataclass, field, fields
from pathlib import Path


class ConfigError(Exception):
    """config.toml is unreadable or has a bad value. The message is written for the person editing the file."""


@dataclass
class Config:
    bot_nick: str = "AutoDJ"
    admins: list[str] = field(default_factory=list)
    prefix: str = "!"
    lang: str = "en"                       # en | th (bot replies and GUI)
    chat_reader: str = "uia"               # uia | logtail | console
    chat_sender: str = "uia"               # uia | logtail | console
    window_title_re: str = ".*"
    process_re: str = "(?i)camfrog"
    window_exclude_re: str = ""
    window_index: int = 0
    auto_discover: bool = True
    view_kind: str = "auto"                # auto | web | text
    web_view_title: str = "wb-log-room-data"
    web_input_title: str = "wb-edit-data"
    send_mode: str = "auto"                # auto | background | foreground
    send_verify: bool = True
    web_strict_body: bool = True
    view_type: str = "Document"
    view_index: int = 0
    input_type: str = "Edit"
    input_index: int = -1
    log_path: str = ""
    log_encoding: str = "utf-8"
    line_regex: str = r"^(?:\[?\d{1,2}:\d{2}(?::\d{2})?\]?\s*)?(?P<user>[^:\s][^:]{0,30}):\s(?P<text>.+)$"
    mpv_path: str = "mpv"
    audio_device: str = ""
    volume: int = 70
    music_dirs: list[str] = field(default_factory=list)
    autoplay_sources: list[str] = field(default_factory=list)
    shuffle: bool = True
    allowed_hosts: list[str] = field(default_factory=lambda: ["youtube.com", "youtu.be", "music.youtube.com"])
    max_requests_per_user: int = 3
    max_queue: int = 50
    max_duration: int = 900
    voteskip_threshold: int = 3
    cooldown: float = 3.0
    send_interval: float = 1.5
    max_msg_len: int = 180
    announce_next: bool = True
    msg_now: str = ""
    msg_next: str = ""
    tts_voice: str = ""
    tts_rate: int = 170
    tts_duck: int = 25
    auto_launch: bool = False
    camfrog_exe: str = ""
    client_title_re: str = ".*"
    room: str = ""
    join_uri_template: str = ""
    launch_timeout: int = 60
    blocked_users: list[str] = field(default_factory=list)
    ytdlp_path: str = ""
    poll_interval: float = 0.5
    log_file: str = ""
    playlist_file: str = "playlist.json"
    search_limit: int = 1
    mpv_timeout: float = 30.0

    @classmethod
    def load(cls, path: str | None) -> "Config":
        p = Path(path) if path else None
        data: dict = {}
        if p and p.exists():
            try:
                text = p.read_text("utf-8-sig")
            except UnicodeDecodeError as ex:
                raise ConfigError(f"{p}: not valid UTF-8 ({ex.reason}). Save the file as UTF-8.") from None
            try:
                data = tomllib.loads(text)
            except tomllib.TOMLDecodeError as ex:
                raise ConfigError(_syntax_message(p, text, ex)) from None
        names = {f.name for f in fields(cls)}
        bad = sorted(set(data) - names)
        if bad:
            lines = []
            for k in bad:
                near = difflib.get_close_matches(k, names, n=1)
                lines.append(f"  {k}" + (f"   (did you mean '{near[0]}'?)" if near else ""))
            raise ConfigError(f"{p or 'config'}: unknown setting(s):\n" + "\n".join(lines))
        cfg = cls(**data)
        _validate(cfg, data, str(p) if p else "config")
        return cfg


# ---- validation -------------------------------------------------------------------------------

_CHOICES = {
    "lang": ("en", "th"),
    "chat_reader": ("uia", "logtail", "console"),
    "chat_sender": ("uia", "logtail", "console"),
    "view_kind": ("auto", "web", "text"),
    "send_mode": ("auto", "background", "foreground"),
}
_REGEXES = ("window_title_re", "process_re", "window_exclude_re", "line_regex", "client_title_re")
_RANGES = {"volume": (0, 100), "max_requests_per_user": (1, 1000), "max_queue": (1, 100000),
           "max_duration": (1, 86400 * 7), "voteskip_threshold": (1, 1000), "cooldown": (0, 3600),
           "send_interval": (0, 3600), "max_msg_len": (20, 2000), "poll_interval": (0.05, 60),
           "tts_duck": (0, 100), "search_limit": (1, 50), "mpv_timeout": (0, 3600)}


def _syntax_message(p: Path, text: str, ex: Exception) -> str:
    m = re.search(r"line (\d+), column (\d+)", str(ex))
    msg = re.sub(r"\s*\(at line.*\)$", "", str(ex))
    out = [f"{p}: TOML syntax error: {msg}"]
    if m:
        ln, col = int(m[1]), int(m[2])
        lines = text.splitlines()
        if 1 <= ln <= len(lines):
            out += [f"  line {ln}: {lines[ln - 1]}", "  " + " " * (len(f"line {ln}: ") + max(col - 1, 0)) + "^"]
            if "=" not in lines[ln - 1]:
                out.append("  This line has no '='. If it is a note or something pasted by accident, "
                           "delete it or put '#' in front of it.")
    out.append("Fix that line, or copy config.example.toml over config.toml to start again.")
    return "\n".join(out)


def _type_name(v) -> str:
    return {str: "text", int: "a whole number", float: "a number", bool: "true/false", list: "a list"}.get(type(v), type(v).__name__)


def _validate(cfg: "Config", data: dict, where: str) -> None:
    defaults = Config()
    for f in fields(Config):
        if f.name not in data:
            continue
        v, want = data[f.name], type(getattr(defaults, f.name))
        ok = (isinstance(v, bool) if want is bool else
              isinstance(v, (int, float)) and not isinstance(v, bool) if want is float else
              isinstance(v, int) and not isinstance(v, bool) if want is int else
              isinstance(v, want))
        if not ok:
            hint = ' (a list looks like ["nick1", "nick2"])' if want is list else ""
            raise ConfigError(f"{where}: '{f.name}' must be {_type_name(getattr(defaults, f.name))}, "
                              f"got {v!r}{hint}")
        if want is list and not all(isinstance(x, str) for x in v):
            raise ConfigError(f"{where}: every item of '{f.name}' must be text, got {v!r}")
    for k, allowed in _CHOICES.items():
        if getattr(cfg, k) not in allowed:
            raise ConfigError(f"{where}: '{k}' must be one of {', '.join(allowed)}; got {getattr(cfg, k)!r}")
    for k, (lo, hi) in _RANGES.items():
        v = getattr(cfg, k)
        if not lo <= v <= hi:
            raise ConfigError(f"{where}: '{k}' must be between {lo} and {hi}; got {v}")
    for k in _REGEXES:
        try:
            re.compile(getattr(cfg, k))
        except re.error as ex:
            raise ConfigError(f"{where}: '{k}' is not a valid regular expression ({ex}). "
                              "Tip: write regexes in 'single quotes' so backslashes stay as they are.") from None
    if not cfg.prefix.strip():
        raise ConfigError(f"{where}: 'prefix' must not be empty (e.g. prefix = \"!\")")
    cfg.prefix = cfg.prefix.strip()
    cfg.admins = [a.strip() for a in cfg.admins if a.strip()]
    cfg.blocked_users = [a.strip() for a in cfg.blocked_users if a.strip()]