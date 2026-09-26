# House Cleaning Bot — Railway Deployment

## 1. Push this to GitHub
```bash
git init
git add bot.py requirements.txt Procfile .gitignore README.md
git commit -m "House cleaning bot"
git branch -M main
git remote add origin https://github.com/<your-username>/<repo-name>.git
git push -u origin main
```
Do NOT commit a token anywhere — it's kept out via `.gitignore` and passed as an
env var in Railway (step 3).

## 2. Create the Railway project
1. Go to https://railway.app and sign in with GitHub.
2. "New Project" → "Deploy from GitHub repo" → pick this repo.
3. Railway auto-detects Python and installs `requirements.txt`.

## 3. Set the bot token
In the Railway project → your service → **Variables** tab:
- Add `HC_BOT_TOKEN` = your bot token (get a fresh one from @BotFather if the
  old one was ever pasted anywhere public).

## 4. Set the start command
Railway should pick up the `Procfile` automatically (`worker: python bot.py`).
If it instead tries to run it as a web service and complains about no open
port, go to **Settings → Deploy** and set the **Start Command** explicitly to:
```
python bot.py
```
and make sure it's deployed as a **Worker**, not a web service (this bot
doesn't listen on any HTTP port — it just polls Telegram).

## 5. Persisting state.json across deploys (important)
Railway's filesystem resets on every redeploy, so `state.json` (whose turn it
is, whether it's been acknowledged) would reset too. To keep it:
1. In the service → **Settings → Volumes**, add a volume, mount it at
   e.g. `/data`.
2. In `bot.py`, change:
   ```python
   STATE_FILE = Path(__file__).parent / "state.json"
   ```
   to:
   ```python
   STATE_FILE = Path("/data/state.json")
   ```
3. Redeploy. From now on `state.json` lives on the persistent volume and
   survives redeploys/restarts.

## 6. Deploy and test
- Push again (or click "Deploy" in Railway) once the volume + path change is in.
- Check the **Deployments → Logs** tab for `Bot starting...`.
- In Telegram: add the bot to your group, send `/setgroup` once, then
  `/whoseturn` to confirm it's alive.

## Updating later
Any `git push` to `main` triggers an automatic redeploy on Railway — no
manual steps needed after the first setup.
