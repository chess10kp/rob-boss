# RobBoss mobile app

Phone stand-in for the Sprout. Pick or shoot a reference, send it to the GIMP API, then follow the lesson. "Check my work" takes a photo of the canvas and asks Gemini whether the current step is ready.

The screen follows the control page in `track1/panel.py`: warm paper background, cards, pigment chips, and the same step copy.

## Secrets

Gradle reads the repo-root `.env` at build time and bakes the values into `BuildConfig`:

- `GEMINI_API_KEY` — Gemini
- `AGENT_API_TOKEN` — bearer token for `POST /process`
- `GEMINI_MODEL` — optional, default `gemini-3.8-flash`
- `ROB_BOSS_API_URL` — optional; otherwise the `BASE_URL` line in `REMOTE_API.md`

Anyone who unpacks the APK can read those values. This build is for demos and local use.

Rebuild after the Cloudflare tunnel URL changes, or set `ROB_BOSS_API_URL` in `.env`.

## Run

Start the GIMP API from the repo root (`./scripts/serve_remote_api.sh`), then install the debug APK:

```bash
cd RobBossMobileApp
./gradlew :app:assembleDebug
```
