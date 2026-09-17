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

# Scouting report only, or a backtest on a past date
python main.py --scout-only
python main.py --scout-only --as-of 2026-03-20

# Fantasy-points-per-game trend lines for your roster and the hottest skaters
python main.py --trends

# Draft board (uses last season until the new one has data)
python main.py --draft --top 150 --draft-md state/draft_board.md

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
├── stats_provider.py    # MoneyPuck data via pyhockey; league-aware player values
├── state_store.py       # JSON snapshots so scouts can diff against the last run
├── opportunity.py       # Common shape for every scouting signal
├── ranker.py            # Merges signals, scores them against your roster
├── scouts/              # Signal detectors (see SCOUTING_PLAN.md)
│   ├── injury_return.py #   IR/O -> active since last run
│   ├── goalie_injury.py #   starter hurt -> backup; start-share drift
│   ├── deployment.py    #   PP-unit and line promotions from per-game TOI
│   ├── regression.py    #   ixG vs goals: buy-low and running-hot
│   ├── hot_streak.py    #   FPPG trend: heating up (pickups) / cooling off (roster)
│   ├── ownership.py     #   Yahoo ownership surges
│   ├── dailyfaceoff.py  #   Line/PP-unit changes from DailyFaceoff pages
│   └── news.py          #   RSS feeds classified by Claude into typed events
├── contracts.py         # Keeper contracts from the league site (state/contracts.json)
├── draft_board.py       # --draft: FPPG board with PP1/COLD/HOT flags, contracts excluded
├── scoring.py           # League fantasy-point formulas (skater and goalie)
├── nhl_stats.py         # Official NHL stats by player ID: season summaries + game logs (cached)
├── trend.py             # Per-game fantasy points, sparklines, hot/cold detection
├── site_writer.py       # Saves each briefing as a static page (state/site/) for hosting
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

### Scouts
- Each scout compares today against the previous run's snapshot in `state/`
  and emits opportunities; the ranker scores them against your weakest
  compatible roster player and groups them as Act Now / Rising / Watchlist
- Player values are fantasy points per game under the league's points
  formula (`scoring.py`; weights in `.env`) computed from official NHL
  season totals, so they match the league site exactly; MoneyPuck fills
  gaps and supplies PP share, expected goals and on-ice numbers. Values
  fall back to last season early in the year. `VALUATION=categories`
  switches to z-scores for category leagues
- Trend lines come from official per-player NHL game logs for the roster,
  contracts and the top of the league (a few hundred cached requests a day)
- Every player card carries a trend line: sparkline of fantasy points per
  game, last 5 vs prior 20, with a note on whether shot volume rose too
  (`FANTASY_POINTS_WEIGHTS` sets the points formula)
- Keeper contracts are fetched from the league site's public Firestore
  collection on every run (cached in `state/contracts.json`); contracted
  players are excluded from the draft board, your own contracts count as
  roster, other GMs' contracts show as trade targets
- The news scout needs `ANTHROPIC_API_KEY`; everything else runs without it
- Full design: `SCOUTING_PLAN.md`
