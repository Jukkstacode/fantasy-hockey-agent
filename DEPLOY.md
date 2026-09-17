# Deploying the Fantasy Hockey Agent to your Elite Mini

This guide gets the agent running in Docker on your Ubuntu server with
twice-daily email delivery via cron.

## Prerequisites

- Docker installed on the Elite Mini (you already have this)
- A Gmail account for sending email
- The agent already auth'd to Yahoo on your Mac (we'll copy the token over)

## Step 1: Generate a Gmail App Password

1. Go to https://myaccount.google.com/security
2. Make sure 2-Step Verification is enabled
3. Go to https://myaccount.google.com/apppasswords
4. Create a new app password named "Fantasy Hockey Agent"
5. Copy the 16-character password (you'll need it in step 3)

## Step 2: Get the project onto the Elite Mini

From your Mac:

```bash
# SSH into the Elite Mini (or use Tailscale if it's set up)
ssh chrisbimm@elite-mini

# Make a directory for the agent
mkdir -p ~/fantasy-hockey-agent
exit
```

From your Mac, copy the project files over (run this from `~/Coding/fantasy-agent`):

```bash
cd ~/Coding/fantasy-agent
rsync -av --exclude venv --exclude __pycache__ --exclude logs \
  ./ chrisbimm@elite-mini:~/fantasy-hockey-agent/
```

## Step 3: Create the .env file on the server

SSH back into the Elite Mini and create the .env file:

```bash
ssh chrisbimm@elite-mini
cd ~/fantasy-hockey-agent
nano .env
```

Paste this in (filling in your real values):

```
YAHOO_CONSUMER_KEY=your_new_consumer_key
YAHOO_CONSUMER_SECRET=your_new_consumer_secret
YAHOO_LEAGUE_ID=8655
YAHOO_TEAM_ID=11
YAHOO_GAME_CODE=nhl
LOG_LEVEL=INFO
WAIVER_MIN_IMPROVEMENT_PCT=15
WAIVER_MIN_GAMES=3
WAIVER_MAX_ADDS_PER_WEEK=4
MATCHUP_GAA_THRESHOLD=3.0

# Scouting: news classification needs a Claude API key (optional; skipped if blank)
ANTHROPIC_API_KEY=
ANTHROPIC_MODEL=claude-opus-5

# Email configuration
GMAIL_USER=your.email@gmail.com
GMAIL_APP_PASSWORD=xxxx xxxx xxxx xxxx
EMAIL_TO=where.you.want.it@example.com
```

Save with Ctrl+O, Enter, then Ctrl+X.

## Step 4: Build the Docker image

```bash
cd ~/fantasy-hockey-agent
docker build -t fantasy-hockey-agent .
```

This downloads the Python base image and installs everything. Takes 1-2 minutes.

## Step 5: Test it

Run a one-off briefing to make sure it works:

```bash
docker run --rm \
  --env-file .env \
  -v ~/fantasy-hockey-agent/auth:/app/auth \
  -v ~/fantasy-hockey-agent/logs:/app/logs \
  -v ~/fantasy-hockey-agent/state:/app/state \
  fantasy-hockey-agent \
  python main.py --email --mode morning
```

You should get an email within a few seconds. Check your spam folder
the first time.

## Step 6: Set up cron for twice-daily delivery

Edit your crontab on the Elite Mini:

```bash
crontab -e
```

Add these two lines (these are in your local timezone, so adjust if your
server is on a different TZ — `date` will tell you):

```
# Morning briefing (lineup + waivers) at 9:00 AM Pacific
0 9 * * * docker run --rm --env-file /home/chrisbimm/fantasy-hockey-agent/.env -v /home/chrisbimm/fantasy-hockey-agent/auth:/app/auth -v /home/chrisbimm/fantasy-hockey-agent/logs:/app/logs -v /home/chrisbimm/fantasy-hockey-agent/state:/app/state fantasy-hockey-agent python main.py --email --mode morning >> /home/chrisbimm/fantasy-hockey-agent/cron.log 2>&1

# Evening lineup check at 3:00 PM Pacific (catches late scratches)
0 15 * * * docker run --rm --env-file /home/chrisbimm/fantasy-hockey-agent/.env -v /home/chrisbimm/fantasy-hockey-agent/auth:/app/auth -v /home/chrisbimm/fantasy-hockey-agent/logs:/app/logs -v /home/chrisbimm/fantasy-hockey-agent/state:/app/state fantasy-hockey-agent python main.py --email --mode evening >> /home/chrisbimm/fantasy-hockey-agent/cron.log 2>&1
```

Save and exit. Verify with `crontab -l`.

## Verifying it works

- Check `~/fantasy-hockey-agent/cron.log` after the next scheduled run
- Check the logs inside the container's mount: `~/fantasy-hockey-agent/logs/agent.log`
- If no email arrives, check spam, then check the logs for errors

## Updating the agent

When you make code changes on your Mac:

```bash
# From your Mac
cd ~/Coding/fantasy-agent
rsync -av --exclude venv --exclude __pycache__ --exclude logs --exclude auth \
  ./ chrisbimm@elite-mini:~/fantasy-hockey-agent/

# On the Elite Mini
ssh chrisbimm@elite-mini
cd ~/fantasy-hockey-agent
docker build -t fantasy-hockey-agent .
```

The auth tokens, logs, and scout state persist because they're mounted as volumes.

## If Yahoo returns 403 "This application is not authorized to perform this action"

Since 2026-07-22 Yahoo gates the Fantasy Sports API behind an approval
process. Apps created before that lost access, even with the permission
still checked in the portal, and OAuth keeps working (tokens mint and
refresh) so the failure only shows up on API calls. Apply at
https://sports.yahoo.com/developer/access/ and wait for approval; nothing in
this repo can work around it.

While waiting, put your roster in `state/my_roster.txt` (one name per line,
add `, G` after goalies) and the scouts will still compare opportunities
against it. Free-agent availability stays unknown until access is restored.

## Re-authorizing Yahoo from the server (no browser)

If the API starts returning "This application is not authorized to perform this
action", the app's Fantasy Sports permission or the token needs a refresh. From
the server:

```bash
cd ~/fantasy-hockey-agent
./dev.sh python main.py --auth
```

It prints an authorization URL. Open it on any device and approve. Yahoo
redirects to the app's registered Redirect URI (`https://localhost:8080/` by
default; set `YAHOO_REDIRECT_URI` in `.env` if yours differs). That page won't
load, but the address bar contains `?code=...`. Paste the code or the whole
URL back into the terminal. Non-interactively:

```bash
./dev.sh python main.py --auth --url-only          # print the URL
./dev.sh python main.py --auth --code '<code-or-url>'
```

The new token is saved to `auth/.env`; the old one is kept as a `.bak` file.

## Local development on the server

`./dev.sh <command>` runs a command in the Docker image with the live project
directory mounted, so code edits apply without a rebuild:

```bash
./dev.sh python main.py --scout-only          # scouting report to console
./dev.sh python main.py --as-of 2026-03-20    # backtest scouts on a past date
```

Rebuild the image whenever `requirements.txt` changes.
