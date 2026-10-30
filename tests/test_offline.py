"""Offline end-to-end test using real nflverse files and a hand-built Polymarket/odds sample
(prices copied from the live BAL @ BUF markets on Oct 30, 2026). Run: python -m tests.test_offline DATA_DIR"""
import csv
import os
import sys
from datetime import datetime, timedelta, timezone

from scanner import engine, ledger, report
from scanner.statsmodel import PlayerModel, build_distributions, team_ratings

D = sys.argv[1] if len(sys.argv) > 1 else "data"
games_csv = list(csv.DictReader(open(os.path.join(D, "games.csv"))))
dists = build_distributions(games_csv)
ratings = team_ratings(games_csv, 2026, 8)
players = PlayerModel(list(csv.DictReader(open(os.path.join(D, "stats_player_week_2026.csv")))),
                      list(csv.DictReader(open(os.path.join(D, "injuries_2026.csv")))), 8)


def mk(slug_rest, typ, title, bid, ask, team=None):
    sides = [{"long": True, "team": {"abbreviation": team} if team else None}, {"long": False}]
    return {"slug": ("aec-" if typ == "MONEYLINE" else "asc-") + "nfl-bal-buf-2026-11-01" + (("-" + slug_rest) if slug_rest else ""),
            "sportsMarketTypeV2": "SPORTS_MARKET_TYPE_" + typ, "title": title, "status": "MARKET_STATUS_OPEN",
            "bestBidQuote": {"value": str(bid)}, "bestAskQuote": {"value": str(ask)}, "feeCoefficient": 0.0695,
            "comboEnabled": True, "marketSides": sides}


event = {"slug": "nfl-bal-buf-2026-11-01", "startTime": "2026-11-01T18:00:00Z", "markets": [
    mk("", "MONEYLINE", "BAL Ravens vs BUF Bills", 0.375, 0.3775, "bal"),
    mk("pos-2pt5", "SPREAD", "x", 0.43, 0.44), mk("pos-3pt5", "SPREAD", "x", 0.535, 0.54),
    mk("neg-2pt5", "SPREAD", "x", 0.335, 0.34), mk("neg-3pt5", "SPREAD", "x", 0.265, 0.27),
    mk("total-53pt5", "TOTAL", "Over 53.5", 0.53, 0.54), mk("total-54pt5", "TOTAL", "Over 54.5", 0.49, 0.495),
    mk("total-55pt5", "TOTAL", "Over 55.5", 0.455, 0.46), mk("tt-buf-14pt5", "TOTAL", "BUF over 14.5", 0.92, 0.96),
    mk("tt-buf-27pt5", "TOTAL", "BUF over 27.5", 0.48, 0.50),
    mk("pyd-josall-gte250", "PROP", "Josh Allen 250+ passing yards", 0.44, 0.47),
    mk("ryd-derhen-gte100", "PROP", "Derrick Henry 100+ rushing yards", 0.25, 0.33),
    mk("td-derhen-gte1", "PROP", "Derrick Henry 1+ touchdowns", 0.52, 0.55),
    mk("rec-dawkno-gte3", "PROP", "Dawson Knox 3+ receptions", 0.30, 0.34),
]}
odds_main = [{"id": "c93c", "home_team": "Buffalo Bills", "away_team": "Baltimore Ravens", "commence_time": "2026-11-01T18:00:00Z",
              "bookmakers": [
                  {"key": b, "markets": [
                      {"key": "h2h", "outcomes": [{"name": "Baltimore Ravens", "price": ma}, {"name": "Buffalo Bills", "price": mh}]},
                      {"key": "spreads", "outcomes": [{"name": "Baltimore Ravens", "price": sa, "point": 3.0},
                                                      {"name": "Buffalo Bills", "price": sh, "point": -3.0}]},
                      {"key": "totals", "outcomes": [{"name": "Over", "price": 1.909, "point": 54.5},
                                                     {"name": "Under", "price": 1.909, "point": 54.5}]}]}
                  for b, ma, mh, sa, sh in [("draftkings", 2.45, 1.571, 1.952, 1.870), ("fanduel", 2.44, 1.581, 1.962, 1.847),
                                            ("betmgm", 2.50, 1.541, 1.952, 1.870), ("bovada", 2.50, 1.571, 2.02, 1.82)]]}]
props = {"bookmakers": [{"key": "draftkings", "markets": [
    {"key": "player_pass_yds", "outcomes": [{"name": "Over", "description": "Josh Allen", "price": 1.87, "point": 236.5},
                                            {"name": "Under", "description": "Josh Allen", "price": 1.95, "point": 236.5}]},
    {"key": "player_rush_yds", "outcomes": [{"name": "Over", "description": "Derrick Henry", "price": 1.87, "point": 82.5},
                                            {"name": "Under", "description": "Derrick Henry", "price": 1.95, "point": 82.5}]},
    {"key": "player_anytime_td", "outcomes": [{"name": "Yes", "description": "Derrick Henry", "price": 1.80}]},
    {"key": "player_receptions", "outcomes": [{"name": "Over", "description": "Dawson Knox", "price": 2.1, "point": 2.5},
                                              {"name": "Under", "description": "Dawson Knox", "price": 1.72, "point": 2.5}]},
]}]}

g = engine.parse_game(event)
assert g and g["away"] == "BAL" and g["home"] == "BUF", g
types = {}
for m in g["markets"]:
    types[m["type"]] = types.get(m["type"], 0) + 1
print("parsed", types)
cons = engine.build_consensus(odds_main, dists)[("BAL", "BUF")]
print("consensus", {k: (round(v, 3) if isinstance(v, float) else v) for k, v in cons.items()})
pc = engine.build_prop_consensus(props)
cands, sk = engine.evaluate_game(g, cons, dists, ratings, players, pc, {"notes": []})
print("cands", len(cands), "skipped", sk)
for c in sorted(cands, key=lambda c: -c["edge_c"])[:10]:
    print(f"  {c['label']:<42} price {c['price']:.3f} cost {c['cost']:.3f} books {c['p_c']:.3f} model {c['p_m'] if c['p_m'] is None else round(c['p_m'], 3)}  edge {c['edge_c']:+.3f}")
flags = {g["slug"]: {"notes": []}}
picks = engine.select_picks(cands, flags)
# fake a second game's pick to exercise parlays
fake = dict(picks[0]) if picks else None
parl = []
if fake:
    fake.update({"game_slug": "nfl-x-y", "game": "X @ Y", "slug": "fake", "label": "Fake leg"})
    parl = engine.build_parlays(picks + [fake])
print("picks", [(p["label"], round(p["ev10"], 2), p["risk"], p["verdict"]) for p in picks])
print("parlays", [(round(x["p"], 3), round(x["min_mult"], 2), round(x["ev10"], 2)) for x in parl])
led = {"bankroll_start": 100.0, "bets": []}
ledger.log_picks(led, picks, parl, "test")
sc = ledger.scorecard(led)
ctx = {"picks": picks, "parlays": parl, "scorecard": sc, "moves": {}, "liq": {}, "status": ["offline test"],
       "slot_name": "Test", "slot_short": "Test", "generated": "now", "week": 8, "n_games": 1,
       "n_markets": len(g["markets"]), "n_priced": len(cands)}
os.makedirs("out", exist_ok=True)
open("out/test_report.html", "w").write(report.render(ctx))
print(report.push_text(ctx, "https://example"))
