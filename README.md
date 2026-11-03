# NFL Polymarket Edge Scanner

Scans every NFL market on **Polymarket US** for the current week (moneylines, spreads, totals, team totals,
player props) and flags bets that are underpriced after Polymarket's fee. Reports go out **Thursday 4:30 PM,
Saturday 6:00 PM, and Sunday 8:00 PM Pacific** as a push notification (ntfy app) linking to a full report page.

## How a bet gets flagged
1. **Polymarket price**: the live price to buy that side, plus the market's taker fee.
2. **Sportsbook consensus**: lines from ~8 US books via The Odds API, with the vig removed. Spreads and totals use
   key-number-aware score distributions learned from 25 years of NFL results, so alt lines near 3/7/10 are priced correctly.
3. **Independent stats model**: team offense/defense ratings and player per-game production (nflverse data),
   adjusted for opponent, with injury status.
4. A bet is listed only if the consensus edge clears a minimum (2% for game lines, 3% for team totals, 4–4.5% for props)
   **and** the stats model also says it's underpriced.

Each pick shows: buy price, fair chance, edge, expected profit per $10, a 1–10 risk meter, a "worth it?" verdict,
liquidity at that price, price movement since the last report, sportsbook count, injuries, and weather.
Parlays combine qualifying legs from **different games in the same week**. The page lists the minimum combo payout
that still makes each parlay worth it, because Polymarket sets combo prices in the app. Nothing stays open past
Monday Night Football.

## Extra voters
Three more signals nudge each bet's fair chance (by at most 4 points combined), on top of the two that must agree:
- **Line movement**: how the sportsbook consensus moved since the week's first report (lines that move tend to keep drifting).
- **News/injury**: ESPN's live injury report and headlines. Props for players listed out/doubtful are dropped; questionable
  players, a starting QB out, or a top receiver out (more targets for teammates) nudge the related bets.
- **Weather**: wind above 12 mph, rain and hard cold lower expected points and passing numbers at outdoor stadiums.

Each voter is scored when bets settle and its trust weight moves between 0.25x and 1.5x, so the report's
"Extra voters' track record" table shows which ones are earning their keep. State lives in `state/voters.json`.

## Paper trading
Every pick is logged to `state/ledger.json` against a $100 paper bankroll (quarter-Kelly, max 3% per bet) and
graded from Polymarket's own settlement. The scorecard on each report shows actual vs. expected results.

## Setup
Repository secrets: `ODDS_API_KEY` (the-odds-api.com) and `NTFY_TOPIC` (your private ntfy channel name).
GitHub Pages serves `docs/` from the `main` branch. Run on demand: **Actions → NFL edge scan → Run workflow**.

Props are limited per run (Thu 3 games, Sat 5, Sun 2) to stay within The Odds API free monthly allowance.
This is a research tool, not financial advice.
