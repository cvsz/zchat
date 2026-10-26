"""Publish a GitHub release from the built dist/ artifacts.

Usage:
  python tools/release.py              # tag from camfrog_auto.APP_VERSION, upload assets
  python tools/release.py --release APP_VERSION  # require version to match configured APP_VERSION
  python tools/release.py --dry-run    # print the plan, change nothing

Run full-build.bat on the exact clean `main` commit after its PR checks pass.
The tag is pinned to that commit. The release is created with the authenticated
`gh` CLI (no token is stored in this repo); `gh auth status` must succeed.

Assets: every feature exe (stable names so the apps' self-update can find their
own file), the ZChat agent chat client, line-status-changer exe + generic
default config, the all-features zip, and SHA256SUMS.txt (the self-update
verifies downloads against it). Local LINE settings are never included.
"""
import argparse
import hashlib
import json
import re
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DIST = ROOT / "dist"
BUILD_SOURCE = DIST / "BUILD_SOURCE.txt"

# Running as `python tools/release.py` puts tools/ (not the repository root)
# on sys.path. Resolve the project's LINE defaults through the checkout.
sys.path.insert(0, str(ROOT))
from line.config_store import default_config

FEATURE_EXES = {
    "room-control.exe": DIST / "room-control" / "room-control.exe",
    "chat-im-private.exe": DIST / "chat-im-private" / "chat-im-private.exe",
    "status-random.exe": DIST / "status-random" / "status-random.exe",
    "status-marquee.exe": DIST / "status-marquee" / "status-marquee.exe",
    "status-changer.exe": DIST / "status-changer" / "status-changer.exe",
    "im-autoreply.exe": DIST / "im-autoreply" / "im-autoreply.exe",
    "AutoDJ.exe": DIST / "auto-dj" / "AutoDJ.exe",
    "web-status.exe": DIST / "web-status" / "web-status.exe",
    "CloudBot-Manager.exe": DIST / "cloudbot-manager" / "CloudBot-Manager.exe",
    "name-color-changer.exe": DIST / "name-color-changer" / "name-color-changer.exe",
    "icon-changer.exe": DIST / "icon-changer" / "icon-changer.exe",
    "name-changer.exe": DIST / "name-changer" / "name-changer.exe",
}
OTHER_ASSETS = {
    "zchat-client.exe": DIST / "zchat-client" / "zchat-client.exe",
    "line-status-changer.exe": ROOT / "line" / "dist" / "line-status-changer.exe",
    "camfrog-features-windows.zip": ROOT / "camfrog-features-windows.zip",
}


def app_version():
    src = (ROOT / "camfrog_auto.py").read_text(encoding="utf-8")
    match = re.search(r'^APP_VERSION = "([^\"]+)\"', src, re.M)
    if not match:
        raise SystemExit("APP_VERSION not found in camfrog_auto.py")
    return match.group(1)


def flat_sums(assets):
    """SHA256SUMS.txt with flat basenames.

    The dist-relative sums (room-control/room-control.exe) do not match the
    flat asset names, and v2.19.0 clients only accept exact names, so the
    release carries this basename form: both old and new clients verify it.
    """
    import hashlib

    lines = []
    for name, path in assets:
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        lines.append("{0}  {1}".format(digest, name))
    return "\n".join(lines) + "\n"


def release_notes(version):
    """The CHANGELOG section for this version, or a short default."""
    text = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    marker = "## [{0}]".format(version)
    if marker in text:
        body = text.split(marker, 1)[1]
        body = body.split("\n## [", 1)[0].strip()
        if body:
            return body
    return "ZChat and Camfrog Windows apps v{0}.\n\nSee CHANGELOG.md for details.".format(
        version
    )


def check_assets():
    missing = [name for name, path in list(FEATURE_EXES.items()) + list(OTHER_ASSETS.items())
               if not path.is_file()]
    if missing:
        raise SystemExit("missing build outputs (run full-build.bat): {0}".format(
            ", ".join(sorted(missing))))


def write_public_line_config(path):
    """Write the generic defaults, never the machine's saved LINE settings."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(default_config(), ensure_ascii=False, indent=2) + "\n",
                    encoding="utf-8", newline="\n")


def release_target():
    """Require a clean main checkout that exactly matches origin/main."""
    def git(*args):
        result = subprocess.run(["git", *args], cwd=ROOT, capture_output=True,
                                text=True)
        if result.returncode:
            raise SystemExit("could not verify release source: git " + " ".join(args))
        return result.stdout.strip()

    if git("status", "--porcelain", "--untracked-files=normal"):
        raise SystemExit("release requires a clean working tree")
    branch = git("branch", "--show-current")
    if branch != "main":
        raise SystemExit("release requires the main branch after PR merge")
    head = git("rev-parse", "HEAD")
    remote = git("ls-remote", "origin", "refs/heads/main").split()
    if not remote or remote[0] != head:
        raise SystemExit("release HEAD must exactly match origin/main")
    return head


def verify_build_source(target):
    """Require full-build.bat's marker to bind artifacts to this clean HEAD."""
    try:
        data = {}
        for line in BUILD_SOURCE.read_text(encoding="utf-8").splitlines():
            key, separator, value = line.partition("=")
            if not separator or key in data:
                raise ValueError("invalid build source marker")
            data[key] = value
    except (OSError, ValueError) as exc:
        raise SystemExit("missing or invalid dist/BUILD_SOURCE.txt; run full-build.bat") from exc
    commit = data.get("commit", "")
    if data.get("clean") != "true":
        raise SystemExit("release artifacts were built from a dirty working tree")
    if commit != target:
        raise SystemExit("build source commit does not match release HEAD")


def remote_tag_target(tag):
    """Return the remote tag's commit, peeling annotated tags when necessary."""
    base_ref = "refs/tags/" + tag
    result = subprocess.run(
        ["git", "ls-remote", "--tags", "origin", base_ref, base_ref + "^{}"],
        cwd=ROOT, capture_output=True, text=True)
    if result.returncode:
        raise SystemExit("could not verify remote release tag")
    refs = {}
    for line in result.stdout.splitlines():
        fields = line.split("\t", 1)
        if len(fields) == 2:
            refs[fields[1]] = fields[0]
    return refs.get(base_ref + "^{}") or refs.get(base_ref)


def verify_remote_tag(tag, target):
    existing = remote_tag_target(tag)
    if existing and existing != target:
        raise SystemExit("remote tag {0} points to {1}, not verified release HEAD {2}".format(
            tag, existing, target))


def verify_feature_hashes():
    """Verify the packaged feature EXEs against the build-produced SHA256SUMS."""
    manifest = DIST / "SHA256SUMS.txt"
    if not manifest.is_file():
        raise SystemExit("missing dist/SHA256SUMS.txt; run a full build first")
    expected = {str(path.relative_to(DIST)).replace("\\", "/"): path
                for path in FEATURE_EXES.values()}
    found = {}
    for line in manifest.read_text(encoding="utf-8").splitlines():
        parts = line.split(maxsplit=1)
        if len(parts) != 2 or not re.fullmatch(r"[0-9a-fA-F]{64}", parts[0]):
            raise SystemExit("invalid SHA256SUMS.txt entry")
        name = parts[1].lstrip("*").replace("\\", "/")
        if name in found or name not in expected:
            raise SystemExit("unexpected or duplicate SHA256SUMS entry: " + name)
        found[name] = parts[0].lower()
    if set(found) != set(expected):
        raise SystemExit("SHA256SUMS.txt does not cover exactly the expected feature EXEs")
    for name, path in expected.items():
        h = hashlib.sha256()
        with path.open("rb") as source:
            for chunk in iter(lambda: source.read(1024 * 1024), b""):
                h.update(chunk)
        if h.hexdigest() != found[name]:
            raise SystemExit("artifact checksum mismatch: " + name)


def verify_zchat_hash():
    """Verify the separate ZChat executable against its build-produced manifest."""
    executable = DIST / "zchat-client" / "zchat-client.exe"
    manifest = DIST / "zchat-client" / "SHA256SUMS.txt"
    if not manifest.is_file():
        raise SystemExit("missing dist/zchat-client/SHA256SUMS.txt; run a full build first")
    entries = manifest.read_text(encoding="ascii").splitlines()
    if len(entries) != 1:
        raise SystemExit("invalid zchat-client/SHA256SUMS.txt manifest")
    parts = entries[0].split(maxsplit=1)
    if (
        len(parts) != 2
        or not re.fullmatch(r"[0-9a-fA-F]{64}", parts[0])
        or parts[1].lstrip("*").replace("\\", "/") != "zchat-client.exe"
    ):
        raise SystemExit("invalid zchat-client/SHA256SUMS.txt entry")
    digest = hashlib.sha256()
    with executable.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    if digest.hexdigest() != parts[0].lower():
        raise SystemExit("artifact checksum mismatch: zchat-client.exe")


def gh_ready():
    result = subprocess.run(["gh", "auth", "status"], capture_output=True)
    return result.returncode == 0


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--tag", default=None, help="override release tag (must match APP_VERSION)")
    parser.add_argument("--release", nargs="?", const="", default=None,
                        help="require release version to match APP_VERSION")
    args = parser.parse_args(argv)

    version = app_version()
    expected_tag = "v{0}".format(version)
    if args.release is not None:
        requested_tag = args.release or expected_tag
        if not re.fullmatch(r"v\d+\.\d+\.\d+", requested_tag):
            raise SystemExit("invalid release version; expected v followed by three numeric components")
        if requested_tag != expected_tag:
            raise SystemExit("release version must match APP_VERSION before publishing")
    tag = args.tag or expected_tag
    if tag != "v{0}".format(version):
        raise SystemExit("release tag must match APP_VERSION to prevent mismatched releases")
    check_assets()
    verify_feature_hashes()
    verify_zchat_hash()
    notes = release_notes(version)
    with tempfile.TemporaryDirectory(prefix="camfrog-release-") as temp_dir:
        public_config = Path(temp_dir) / "line_config.json"
        write_public_line_config(public_config)
        pairs = (list(FEATURE_EXES.items())
                 + list(OTHER_ASSETS.items())
                 + [("line_config.json", public_config)])
        sums_path = Path(temp_dir) / "SHA256SUMS.txt"
        sums_path.write_text(flat_sums(pairs), encoding="utf-8", newline="\n")
        pairs.append(("SHA256SUMS.txt", sums_path))
        assets = [str(path) for _name, path in pairs]
        asset_names = [name for name, _path in pairs]

        print("release : {0}".format(tag))
        print("title   : ZChat and Camfrog Windows apps {0}".format(version))
        print("notes   : {0} chars from CHANGELOG.md".format(len(notes)))
        for name in asset_names:
            print("  asset : {0}".format(name))
        if args.dry_run:
            print("dry-run: nothing was created.")
            return 0
        target = release_target()
        verify_build_source(target)
        verify_remote_tag(tag, target)
        if not gh_ready():
            print("gh is not authenticated; run `gh auth login` first.")
            return 2
        exists = subprocess.run(["gh", "release", "view", tag], capture_output=True)
        if exists.returncode == 0:
            print("release {0} already exists; refusing to overwrite published assets.".format(tag))
            return 3
        with tempfile.NamedTemporaryFile("w", suffix=".md", delete=False,
                                         encoding="utf-8") as handle:
            handle.write(notes)
            notes_path = handle.name
        try:
            command = (["gh", "release", "create", tag] + assets
                       + ["--target", target,
                          "--title", "ZChat and Camfrog Windows apps {0}".format(version),
                          "--notes-file", notes_path])
            return subprocess.run(command).returncode
        finally:
            Path(notes_path).unlink(missing_ok=True)


if __name__ == "__main__":
    sys.exit(main())
