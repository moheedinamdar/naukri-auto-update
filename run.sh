#!/usr/bin/env bash
# Naukri profile auto-update — one-command runner.
#   First run : creates a venv, installs deps + bundled Chromium, scaffolds .env.
#   Every run : refreshes your Naukri profile by re-uploading your resume (headful).
#
# Nothing here needs sudo. If it isn't executable:  chmod +x run.sh  (or: bash run.sh)
set -uo pipefail
cd "$(dirname "$0")"

RED=$'\033[31m'; YEL=$'\033[33m'; GRN=$'\033[32m'; DIM=$'\033[2m'; RST=$'\033[0m'
info() { printf '%s[setup]%s %s\n' "$DIM" "$RST" "$*"; }
warn() { printf '%s[warn ]%s %s\n' "$YEL" "$RST" "$*" >&2; }
err()  { printf '%s[error]%s %s\n' "$RED" "$RST" "$*" >&2; }
ok()   { printf '%s[ ok  ]%s %s\n' "$GRN" "$RST" "$*"; }
die()  { err "$1"; exit "${2:-1}"; }

# Friendly message if something unexpected aborts the script (never a bare stack).
trap 'code=$?; if [ "$code" -ne 0 ]; then err "run.sh stopped (exit $code). Read the messages above, fix, and re-run (NAUKRI_DEBUG=1 for detail)."; fi' EXIT

# --- 1) A Playwright-compatible Python (3.11-3.13) -------------------------
PYBIN=""
for cand in python3.13 python3.12 python3.11; do
  command -v "$cand" >/dev/null 2>&1 && { PYBIN="$cand"; break; }
done
if [ -z "$PYBIN" ]; then
  err "Need Python 3.11, 3.12 or 3.13 (Playwright has no wheels for 3.14+)."
  err "Install one, e.g.:  brew install python@3.13"
  exit 2
fi

# --- 2) Guardrail: never run if secrets are tracked by git -----------------
if git rev-parse --is-inside-work-tree >/dev/null 2>&1; then
  leak=$(git ls-files 2>/dev/null | grep -E '(^|/)\.env$|(^|/)\.env\.[^/]*$|storage_state\.json|-profile/|^artifacts/|^resume/.+\.(pdf|docx?|rtf)$' | grep -v '^\.env\.example$' || true)
  if [ -n "$leak" ]; then
    err "Sensitive files are tracked by git — refusing to run so you don't leak them:"
    printf '%s   - %s%s\n' "$RED" "$leak" "$RST" >&2
    err "Fix:  git rm --cached <file>   then confirm .gitignore covers it (see SECURITY.md)."
    exit 6
  fi
fi

# --- 3) Virtualenv (rebuild if it was made with an unsupported Python) -----
if [ -d .venv ]; then
  cur=$(.venv/bin/python -c 'import sys;print("%d.%d"%sys.version_info[:2])' 2>/dev/null || echo none)
  case "$cur" in 3.11|3.12|3.13) : ;; *) info "recreating venv (was Python $cur)"; rm -rf .venv ;; esac
fi
if [ ! -d .venv ]; then
  info "creating virtual environment ..."
  "$PYBIN" -m venv .venv || die "could not create the venv (is the python 'venv' module installed?)" 1
fi
# shellcheck disable=SC1091
source .venv/bin/activate || die "could not activate the venv" 1

# --- 4) First-run install (deps + bundled Chromium) -----------------------
if ! python -c 'import playwright' >/dev/null 2>&1; then
  info "installing dependencies (one time) ..."
  pip install -q --upgrade pip        || die "pip upgrade failed — check your internet/proxy, then re-run." 1
  pip install -q -r requirements.txt  || die "dependency install failed — check your internet/proxy, then re-run." 1
fi
info "ensuring bundled Chromium is present ..."
python -m playwright install chromium >/dev/null 2>&1 \
  || python -m playwright install chromium \
  || warn "Chromium download failed. Fine if NAUKRI_BROWSER=chrome/msedge; otherwise check your connection."

# --- 5) .env scaffold ------------------------------------------------------
if [ ! -f .env ]; then
  [ -f .env.example ] || die ".env and .env.example are both missing — cannot continue." 2
  cp .env.example .env
  chmod 600 .env 2>/dev/null || true
  warn "Created .env from .env.example."
  warn "Next: open .env, set NAUKRI_EMAIL and NAUKRI_PASSWORD, then run ./run.sh again."
  trap - EXIT
  exit 2
fi
chmod 600 .env 2>/dev/null || true
set -a; # shellcheck disable=SC1091
source .env; set +a

# --- 6) Run (Python prints its own friendly SUCCESS/FAILED banner) ---------
trap - EXIT
python naukri_update.py
exit $?
