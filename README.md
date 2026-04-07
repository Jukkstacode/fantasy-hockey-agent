# Fantasy Hockey Agent

An automated agent that manages your Yahoo Fantasy Hockey team — optimizing daily lineups and making waiver wire pickups.

## Architecture

```
┌─────────────────┐     ┌──────────────────┐     ┌─────────────────┐
│  NHL API         │     │  Yahoo Fantasy   │     │  Decision       │
│  (nhle.com)      │────▶│  API (yfpy)      │────▶│  Engine         │
│  - Schedule      │     │  - Roster        │     │  - Lineup opt   │
│  - Injuries      │     │  - Free agents   │     │  - Waiver eval  │
│  - Player stats  │     │  - Transactions  │     │  - Streaming    │
└─────────────────┘     └──────────────────┘     └─────────────────┘
                                                         │
                                                         ▼
                                                  ┌─────────────────┐
                                                  │  Actions        │
                                                  │  - Set lineup   │
                                                  │  - Add/drop     │
                                                  │  - Log decisions│
                                                  └─────────────────┘
```

## Setup

### 1. Yahoo App Registration
1. Go to https://developer.yahoo.com/apps/
2. Click "Create an App"
3. Set "Application Type" to "Installed Application"
4. Under "API Permissions", select "Fantasy Sports" with **Read/Write** access
5. Save your **Consumer Key** and **Consumer Secret**

### 2. Environment Setup

```bash
cd fantasy-hockey-agent
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

### 3. Configuration

Copy the example env file and fill in your credentials:

```bash
cp .env.example .env
# Edit .env with your Yahoo consumer key/secret and league ID
```

### 4. First Run (OAuth)

```bash
python main.py --auth
```

This opens a browser for Yahoo OAuth. After authorizing, the token is saved locally for future use.

### 5. Running the Agent

```bash
# Dry run (shows what it would do, no changes)
python main.py --dry-run

# Live run (makes actual roster moves)
python main.py

# Run as a daily cron job
crontab -e
# Add: 0 9 * * * /path/to/venv/bin/python /path/to/main.py >> /path/to/agent.log 2>&1
```

## Project Structure

```
fantasy-hockey-agent/
├── main.py              # Entry point and CLI
├── config.py            # Configuration and environment loading
├── yahoo_client.py      # Yahoo Fantasy API wrapper
├── nhl_client.py        # NHL API client (schedule, injuries, stats)
├── lineup_optimizer.py  # Daily lineup optimization logic
├── waiver_manager.py    # Waiver wire / free agent evaluation
├── decision_log.py      # Logs all decisions for review
├── requirements.txt
├── .env.example
└── README.md
```

## Decision Logic

### Lineup Optimizer
- Starts all players who have games that day
- Benches players on off days, injured, or DTD
- Prefers players on favorable matchups (opponent GAA, save %)
- Handles back-to-back detection for goalies

### Waiver Manager
- Scans free agents and compares to roster weak spots
- Factors in: recent performance, schedule density, position need
- Configurable thresholds to avoid churning
