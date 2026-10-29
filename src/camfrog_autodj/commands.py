"""Chat command router with decorators and admin/cooldown checks."""
from __future__ import annotations
import logging
import time

from .models import UserError

log = logging.getLogger("autodj")
COMMANDS: dict[str, tuple] = {}


def command(*names, admin=False):
    def deco(fn):
        for n in names:
            if n in COMMANDS:  # a silent overwrite would make one command unreachable
                raise ValueError(f"command name {n!r} is registered twice")
            COMMANDS[n] = (fn, admin)
        return fn
    return deco


class Router:
    def __init__(self, engine):
        self.e = engine
        self.last: dict[str, float] = {}

    def is_admin(self, msg) -> bool:
        return msg.local or msg.user.lower() in self.e.admins

    def handle(self, msg):
        e, p = self.e, self.e.cfg.prefix
        text = msg.text.strip()
        if not text.startswith(p):
            return
        name, _, arg = text[len(p):].partition(" ")
        entry = COMMANDS.get(name.lower())
        if not entry:
            return
        fn, need_admin = entry
        admin = self.is_admin(msg)
        log.info("command %s%s (%s) from %s%s", p, name, arg, msg.user, ", admin" if admin else "")
        if not admin and msg.user.lower() in e.blocked:
            log.info("blocked: ignoring %s's %s%s", msg.user, p, name)
            return  # blocked users get no reply at all
        if need_admin and not admin:
            e.say(f"{msg.user}: {e.t('admin_only')}")
            return
        now = time.monotonic()
        if not admin:
            if now - self.last.get(msg.user, -1e9) < e.cfg.cooldown:
                log.info("cooldown: dropping %s's %s%s (%.1fs to go)", msg.user, p, name,
                         e.cfg.cooldown - (now - self.last[msg.user]))
                return
            self.last[msg.user] = now
        try:
            fn(e, msg, arg.strip(), admin)
        except UserError as ex:
            e.say(f"{msg.user}: {ex}")
        except Exception:
            log.exception("command failed: %s", text)
            e.say(f"{msg.user}: {e.t('error')}")


def _line(t) -> str:
    return f"{t.title} [{t.dur}] ({t.requester})"


@command("help", "commands", "ช่วยเหลือ")
def _help(e, m, a, admin):
    p = e.cfg.prefix
    e.say(e.t("help_base", p=p) + (e.t("help_admin", p=p) if admin else ""))


@command("play", "request", "req", "p", "เล่น")
def _play(e, m, a, admin):
    c, p = e.cfg, e.cfg.prefix
    if not a:
        raise UserError(e.t("usage_play", p=p))
    if not admin:
        if len(e.queue.requests) >= c.max_queue:
            raise UserError(e.t("queue_full"))
        if e.queue.count_by(m.user) >= c.max_requests_per_user:
            raise UserError(e.t("max_requests", n=c.max_requests_per_user))
    tracks = e.resolve(a, m.user, admin)
    if not tracks:
        raise UserError(e.t("nothing_found"))
    if not admin:
        tracks = tracks[:1]
        t = tracks[0]
        if t.duration is None and t.source.startswith("http"):
            raise UserError(e.t("live_not_allowed"))
        if t.duration and t.duration > c.max_duration:
            raise UserError(e.t("too_long", m=c.max_duration // 60))
    pos = 0
    with e.queue.lock:
        if not admin:
            if len(e.queue.requests) >= c.max_queue:
                raise UserError(e.t("queue_full"))
            if e.queue.count_by(m.user) >= c.max_requests_per_user:
                raise UserError(e.t("max_requests", n=c.max_requests_per_user))
            src = tracks[0].source
            if (e.current and e.current.source == src) or any(r.source == src for r in e.queue.requests):
                raise UserError(e.t("already_queued"))
        for t in tracks:
            pos = e.queue.add_request(t)
    e.wake.set()
    if len(tracks) == 1:
        e.say(e.t("queued_one", pos=pos, title=tracks[0].title, dur=tracks[0].dur, user=m.user))
    else:
        e.say(e.t("queued_many", n=len(tracks), user=m.user))


@command("last", "requested", "ขอล่าสุด")
def _last(e, m, a, admin):
    items = e.recent_requests(5)
    if not items:
        raise UserError(e.t("requested_none"))
    e.say(e.t("last_head") + " | ".join(f"{t.requester}: {t.title}" for t in items))


@command("np", "nowplaying", "เพลงนี้")
def _np(e, m, a, admin):
    t = e.current
    e.say(e.fmt_now(t) if t else e.t("nothing_playing").capitalize())


@command("next", "upnext", "ถัดไป")
def _next(e, m, a, admin):
    t = e.queue.peek()
    e.say(e.fmt_next(t) if t else e.t("nothing_queued"))


@command("queue", "q", "คิว")
def _queue(e, m, a, admin):
    with e.queue.lock:
        items = list(e.queue.requests)
    if not items:
        e.say(e.t("queue_empty"))
        return
    shown = " | ".join(f"{i}) {_line(t)}" for i, t in enumerate(items[:5], 1))
    e.say(shown + (" " + e.t("queue_more", n=len(items) - 5) if len(items) > 5 else ""))


@command("skip", "ข้าม")
def _skip(e, m, a, admin):
    cur = e.current
    if not cur:
        raise UserError(e.t("nothing_playing"))
    if admin or cur.requester.lower() == m.user.lower():
        e.skip()
        e.say(e.t("skipped"))
    else:
        raise UserError(e.t("skip_denied", p=e.cfg.prefix))


@command("voteskip", "vs", "โหวตข้าม")
def _voteskip(e, m, a, admin):
    if not e.current:
        raise UserError(e.t("nothing_playing"))
    e.votes.add(m.user.lower())
    n, need = len(e.votes), e.cfg.voteskip_threshold
    if n >= need:
        e.skip()
        e.say(e.t("vote_passed", n=n, need=need))
    else:
        e.say(e.t("vote_progress", n=n, need=need))


@command("remove", "rm", "ลบ")
def _remove(e, m, a, admin):
    if not a.isdigit():
        raise UserError(e.t("remove_usage", p=e.cfg.prefix))
    t = e.queue.get(int(a))
    if not t:
        raise UserError(e.t("no_such_item"))
    if not (admin or t.requester.lower() == m.user.lower()):
        raise UserError(e.t("remove_own"))
    if e.queue.remove_track(t):
        e.say(e.t("removed", title=t.title))
    else:
        raise UserError(e.t("no_such_item"))


@command("pause", "หยุดชั่วคราว", admin=True)
def _pause(e, m, a, admin):
    e.pause()


@command("resume", "unpause", "เล่นต่อ", admin=True)
def _resume(e, m, a, admin):
    e.resume()


@command("vol", "volume", "เสียง", admin=True)
def _vol(e, m, a, admin):
    if a.isdigit():
        e.set_volume(int(a))
    e.say(e.t("volume", v=e.volume))


@command("shuffle", "สุ่ม", admin=True)
def _shuffle(e, m, a, admin):
    if a.lower() not in ("on", "off"):
        raise UserError(e.t("usage_shuffle", p=e.cfg.prefix))
    e.queue.set_shuffle(a.lower() == "on")
    e.say(e.t("shuffle_state", s=e.t(a.lower())))


@command("repeat", "วนซ้ำ", admin=True)
def _repeat(e, m, a, admin):
    if a.lower() not in ("off", "one"):
        raise UserError(e.t("usage_repeat", p=e.cfg.prefix))
    e.queue.repeat = a.lower()
    e.say(e.t("repeat_state", s=e.t(a.lower())))


@command("clear", "ล้างคิว", admin=True)
def _clear(e, m, a, admin):
    e.say(e.t("cleared", n=e.queue.clear()))


@command("load", "โหลด", admin=True)
def _load(e, m, a, admin):
    if not a:
        raise UserError(e.t("load_usage", p=e.cfg.prefix))
    e.load_async(a.strip('"'))


@command("say", "พูด", admin=True)
def _say(e, m, a, admin):
    if not a:
        raise UserError(e.t("say_usage", p=e.cfg.prefix))
    e.say(a)


@command("speak", "tts", "ออกเสียง", admin=True)
def _speak(e, m, a, admin):
    if not a:
        raise UserError(e.t("speak_usage", p=e.cfg.prefix))
    e.speak(a)


@command("history", "recent", "ประวัติ")
def _history(e, m, a, admin):
    items = list(e.history)[:5]
    if not items:
        e.say(e.t("history_empty"))
        return
    e.say(e.t("history_head") + " " + " | ".join(f"{i}) {_line(t)}" for i, t in enumerate(items, 1)))


@command("block", "บล็อก", admin=True)
def _block(e, m, a, admin):
    nick = a.strip()
    if not nick:
        raise UserError(e.t("block_usage", p=e.cfg.prefix))
    if nick.lower() in e.admins:
        raise UserError(e.t("cannot_block_admin"))
    e.blocked.add(nick.lower())
    e.say(e.t("blocked_state", nick=nick))


@command("unblock", "เลิกบล็อก", admin=True)
def _unblock(e, m, a, admin):
    nick = a.strip()
    if not nick:
        raise UserError(e.t("unblock_usage", p=e.cfg.prefix))
    e.blocked.discard(nick.lower())
    e.say(e.t("unblocked_state", nick=nick))