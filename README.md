# Naukri profile auto-update

Keep your [Naukri.com](https://www.naukri.com) profile near the top of recruiter
searches by automatically **re-uploading your resume**, which refreshes your
profile's *"last updated"* time, the signal Naukri's recruiter search ranks on.

It drives a **real browser** (bundled Chromium by default; optionally your Chrome
or Edge) in an **isolated profile**, logs in, and re-uploads the resume from your
`resume/` folder. You run it with **one command**: `./run.sh`.

---

## Quick start (5 minutes)

```bash
# 1. Clone
git clone git@github.com:moheedinamdar/naukri-auto-update.git
cd naukri-auto-update

# 2. Turn on the commit guard (blocks accidental secret commits)
./scripts/install-hooks.sh

# 3. Put your resume PDF in ./resume/   (or skip: step 5 will ask for it)
cp /path/to/your_resume.pdf resume/

# 4. First run: creates .env for you, then stops so you can fill it in
./run.sh
#    -> edit .env, set NAUKRI_EMAIL and NAUKRI_PASSWORD

# 5. Run it for real
./run.sh
```

That's it. On success you'll see a **`SUCCESS`** banner and your Naukri profile's
resume will show *"Uploaded today"*.

> **First run tips**
>
> - A **browser window pops up for a few seconds**: that's required (see below).
> - If Naukri shows a one-time OTP / "verify it's you", **complete it in that window
>   once**. The tool remembers your device afterwards and won't ask again.
> - No PDF in `resume/`? It **asks for the absolute path** and copies it into
>   `resume/` for you.

---

## Why a visible browser? (headless doesn't work)

Naukri is protected by **Akamai bot detection**, which serves an *"Access Denied"*
page to **headless** browsers. Old and new headless modes alike. So the browser
**always runs headful** (a real, visible window). Practical consequences:

- Run it on a machine **with a display** (your Mac/PC), not a headless server.
- It **cannot run on headless CI** (e.g. GitHub-hosted Actions). There is
  deliberately **no CI workflow** in this repo.

---

## Choose your browser

Set `NAUKRI_BROWSER` in `.env`:

| Value | Uses | Notes |
| --- | --- | --- |
| `chromium` *(default)* | Playwright's bundled Chromium | No system browser needed; downloaded on first run. |
| `chrome` | Your installed Google Chrome | Runs in an **isolated** profile. |
| `msedge` | Your installed Microsoft Edge | Runs in an **isolated** profile. |

Every choice uses its **own dedicated, git-ignored profile folder** inside the
project (`.chromium-profile/`, `.chrome-profile/`, `.edge-profile/`). **Your real
browser profiles are never opened or modified.** If a chosen system browser can't
launch, it falls back to bundled Chromium with a warning.

---

## Configuration (`.env`)

Copy [`.env.example`](.env.example) to `.env` (auto-created on first run).

| Variable | Required | Default | Purpose |
| --- | --- | --- | --- |
| `NAUKRI_EMAIL` | ✅ |, | Your Naukri login email |
| `NAUKRI_PASSWORD` | ✅ |, | Your Naukri password |
| `NAUKRI_BROWSER` | | `chromium` | `chromium` \| `chrome` \| `msedge` |
| `NAUKRI_RESUME_PATH` | | *(auto)* | Explicit resume path (abs or relative to this folder) |
| `NAUKRI_RESUME_NAME` | | *(auto)* | Pick a specific file inside `resume/` |
| `NAUKRI_DEBUG` | | `0` | `1` = verbose logs + full tracebacks |
| `NAUKRI_LOG_FILE` | |, | Also write the log here (relative → `artifacts/`) |
| `NAUKRI_ARTIFACT_RETENTION_DAYS` | |, | Auto-delete old screenshots/logs |

**Resume selection order:** `NAUKRI_RESUME_PATH` → `NAUKRI_RESUME_NAME` → newest
`*.pdf` in `resume/` → (first run) prompt for a path and copy it in.

---

## Run it daily

Just run it yourself once a day:

```bash
./run.sh
```

**Optional. Schedule it (macOS).** Installs a LaunchAgent that runs `./run.sh`
headful every day at 10:00 (a browser window appears briefly):

```bash
./scripts/install-launchagent.sh
# remove later:  launchctl unload ~/Library/LaunchAgents/local.naukri-update.plist \
#                && rm ~/Library/LaunchAgents/local.naukri-update.plist
```

---

## Validate your setup (no browser)

```bash
python naukri_update.py --preflight
```

Checks your credentials, resume, and git-safety and prints exactly what's wrong (if
anything) without launching a browser.

---

## Exit codes

| Code | Meaning |
| --- | --- |
| `0` | Success |
| `1` | Unexpected error (re-run with `NAUKRI_DEBUG=1`) |
| `2` | Configuration / credentials problem |
| `3` | Login or OTP challenge |
| `4` | Resume file problem |
| `5` | Blocked by Naukri's Akamai bot protection |
| `6` | Aborted: sensitive files are tracked by git |

---

## Troubleshooting

- **"Created .env … set NAUKRI_EMAIL and NAUKRI_PASSWORD"** → open `.env`, fill the
  two values, run `./run.sh` again.
- **Exit 5 / `blocked-*.png` (Access Denied)** → you're on a VPN/datacenter IP, or a
  human check is pending. Run on a normal connection and complete any check in the
  visible window once.
- **Exit 3 / stuck at OTP** → complete the OTP in the browser window once; the
  profile then remembers your device.
- **Exit 4 / resume** → put a valid PDF in `resume/` or set `NAUKRI_RESUME_PATH`.
- **Exit 6 / git-safety** → a secret is tracked; `git rm --cached <file>` and see
  [SECURITY.md](SECURITY.md).
- **`resume-upload-failed.png`** → Naukri changed its page; update `SEL["resume_input"]`
  in [`naukri_update.py`](naukri_update.py).
- **Python 3.14 error on install** → `run.sh` auto-selects Python 3.11-3.13; install
  one with `brew install python@3.13`.

---

## How it works

1. **Preflight**: validates git-safety, credentials, and resume (may prompt once).
2. **Launch**: a headful, isolated browser profile.
3. **Warm up**: loads the homepage to pick up anti-bot cookies.
4. **Login**, only if the persistent profile isn't already logged in.
5. **Re-upload**: sets the resume on Naukri's hidden file input; verifies on-page.
6. **Wrap up**: prints a summary and prunes old artifacts (if configured).

Screenshots for every step land in `artifacts/` (git-ignored) to make debugging easy.

---

## Legal / responsible use

This automates **your own** Naukri account. Automating logins may be against
Naukri's Terms of Use: use it on your own account, keep it to once a day, and never
share your credentials. Provided under the [MIT License](LICENSE), **as-is**.
