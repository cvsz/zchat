"""Tk-free tests for the prototype web status updater (no network)."""
import os
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tools import web_status as ws  # noqa: E402
import web_status_gui as wsg  # noqa: E402


@pytest.fixture(autouse=True)
def _isolated_credentials(tmp_path, monkeypatch):
    """Point the credential store at an empty folder and drop ambient values."""
    monkeypatch.setattr(ws, "BASE", tmp_path)
    monkeypatch.delenv("CAMFROG_USER", raising=False)
    monkeypatch.delenv("CAMFROG_PASSWORD", raising=False)
    monkeypatch.delenv("WEBAUTH_USER", raising=False)
    monkeypatch.delenv("WEBAUTH_BOT_USER", raising=False)


class FakeResponse:
    def __init__(self, body):
        self.body = body

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self):
        return self.body


class FakeOpener:
    def __init__(self, body):
        self.body = body
        self.calls = []

    def open(self, request, timeout=None):
        self.calls.append(request)
        return FakeResponse(self.body)


def _patch_opener(monkeypatch, body):
    fake = FakeOpener(body)
    monkeypatch.setattr(ws.urllib.request, "build_opener", lambda *args: fake)
    return fake


def _patch_opener_sequence(monkeypatch, bodies):
    """One reply per call: e.g. the login result, then the profile page."""
    remaining = list(bodies)

    class SequenceOpener(FakeOpener):
        def open(self, request, timeout=None):
            self.calls.append(request)
            body = remaining.pop(0) if len(remaining) > 1 else remaining[0]
            return FakeResponse(body)

    fake = SequenceOpener(b"")
    monkeypatch.setattr(ws.urllib.request, "build_opener", lambda *args: fake)
    return fake


def test_dry_run_touches_nothing(monkeypatch, capsys):
    monkeypatch.setattr(ws.urllib.request, "build_opener", lambda *a: (_ for _ in ()).throw(
        AssertionError("no network in dry-run")))
    monkeypatch.setattr(ws.getpass, "getpass", lambda *a: (_ for _ in ()).throw(
        AssertionError("no password prompt in dry-run")))
    assert ws.main(["--login", "Seaza", "--status", "hello"]) == 0
    out = capsys.readouterr().out
    assert "profiles.camfrog.com/home.php" in out


def test_how_to_capture_exit_ok(capsys):
    assert ws.main(["--login", "Seaza", "--status", "x", "--how-to-capture"]) == 0
    out = capsys.readouterr().out
    assert "cookies.txt" in out


def test_empty_status_refused(capsys):
    assert ws.main(["--login", "Seaza", "--status", "   "]) == 2


def test_live_captcha_blocks_without_bypass(monkeypatch, capsys):
    monkeypatch.setattr(ws.getpass, "getpass", lambda *a: "s3cret-pw")
    _patch_opener(monkeypatch, b"captcha")
    assert ws.main(["--login", "Seaza", "--status", "hi", "--live"]) == 3
    out = capsys.readouterr().out
    assert "CAPTCHA" in out
    assert "s3cret-pw" not in out


def test_live_wrong_password_reports_auth_failure(monkeypatch, capsys):
    monkeypatch.setattr(ws.getpass, "getpass", lambda *a: "wrong")
    _patch_opener(monkeypatch, b"password")
    assert ws.main(["--login", "Seaza", "--status", "hi", "--live"]) == 2
    assert "incorrect" in capsys.readouterr().out


def test_live_login_success_still_gated(monkeypatch, capsys):
    monkeypatch.setattr(ws.getpass, "getpass", lambda *a: "pw")
    _patch_opener_sequence(monkeypatch, [
        b"https://profiles.camfrog.com/en/",               # login reply
        b"<title>Camfrog - home</title>var _user_id = '1'; var nick = 'Seaza';",  # signed-in profile page
    ])
    assert ws.main(["--login", "Seaza", "--status", "hi", "--live"]) == 3
    out = capsys.readouterr().out
    assert "not confirmed" in out and "--confirm-update" in out


def test_live_prompt_cancel_sends_nothing(monkeypatch, capsys):
    def cancel(*args):
        raise EOFError

    monkeypatch.setattr(ws.getpass, "getpass", cancel)
    assert ws.main(["--login", "Seaza", "--status", "hi", "--live"]) == 2
    assert "nothing was sent" in capsys.readouterr().out


def test_live_network_failure_returns_int_code(monkeypatch, capsys):
    import urllib.error

    monkeypatch.setattr(ws.getpass, "getpass", lambda *a: "pw")

    class DeadOpener:
        def open(self, request, timeout=None):
            raise urllib.error.URLError("no route to host")

    monkeypatch.setattr(ws.urllib.request, "build_opener", lambda *a: DeadOpener())
    code = ws.main(["--login", "Seaza", "--status", "hi", "--live"])
    assert code == 2 and isinstance(code, int)
    out = capsys.readouterr().out
    assert "login request failed" in out
    assert "pw" not in out


def _cookie_file(tmp_path, value="s3cret-value"):
    path = tmp_path / "camfrog-cookies.txt"
    path.write_text("# Netscape HTTP Cookie File\n"
                    ".camfrog.com\tTRUE\t/\tTRUE\t0\tsess\t{0}\n".format(value),
                    encoding="utf-8")
    return str(path)


def test_probe_missing_cookie_file(capsys):
    assert ws.main(["--login", "Seaza", "--status", "hi",
                    "--cookies-file", "/nonexistent/x.txt", "--probe"]) == 2


def test_probe_bad_cookie_file(tmp_path, capsys):
    bad = tmp_path / "bad.txt"
    bad.write_text("not a cookie file\n", encoding="utf-8")
    assert ws.main(["--login", "Seaza", "--status", "hi",
                    "--cookies-file", str(bad), "--probe"]) == 2


def test_probe_detects_logged_out_session(tmp_path, monkeypatch, capsys):
    fake = _patch_opener(monkeypatch, b"<title>Camfrog - Login Page</title>Sign On")
    assert ws.main(["--login", "Seaza", "--status", "hi",
                    "--cookies-file", _cookie_file(tmp_path), "--probe"]) == 0
    assert fake.calls, "probe must hit the network"
    out = capsys.readouterr().out
    assert "NOT signed in" in out
    assert "s3cret-value" not in out
    assert "no status was changed" in out


def test_probe_never_claims_verified_blindly(tmp_path, monkeypatch, capsys):
    _patch_opener(monkeypatch, b"<title>Camfrog - Profile Page</title>hello")
    assert ws.main(["--login", "Seaza", "--status", "hi",
                    "--cookies-file", _cookie_file(tmp_path), "--probe"]) == 0
    assert "no signed-in marker" in capsys.readouterr().out


def test_probe_requires_cookies_file():
    with pytest.raises(SystemExit) as exc:
        ws.main(["--login", "Seaza", "--status", "hi", "--probe"])
    assert exc.value.code == 2

def test_parse_pool_drops_blanks():
    assert ws.parse_pool("  hi\n\n  hello world  \n") == ["hi", "hello world"]
    assert ws.parse_pool("   \n ") == []


def test_next_rotation_wraps_around():
    pool = ["a", "b"]
    assert ws.next_rotation(pool, 0) == ("a", 1)
    assert ws.next_rotation(pool, 1) == ("b", 0)
    assert ws.next_rotation(pool, 5) == ("b", 0)


def test_next_rotation_empty_pool_refused():
    with pytest.raises(ValueError):
        ws.next_rotation([], 0)


@pytest.mark.parametrize("module", [ws, wsg], ids=["cli", "gui"])
def test_parse_rotate_interval_clamps_and_preserves_fractional_seconds(module):
    assert module.ROTATE_INTERVAL_DEFAULT == 60
    assert module.parse_rotate_interval("60") == 60.0
    assert module.parse_rotate_interval("120.5") == 120.5
    assert module.parse_rotate_interval("5") == module.ROTATE_INTERVAL_MIN
    assert module.parse_rotate_interval("999999") == module.ROTATE_INTERVAL_MAX
    for bad in ("", "abc", "nan", "inf", None):
        with pytest.raises(ValueError):
            module.parse_rotate_interval(bad)


def test_web_status_cli_timeout_defaults_to_two_seconds():
    assert ws.build_parser().parse_args(["--status", "hello"]).timeout == 2.0


def test_perform_update_refused_until_confirmed():
    ok, message = ws.perform_update(None, "hi", 1.0)
    assert ok is False
    assert "not confirmed" in message and "--confirm-update" in message


def test_perform_update_confirmed_needs_csrf(monkeypatch):
    fake = _patch_opener(
        monkeypatch,
        b"<title>no token here</title>var _user_id = '1'; var nick = 'Seaza';")
    ok, message = ws.perform_update(
        fake, "hi", 1.0, confirm=True, expected_login="Seaza")
    assert ok is False
    assert "CSRF" in message
    assert fake.calls  # it did read the profile page first


def test_perform_update_confirmed_posts_status(monkeypatch):
    page = (b"<title>Camfrog</title>"
            b"var csrf = '90d230418b54c662c347d84f32f663e0a94fc5abd9d705a0';"
            b"var _user_id = '1';"
            b"var nick = 'Seaza';")
    fake = _patch_opener(monkeypatch, page)
    posted = {}

    def fake_post(opener, url, fields, timeout):
        posted["url"] = url
        posted["fields"] = fields
        return '{"response":"ok"}'

    monkeypatch.setattr(ws, "_post", fake_post)
    ok, message = ws.perform_update(
        fake, "hi there", 1.0, confirm=True, expected_login="Seaza")
    assert ok is True, message
    assert posted["url"] == ws.WEB_UPDATE_URL
    assert posted["fields"] == {"status": "hi there",
                                "csrf": "90d230418b54c662c347d84f32f663e0a94fc5abd9d705a0"}


def test_requests_carry_a_browser_user_agent(monkeypatch):
    """The edge blocks Python-urllib with 403; every request must look like a browser."""
    seen = {}

    class RecordingOpener:
        def open(self, request, timeout=None):
            seen["headers"] = dict(request.headers)
            return FakeResponse(b"ok")

    monkeypatch.setattr(ws.urllib.request, "build_opener",
                        lambda *a: RecordingOpener())
    ws._post(RecordingOpener(), ws.LOGIN_URL, {"a": "b"}, 1.0)
    ws.fetch_profile(RecordingOpener(), "Seaza", 1.0)
    joined = " ".join(seen["headers"])  # urllib capitalizes header names
    assert "Mozilla/5.0" in seen["headers"].get("User-agent", "") or \
        "Mozilla/5.0" in joined


def test_extract_csrf_from_logged_in_page():
    page = ("<html>var csrf = '90d230418b54c662c347d84f32f663e0a94fc5ab';"
            "var nick = 'Seaza';</html>")
    assert ws.extract_csrf(page).startswith("90d2")
    assert ws.extract_csrf("<html>nothing</html>") == ""


def test_summarize_session_reports_signed_in():
    page = ("<title>Camfrog - \u0e0b\u0e48\u0e2d\u0e19</title>"
            "<div class=\"nav-user-logged\">var _user_id = '146802792';"
            "var nick = 'Seaza';")
    verdict, detail = ws.summarize_session(page)
    assert verdict == "signed-in"
    assert "Seaza" in detail and "146802792" in detail


def test_encrypted_credentials_roundtrip(tmp_path, monkeypatch):
    """DPAPI protect/unprotect on Windows; a reversible stub everywhere else."""
    if os.name == "nt":
        ok, message = ws.save_credentials("Seaza", "s3cret")
        assert ok, message
        assert (tmp_path / ".env.enc").is_file()
        assert "CAMFROG_PASSWORD=s3cret" not in (tmp_path / ".env.enc").read_text()
        monkeypatch.delenv("CAMFROG_USER", raising=False)
        monkeypatch.delenv("CAMFROG_PASSWORD", raising=False)
        assert ws.load_env_enc() is True
        assert os.environ["CAMFROG_USER"] == "Seaza"
        assert os.environ["CAMFROG_PASSWORD"] == "s3cret"
    else:
        monkeypatch.setattr(ws, "dpapi_protect",
                            lambda data: b"blob:" + data)
        monkeypatch.setattr(ws, "dpapi_unprotect",
                            lambda blob: blob[len(b"blob:"):])
        ok, message = ws.save_credentials("Seaza", "s3cret")
        assert ok, message
        assert ws.load_env_enc() is True
        assert os.environ["CAMFROG_USER"] == "Seaza"
        assert os.environ["CAMFROG_PASSWORD"] == "s3cret"


def test_load_cookie_jar_rejects_the_encrypted_account_file(tmp_path):
    """Pointing the cookies field at .env.enc must explain the mix-up."""
    path = tmp_path / ".env.enc"
    path.write_text("AQAAANCMnd8\n", encoding="utf-8")
    with pytest.raises(ValueError) as exc:
        ws.load_cookie_jar(str(path))
    assert "encrypted account" in str(exc.value)
    assert "Account section" in str(exc.value)


def test_load_cookie_jar_names_the_extension_on_bad_files(tmp_path):
    path = tmp_path / "cookies.txt"
    path.write_text("not a cookies file\n", encoding="utf-8")
    with pytest.raises(ValueError) as exc:
        ws.load_cookie_jar(str(path))
    assert "Get cookies.txt LOCALLY" in str(exc.value)


def test_cookie_from_paste_builds_a_jar():
    jar = ws.cookie_from_paste("cf_session=abc123")
    cookies = list(jar)
    assert len(cookies) == 1 and cookies[0].name == "cf_session"
    assert cookies[0].value == "abc123"
    assert cookies[0].domain == ".camfrog.com"


def test_cookie_from_paste_accepts_several_pairs():
    jar = ws.cookie_from_paste("cf_session=abc; other=xyz")
    assert sorted(c.name for c in jar) == ["cf_session", "other"]


def test_cookie_from_paste_accepts_phpsessid():
    jar = ws.cookie_from_paste("PHPSESSID=v6trce6eq3ff6lirkdvt0b3sac")
    cookies = list(jar)
    assert len(cookies) == 1
    assert cookies[0].name == "PHPSESSID"
    assert cookies[0].value == "v6trce6eq3ff6lirkdvt0b3sac"

    # raw hash captured from home.php network
    jar2 = ws.cookie_from_paste("v6trce6eq3ff6lirkdvt0b3sac")
    cookies2 = list(jar2)
    assert len(cookies2) == 1
    assert cookies2[0].name == "PHPSESSID"
    assert cookies2[0].value == "v6trce6eq3ff6lirkdvt0b3sac"


@pytest.mark.parametrize("bad", ["", "   ", "no-equals-sign", "=value", "name="])
def test_cookie_from_paste_rejects_garbage(bad):
    with pytest.raises(ValueError) as exc:
        ws.cookie_from_paste(bad)
    assert "devtools" in str(exc.value) or "home.php" in str(exc.value)


@pytest.mark.parametrize(("key", "expected", "value"), [
    ("WEBAUTH_USER", "PHPSESSID", "PHPSESSID=session-value"),
    ("WEBAUTH_BOT_USER", "cloudbot", "cloudbot=bot-session-value"),
])
def test_environment_cookie_accepts_expected_single_cookie(
        monkeypatch, key, expected, value):
    monkeypatch.setenv(key, value)
    assert ws.environment_cookie(key, expected) == value
    assert wsg.environment_cookie(key, expected) == value


def test_environment_cookie_rejects_wrong_or_multiple_cookie_names(monkeypatch):
    monkeypatch.setenv("WEBAUTH_USER", "cloudbot=bot-session-value")
    with pytest.raises(ValueError, match="PHPSESSID"):
        ws.environment_cookie("WEBAUTH_USER", "PHPSESSID")
    monkeypatch.setenv(
        "WEBAUTH_USER", "PHPSESSID=session-value; other=extra-value")
    with pytest.raises(ValueError, match="exactly one"):
        wsg.environment_cookie("WEBAUTH_USER", "PHPSESSID")


@pytest.mark.parametrize(("module", "entry"), [
    (ws, "main"), (wsg, "cli_main"),
])
def test_live_cli_uses_env_cookie_without_password_or_post_when_unconfirmed(
        tmp_path, monkeypatch, capsys, module, entry):
    monkeypatch.setattr(wsg, "BASE", tmp_path)
    secret = "PHPSESSID=synthetic-session-value"
    monkeypatch.setenv("WEBAUTH_USER", secret)
    monkeypatch.setattr(
        module.getpass, "getpass",
        lambda *_args: pytest.fail("cookie auth must not prompt for a password"))
    monkeypatch.setattr(
        module, "live_update",
        lambda *_args, **_kwargs: pytest.fail("cookie auth must not use password login"))

    assert getattr(module, entry)([
        "--login", "Example", "--status", "synthetic", "--live"
    ]) == 3
    output = capsys.readouterr().out
    assert "refused" in output
    assert secret not in output


def test_stored_account_reads_the_environment(monkeypatch):
    monkeypatch.setenv("CAMFROG_USER", "Seaza")
    monkeypatch.setenv("CAMFROG_PASSWORD", "pw")
    assert ws.stored_account() == ("Seaza", "pw")
    monkeypatch.delenv("CAMFROG_PASSWORD")
    assert ws.stored_account() is None


def test_live_update_signs_in_then_updates(monkeypatch):
    """The cookie-free path: login -> session page -> gated update."""
    monkeypatch.setattr(ws, "attempt_login",
                        lambda *a: "https://profiles.camfrog.com/en/")
    monkeypatch.setattr(ws, "fetch_profile",
                        lambda *a: "var _user_id = '1'; var nick = 'Seaza'; "
                                   "nav-user-logged")
    seen = {}

    def fake_update(opener, status, timeout, confirm=False, expected_login=None):
        seen["confirm"] = confirm
        seen["status"] = status
        seen["expected_login"] = expected_login
        return True, "status updated."

    monkeypatch.setattr(ws, "perform_update", fake_update)
    code, message = ws.live_update("Seaza", "pw", "hello", 1.0, confirm=True)
    assert code == ws.EXIT_OK, message
    assert seen == {"confirm": True, "status": "hello",
                    "expected_login": "Seaza"}
    assert "Seaza" in message


def test_live_update_gated_without_confirmation(monkeypatch):
    monkeypatch.setattr(ws, "attempt_login",
                        lambda *a: "https://profiles.camfrog.com/en/")
    monkeypatch.setattr(ws, "fetch_profile", lambda *a: "var _user_id = '1'; var nick = 'Seaza';")
    monkeypatch.setattr(ws, "perform_update",
                        lambda *a, **k: (False, "update refused: not confirmed"))
    code, message = ws.live_update("Seaza", "pw", "hello", 1.0)
    assert code == ws.EXIT_BLOCKED
    assert "not confirmed" in message


def test_live_update_reports_wrong_password(monkeypatch):
    monkeypatch.setattr(ws, "attempt_login", lambda *a: "password")
    code, message = ws.live_update("Seaza", "pw", "hello", 1.0)
    assert code == ws.EXIT_USAGE
    assert "nickname or password incorrect" in message


def test_env_enc_is_gitignored():
    import subprocess
    root = Path(__file__).resolve().parents[1]
    rc = subprocess.run(["git", "-C", str(root), "check-ignore", ".env.enc"],
                        capture_output=True)
    assert rc.returncode == 0, ".env.enc must be git-ignored"


def _chrome_profile(tmp_path, name="Default", rows=((".camfrog.com", "sess", "plain", b""),),
                   network=False):
    import sqlite3

    profile = tmp_path / "chrome" / name
    if network:
        profile = profile / "Network"
    profile.mkdir(parents=True)
    db = profile / "Cookies"
    connection = sqlite3.connect(db)
    connection.execute("CREATE TABLE cookies(host_key TEXT, name TEXT, value TEXT, "
                       "encrypted_value BLOB, path TEXT, is_secure INTEGER, expires_utc INTEGER)")
    for host, cookie, value, encrypted in rows:
        connection.execute("INSERT INTO cookies VALUES(?,?,?,?,?,?,?)",
                           (host, cookie, value, encrypted, "/", 0, 0))
    connection.commit()
    connection.close()
    return tmp_path / "chrome"


def test_find_chrome_profiles_only_standard_dirs(tmp_path):
    base = _chrome_profile(tmp_path)
    (base / "Profile 1").mkdir()
    import sqlite3
    connection = sqlite3.connect(base / "Profile 1" / "Cookies")
    connection.execute("CREATE TABLE cookies(a)")
    connection.commit()
    connection.close()
    (base / "Other").mkdir()
    found = ws.find_chrome_profiles(str(base))
    assert [name for name, _db in found] == ["Default", "Profile 1"]
    assert ws.find_chrome_profiles(str(tmp_path / "nope")) == []


def test_find_chrome_profiles_network_layout(tmp_path):
    """Chrome 127+ keeps the cookie DB under <profile>/Network/Cookies."""
    base = _chrome_profile(tmp_path, network=True)
    (base / "Profile 1" / "Network").mkdir(parents=True)
    import sqlite3
    connection = sqlite3.connect(base / "Profile 1" / "Network" / "Cookies")
    connection.execute("CREATE TABLE cookies(a)")
    connection.commit()
    connection.close()
    found = ws.find_chrome_profiles(str(base))
    assert [name for name, _db in found] == ["Default", "Profile 1"]
    assert found[0][1].endswith("Network" + os.sep + "Cookies")


def test_find_chrome_profiles_prefers_network_over_legacy(tmp_path):
    """A stale legacy Cookies file must not shadow the live Network database."""
    base = _chrome_profile(tmp_path, network=True)
    (base / "Default" / "Cookies").write_bytes(b"stale legacy db")
    found = dict(ws.find_chrome_profiles(str(base)))
    assert found["Default"].endswith("Network" + os.sep + "Cookies")


def test_import_from_network_layout(tmp_path):
    jar = ws.import_chrome_jar(str(_chrome_profile(tmp_path, network=True)))
    assert len(jar) == 1
    assert [c.value for c in jar] == ["plain"]


def test_both_modules_find_the_network_layout(tmp_path):
    """The self-contained GUI copy must not drift from the source copy."""
    import web_status_gui as wsg

    base = _chrome_profile(tmp_path, network=True)
    for mod in (ws, wsg):
        found = dict(mod.find_chrome_profiles(str(base)))
        assert found["Default"].endswith("Network" + os.sep + "Cookies"), mod.__name__


def test_read_cookie_rows_reports_chrome_lock_clearly(tmp_path, monkeypatch):
    """Chrome locks its DB (sharing violation): say what to do, never guess."""
    base = _chrome_profile(tmp_path, network=True)
    db = str(base / "Default" / "Network" / "Cookies")

    def locked(*args, **kwargs):
        raise PermissionError(32, "sharing violation")

    monkeypatch.setattr(ws.shutil, "copyfile", locked)
    with pytest.raises(ValueError) as exc:
        ws.read_cookie_rows(db)
    assert "Close Chrome" in str(exc.value)
    assert "--cookies-file" in str(exc.value)


def test_import_chrome_jar_surfaces_the_lock_message(tmp_path, monkeypatch):
    def locked(*args, **kwargs):
        raise PermissionError(32, "sharing violation")

    monkeypatch.setattr(ws.shutil, "copyfile", locked)
    with pytest.raises(ValueError) as exc:
        ws.import_chrome_jar(str(_chrome_profile(tmp_path, network=True)))
    assert "Close Chrome" in str(exc.value)


def test_read_cookie_rows_prefers_the_snapshot_copy(tmp_path):
    base = _chrome_profile(tmp_path, network=True)
    rows = ws.read_cookie_rows(str(base / "Default" / "Network" / "Cookies"))
    assert rows and rows[0][1] == "sess"


def test_import_plain_values_and_redacts_summary(tmp_path):
    jar = ws.import_chrome_jar(str(_chrome_profile(tmp_path)))
    assert len(jar) == 1
    summary = ws.cookie_summary(jar)
    assert ".camfrog.com" in summary
    assert "plain" not in summary


def test_import_skips_foreign_and_evil_domains(tmp_path):
    base = _chrome_profile(tmp_path, rows=(
        ("other.com", "a", "x", b""),
        ("evilcamfrog.com", "b", "y", b""),
        ("profiles.camfrog.com", "c", "z", b"")))
    jar = ws.import_chrome_jar(str(base))
    assert sorted(c.name for c in jar) == ["c"]


def test_import_decrypts_blobs_without_values(tmp_path, monkeypatch):
    seen = []

    def fake_decrypt(blob, user_data=""):
        seen.append((blob, user_data))
        return "decrypted!"

    monkeypatch.setattr(ws, "decrypt_chrome_value", fake_decrypt)
    base = _chrome_profile(tmp_path, rows=((".camfrog.com", "s", "", b"\x01\x02"),))
    jar = ws.import_chrome_jar(str(base))
    assert [c.value for c in jar] == ["decrypted!"]
    assert seen and seen[0][0] == b"\x01\x02"


def test_import_reports_bad_decrypt_without_values(tmp_path, monkeypatch, capsys):
    def boom(blob, user_data=""):
        raise ValueError("DPAPI could not decrypt this cookie")

    monkeypatch.setattr(ws, "decrypt_chrome_value", boom)
    base = _chrome_profile(tmp_path, rows=((".camfrog.com", "s", "", b"\x01"),))
    with pytest.raises(ValueError) as exc:
        ws.import_chrome_jar(str(base))
    assert "no usable camfrog.com cookies" in str(exc.value)
    assert "\x01" not in str(exc.value)


def test_import_missing_store_is_a_clean_error(tmp_path):
    with pytest.raises(ValueError) as exc:
        ws.import_chrome_jar(str(tmp_path / "nothing"))
    assert "no Chrome cookie store" in str(exc.value)


def test_dpapi_needs_windows():
    with pytest.raises(ValueError) as exc:
        ws.dpapi_unprotect(b"\x01\x02")
    assert "Windows" in str(exc.value)


def test_aes_cookie_without_library_is_a_clean_error(monkeypatch):
    import builtins

    original_import = builtins.__import__

    def import_without_cryptography(name, *args, **kwargs):
        if name == "cryptography" or name.startswith("cryptography."):
            raise ImportError("cryptography intentionally unavailable for this test")
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", import_without_cryptography)
    with pytest.raises(ValueError) as exc:
        ws.decrypt_chrome_value(b"v10" + b"\x00" * 30, "")
    assert "cryptography" in str(exc.value)


def test_probe_chrome_flag_conflicts_with_file():
    with pytest.raises(SystemExit) as exc:
        ws.main(["--login", "Seaza", "--status", "hi", "--probe",
                 "--chrome", "--cookies-file", "x.txt"])
    assert exc.value.code == 2


def test_probe_chrome_missing_store(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv(ws.CHROME_ENV_OVERRIDE, str(tmp_path / "nothing"))
    assert ws.main(["--login", "Seaza", "--status", "hi", "--probe", "--chrome"]) == 2
    assert "no Chrome cookie store" in capsys.readouterr().out


def test_probe_chrome_reports_verdict(tmp_path, monkeypatch, capsys):
    base = _chrome_profile(tmp_path)
    monkeypatch.setenv(ws.CHROME_ENV_OVERRIDE, str(base))
    fake = FakeOpener(b"<title>Camfrog - Login Page</title>Sign On")
    monkeypatch.setattr(ws.urllib.request, "build_opener", lambda *a: fake)
    assert ws.main(["--login", "Seaza", "--status", "hi", "--probe", "--chrome"]) == 0
    out = capsys.readouterr().out
    assert "from Chrome" in out and "NOT signed in" in out
    assert "plain" not in out


def test_data_blob_pins_backing_buffer():
    blob = ws._data_blob(b"abc")
    assert blob.cbData == 3
    assert blob._backing.value == b"abc"
    import ctypes
    assert ctypes.string_at(blob.pbData, blob.cbData) == b"abc"


@pytest.mark.parametrize("content", [
    "{not json",
    "[]",
    '{"os_crypt": "nope"}',
    '{"os_crypt": {}}',
    '{"os_crypt": {"encrypted_key": "!!!not-base64!!!"}}',
])
def test_chrome_aes_key_malformed_local_state(tmp_path, content):
    local_state = tmp_path / "Local State"
    local_state.write_text(content, encoding="utf-8")
    with pytest.raises(ValueError) as exc:
        ws.chrome_aes_key(str(tmp_path))
    assert "Local State" in str(exc.value)


def test_chrome_aes_key_missing_file(tmp_path):
    with pytest.raises(ValueError):
        ws.chrome_aes_key(str(tmp_path / "nothing"))


@pytest.mark.parametrize(("reply", "state"), [
    ('{}', "outcome unknown"),
    ('[]', "outcome unknown"),
    ('null', "outcome unknown"),
    ('{"response":""}', "outcome unknown"),
    ('{"response":"   "}', "outcome unknown"),
    ('{"response":null}', "outcome unknown"),
    ('{"error":true,"response":"denied"}', "refused"),
])
def test_perform_update_classifies_unacknowledged_server_reply(
        monkeypatch, reply, state):
    monkeypatch.setattr(ws, "fetch_profile",
                        lambda *a: "var _user_id = '1'; var nick = 'Seaza'; "
                        "var csrf = '90d230418b54c662c347d84f32f663e0a94fc5abd9d705a0';")
    monkeypatch.setattr(ws, "_post", lambda *a: reply)
    ok, message = ws.perform_update(
        object(), "new status", 1.0, confirm=True, expected_login="Seaza")
    assert not ok
    assert state in message


@pytest.mark.parametrize("module", [ws, wsg], ids=["cli", "gui"])
def test_perform_update_rejects_mismatched_account_before_post(monkeypatch, module):
    monkeypatch.setattr(
        module, "fetch_profile",
        lambda *a: "var _user_id = '2'; var nick = 'DifferentAccount'; "
        "var csrf = '90d230418b54c662c347d84f32f663e0a94fc5abd9d705a0';")
    posts = []
    monkeypatch.setattr(module, "_post", lambda *a: posts.append(a))

    ok, message = module.perform_update(
        object(), "status", 2.0, confirm=True, expected_login="Seaza")

    assert not ok
    assert "does not match" in message
    assert posts == []


@pytest.mark.parametrize("module", [ws, wsg], ids=["cli", "gui"])
def test_perform_update_requires_selected_account_before_fetch(monkeypatch, module):
    fetches = []
    monkeypatch.setattr(module, "fetch_profile", lambda *a: fetches.append(a))

    ok, message = module.perform_update(object(), "status", 2.0, confirm=True)

    assert not ok
    assert "expected Camfrog account identity" in message
    assert fetches == []


def test_perform_update_reports_safe_response_diagnostics(monkeypatch):
    csrf = "90d230418b54c662c347d84f32f663e0a94fc5abd9d705a0"
    server_response = (
        "<html><head><title>Request denied</title>"
        "<script>var csrf = '{0}';</script></head>"
        "<body><p>Rate limited for private status</p>"
        "<p>Contact admin@example.test, reference 123456789</p>"
        "<input type='hidden' name='csrf' value='{0}'></body></html>".format(csrf))
    monkeypatch.setattr(
        wsg, "fetch_profile",
        lambda *a: "var _user_id = '1'; var nick = 'Seaza'; var csrf = '{0}';".format(csrf))
    monkeypatch.setattr(
        wsg, "_post", lambda *a: json.dumps({
            "error": True, "response": server_response, "ok": False}))

    ok, message = wsg.perform_update(
        object(), "private status", 2.0, confirm=True, expected_login="Seaza")

    assert not ok
    assert "response_keys=error,ok,response" in message
    assert "server_response=string(" in message
    assert "Request denied" in message
    assert "Rate limited" in message
    assert "[email redacted]" in message
    assert "[number redacted]" in message
    assert "response_chars=" in message
    assert "private status" not in message
    assert csrf not in message
    assert "var csrf" not in message


def test_perform_update_includes_http_metadata_for_unconfirmed_reply(monkeypatch):
    monkeypatch.setattr(wsg, "fetch_profile",
                        lambda *a: "var _user_id = '1'; var nick = 'Seaza'; "
                        "var csrf = '90d230418b54c662c347d84f32f663e0a94fc5abd9d705a0';")
    monkeypatch.setattr(
        wsg, "_post",
        lambda *a: wsg.WebStatusReply(
            '{"response":""}', status_code=503,
            content_type="application/json"))

    ok, message = wsg.perform_update(
        object(), "status", 2.0, confirm=True, expected_login="Seaza")

    assert not ok
    assert "outcome unknown" in message
    assert "http_status=503" in message
    assert "content_type=application/json" in message
    assert "response_chars=15" in message


@pytest.mark.parametrize("module", [ws, wsg], ids=["cli", "gui"])
def test_perform_update_rejects_non_success_http_even_with_success_json(
        monkeypatch, module):
    monkeypatch.setattr(
        module, "fetch_profile",
        lambda *a: "var _user_id = '1'; var nick = 'Seaza'; "
        "var csrf = '90d230418b54c662c347d84f32f663e0a94fc5abd9d705a0';")
    monkeypatch.setattr(
        module, "_post",
        lambda *a: wsg.WebStatusReply(
            '{"response":"accepted"}', status_code=503,
            content_type="application/json"))

    ok, message = module.perform_update(
        object(), "status", 2.0, confirm=True, expected_login="Seaza")

    assert not ok
    assert "HTTP" in message and "503" in message
    assert "outcome unknown" in message


@pytest.mark.parametrize("module", [ws, wsg], ids=["cli", "gui"])
def test_perform_update_marks_post_transport_error_outcome_unknown(
        monkeypatch, module):
    monkeypatch.setattr(
        module, "fetch_profile",
        lambda *a: "var _user_id = '1'; var nick = 'Seaza'; "
        "var csrf = '90d230418b54c662c347d84f32f663e0a94fc5abd9d705a0';")

    def fail_post(*_args):
        raise TimeoutError("simulated network timeout")

    monkeypatch.setattr(module, "_post", fail_post)
    ok, message = module.perform_update(
        object(), "private status", 2.0, confirm=True,
        expected_login="Seaza")

    assert not ok
    assert "outcome unknown" in message
    assert "check the profile before retrying" in message
    assert "private status" not in message


@pytest.mark.parametrize("module", [ws, wsg], ids=["cli", "gui"])
@pytest.mark.parametrize("reply", ['{"response":"new status"}', '{"response":"ok"}'])
def test_perform_update_accepts_profile_ui_success_response(monkeypatch, module, reply):
    monkeypatch.setattr(module, "fetch_profile",
                        lambda *a: "var _user_id = '1'; var nick = 'Seaza'; "
                        "var csrf = '90d230418b54c662c347d84f32f663e0a94fc5abd9d705a0';")
    monkeypatch.setattr(module, "_post", lambda *a: reply)

    ok, message = module.perform_update(
        object(), "new status", 1.0, confirm=True, expected_login="Seaza")

    assert ok, message


def test_live_update_requires_verified_session_before_post(monkeypatch):
    monkeypatch.setattr(ws, "attempt_login", lambda *a: "https://profiles.camfrog.com/en/")
    monkeypatch.setattr(ws, "fetch_profile", lambda *a: "var nick = 'Seaza';")
    def unexpected_update(*args, **kwargs):
        raise AssertionError("No POST before confirming signed-in session")
    monkeypatch.setattr(ws, "perform_update", unexpected_update)
    code, message = ws.live_update("Seaza", "password", "hello", 1.0, confirm=True)
    assert code == ws.EXIT_BLOCKED
    assert "not confirmed signed in" in message


def test_unverified_session_explains_confirmation_and_blocks_post(monkeypatch):
    monkeypatch.setattr(ws, "attempt_login", lambda *a: "https://profiles.camfrog.com/en/")
    monkeypatch.setattr(ws, "fetch_profile", lambda *a: "<title>Camfrog</title>")
    monkeypatch.setattr(ws, "perform_update", lambda *a, **kw: (_ for _ in ()).throw(AssertionError("must not POST")))
    code, message = ws.live_update("test", "secret", "hi", 1.0, confirm=False)
    assert code == ws.EXIT_BLOCKED
    assert "not confirmed signed in" in message
    assert "--confirm-update" in message
