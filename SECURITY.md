# Security

This repository is **public**. It is designed so that your credentials and
personal data **stay on your machine** and can never be committed.

## What is sensitive (never commit these)

These are all git-ignored and additionally blocked by a pre-commit hook:

| Path | Why it's sensitive |
| --- | --- |
| `.env` | Your Naukri email + **password** |
| `*-profile/` (e.g. `.chromium-profile/`) | Your **logged-in session cookies** |
| `resume/*.pdf` (and `.doc/.docx/.rtf`) | Your personal resume |
| `artifacts/` | Screenshots that may show your profile |
| `*.log`, `storage_state.json` | Logs / saved session |

Only `.env.example`, `resume/.gitkeep`, `resume/README.md` and the
`*.plist.template` are tracked from those areas.

## Layered guardrails

1. **`.gitignore`** — excludes every sensitive path above.
2. **Pre-commit hook** (`.githooks/pre-commit`) — blocks a commit if any sensitive
   file is staged. Enable it once: `./scripts/install-hooks.sh`.
3. **Runtime preflight** — `naukri_update.py` refuses to run (exit code 6) if git is
   already **tracking** a sensitive file, and `run.sh` does the same check before it
   starts. This catches mistakes before anything is pushed.
4. **Log redaction** — the password is never printed; the email is masked; sessions
   are never logged. Full tracebacks appear only with `NAUKRI_DEBUG=1`.

## Verify before you push

```bash
git status                 # no .env / resume / *-profile / artifacts listed
git add -A && git status   # still clean of the above (hook also blocks the commit)
```

## If a secret was committed or pushed

1. **Rotate it immediately** — change your Naukri password.
2. Remove it from history (not just the latest commit):

   ```bash
   git rm --cached .env
   # purge from all history:
   pipx run git-filter-repo --path .env --invert-paths   # or use BFG
   git push --force
   ```

3. Consider the exposed secret permanently compromised even after removal.

## Responsible use

This tool automates **your own** Naukri account. Automating logins may be against
Naukri's Terms of Use — use it on your own account, keep the frequency reasonable
(once a day), and never share your credentials. You are responsible for your use.

## Reporting

Found a security issue in this tool? Please open a private report / advisory on the
repository rather than a public issue.
