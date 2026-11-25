# Contributing

Thanks for helping out. Bug reports, fixes, translations and small features are all welcome.

## Before you start

- **Bugs.** Open an issue with what you did, what you expected and what happened. Include:
  - your OS and Claude Code version (`claude --version`)
  - the relevant lines from the server log
  - **no tokens or secrets** in any of it
- **Features.** Open an issue first so we can agree on the shape before you write code. Small, focused
  changes are much easier to review and merge than large ones.
- **Security problems.** Don't open a public issue. Use GitHub's private vulnerability reporting on this
  repository ("Security" tab → "Report a vulnerability").

## Run it locally

You need Python 3.10 or newer and nothing else.

```bash
git clone https://github.com/imkurow/claude-code-leaderboard.git
cd claude-code-leaderboard
export LB_DB=/tmp/lb-dev.db LB_ADMIN_KEY=dev-key
python3 manage.py demo        # made-up participants with 45 days of usage
python3 server.py             # http://127.0.0.1:8790, admin at /admin with "dev-key"
```

- **Language.** `LB_LANG=id` switches the interface to Bahasa Indonesia.
- **Cloudflare Access.** To act as a logged-in Access user without Cloudflare, set
  `LB_DEV_EMAIL=you@example.com`. It is ignored once Cloudflare Access is configured.
- **Telemetry from your own Claude Code.** Run the installer against your local server:
  `curl -fsSL http://127.0.0.1:8790/install.sh | bash -s -- <token from /admin>`.
  Remove it again with `--uninstall`.

## Project layout

| Path | What it is |
|---|---|
| `server.py` | HTTP server: OTLP receiver, history import, leaderboard and admin APIs, page serving |
| `lbcommon.py` | Config loading, database schema and migrations, shared helpers |
| `accessauth.py` | Cloudflare Access JWT verification (RS256, standard library only) |
| `achievements.py` | The badge catalog and the rules that unlock each badge |
| `manage.py` | Command-line admin, including `demo` |
| `static/index.html` | Leaderboard page: podium, list, detail card, photo upload |
| `static/admin.html`, `static/admin-login.html` | Admin page and its passphrase sign-in |
| `static/join.html` | "How to join" page |
| `static/install.sh`, `static/install.ps1` | Installers for macOS/Linux and Windows (Claude Code telemetry, or the 9router sync) |
| `static/9router-sync.js` | The 9router sync (Node.js) the installers set up on a 9router machine |
| `deploy/`, `Dockerfile`, `docker-compose.yml`, `run*.sh` | Ways to keep it running |

## Ground rules

- **Standard library only.**
  - The server must run with a plain `python3` and no `pip install`.
  - The pages are plain HTML, CSS and JavaScript with no build step and no frameworks.
  - Fonts come from Google Fonts. Nothing else is loaded from a CDN.
- **Numbers, never content.** Nothing that reaches the server may include prompts, code, file names or
  paths. If a feature needs new data from laptops, explain in the issue what exactly is sent.
- **Installers must be safe to re-run.**
  - Back up `settings.json` before changing it.
  - Only touch the keys the installer owns.
  - Keep `--uninstall` working.
  - Re-running the history import must replace that machine's history, never add to it.
- **Every user-facing string is bilingual (English and Indonesian).**
  - In HTML, the installers and `join.html`, wrap text as `⟦English text‖Teks Indonesia⟧`. The server
    picks one side when it serves the file. Keep code, paths and placeholders like `__LB_URL__` outside
    the markers, and make sure quoting is still valid with either side.
  - In page JavaScript, use `T("English", "Indonesia")`.
  - In Python, use `T(...)` in `server.py` and `t(...)` in `manage.py`.
  - If you don't speak Indonesian, write the English and leave a note in the PR. Someone will fill it in.
- **Writes from the browser** go through the existing `X-LB-Request` header and same-origin check, and
  admin actions go through `admin_action()` so they are logged.
- **Light and dark mode.** Colours come from the CSS variables at the top of each page, defined for
  both themes. Please check both, plus a phone-width window, for any visual change.

## Checks before you open a pull request

CI runs these (see `.github/workflows/ci.yml`). You can run the same locally:

```bash
python3 -m py_compile server.py manage.py lbcommon.py accessauth.py achievements.py
bash -n static/install.sh run.sh run-tunnel.sh
```

Then start a server on a demo database and check that:

- the board, the detail card (click someone) and `/admin` work in both `LB_LANG=en` and `LB_LANG=id`
- `curl -s http://127.0.0.1:8790/install.sh | bash -n` passes
- no `⟦` is left in any served page

If you changed `install.ps1`, please say whether you could test it on Windows. We have fewer chances to
test that one.

## Pull requests

- **One topic per PR**, with a short description of what changed and why.
- **Screenshots** of the before and after for anything visual.
- **Configuration.** Update `README.md` and `config.env.example` when you add or change a setting.
- **Licence.** By contributing you agree that your work is released under the [MIT License](LICENSE).
