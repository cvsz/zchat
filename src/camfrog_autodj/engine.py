"""AutoDJ engine — playback loop, chat I/O, commands, sources."""
from __future__ import annotations
import logging
import os
import queue
import textwrap
import threading
import time
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from .commands import Router
from .library import MEDIA, LocalIndex, host_ok, is_url, local_track, remote_tracks, scan_folder
from .messages import tr
from .models import Track, UserError, YtdlpMissing
from .player import MpvPlayer
from .playqueue import PlayQueue
from .playlist import PlaylistStore
from .textutil import clean_text, norm

log = logging.getLogger("autodj")

MAX_FAIL_ANNOUNCE = 3   # consecutive failed tracks announced in the room; after that only the log hears about it
MAX_BACKOFF = 30.0      # seconds, ceiling of the pause between consecutive failed tracks
ECHO_TTL = 120.0        # seconds a line we posted is remembered, to recognise it when it comes back from the room


class Engine:
    def __init__(self, cfg, adapter, player=None, index=None):
        self.cfg, self.adapter = cfg, adapter
        self.lang = cfg.lang
        self.player = player or MpvPlayer(cfg)
        self.index = index or LocalIndex(cfg.music_dirs)
        pf = getattr(cfg, "playlist_file", "")
        self.playlist = PlaylistStore(pf) if pf else None
        self.queue = PlayQueue(cfg.shuffle)
        self.router = Router(self)
        self.admins = {a.lower() for a in cfg.admins}
        self.current: Track | None = None
        self.volume = cfg.volume
        self.votes: set[str] = set()
        self.paused = False
        self.started_at = 0.0
        self._paused_at: float | None = None
        self._paused_total = 0.0
        self.outbox: queue.Queue[str] = queue.Queue()
        self.chat_log: deque[tuple[str, str]] = deque(maxlen=300)
        self.chat_seq = 0
        self.stop_evt = threading.Event()
        self.chat_done = threading.Event()
        self.wake = threading.Event()
        self.exec = ThreadPoolExecutor(8)  # lookups can take seconds: keep !skip and friends responsive meanwhile
        self.history: deque[Track] = deque(maxlen=20)  # newest first
        self.blocked: set[str] = {b.lower() for b in cfg.blocked_users}
        self.fail_delay = lambda n: min(MAX_BACKOFF, 2.0 ** (n - 1))  # n = consecutive failures so far
        self._sent: deque[tuple[float, str]] = deque(maxlen=64)
        self._sent_lock = threading.Lock()

    # ---- text -----------------------------------------------------------
    def t(self, key: str, **kw) -> str:
        return tr(self.lang, key, **kw)

    def fmt_now(self, t: Track) -> str:
        try:
            return self.cfg.msg_now.format(**t.fields()) if self.cfg.msg_now else self.t("now_playing", **t.fields())
        except Exception:  # a typo in msg_now must not take down the player-loop thread
            log.exception("bad msg_now template %r: falling back to the built-in text", self.cfg.msg_now)
            return self.t("now_playing", **t.fields())

    def fmt_next(self, t: Track) -> str:
        try:
            return self.cfg.msg_next.format(**t.fields()) if self.cfg.msg_next else self.t("up_next", **t.fields())
        except Exception:
            log.exception("bad msg_next template %r: falling back to the built-in text", self.cfg.msg_next)
            return self.t("up_next", **t.fields())

    # ---- chat out -------------------------------------------------------
    def log_chat(self, who: str, text: str):
        self.chat_log.append((who, text))
        self.chat_seq += 1
        log.info("chat %s %s: %s", ">>" if who == self.cfg.bot_nick else "<<", who, text)

    def say(self, text: str):
        text = clean_text(text)
        if not text:
            return
        self.log_chat(self.cfg.bot_nick, text)
        for i, chunk in enumerate(textwrap.wrap(text, self.cfg.max_msg_len, break_long_words=True)):
            if i and chunk.startswith(self.cfg.prefix):
                chunk = "… " + chunk  # a wrapped continuation must never look like a command
            self.outbox.put(chunk)

    # The room shows our own lines back to us under the account's nick, which may differ from bot_nick.
    # Remembering what we posted lets us ignore those echoes instead of treating them as commands.
    def _remember_sent(self, text: str):
        with self._sent_lock:
            self._sent.append((time.monotonic() + ECHO_TTL, norm(text)))

    def _is_echo(self, text: str) -> bool:
        now, want = time.monotonic(), norm(text)
        with self._sent_lock:
            while self._sent and self._sent[0][0] < now:
                self._sent.popleft()
            for i, (_, s) in enumerate(self._sent):
                if s == want:
                    del self._sent[i]
                    return True
        return False

    def _sender(self):
        while not self.stop_evt.is_set():
            try:
                m = self.outbox.get(timeout=0.5)
            except queue.Empty:
                continue
            self._remember_sent(m)  # before posting: the echo can show up before send() returns
            try:
                self.adapter.send(m)
            except Exception as ex:
                log.warning("send failed (%s); retrying once in 2s", ex)
                self.stop_evt.wait(2.0)
                if self.stop_evt.is_set():
                    break
                try:
                    self.adapter.send(m)
                    log.info("send retry ok: %s", m)
                except Exception:
                    log.exception("send retry failed too, dropped: %s", m)
            self.stop_evt.wait(self.cfg.send_interval)  # flood control (wakes up at shutdown)

    # ---- controls -------------------------------------------------------
    def skip(self):
        self.player.stop()

    def pause(self):
        if self.current and not self.paused:
            if self.player.set_pause(True) is False:
                log.warning("pause failed: mpv did not accept the command")
                return
            self.paused, self._paused_at = True, time.monotonic()

    def resume(self):
        if self.paused:
            if self.player.set_pause(False) is False:
                log.warning("resume failed: mpv did not accept the command")
                return
            self._paused_total += time.monotonic() - (self._paused_at or time.monotonic())
            self.paused, self._paused_at = False, None

    def position(self) -> float:
        if not self.current:
            return 0.0
        end = self._paused_at or time.monotonic()
        return max(0.0, end - self.started_at - self._paused_total)

    def set_volume(self, v: int):
        self.volume = max(0, min(100, int(v)))
        self.player.set_volume(self.volume)

    def speak(self, text: str):
        """Text-to-speech into the same output (the Camfrog mic); music is ducked meanwhile."""
        text = clean_text(text)
        if not text:
            return

        def job():
            import tempfile
            from .tts import synth
            wav = os.path.join(tempfile.gettempdir(), f"autodj-tts-{time.time_ns()}.wav")
            try:
                synth(text, wav, self.cfg)
                self.player.set_volume(self.cfg.tts_duck)
                self.player.play_file(wav)
            except Exception:
                log.exception("tts failed")
            finally:
                self.player.set_volume(self.volume)
                Path(wav).unlink(missing_ok=True)
        self.exec.submit(job)

    def snapshot(self) -> dict:
        with self.queue.lock:
            reqs = list(self.queue.requests)
        return dict(current=self.current, position=self.position(), paused=self.paused,
                    volume=self.volume, queue=reqs, next=self.queue.peek(),
                    shuffle=self.queue.shuffle, repeat=self.queue.repeat, history=list(self.history))

    # ---- sources --------------------------------------------------------
    def _remote(self, query: str, user: str = "AutoDJ", playlist: bool = False,
                search_limit: int | None = None) -> list[Track]:
        try:
            sl = search_limit if search_limit is not None else self.cfg.search_limit
            return remote_tracks(query, user, playlist=playlist, ytdlp_path=self.cfg.ytdlp_path,
                                 mpv_path=self.cfg.mpv_path, search_limit=sl)
        except YtdlpMissing as ex:
            log.error("%s", ex)
            raise UserError(self.t("yt_missing")) from None

    def recent_requests(self, n: int = 5) -> list[Track]:
        """The newest playlist.json entries that were added by requests (!last)."""
        if not self.playlist:
            return []
        tracks = self.playlist.load()
        fresh = [t for t in tracks if getattr(t, "added", "")]
        return fresh[-n:]

    def resolve(self, arg: str, user: str, admin: bool) -> list[Track]:
        arg = arg.strip().strip('"')
        if not arg:
            raise UserError(self.t("nothing_found"))
        if not is_url(arg):
            host = arg.split("/")[0].lower()
            if "." in host and any(host == h or host.endswith("." + h) for h in self.cfg.allowed_hosts):
                arg = "https://" + arg
        if is_url(arg):
            if not admin and not host_ok(arg, self.cfg.allowed_hosts):
                raise UserError(self.t("host_denied"))
            return self._remote(arg, user, playlist=admin)
        if os.path.isabs(arg):
            p = Path(arg)
            if not admin and not self.index.contains(p):
                raise UserError(self.t("file_not_in_lib"))
            if p.is_dir():
                if not admin:
                    raise UserError(self.t("folders_admin"))
                return scan_folder(p, user)
            if p.is_file() and p.suffix.lower() in MEDIA:
                return [local_track(p, user)]
            raise UserError(self.t("file_not_found"))
        hit = self.index.find(arg, user)
        return [hit] if hit else self._remote(arg, user)

    def request(self, arg: str, user: str = "DJ"):
        """Admin request (GUI): no limits, runs in the background."""
        def job():
            try:
                tracks = self.resolve(arg, user, True)
                if not tracks:
                    raise UserError(self.t("nothing_found"))
                pos = 0
                for t in tracks:
                    pos = self.queue.add_request(t)
                self.wake.set()
                if self.playlist:
                    self.playlist.add(tracks)
                self.say(self.t("queued_many", n=len(tracks), user=user) if len(tracks) > 1 else
                         self.t("queued_one", pos=pos, title=tracks[0].title, dur=tracks[0].dur, user=user))
            except UserError as ex:
                self.say(str(ex))
            except Exception:
                log.exception("request failed: %s", arg)
                self.say(self.t("error"))
        self.exec.submit(job)

    def load_source(self, src: str) -> list[Track]:
        if is_url(src):
            return self._remote(src, playlist=True)
        p = Path(src)
        if p.is_dir():
            return scan_folder(p)
        if p.is_file():
            return [local_track(p)]
        raise UserError(self.t("not_found", src=src))

    def load_async(self, src: str, announce: bool = True):
        def job():
            try:
                tracks = self.load_source(src)
                self.queue.add_pool(tracks)
                self.wake.set()
                if self.playlist:
                    self.playlist.add(tracks)
                if announce:
                    self.say(self.t("added_pool", n=len(tracks)))
            except Exception as ex:
                log.exception("load failed")
                if announce:
                    self.say(self.t("load_failed", err=ex))
        self.exec.submit(job)

    def _load_all(self):
        try:
            self.index.scan()
            self.queue.add_pool(self.index.tracks)
        except Exception:
            log.exception("library scan failed")
        for s in self.cfg.autoplay_sources:
            try:
                self.queue.add_pool(self.load_source(s))
            except Exception:
                log.exception("autoplay source failed: %s", s)
        if self.playlist:
            try:
                self.queue.add_pool(self.playlist.load())
            except Exception:
                log.exception("playlist load failed")
        log.info("autoplay pool: %d tracks", len(self.queue.pool))
        self.wake.set()

    # ---- playback loop --------------------------------------------------
    def _announce(self, t: Track):
        self.say(self.fmt_now(t))
        nxt = self.queue.peek()
        if self.cfg.announce_next and nxt:
            self.say(self.fmt_next(nxt))

    def _announce_if_playing(self, t: Track, wait: float = 3.0):
        """During a failure streak: announce only if the track is still playing after a few seconds."""
        if not self.stop_evt.wait(wait) and self.current is t:
            self._announce(t)

    def _player_loop(self):
        fails = 0  # consecutive tracks that failed to play
        while not self.stop_evt.is_set():
            t = self.queue.pop()
            if t is None:
                self.wake.wait(2)
                self.wake.clear()
                continue
            recorded = False
            while not self.stop_evt.is_set():
                self.current = t
                self.votes.clear()
                self.paused, self._paused_at, self._paused_total = False, None, 0.0
                self.started_at = time.monotonic()
                if fails < MAX_FAIL_ANNOUNCE:
                    self._announce(t)
                else:
                    self.exec.submit(self._announce_if_playing, t)
                log.info("playing %s", t.source)
                try:
                    ok = self.player.play(t.source, self.volume)
                except Exception:
                    log.exception("cannot start mpv (mpv_path=%r): install mpv and set mpv_path in config.toml",
                                  self.cfg.mpv_path)
                    ok = False
                if not ok:
                    fails += 1
                    self.current = None
                    if fails <= MAX_FAIL_ANNOUNCE:
                        self.say(self.t("cannot_play", title=t.title))
                    elif fails == MAX_FAIL_ANNOUNCE + 1:
                        log.warning("%d tracks in a row failed to play: not announcing further failures in the room "
                                    "(check the lines above; run AutoDJ.exe --check)", fails - 1)
                    self.stop_evt.wait(self.fail_delay(fails))  # back off: never spin on a broken setup
                    break
                fails = 0
                if not recorded:
                    self.history.appendleft(t)
                    recorded = True
                if self.queue.repeat != "one" or getattr(self.player, "skipped", False):
                    break
            self.current = None

    # ---- main -----------------------------------------------------------
    def _chat_loop(self):
        try:
            for msg in self.adapter.messages():
                if self.stop_evt.is_set():
                    break
                if msg.user.lower() == self.cfg.bot_nick.lower() or self._is_echo(msg.text):
                    continue
                self.log_chat(msg.user, msg.text)
                self.exec.submit(self.router.handle, msg)
        except Exception:
            if not self.stop_evt.is_set():
                log.exception("chat loop ended")
        finally:
            self.chat_done.set()

    def start(self):
        """Non-blocking: used by the GUI."""
        if hasattr(self.player, "available") and not self.player.available():
            log.error("mpv not found (mpv_path=%r). Install mpv and set mpv_path = \"C:/mpv/mpv.exe\" "
                      "in config.toml; nothing will play until then.", self.cfg.mpv_path)
        for fn in (self._sender, self._player_loop, self._load_all, self._chat_loop):
            threading.Thread(target=fn, daemon=True).start()

    def run(self):
        self.start()
        try:
            while not self.chat_done.wait(0.5):
                pass
            end = time.monotonic() + 3  # let queued replies flush
            while not self.outbox.empty() and time.monotonic() < end:
                time.sleep(0.1)
        except KeyboardInterrupt:
            pass
        finally:
            self.shutdown()

    def shutdown(self):
        self.stop_evt.set()
        self.wake.set()
        self.player.stop()
        self.adapter.close()
        self.exec.shutdown(wait=False, cancel_futures=True)