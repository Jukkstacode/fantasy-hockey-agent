# Scouting Agent Plan: Finding Pickups Before Your League Does

> **Status (2026-09-15):** phases 0 through 5 are implemented. `scouts/`,
> `ranker.py`, `state_store.py`, `opportunity.py` and the league-aware
> `stats_provider.py` exist and the briefing has a Scouting Report section.
> Not yet done: DailyFaceoff scraping and Yahoo write-back (phase 6). The
> news scout runs only when `ANTHROPIC_API_KEY` is set. Yahoo auth on the
> server currently returns 403 and needs re-authorization (see DEPLOY.md).

This guide extends the existing daily-briefing agent into a **scouting system** that
watches for the situations that create fantasy value and tells you when an available
player is about to become worth more than someone on your roster.

The four situations you asked for, plus two more that are just as profitable:

| Signal | Why it matters | How we detect it |
|---|---|---|
| Player returning from injury | Talent at a discount; often dropped while hurt | Yahoo status flag changes + news confirmation |
| Goalie injured | His backup inherits 60%+ of starts overnight | Yahoo status/news + NHL roster lookup + recent start share |
| Promoted to PP1 | Power-play time is the single biggest driver of skater points | Per-game 5-on-4 ice time from MoneyPuck via pyhockey |
| Moved up a line | More even-strength minutes with better linemates | Per-game 5-on-5 ice time rank within team |
| Positive regression candidate | Shooting well below expected, points will come | Individual expected goals (ixG) minus actual goals |
| Ownership surge | Other managers have noticed; act before waivers clear | Yahoo `percent_owned` delta between runs |

---

## 1. The core idea: snapshot and diff

Almost every signal above is a **change**, not a state. "Injured" is not useful;
"was injured yesterday and is healthy today" is. So the foundation is a small
state store that persists what the agent saw last run, and every scout compares
today against it.

```
state/
  players.json        # per player: yahoo status, percent_owned, team, positions, last seen
  deployment.json     # per skater: rolling PP TOI, ES TOI, team rank, last 10 games
  goalies.json        # per goalie: last 10 starts, start share, status
  news_seen.json      # hashes of news items already classified
  opportunities.json  # what we already told you about, so we don't repeat it daily
```

Each run:

1. Load previous state.
2. Fetch current data from Yahoo, the NHL API, pyhockey, and news feeds.
3. Every **scout** compares current vs. previous and emits `Opportunity` objects.
4. The **ranker** scores each opportunity against your roster's weakest compatible slot.
5. The **briefing** groups them by urgency and emails you.
6. Save current state as the new baseline.

### The Opportunity object

Every scout produces the same shape, so the ranker and briefing don't care where a
signal came from:

```python
@dataclass
class Opportunity:
    player_name: str
    nhl_team: str
    positions: list[str]          # Yahoo eligible positions
    yahoo_key: str | None         # None if we couldn't match the name
    signal: str                   # "injury_return" | "goalie_backup" | "pp_promotion" |
                                  # "line_promotion" | "regression" | "ownership_surge" | "news"
    evidence: str                 # human-readable, goes in the email
    confidence: float             # 0-1
    urgency: str                  # "now" | "this_week" | "watch"
    projected_value: float        # same scale as StatsProvider scores
    available: bool               # free agent / on waivers in your league
    rostered_by: str | None       # team name if owned (trade target)
    source_url: str | None
```

Signals that agree get combined: a PP-time jump **plus** a news item saying "moved to
the top unit" is one opportunity with higher confidence, not two emails.

---

## 2. The scouts

Each scout is one Python module with a single `scan(state, ctx) -> list[Opportunity]`
method. They run in sequence inside the existing `main.py` flow.

### 2.1 InjuryReturnScout

**Data:** Yahoo player `status` field (`IR`, `IR-LT`, `O`, `DTD`, `NA`, or blank), which
you already pull for every league player. Add the NHL roster endpoint as a second
source for whether the player is on the active roster:

```
GET https://api-web.nhle.com/v1/roster/{TEAM}/current
```

**Logic:**

- Status was `IR`/`O`/`IR-LT` last run and is now blank or `DTD` → returning.
- Player was missing from the NHL active roster and is now present → activated.
- News scout (2.5) reports `injury_return` for the same player → corroborate.
- Rank by **last healthy season value**, not current-season stats (which are empty or
  stale for someone who just missed 30 games). `StatsProvider` should load the prior
  season too and fall back to it when games played this season is under 10.

**Urgency:** `now` if projected value beats your worst roster player at that position
by the configured threshold. Star players coming off long-term IR are the highest
value pickups of the year and are usually only available for a few hours.

### 2.2 GoalieInjuryScout

**Data:** Yahoo goalie statuses, the NHL roster endpoint above (to find teammates at
position G), and `pyhockey.goalie_games()` for who has actually been starting.

**Logic:**

1. Detect any goalie whose status changed to `O`/`IR`/`DTD`, or a news item classified
   `goalie_injury`, or a goalie who was the team's primary starter and has not started
   in the last 4 team games while healthy on paper (hidden injury or benching).
2. Look up the injured goalie's team and list the other goalies on the roster.
3. For each, compute start share over the last 10 team games from `goalie_games`.
4. Emit an opportunity for the backup with `urgency="now"` if available in your league.
   Include the team's next 7 days of games so you know how many starts you're buying.
5. If the team called up an AHL goalie (new name appears on the NHL roster at G), flag
   him too at lower confidence.

A second, subtler signal from the same data: **start share drift**. A backup who has
started 3 of the last 4 games while the starter is "healthy" is telling you something
the injury report isn't yet.

### 2.3 DeploymentScout (power play and line promotions)

This is the highest-value scout and the one your current agent has no visibility into.

**Data:** `pyhockey.skater_games(season=..., situation="5on4")` gives per-game
power-play ice time for every skater. `situation="5on5"` gives even-strength time.
MoneyPuck data updates the morning after games, so run this in the morning briefing.

**Logic for PP promotion:**

- Rolling average PP TOI over the last 3 games vs. the previous 10.
- Flag if the jump is at least 60 seconds per game **and** the player is now in his
  team's top 5 by PP TOI over those 3 games (a proxy for PP1). Most teams give PP1
  about 70% of power-play minutes, so top-5 team rank is a reliable PP1 signal.
- Confidence rises with the size of the jump and the number of games it has held.

**Logic for line promotion:**

- Same rolling comparison on 5-on-5 TOI.
- Forwards: moved into the top 6 forwards by ES TOI on their team.
- Defensemen: moved into the top 4.
- Bonus: if the pyhockey game rows include on-ice teammates or line data, check who he
  is playing with. If not, DailyFaceoff's line combinations page gives the current
  lines directly (see section 4) and is a good corroborating source.

**Preseason note:** Training camp and preseason games in late September are where the
biggest promotions get decided. MoneyPuck does not cover preseason, so for the next
three weeks the news scout is the only source for "X is skating on the top line with
McDavid in camp." Weight those news items heavily until real games start.

### 2.4 RegressionScout

**Data:** `pyhockey.skater_seasons()` gives `I_F_xGoals` (individual expected goals)
and `I_F_goals` (actual). Check the exact column names in the dataframe once; MoneyPuck
uses this naming convention.

**Logic:**

- Skaters with at least 15 games, ixG minus goals ≥ 3 → shooting cold, likely to
  regress upward. Good buy-low targets, and good trade targets if rostered.
- The reverse (goals well above ixG) on **your own roster** → sell-high or expect a
  drop. Include these in the briefing as "your players running hot".
- Shot volume matters in most Yahoo leagues (SOG is a category). Rank by shots per game
  too, not just points.

### 2.5 NewsScout (the LLM piece)

This is the one job that needs a language model: turning unstructured beat-reporter
text into structured events.

**Feeds to poll** (all free, check each URL once and adjust):

| Source | URL | Notes |
|---|---|---|
| Rotowire NHL player news | `https://www.rotowire.com/rss/news.php?sport=NHL` | Best signal-to-noise; fantasy-oriented blurbs |
| DailyFaceoff news | `https://www.dailyfaceoff.com/` (check for an RSS link in the page source) | Line combos and starting goalies |
| NHL.com news | `https://www.nhl.com/news` (RSS link in page source) | Official injury and roster moves |
| TSN hockey | `https://www.tsn.ca/nhl` | Canadian beat coverage |
| Sportsnet | `https://www.sportsnet.ca/hockey/nhl/` | Same |
| Reddit r/fantasyhockey | `https://www.reddit.com/r/fantasyhockey/new/.rss` | Noisy but early; managers post line changes from morning skates |
| Team beat reporters on X | not fetchable without an API key | Skip unless you pay for X API access |

Store a hash of every item you've processed in `news_seen.json` and only classify new
ones. Expect 50 to 150 new items a day in season.

**Classification with Claude.** Batch the new items into one request and ask for a
typed list back. Using the Anthropic Python SDK with structured outputs:

```python
# news_scout.py
from pydantic import BaseModel
from typing import Literal
import anthropic

class NewsEvent(BaseModel):
    player_name: str
    nhl_team: str                 # 3-letter abbreviation
    event: Literal[
        "injury_return", "injury_out", "goalie_injury", "goalie_start_change",
        "pp_promotion", "pp_demotion", "line_promotion", "line_demotion",
        "callup", "trade", "healthy_scratch", "other",
    ]
    fantasy_relevant: bool        # false for "signed a PTO" style noise
    confidence: float             # 0-1
    summary: str                  # one sentence, will appear in the email
    source_url: str

class NewsBatch(BaseModel):
    events: list[NewsEvent]

SYSTEM = """You extract fantasy-hockey-relevant roster events from NHL news items.
Only emit events that change a player's expected production: injuries and returns,
goalie starts, power-play or line assignments, call-ups, trades, scratches.
Ignore contract news, milestones, and opinion pieces. Use the source URL given with
each item. If a team abbreviation is not stated, infer it from context."""

def classify(items: list[dict]) -> list[NewsEvent]:
    client = anthropic.Anthropic()
    text = "\n\n".join(
        f"[{i}] {it['title']}\n{it['body']}\nURL: {it['url']}"
        for i, it in enumerate(items)
    )
    response = client.messages.parse(
        model="claude-opus-5",
        max_tokens=16000,
        system=SYSTEM,
        output_config={"effort": "low"},   # classification, not reasoning
        messages=[{"role": "user", "content": text}],
        output_format=NewsBatch,
    )
    return [e for e in response.parsed_output.events if e.fantasy_relevant]
```

Requires `pip install anthropic pydantic feedparser` and an `ANTHROPIC_API_KEY` in
`.env` (this is separate from your Claude Code login; see section 6 for a way to use
your subscription instead).

**Cost:** roughly 100 items × 150 tokens = 15K input tokens per day plus a few
thousand output. At Opus 5 pricing that is around $0.10 to $0.20 per day for the
whole season. If you want it cheaper, `claude-sonnet-5` handles this classification
fine at under half that.

**Merging:** every `NewsEvent` becomes an `Opportunity` with `signal="news"`. The
ranker then looks for a structured scout hit on the same player within the last 3 days
and merges them, boosting confidence.

### 2.6 OwnershipScout

**Data:** Yahoo's `percent_owned` sub-resource (section 3). Store it per player each run.

**Logic:** a free agent whose ownership rose more than 10 points since yesterday is
being picked up league-wide. This is a lagging indicator, but it catches things every
other scout missed, and it tells you how many hours you have left before he's gone.

---

## 3. Getting more out of the Yahoo API

Your `yahoo_client.py` says the API is read-only. **That is not correct.** The Yahoo
Fantasy Sports API supports roster changes and add/drop transactions when the app is
registered with Read/Write permission, which yours is. The `yfpy` library simply
doesn't wrap the write endpoints.

### 3.1 Reads that would improve the scouts today

| What | Endpoint | Why |
|---|---|---|
| Only available players | `league/{league_key}/players;status=A;sort=AR;count=25;start=0` | Replaces the 12-roster scan in `_build_rostered_set` (which currently takes 16 seconds and has a dead loop at lines 74-88) |
| Waiver-wire players only | `...;status=W` | Players just dropped by others, with the waiver clear date |
| Ownership trend | `league/{league_key}/players;player_keys=...;out=percent_owned` | Feeds OwnershipScout |
| Recent form, in your league's scoring | `player/{player_key}/stats;type=lastweek` and `type=lastmonth` | Yahoo computes fantasy points using your league's categories, so this is the ground truth for "hot" |
| League scoring categories | `league/{league_key}/settings` (you already fetch this, but don't use it) | Your value score should weight the stats your league actually counts. A hits-and-blocks league values a very different player than a points-only league. |
| League transactions | `league/{league_key}/transactions;types=add,drop` | See what competitors are grabbing, and what they just dropped |
| Roster positions | same settings call | Replaces the hardcoded slot counts in `lineup_optimizer.py` line 146 |

`yfpy` exposes these through `query.get_league_players(...)` and the lower-level
`query.query(url, ...)` method for anything it doesn't wrap. The raw URL prefix is
`https://fantasysports.yahooapis.com/fantasy/v2/` and your league key is
`{game_id}.l.{league_id}` (the game id changes each season; `get_game_key_by_season`
gives it to you).

**The single biggest accuracy improvement available:** replace the generic
points-per-hour score in `stats_provider.py` with a category-weighted projection built
from your league settings. Read the stat categories once, map each to a MoneyPuck
column (goals, assists, shots, hits, blocks, PP points, plus/minus; goalie wins, GAA,
save percentage, shutouts), compute z-scores per category across the player pool, and
sum the z-scores for the categories your league uses. That is what every serious
ranking tool does, and it makes "would this player beat mine" an honest comparison.

### 3.2 Writes (optional, for later)

If you decide you want the agent to act instead of advise:

| Action | Method | Endpoint |
|---|---|---|
| Set lineup | `PUT` | `team/{team_key}/roster` with an XML body of player keys and positions |
| Add / drop / swap | `POST` | `league/{league_key}/transactions` with `type=add`, `drop`, or `add/drop` |
| Waiver claim with FAAB bid | `POST` | same, with `faab_bid` |

The `yahoo_fantasy_api` package (by spilchen, on PyPI) wraps all three as
`Team.change_positions()`, `Team.add_player()`, `Team.drop_player()`, and
`Team.add_and_drop_players()`, and can share the OAuth token you already have.

Recommended path: keep the human in the loop for add/drops (a bad auto-drop is
painful), but let the agent set the lineup automatically each morning, since that is
a low-risk, high-tedium task. A middle ground for pickups: the email lists numbered
moves, and you reply or run `python main.py --approve 2` to execute one.

---

## 4. Data sources beyond Yahoo

| Source | Access | Use |
|---|---|---|
| NHL API `api-web.nhle.com/v1` | Free, no key. You already use it. | `roster/{TEAM}/current` for goalie teammates and call-ups; `gamecenter/{game_id}/boxscore` for TOI per player the same night (faster than MoneyPuck); `player/{id}/landing` for status |
| NHL stats API `api.nhle.com/stats/rest/en/skater/summary` | Free | Season summaries with PP points, hits, blocks, shots (fills gaps in MoneyPuck for banger categories) |
| MoneyPuck via `pyhockey` | Free, already installed | `skater_games` and `goalie_games` are the backbone of DeploymentScout and GoalieInjuryScout; `skater_seasons(situation="5on4")` gives PP season rates |
| DailyFaceoff line combinations | Scrape `https://www.dailyfaceoff.com/teams/{team-slug}/line-combinations` | Current lines and PP units as a human editor sees them. Scrape once per morning per team, cache, and set a real User-Agent. |
| DailyFaceoff starting goalies | `https://www.dailyfaceoff.com/starting-goalies/` | Confirmed and projected starters for tonight; feeds the evening lineup check |
| Natural Stat Trick | Scrape, be gentle | Line-level and pairing-level stats if you want to know whether a promotion is to a *good* line |
| Left Wing Lock | Scrape | Alternative for lines and starting goalies |

Scraping note: these sites tolerate light personal use. One request per team per
day, cached to disk, with a descriptive User-Agent, is fine. Do not hit them from the
evening run too.

---

## 5. The ranker and the briefing

### Ranking

For each opportunity:

```
gain = projected_value(opportunity) - value(weakest compatible roster player)
score = gain * confidence * urgency_multiplier * schedule_factor
```

- `urgency_multiplier`: 2.0 for `now`, 1.0 for `this_week`, 0.5 for `watch`
- `schedule_factor`: games in the next 7 days ÷ 3.5, capped at 1.4 (a 4-game week
  beats a 2-game week)
- Drop anything with `gain <= 0` unless it is a goalie backup signal (those are worth
  a bench slot even at equal value, because of the start volume)
- Never recommend more than `WAIVER_MAX_ADDS_PER_WEEK`, but always show `now` items
  regardless of cap
- Don't repeat: if you told the user about a player yesterday and nothing changed, show
  him in a one-line "still available" list instead of a full card

### Briefing layout

```
🚨 ACT NOW
   1. ADD Jesper Wallstedt (MIN, G) / DROP Joonas Korpisalo
      Gustavsson placed on IR (lower body). Wallstedt started 2 of last 3.
      MIN plays 4 times this week. 38% owned, up 22 since yesterday.
      Source: rotowire.com/...

📈 RISING
   2. ADD Cole Perfetti (WPG, C/RW) / DROP Ryan Hartman
      PP TOI up from 0:48 to 2:51 over last 3 games, now 3rd on WPG.
      DailyFaceoff lists him on PP1 with Scheifele and Connor.

👀 WATCHLIST (not yet worth a drop, or rostered elsewhere)
   • Rasmus Andersson (CGY, D) — ixG 6.2 vs 2 goals, due for positive regression.
     Rostered by "Puck Dynasty". Trade target.
   • Ilya Sorokin — expected back from IR within a week per Newsday.

🌡️ YOUR ROSTER
   • Egor Chinakhov is shooting 24%, 3 goals above ixG. Expect a cooldown.
   • Valeri Nichushkin: PP TOI down 1:10 over last 5. Watch for demotion.

📋 LINEUP (unchanged from today's optimizer output)
```

Optionally, one more Claude call at the end writes a three-sentence narrative summary
at the top of the email ("The big move today is..."). This is a nicety, not a need;
the structured cards are the product.

---

## 6. Where the agent runs: three options

**Option A: extend the existing Python cron job (recommended).**
Add the scouts as modules, add one Claude API call for news classification, keep
the Docker + cron deployment you already have on the Elite Mini. Lowest cost, no new
infrastructure, and you already own every piece. Needs an `ANTHROPIC_API_KEY` in
`.env` for the news scout only.

**Option B: headless Claude Code for the news scout.**
Your server already has Claude Code logged in with your subscription, and it has web
search and fetch built in. A cron line like

```
15 8 * * * cd ~/fantasy-hockey-agent && claude -p "$(cat prompts/news_scout.md)" \
  --allowedTools "WebSearch,WebFetch" --output-format json > state/news_today.json
```

lets Claude do the combing itself (search for each of your roster players plus the
league's top 40 free agents, summarize injury and line news as JSON) without you
writing any feed parsers. It draws on your usage credits rather than an API key.
Slower and less deterministic than option A's RSS approach, but zero code for the
fetching layer. A reasonable hybrid: RSS + API classification for the daily bulk,
and a headless Claude Code run once a day for "anything I missed about these 25
specific players."

**Option C: Anthropic Managed Agents with a scheduled deployment.**
Anthropic hosts the agent loop and runs it on a cron schedule for you. Clean, but your
Yahoo OAuth token and league state live on your server, so you'd be pushing
credentials into a hosted sandbox for little gain. Not worth it at this scale.

---

## 7. Fixes to the existing code found while reviewing it

Do these first; two are important.

1. **Rotate your Yahoo consumer key and secret.** `DEPLOY.md` lines 54-55 contain
   real credentials and that file is committed in `50eb86d`. Create a new app key at
   https://developer.yahoo.com/apps/, update `.env`, and replace those lines in
   `DEPLOY.md` with placeholders. If the repo is ever pushed to a remote, also rewrite
   the history (`git filter-repo`) before pushing.
2. **`.gitignore` contains a stray heredoc.** Its first line is literally
   `cat > .gitignore << 'EOF'` and its last is `EOF`. Harmless today, but delete them.
3. **`waiver_manager._build_rostered_set`** has a dead loop (lines 74-88) that fetches
   your own roster once per team before the real loop runs. Replace the whole method
   with the `status=A` player filter from section 3.1 and the run gets about 15
   seconds faster.
4. **`nhl_client.get_injuries()`** is a stub that returns an empty list. The
   InjuryReturnScout replaces it.
5. **`lineup_optimizer._assign_positions`** hardcodes roster slots (line 146, marked
   TODO). Read them from league settings so the optimizer is right for this league.
6. **`stats_provider`** scores every league the same way. Section 3.1 explains the
   category-weighted fix.
7. **Season boundary:** `_current_season()` in `stats_provider.py` switches to the
   new season in September, but MoneyPuck will have no 2026-27 rows until games start
   in October. Until a player has 10 games, fall back to his 2025-26 row so the
   October briefings aren't empty.

---

## 8. Build order

Each phase is useful on its own and takes an evening or two.

| Phase | What | Files |
|---|---|---|
| 0 | Rotate secrets, fix `.gitignore` | `DEPLOY.md`, `.gitignore`, `.env` |
| 1 | State store + `Opportunity` model; `status=A` filter; category-weighted scoring from league settings; roster slots from settings | `state_store.py`, `opportunity.py`, `yahoo_client.py`, `stats_provider.py`, `lineup_optimizer.py` |
| 2 | InjuryReturnScout, GoalieInjuryScout, OwnershipScout (all Yahoo + NHL roster data, no new dependencies) | `scouts/injury_return.py`, `scouts/goalie_injury.py`, `scouts/ownership.py`, `nhl_client.py` |
| 3 | DeploymentScout and RegressionScout from pyhockey game data | `scouts/deployment.py`, `scouts/regression.py` |
| 4 | NewsScout with RSS + Claude classification; merge logic in the ranker | `scouts/news.py`, `ranker.py`, `requirements.txt` |
| 5 | New briefing sections and email template; watchlist and "your roster" sections | `email_sender.py`, `main.py` |
| 6 | Optional: DailyFaceoff line/goalie scrape for corroboration; optional auto-lineup write-back | `scouts/dailyfaceoff.py`, `yahoo_client.py` |

Phase 2 is where you start getting goalie-backup and injury-return alerts, which is
the earliest payoff. Phase 3 is the biggest edge over other managers in your league.

---

## 9. Draft and preseason (next three weeks)

The scouts need game data to work, and the season starts in early October. Between
now and then the useful signals are:

- **Camp line combinations** from beat reporters and DailyFaceoff: who is skating with
  whom, and who is on the first PP unit in practice. The news scout catches these.
- **Goalie tandems:** which teams have a clear 1A/1B split versus a true starter. A
  1B on a good team is a better late-round pick than a 1A on a bad one.
- **Last season's regression candidates** (section 2.4) are your draft-day value
  picks: players whose point totals undersold their chances.
- **Yahoo ADP** (average draft position, available through the players endpoint with
  `sort=AR` during draft season) versus your category-weighted projection gives you a
  list of players the room is undervaluing.

If you want, a `python main.py --draft` mode that prints your top-200 board by
category-weighted value, annotated with regression and PP-unit flags, is about half a
day of work once phase 1 is done.
