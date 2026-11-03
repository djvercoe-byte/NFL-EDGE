"""Entry point. Usage:  python -m scanner.run [--force] [--no-push] [--no-wait] [--offline DIR]"""
import argparse
import json
import os
import sys
import time
import traceback
import urllib.request
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from . import engine, ledger, report, sources, voters
from .statsmodel import (GAMES_URL, PlayerModel, build_distributions, injuries_url, read_csv_text,
                         stats_url, team_ratings)
from .teams import TEAMS, norm_abbr, short

PT = ZoneInfo("America/Los_Angeles")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STATE = os.path.join(ROOT, "state")
DOCS = os.path.join(ROOT, "docs")

# weekday (Mon=0), hour, minute in Pacific time
SLOTS = {"thu": (3, 16, 30, "Thursday 4:30 PM"), "sat": (5, 18, 0, "Saturday 6:00 PM"),
         "sun": (6, 20, 0, "Sunday 8:00 PM")}
PROPS_GAMES = {"thu": 3, "sat": 5, "sun": 2, "manual": 3}
PROP_MARKETS = [v[0] for v in engine.PROP_KINDS.values()]
CREDIT_RESERVE = 40


def log(*a):
    print(datetime.now(timezone.utc).strftime("%H:%M:%S"), *a, flush=True)


def find_slot(now_pt):
    for name, (wd, h, mi, _) in SLOTS.items():
        target = now_pt.replace(hour=h, minute=mi, second=0, microsecond=0)
        if now_pt.weekday() == wd and target - timedelta(minutes=50) <= now_pt <= target + timedelta(hours=3):
            return name, target
    return None, None


def week_end(now_utc):
    """Tuesday 10:00 UTC after the coming Monday night game (MNF is over by ~04:30 UTC Tuesday)."""
    days = (1 - now_utc.weekday()) % 7 or 7
    d = (now_utc + timedelta(days=days)).replace(hour=10, minute=0, second=0, microsecond=0)
    if d - now_utc > timedelta(days=7):
        d -= timedelta(days=7)
    return d


def load_json(path, default):
    try:
        with open(path) as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def save_json(path, obj):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        json.dump(obj, f, indent=1, default=str)


def push(topic, title, body, click):
    headers = {"Title": title.encode("ascii", "ignore").decode(), "Tags": "football,chart_with_upwards_trend",
               "Priority": "default"}
    if click:
        headers["Click"] = click
    req = urllib.request.Request(f"https://ntfy.sh/{topic}", data=body.encode("utf-8"), method="POST",
                                 headers=headers)
    with urllib.request.urlopen(req, timeout=30) as r:
        return r.status


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--force", action="store_true", help="run now regardless of schedule")
    ap.add_argument("--no-push", action="store_true")
    ap.add_argument("--no-wait", action="store_true")
    args = ap.parse_args()

    now_utc = datetime.now(timezone.utc)
    now_pt = now_utc.astimezone(PT)
    slot, target = find_slot(now_pt)
    if args.force:
        slot = slot or "manual"
    if not slot:
        log(f"Not a report window ({now_pt:%a %H:%M} PT). Exiting.")
        return 0
    meta = load_json(os.path.join(STATE, "meta.json"), {})
    run_key = f"{now_pt:%Y-%m-%d}-{slot}"
    if not args.force and meta.get("last_run_key") == run_key:
        log(f"{run_key} already sent. Exiting.")
        return 0
    # Start the scan ~4 minutes before the slot so the push lands on time
    if target and not args.no_wait and not args.force:
        wait = (target - timedelta(minutes=4) - datetime.now(PT)).total_seconds()
        if wait > 0:
            log(f"Waiting {wait / 60:.1f} min for {SLOTS[slot][3]} PT")
            time.sleep(wait)
        now_utc = datetime.now(timezone.utc)

    status = []
    t_end = week_end(now_utc)
    log("Week window ends", t_end)

    # ---------------------------------------------------------------- nflverse data + models
    games_csv = read_csv_text(sources.http_get(GAMES_URL, timeout=120).decode())
    season = max(int(g["season"]) for g in games_csv if g["result"] != "")
    upcoming = [g for g in games_csv if g["season"] == str(season) and g["result"] == ""]
    cur_week = min((int(g["week"]) for g in upcoming), default=18)
    dists = build_distributions(games_csv)
    ratings = team_ratings(games_csv, season, cur_week)
    log(f"Season {season} week {cur_week}; margin sd {dists['margin'].sigma:.1f}, total sd {dists['total'].sigma:.1f}")
    try:
        stats_rows = read_csv_text(sources.http_get(stats_url(season), timeout=180).decode())
    except Exception as ex:
        stats_rows = []
        status.append(f"Player stats unavailable ({ex}); props skipped.")
    try:
        inj_rows = read_csv_text(sources.http_get(injuries_url(season), timeout=120).decode())
    except Exception:
        inj_rows = []
        status.append("Injury report unavailable this run.")
    players = PlayerModel(stats_rows, inj_rows, cur_week)

    # ---------------------------------------------------------------- Polymarket games this week
    evs = sources.poly_nfl_events()
    import re
    game_evs = [e for e in evs if re.match(r"^nfl-[a-z]+-[a-z]+-\d{4}-\d{2}-\d{2}$", e.get("slug", ""))]
    in_week = []
    for e_ in game_evs:
        ko = datetime.fromisoformat(e_["startTime"].replace("Z", "+00:00"))
        if now_utc + timedelta(minutes=5) < ko < t_end:
            in_week.append(e_["slug"])
    log(f"{len(in_week)} Polymarket games left this week")
    games = []
    full_events = {}
    for s in in_week:
        try:
            ev = sources.poly_event(s)
            full_events[s] = ev
            g = engine.parse_game(ev)
            if g:
                games.append(g)
        except Exception as ex:
            status.append(f"Couldn't load {s}: {ex}")
    n_markets = sum(len(g["markets"]) for g in games)

    # ---------------------------------------------------------------- sportsbook consensus
    odds = sources.OddsClient(os.environ.get("ODDS_API_KEY", ""))
    cons = {}
    try:
        main = odds.mainlines(now_utc.strftime("%Y-%m-%dT%H:%M:%SZ"), t_end.strftime("%Y-%m-%dT%H:%M:%SZ"))
        cons = engine.build_consensus(main, dists)
    except Exception as ex:
        status.append(f"Sportsbook lines unavailable: {ex}")
    log(f"Consensus for {len(cons)} games; credits remaining {odds.remaining}")
    vst = voters.load_state(os.path.join(STATE, "voters.json"))
    voters.record_lines(vst, cons, now_utc)

    # props: limited games to stay inside the free Odds API allowance
    def prop_priority(g):
        return -sum(1 for m in g["markets"] if m["type"] in ("PROP", "TD"))
    prop_games = sorted(games, key=lambda g: (g["kickoff"] > now_utc + timedelta(hours=30), prop_priority(g)))
    budget = PROPS_GAMES.get(slot, 3)
    prop_cons_by_game = {}
    for g in prop_games:
        if budget <= 0:
            break
        c = cons.get((g["away"], g["home"]))
        if not c or not c.get("odds_id"):
            continue
        if odds.remaining is not None and odds.remaining - len(PROP_MARKETS) < CREDIT_RESERVE:
            status.append("Skipped some props to protect the Odds API monthly allowance.")
            break
        try:
            pj = odds.event_props(c["odds_id"], PROP_MARKETS)
            prop_cons_by_game[g["slug"]] = engine.build_prop_consensus(pj)
            budget -= 1
        except Exception as ex:
            status.append(f"Props unavailable for {g['label']}: {ex}")
    log(f"Props priced for {len(prop_cons_by_game)} games; credits remaining {odds.remaining}")

    # ---------------------------------------------------------------- flags: weather, QB injuries, neutral sites
    flags = {}
    wx_by_game, team_qb = {}, {}
    sched = {}
    for gr in games_csv:
        if gr["season"] == str(season):
            sched[(norm_abbr(gr["away_team"]), norm_abbr(gr["home_team"]))] = gr
    for g in games:
        f = {"notes": []}
        row = sched.get((g["away"], g["home"]), {})
        neutral = (row.get("location") or "").lower() == "neutral"
        f["neutral"] = neutral
        roof = (row.get("roof") or "").lower()
        team_roof = TEAMS[g["home"]][4]
        if not neutral and team_roof == "open" and roof not in ("dome", "closed"):
            w = sources.weather_at(TEAMS[g["home"]][2], TEAMS[g["home"]][3], g["kickoff"])
            if w:
                wx_by_game[g["slug"]] = w
                temp, wind, rain = w
                if wind >= 15:
                    f["notes"].append(f"wind {wind:.0f} mph forecast")
                    f["weather_risk"] = True
                if rain >= 2.0:
                    f["notes"].append("rain forecast")
                    f["weather_risk"] = True
                if temp <= 25:
                    f["notes"].append(f"cold ({temp:.0f}°F)")
        for side, col in (("away", "away_qb_name"), ("home", "home_qb_name")):
            qb = row.get(col)
            if qb:
                team_qb[g[side]] = qb
            st = players.injury_status(qb) if qb else ""
            if st:
                f["notes"].append(f"{short(g[side])} QB {qb} listed {st}")
                f["qb_risk"] = True
        flags[g["slug"]] = f

    # ---------------------------------------------------------------- evaluate
    cands, skipped = [], {"suspect": 0, "no_fair": 0}
    for g in games:
        c = cons.get((g["away"], g["home"]))
        out, sk = engine.evaluate_game(g, c, dists, ratings, players, prop_cons_by_game.get(g["slug"], {}),
                                       flags[g["slug"]])
        cands.extend(out)
        for k in sk:
            skipped[k] += sk[k]
    # ---------------------------------------------------------------- extra voters: line movement, news/injury, weather
    injuries = {}
    try:
        injuries = voters.parse_espn_injuries(sources.espn_injuries())
    except Exception as ex:
        status.append(f"ESPN injury report unavailable ({ex}); news/injury voter used the nflverse report only.")
    for nm_, rec_ in players.injury.items() if hasattr(players, "injury") else []:
        if nm_ not in injuries and rec_ and rec_[0] >= cur_week - 1:
            injuries[nm_] = {"status": rec_[1], "team": players.team_of.get(nm_, ""), "pos": "", "name": nm_}
    names = {voters.norm_name(c["player"]): c["player"] for c in cands if c.get("player")}
    names.update({voters.norm_name(q_): q_ for q_ in team_qb.values()})
    try:
        voters.apply_espn_news(injuries, sources.espn_news(), now_utc, names)
    except Exception as ex:
        status.append(f"ESPN news unavailable ({ex}).")
    n_before = len(cands)
    cands = voters.apply(cands, vst, dists, cons, injuries, team_qb, players, wx_by_game)
    if n_before - len(cands):
        status.append(f"News/injury voter dropped {n_before - len(cands)} prop sides for players listed out or doubtful.")
    picks = engine.select_picks(cands, flags)

    # liquidity check on the picks (order book)
    liq = {}
    for p in picks:
        try:
            liq[f"{p['slug']}|{p['side']}"] = engine.book_liquidity(sources.poly_book(p["slug"]), p["side"], p["price"])
        except Exception:
            pass
    picks = [p for p in picks if liq.get(f"{p['slug']}|{p['side']}", 999) >= 20]
    for p in picks:
        lq = liq.get(f"{p['slug']}|{p['side']}")
        if lq is not None and lq < 75:
            p["risk"] = min(10, p["risk"] + 1)
            p["notes"].append("thin order book at this price")
    parlays = engine.build_parlays(picks)

    # line movement vs. last report
    prev = load_json(os.path.join(STATE, "prices.json"), {})
    moves = {}
    cur_prices = {}
    for c in cands:
        k = f"{c['slug']}|{c['side']}"
        cur_prices[k] = round(c["price"], 4)
        if k in prev.get("prices", {}):
            moves[k] = c["price"] - prev["prices"][k]

    # ---------------------------------------------------------------- ledger
    led = ledger.load(os.path.join(STATE, "ledger.json"))
    ev_cache = dict(full_events)

    def get_event(slug):
        if slug not in ev_cache:
            try:
                ev_cache[slug] = sources.poly_event(slug)
            except Exception:
                ev_cache[slug] = None
        return ev_cache[slug]

    graded = ledger.grade(led, get_event, now_utc)
    n_voter = voters.learn(vst, led)
    added = ledger.log_picks(led, picks, parlays, run_key)
    sc = ledger.scorecard(led)
    log(f"Graded {graded}, logged {added}; record {sc['wins']}-{sc['losses']}")

    # ---------------------------------------------------------------- report
    status.extend([
        f"Polymarket US: {len(games)} games, {n_markets:,} markets parsed (moneyline, spreads, totals, team totals, player props).",
        f"Sportsbook consensus: {len(cons)} games; props priced for {len(prop_cons_by_game)} games this run.",
        f"Skipped {skipped['no_fair']} markets with no sportsbook price and {skipped['suspect']} that looked mis-mapped.",
        f"The Odds API credits remaining this month: {odds.remaining if odds.remaining is not None else 'unknown'}.",
        f"Stats model: {ratings['n_games']} games this season (+ fading weight on last season); home field {ratings['hfa']:.1f} pts.",
        f"Extra voters: lines tracked for {len(vst['lines'])} games, {len(injuries)} injury listings, weather for {len(wx_by_game)} "
        f"outdoor games; {n_voter} voter calls scored on newly settled bets.",
    ])
    gen = datetime.now(PT).strftime("%a %b %-d, %-I:%M %p PT")
    ctx = {"picks": picks, "parlays": parlays, "scorecard": sc, "moves": moves, "liq": liq, "status": status,
           "slot_name": SLOTS.get(slot, (0, 0, 0, "Manual run"))[3] + " report", "slot_short": SLOTS[slot][3][:3] if slot in SLOTS else "Test",
           "generated": gen, "week": cur_week, "n_games": len(games), "n_markets": n_markets, "n_priced": len(cands), "voters": vst}
    page = report.render(ctx)
    os.makedirs(os.path.join(DOCS, "reports"), exist_ok=True)
    with open(os.path.join(DOCS, "index.html"), "w") as f:
        f.write(page)
    with open(os.path.join(DOCS, "reports", f"{run_key}.html"), "w") as f:
        f.write(page)

    repo = os.environ.get("GITHUB_REPOSITORY", "")
    url = ""
    if "/" in repo:
        owner, name = repo.split("/", 1)
        url = f"https://{owner.lower()}.github.io/{name}/reports/{run_key}.html"
    title, body = report.push_text(ctx, url)
    log(title)
    log(body)
    topic = os.environ.get("NTFY_TOPIC", "")
    if topic and not args.no_push:
        try:
            push(topic, title, body, url)
            log("Push sent")
        except Exception as ex:
            log("Push failed:", ex)

    save_json(os.path.join(STATE, "prices.json"), {"run": run_key, "prices": cur_prices})
    ledger.save(os.path.join(STATE, "ledger.json"), led)
    voters.save_state(os.path.join(STATE, "voters.json"), vst)
    meta.update({"last_run_key": run_key if slot != "manual" else meta.get("last_run_key"),
                 "last_run_at": now_utc.isoformat(), "odds_remaining": odds.remaining})
    save_json(os.path.join(STATE, "meta.json"), meta)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        traceback.print_exc()
        topic = os.environ.get("NTFY_TOPIC", "")
        if topic:
            try:
                push(topic, "NFL Edge: scan failed", "The scheduled scan hit an error. Check the GitHub Actions log.", "")
            except Exception:
                pass
        sys.exit(1)
