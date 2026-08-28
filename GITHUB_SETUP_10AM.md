# GitHub Actions — daily at 10 AM IST (04:30 UTC)

Your local repo is ready and committed. Now connect it to GitHub (free) and add your private Garmin token as a secret.

## What you already have
- Folder: `C:\Users\Hp\Downloads\NM\garmin-ai`
- Workflow: `.github/workflows/garmin-sync.yml` — runs `py sync_garmin.py --days 7 --sink files --out ./garmin` daily at 10 AM IST
- Local git commit done on branch `master`
- Token saved privately at `C:\Users\Hp\.garminconnect\garmin_tokens.json` (never committed, via .gitignore)

## Steps — do this once (5 minutes)

### 1. Create an empty GitHub repo
1. Go to https://github.com/new
2. Repo name: `garmin-ai`  (or any name you like)
3. Choose **Private** (so your health data stays private)
4. **Do NOT** check "Add a README" / .gitignore / license — keep it empty
5. Click **Create repository**

GitHub will show you a page with a URL like `https://github.com/YOURNAME/garmin-ai.git` — copy it.

### 2. Push your local code to GitHub
In PowerShell (same folder):

```powershell
cd "C:\Users\Hp\Downloads\NM\garmin-ai"
git remote add origin https://github.com/YOURNAME/garmin-ai.git
git branch -M main
git push -u origin main
```

Replace `YOURNAME` with your GitHub username. You will be asked to log in to GitHub in the browser — approve it.

### 3. Add your Garmin token as a secret (so GitHub can sync without asking for password)
Your token is at `C:\Users\Hp\.garminconnect\garmin_tokens.json` — it auto-refreshes and avoids 2FA each run.

1. On your new GitHub repo page, click **Settings** (top)
2. Left sidebar: **Secrets and variables → Actions**
3. Click **New repository secret**
4. Name: `GARMINTOKENS`
5. Value: open `C:\Users\Hp\.garminconnect\garmin_tokens.json` in Notepad, copy the **entire** JSON content (Ctrl+A, Ctrl+C), paste into the Value box
   - To open quickly: press Windows+R, paste `notepad %USERPROFILE%\.garminconnect\garmin_tokens.json` and Enter
   - **Never share this file or paste it in chat** — treat it like a password
6. Click **Add secret**

### 4. Test it now
1. In your GitHub repo, click **Actions** tab (top)
2. Left side: click **Garmin Daily Sync**
3. Right side: click **Run workflow** → **Run workflow** button
4. Wait ~2 minutes, refresh — you should see a green check. Your `garmin/` folder will be updated.

### 5. Daily schedule
- Workflow runs automatically every day at **10:00 AM IST** (cron `30 4 * * *` UTC).
- To change time: edit `.github/workflows/garmin-sync.yml`, change the `cron:` line, commit & push.

### Tips
- Your data commits back to the repo as `garmin/data.json` + `garmin/daily/*.md` + `garmin/activities/*.md` — you can browse it on GitHub.
- To sync longer history, change `--days 7` to `--days 30` in the workflow file.
- If Garmin ever asks to re-login (rare, if refresh token expires), just run locally: `py sync_garmin.py --login` and then update the `GARMINTOKENS` secret again with the new file content.
- To also set up local Windows Task Scheduler at 10 AM later, tell me and I'll add it — both can run together.

Need help? Tell me your GitHub repo URL once you create it and I’ll check the push.
