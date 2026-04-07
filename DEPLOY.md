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
YAHOO_CONSUMER_KEY=dj0yJmk9TU11MXJJYksxdkZRJmQ9WVdrOWFuQlljV1V4WWpNbWNHbzlNQT09JnM9Y29uc3VtZXJzZWNyZXQmc3Y9MCZ4PTdi
YAHOO_CONSUMER_SECRET=57b697673cf78a68cfee4302b439f0344b6ebe0f
YAHOO_LEAGUE_ID=8655
YAHOO_TEAM_ID=11
YAHOO_GAME_CODE=nhl
LOG_LEVEL=INFO
WAIVER_MIN_IMPROVEMENT_PCT=15
WAIVER_MIN_GAMES=3
WAIVER_MAX_ADDS_PER_WEEK=4
MATCHUP_GAA_THRESHOLD=3.0

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
0 9 * * * docker run --rm --env-file /home/chrisbimm/fantasy-hockey-agent/.env -v /home/chrisbimm/fantasy-hockey-agent/auth:/app/auth -v /home/chrisbimm/fantasy-hockey-agent/logs:/app/logs fantasy-hockey-agent python main.py --email --mode morning >> /home/chrisbimm/fantasy-hockey-agent/cron.log 2>&1

# Evening lineup check at 3:00 PM Pacific (catches late scratches)
0 15 * * * docker run --rm --env-file /home/chrisbimm/fantasy-hockey-agent/.env -v /home/chrisbimm/fantasy-hockey-agent/auth:/app/auth -v /home/chrisbimm/fantasy-hockey-agent/logs:/app/logs fantasy-hockey-agent python main.py --email --mode evening >> /home/chrisbimm/fantasy-hockey-agent/cron.log 2>&1
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

The auth tokens and logs persist because they're mounted as volumes.
