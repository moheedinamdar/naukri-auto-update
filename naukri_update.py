#!/usr/bin/env python3
"""Keep a Naukri.com profile near the top of recruiter searches, automatically.

Naukri ranks profiles heavily by *how recently they were updated*. Its new
profile UI has no editable "resume headline" field, so this tool logs in and
re-uploads your resume PDF, which refreshes the profile's "last updated" time.

Browser (env NAUKRI_BROWSER): chromium (bundled, default) | chrome | msedge.
Whichever you pick runs HEADFUL with its OWN dedicated, git-ignored profile dir,
so your real Chrome/Edge profiles are never opened or modified. Headless does NOT
work: Naukri's Akamai bot protection serves an "Access Denied" page to headless
browsers.

Credentials come ONLY from environment variables (see .env.example). Nothing
sensitive is ever printed to the terminal or written into the repo.

    python naukri_update.py             # do the update (called by ./run.sh)
    python naukri_update.py --preflight # validate setup, no browser launched
"""
from __future__ import annotations

import argparse
import logging
import os
import re
import shutil
import stat
import subprocess
import sys
import time
from pathlib import Path

from playwright.sync_api import Page, TimeoutError as PWTimeout, sync_playwright

# --- Paths -----------------------------------------------------------------
HERE = Path(__file__).resolve().parent
ARTIFACTS = HERE / "artifacts"
RESUME_DIR = HERE / "resume"

# --- URLs ------------------------------------------------------------------
HOME_URL = "https://www.naukri.com/"
LOGIN_URL = "https://www.naukri.com/nlogin/login"
PROFILE_URL = "https://www.naukri.com/mnjuser/profile"

# --- Browser selection -----------------------------------------------------
# Pick with NAUKRI_BROWSER. Each gets its OWN dedicated profile dir (git-ignored),
# so your real system browser profiles are never touched.
SUPPORTED_BROWSERS = {
    "chromium": None,     # Playwright's bundled Chromium (no system browser needed)
    "chrome": "chrome",   # system Google Chrome, in an isolated profile
    "msedge": "msedge",   # system Microsoft Edge, in an isolated profile
}
PROFILE_DIRS = {
    "chromium": HERE / ".chromium-profile",
    "chrome": HERE / ".chrome-profile",
    "msedge": HERE / ".edge-profile",
}

MAX_RESUME_MB = 2  # Naukri's stated resume size limit

# Injected before every page load to hide the most obvious automation tells.
STEALTH_JS = """
Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
window.chrome = window.chrome || { runtime: {} };
Object.defineProperty(navigator, 'languages', {get: () => ['en-IN', 'en']});
Object.defineProperty(navigator, 'plugins', {get: () => [1, 2, 3, 4, 5]});
"""

# --- Selectors -------------------------------------------------------------
# Naukri is a single-page app whose markup changes now and then. If a step fails,
# open the artifacts/*.png screenshot and update the matching entry.
SEL = {
    "user": "#usernameField",
    "pass": "#passwordField",
    "login_btn": "button[type='submit']",
    # Naukri "TopTier" profile UI: resume upload is a hidden file input.
    "resume_input": "input#resume[type='file']",
    "resume_update_btn": "button:has-text('Update')",
    # Signs of an OTP / "verify it's you" challenge:
    "otp_hint": "text=/one[- ]?time password|enter otp|verify it'?s you/i",
}

# --- Exit codes ------------------------------------------------------------
EXIT_OK = 0
EXIT_UNEXPECTED = 1
EXIT_CONFIG = 2
EXIT_LOGIN = 3
EXIT_RESUME = 4
EXIT_BLOCKED = 5
EXIT_GIT_SAFETY = 6

EXIT_MEANING = {
    EXIT_OK: "success",
    EXIT_UNEXPECTED: "unexpected error",
    EXIT_CONFIG: "configuration / credentials problem",
    EXIT_LOGIN: "login or OTP challenge",
    EXIT_RESUME: "resume file problem",
    EXIT_BLOCKED: "blocked by Naukri's Akamai bot protection",
    EXIT_GIT_SAFETY: "aborted: sensitive files are tracked by git",
}

PLACEHOLDER_VALUES = {"", "you@example.com", "your-naukri-password", "changeme"}


class HardFail(Exception):
    """Stop the run with a specific exit code and a friendly, actionable message."""

    def __init__(self, code: int, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


# --- Logging ---------------------------------------------------------------
log = logging.getLogger("naukri")
_START = time.monotonic()
_STEP = {"n": 0, "total": 0}
_LEVEL_SHORT = {"WARNING": "WARN", "CRITICAL": "CRIT"}


class _Fmt(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        elapsed = time.monotonic() - _START
        ts = time.strftime("%H:%M:%S")
        level = _LEVEL_SHORT.get(record.levelname, record.levelname)
        return f"[{ts} +{elapsed:5.1f}s] {level:<5} {record.getMessage()}"


def setup_logging() -> None:
    level = logging.DEBUG if os.environ.get("NAUKRI_DEBUG") == "1" else logging.INFO
    log.setLevel(level)
    log.handlers.clear()
    stream = logging.StreamHandler(sys.stdout)
    stream.setFormatter(_Fmt())
    log.addHandler(stream)

    logfile = os.environ.get("NAUKRI_LOG_FILE", "").strip()
    if logfile:
        try:
            path = Path(logfile)
            if not path.is_absolute():
                path = ARTIFACTS / logfile
            path.parent.mkdir(parents=True, exist_ok=True)
            handler = logging.FileHandler(path, encoding="utf-8")
            handler.setFormatter(_Fmt())
            log.addHandler(handler)
            log.debug(f"also logging to {path}")
        except Exception as exc:
            log.warning(f"could not open NAUKRI_LOG_FILE ({exc}); logging to stdout only")


def step(msg: str) -> None:
    _STEP["n"] += 1
    total = _STEP["total"]
    prefix = f"STEP {_STEP['n']}/{total}" if total else f"STEP {_STEP['n']}"
    log.info(f"{prefix}  {msg}")


def redact_email(email: str) -> str:
    email = (email or "").strip()
    if "@" not in email:
        return "***"
    user, _, domain = email.partition("@")
    name, _, tld = domain.partition(".")

    def mask(s: str) -> str:
        return (s[0] + "***") if s else "***"

    return f"{mask(user)}@{mask(name)}.{tld or '***'}"


def shot(page: Page, name: str) -> None:
    """Save a screenshot for debugging (best-effort; git-ignored)."""
    try:
        ARTIFACTS.mkdir(parents=True, exist_ok=True)
        path = ARTIFACTS / f"{name}.png"
        page.screenshot(path=str(path), full_page=True)
        log.info(f"saved screenshot -> artifacts/{path.name} (may show personal data; git-ignored)")
    except Exception as exc:  # pragma: no cover - screenshots are non-critical
        log.debug(f"could not save screenshot {name}: {exc}")


# --- Git-safety guardrail --------------------------------------------------

def _git(*args: str) -> str | None:
    try:
        out = subprocess.run(
            ["git", *args], cwd=str(HERE), capture_output=True, text=True, timeout=10
        )
    except Exception:
        return None
    return out.stdout if out.returncode == 0 else None


def _is_sensitive(path: str) -> bool:
    base = os.path.basename(path)
    if base in (".env.example",) or path in ("resume/.gitkeep", "resume/README.md"):
        return False
    if base == ".env" or base.startswith(".env.") or base == "storage_state.json":
        return True
    if "-profile/" in (path + "/") or path.startswith("artifacts/"):
        return True
    if path.startswith("resume/") and base not in (".gitkeep", "README.md"):
        return True
    if path.endswith(".plist") and not path.endswith(".plist.template"):
        return True
    return False


def git_tracked_sensitive() -> list[str]:
    """Sensitive paths git is tracking or has staged (should always be empty)."""
    if _git("rev-parse", "--is-inside-work-tree") is None:
        return []
    found = set()
    for cmd in (("ls-files",), ("diff", "--cached", "--name-only")):
        for line in (_git(*cmd) or "").splitlines():
            line = line.strip()
            if line and _is_sensitive(line):
                found.add(line)
    return sorted(found)


# --- Resume resolution -----------------------------------------------------

def _looks_like_pdf(path: Path) -> bool:
    try:
        with open(path, "rb") as fh:
            return fh.read(5).startswith(b"%PDF")
    except Exception:
        return False


def _validate_resume(path: Path) -> Path:
    if not path.exists() or not path.is_file():
        raise HardFail(EXIT_RESUME, f"resume file not found: {path}")
    size = path.stat().st_size
    if size == 0:
        raise HardFail(EXIT_RESUME, f"resume file is empty: {path}")
    if path.suffix.lower() == ".pdf" and not _looks_like_pdf(path):
        raise HardFail(EXIT_RESUME, f"not a valid PDF (missing %PDF header): {path}")
    mb = size / (1024 * 1024)
    if mb > MAX_RESUME_MB:
        log.warning(
            f"resume is {mb:.1f} MB — Naukri's limit is ~{MAX_RESUME_MB} MB; "
            "the upload may be rejected."
        )
    return path


def _clean_path(raw: str) -> str:
    raw = raw.strip().strip('"').strip("'").strip()
    raw = raw.replace("\\ ", " ")  # macOS drag-and-drop escapes spaces
    return os.path.expandvars(os.path.expanduser(raw))


def _bootstrap_resume() -> Path:
    """No resume found — ask for an absolute path (once) and copy it into resume/."""
    if not sys.stdin.isatty():
        raise HardFail(
            EXIT_RESUME,
            "No resume found in ./resume/ and not running interactively.\n"
            "    Add a PDF to ./resume/  (or set NAUKRI_RESUME_PATH in .env), then re-run.",
        )
    log.warning("No resume found in ./resume/ — let's set one up (one time only).")
    print("\n  Enter the ABSOLUTE path to your resume PDF (or press Enter to cancel):", flush=True)
    for _ in range(5):
        try:
            raw = input("  resume path> ")
        except (EOFError, KeyboardInterrupt):
            raise HardFail(EXIT_RESUME, "cancelled — no resume provided.")
        if not raw.strip():
            raise HardFail(EXIT_RESUME, "cancelled — no resume provided.")
        src = Path(_clean_path(raw))
        if not src.exists() or not src.is_file():
            print(f"  ! not found: {src}", flush=True)
            continue
        if src.suffix.lower() != ".pdf" or not _looks_like_pdf(src):
            print(f"  ! not a valid PDF: {src}", flush=True)
            continue
        try:
            RESUME_DIR.mkdir(parents=True, exist_ok=True)
            dest = RESUME_DIR / src.name
            shutil.copy2(src, dest)
        except Exception as exc:
            raise HardFail(EXIT_RESUME, f"could not copy resume into ./resume/: {exc}")
        log.info(f"copied resume -> resume/{dest.name} (git-ignored; reused automatically next time)")
        return _validate_resume(dest)
    raise HardFail(EXIT_RESUME, "too many invalid attempts — no resume provided.")


def resolve_resume() -> Path:
    """Find the resume to upload (env override -> resume/ folder -> first-run prompt)."""
    env_path = os.environ.get("NAUKRI_RESUME_PATH", "").strip()
    if env_path:
        p = Path(_clean_path(env_path))
        if not p.is_absolute():
            p = (HERE / env_path).resolve()
        log.debug(f"using NAUKRI_RESUME_PATH -> {p}")
        return _validate_resume(p)

    env_name = os.environ.get("NAUKRI_RESUME_NAME", "").strip()
    if env_name:
        log.debug(f"using NAUKRI_RESUME_NAME -> resume/{env_name}")
        return _validate_resume((RESUME_DIR / env_name).resolve())

    pdfs = (
        sorted(RESUME_DIR.glob("*.pdf"), key=lambda p: p.stat().st_mtime, reverse=True)
        if RESUME_DIR.exists()
        else []
    )
    if len(pdfs) == 1:
        return _validate_resume(pdfs[0])
    if len(pdfs) > 1:
        chosen = pdfs[0]
        others = ", ".join(p.name for p in pdfs[1:])
        log.warning(
            f"multiple resumes in ./resume/ — using the newest: {chosen.name} "
            f"(others: {others}). Override with NAUKRI_RESUME_NAME=<file> in .env."
        )
        return _validate_resume(chosen)
    return _bootstrap_resume()


# --- Preflight -------------------------------------------------------------

def preflight() -> Path:
    """Validate everything before touching a browser. Returns the resume path."""
    step("Preflight checks")

    # 1) Git-safety FIRST — never run (or leak) if secrets are tracked.
    tracked = git_tracked_sensitive()
    if tracked:
        listing = "\n    - ".join(tracked)
        raise HardFail(
            EXIT_GIT_SAFETY,
            "Sensitive files are tracked by git — refusing to run so they can't leak:\n"
            f"    - {listing}\n"
            "    Fix: git rm --cached <file> ; confirm .gitignore covers it. If it was "
            "already pushed, rotate the secret. See SECURITY.md.",
        )

    # 2) Credentials.
    problems = []
    email = os.environ.get("NAUKRI_EMAIL", "").strip()
    password = os.environ.get("NAUKRI_PASSWORD", "").strip()
    if email in PLACEHOLDER_VALUES:
        problems.append("NAUKRI_EMAIL is missing or still the placeholder — edit .env")
    elif not re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", email):
        problems.append(f"NAUKRI_EMAIL doesn't look like an email ({redact_email(email)})")
    if password in PLACEHOLDER_VALUES:
        problems.append("NAUKRI_PASSWORD is missing or still the placeholder — edit .env")
    if problems:
        raise HardFail(EXIT_CONFIG, "Configuration problems:\n    - " + "\n    - ".join(problems))

    # 3) Browser choice (warn only; we fall back to bundled chromium).
    browser = os.environ.get("NAUKRI_BROWSER", "chromium").strip().lower()
    if browser not in SUPPORTED_BROWSERS:
        log.warning(
            f"unknown NAUKRI_BROWSER='{browser}' — will use bundled chromium. "
            f"Valid values: {', '.join(SUPPORTED_BROWSERS)}."
        )
        browser = "chromium"

    # 4) .env permission hygiene.
    env_file = HERE / ".env"
    if env_file.exists() and (stat.S_IMODE(env_file.stat().st_mode) & 0o077):
        log.warning("your .env is readable by other users. Run:  chmod 600 .env")

    # 5) Resume (may prompt on first run).
    resume = resolve_resume()
    where = f"resume/{resume.name}" if resume.parent == RESUME_DIR else str(resume)
    log.info(f"config OK — user {redact_email(email)} · browser {browser} · headful (always)")
    log.info(f"resume ready -> {where}")
    return resume


# --- Browser steps ---------------------------------------------------------

def is_blocked(page: Page) -> bool:
    """True if Naukri's Akamai bot-protection served an 'Access Denied' page."""
    try:
        body = (page.content() or "").lower()
    except Exception:
        return False
    title = (page.title() or "").lower()
    markers = ("access denied", "edgesuite.net", "you don't have permission to access", "reference #")
    return "access denied" in title or any(m in body for m in markers)


def _blocked_fail(page: Page, where: str) -> HardFail:
    shot(page, f"blocked-{where}")
    return HardFail(
        EXIT_BLOCKED,
        f"Naukri's Akamai bot protection blocked the browser on the {where} page "
        "(Access Denied).\n"
        "    You're already headful; complete any human check ONCE in the window that "
        "opened — the persistent profile then remembers it. Avoid VPN/datacenter IPs.",
    )


def warm_up(page: Page) -> None:
    step("Warm up on the homepage (sets anti-bot cookies)")
    page.goto(HOME_URL, wait_until="domcontentloaded")
    page.wait_for_timeout(4000)
    if is_blocked(page):
        raise _blocked_fail(page, "homepage")
    log.debug("homepage loaded, not blocked.")


def is_logged_in(page: Page) -> bool:
    page.goto(PROFILE_URL, wait_until="domcontentloaded")
    page.wait_for_timeout(2500)
    if is_blocked(page):
        raise _blocked_fail(page, "profile")
    url = page.url.lower()
    if "login" in url or "nlogin" in url:
        return False
    return page.locator(SEL["user"]).count() == 0


def login(page: Page, email: str, password: str) -> None:
    log.info("logging in (credentials come from env; they are never logged)...")
    page.goto(LOGIN_URL, wait_until="domcontentloaded")
    page.wait_for_timeout(2000)
    if is_blocked(page):
        raise _blocked_fail(page, "login")
    try:
        page.wait_for_selector(SEL["user"], timeout=30000)
    except PWTimeout:
        shot(page, "login-no-form")
        raise HardFail(EXIT_LOGIN, "the login form didn't load — try again in a minute.")

    try:
        page.fill(SEL["user"], email)
        page.fill(SEL["pass"], password)
        page.click(SEL["login_btn"])
        try:
            page.wait_for_url("**/mnjuser/**", timeout=30000)
        except PWTimeout:
            pass
        page.wait_for_timeout(3000)
        otp = page.locator(SEL["otp_hint"]).count() > 0 or "otp" in page.url.lower()
        logged_in = False if otp else is_logged_in(page)
    except HardFail:
        raise
    except Exception as exc:
        shot(page, "login-error")
        raise HardFail(
            EXIT_LOGIN,
            "login didn't complete — the page closed or navigation failed "
            f"({exc.__class__.__name__}). Re-run; if it keeps happening, finish "
            "logging in manually in the browser window that opens.",
        )

    if otp:
        shot(page, "login-otp-challenge")
        raise HardFail(
            EXIT_LOGIN,
            "Naukri asked for an OTP / device verification.\n"
            "    Complete it ONCE in the open browser window — the persistent profile "
            "then remembers this device and future runs won't ask again.",
        )
    if not logged_in:
        shot(page, "login-failed")
        raise HardFail(
            EXIT_LOGIN,
            "login failed — double-check NAUKRI_EMAIL / NAUKRI_PASSWORD in your .env.",
        )
    log.info("login OK.")


def update_resume(page: Page, pdf: Path) -> bool:
    step(f"Re-upload resume ({pdf.name})")
    page.goto(PROFILE_URL, wait_until="domcontentloaded")
    page.wait_for_timeout(4000)
    if is_blocked(page):
        raise _blocked_fail(page, "profile")

    def do_upload() -> None:
        page.locator(SEL["resume_input"]).set_input_files(str(pdf), timeout=15000)

    log.debug("locating the resume file input ...")
    try:
        do_upload()
    except Exception:
        log.debug("direct input not ready; clicking 'Update' then retrying ...")
        try:
            page.locator(SEL["resume_update_btn"]).first.click(timeout=5000)
            page.wait_for_timeout(1000)
        except Exception:
            pass
        try:
            do_upload()
        except Exception as exc:
            shot(page, "resume-upload-failed")
            raise HardFail(
                EXIT_RESUME,
                "could not attach the resume — Naukri's upload markup may have changed "
                f"(update SEL['resume_input']). Details: {exc}",
            )

    page.wait_for_timeout(6000)
    shot(page, "resume-updated")
    body = ""
    try:
        body = (page.content() or "").lower()
    except Exception:
        pass
    confirmed = pdf.name.lower() in body or "success" in body or "uploaded" in body
    if confirmed:
        log.info("upload confirmed on the page.")
    else:
        log.warning("could not text-confirm the upload; check artifacts/resume-updated.png.")
    return confirmed


def launch_context(pw, browser: str):
    """Launch a headful persistent context in an ISOLATED profile dir."""
    common = dict(
        headless=False,  # ALWAYS headful — headless is blocked by Akamai.
        locale="en-IN",
        timezone_id="Asia/Kolkata",
        viewport={"width": 1366, "height": 900},
        args=["--disable-blink-features=AutomationControlled"],
    )
    channel = SUPPORTED_BROWSERS.get(browser)
    if channel:
        profile_dir = PROFILE_DIRS[browser]
        profile_dir.mkdir(parents=True, exist_ok=True)
        try:
            ctx = pw.chromium.launch_persistent_context(
                user_data_dir=str(profile_dir), channel=channel, **common
            )
            log.info(f"launched {browser} (headful, isolated profile {profile_dir.name}/).")
            return ctx
        except Exception as exc:
            log.warning(
                f"could not launch '{browser}' ({exc.__class__.__name__}) — "
                "falling back to bundled Chromium."
            )

    profile_dir = PROFILE_DIRS["chromium"]
    profile_dir.mkdir(parents=True, exist_ok=True)
    ctx = pw.chromium.launch_persistent_context(user_data_dir=str(profile_dir), **common)
    log.info(f"launched bundled chromium (headful, isolated profile {profile_dir.name}/).")
    return ctx


# --- Postflight ------------------------------------------------------------

def _prune_artifacts() -> None:
    days = os.environ.get("NAUKRI_ARTIFACT_RETENTION_DAYS", "").strip()
    if not days.isdigit():
        return
    cutoff = time.time() - int(days) * 86400
    removed = 0
    for f in ARTIFACTS.glob("*"):
        try:
            if f.is_file() and f.stat().st_mtime < cutoff:
                f.unlink()
                removed += 1
        except Exception:
            pass
    if removed:
        log.debug(f"pruned {removed} old artifact(s) (> {days} days).")


def postflight(confirmed: bool, browser: str, resume: Path) -> None:
    step("Wrap up")
    if confirmed:
        log.info("Resume re-uploaded and confirmed — your profile's 'last updated' time is refreshed.")
    else:
        log.warning("Resume was submitted but not text-confirmed — verify artifacts/resume-updated.png.")
    profile = PROFILE_DIRS.get(browser, PROFILE_DIRS["chromium"]).name
    log.info(f"summary: browser={browser} · resume={resume.name} · session kept in {profile}/ (git-ignored)")
    _prune_artifacts()


# --- Orchestration ---------------------------------------------------------

def run() -> int:
    _STEP["total"] = 6
    log.info("Naukri profile auto-update — starting.")

    resume = preflight()  # STEP 1 (may prompt for a resume on first run)
    email = os.environ["NAUKRI_EMAIL"].strip()
    password = os.environ["NAUKRI_PASSWORD"].strip()
    browser = os.environ.get("NAUKRI_BROWSER", "chromium").strip().lower()
    if browser not in SUPPORTED_BROWSERS:
        browser = "chromium"

    ARTIFACTS.mkdir(parents=True, exist_ok=True)

    with sync_playwright() as pw:
        step(f"Launch browser ({browser}, headful)")
        context = launch_context(pw, browser)
        context.add_init_script(STEALTH_JS)
        page = context.pages[0] if context.pages else context.new_page()
        page.set_default_timeout(30000)
        try:
            warm_up(page)  # STEP 3
            step("Check login state")  # STEP 4
            if is_logged_in(page):
                log.info("already logged in (persistent profile).")
            else:
                login(page, email, password)
            confirmed = update_resume(page, resume)  # STEP 5
            postflight(confirmed, browser, resume)  # STEP 6
            return EXIT_OK
        finally:
            context.close()


def _banner(code: int) -> None:
    if code == EXIT_OK:
        log.info("================  SUCCESS  ================")
    else:
        meaning = EXIT_MEANING.get(code, "unknown")
        log.error(f"================  FAILED (exit {code}: {meaning})  ================")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Refresh a Naukri profile by re-uploading the resume (headful)."
    )
    parser.add_argument(
        "--preflight",
        action="store_true",
        help="Validate setup (env, resume, git-safety) without launching a browser.",
    )
    args = parser.parse_args(argv)

    setup_logging()
    try:
        if args.preflight:
            preflight()
            log.info("Preflight OK — you're ready to run ./run.sh")
            _banner(EXIT_OK)
            return EXIT_OK
        code = run()
        _banner(code)
        return code
    except HardFail as hf:
        for line in hf.message.splitlines():
            log.error(line)
        _banner(hf.code)
        return hf.code
    except KeyboardInterrupt:
        log.error("interrupted.")
        _banner(EXIT_UNEXPECTED)
        return EXIT_UNEXPECTED
    except Exception as exc:
        if os.environ.get("NAUKRI_DEBUG") == "1":
            log.exception("unexpected error")
        else:
            log.error(f"unexpected error: {exc}")
            log.error("Re-run with NAUKRI_DEBUG=1 for a full trace; check artifacts/*.png for a screenshot.")
        _banner(EXIT_UNEXPECTED)
        return EXIT_UNEXPECTED


if __name__ == "__main__":
    sys.exit(main())
