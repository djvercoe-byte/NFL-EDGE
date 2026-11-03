"""Core: turn Polymarket US NFL markets + sportsbook consensus + stats model into graded picks."""
import itertools
import math
import re
from datetime import datetime, timezone

from .dists import devig2, median, norm_cdf, norm_ppf, poisson_lambda_for, poisson_sf
from .statsmodel import (model_game, model_ml_prob_away, model_spread_prob, model_team_total_prob,
                         model_total_prob, norm_name)
from .teams import FULLNAME_TO_ABBR, POLY_TO_ABBR, TEAMS, short

# Minimum edge (fair prob minus all-in cost) before we call something a pick, by market type.
MIN_EDGE = {"ML": 0.020, "SPREAD": 0.020, "TOTAL": 0.020, "TEAM_TOTAL": 0.030, "PROP": 0.040, "TD": 0.045}
# How far the sportsbook consensus may sit from Polymarket's mid before we assume a data/mapping problem.
SANITY = {"ML": 0.15, "SPREAD": 0.18, "TOTAL": 0.18, "TEAM_TOTAL": 0.20, "PROP": 0.25, "TD": 0.20}
BASE_RISK = {"ML": 2, "SPREAD": 3, "TOTAL": 3, "TEAM_TOTAL": 4, "PROP": 5, "TD": 6}
W_CONSENSUS = 0.70  # blend weight for sportsbook consensus vs. stats model

PROP_KINDS = {  # polymarket slug kind -> (odds api market, display noun, value type)
    "pyd": ("player_pass_yds", "passing yards", "cont"),
    "ryd": ("player_rush_yds", "rushing yards", "cont"),
    "recyd": ("player_reception_yds", "receiving yards", "cont"),
    "rec": ("player_receptions", "receptions", "count"),
    "ptd": ("player_pass_tds", "passing TDs", "count"),
    "td": ("player_anytime_td", "touchdowns", "td"),
}
PROP_SD = {
    "pyd": lambda L: max(55.0, 0.24 * L + 12),
    "ryd": lambda L: max(16.0, 0.50 * L + 10),
    "recyd": lambda L: max(16.0, 0.55 * L + 10),
}

NUM_RE = re.compile(r"^(\d+)pt(\d+)$")


def slug_num(s):
    m = NUM_RE.match(s)
    if m:
        return float(f"{m.group(1)}.{m.group(2)}")
    return float(s)


def q(v):
    try:
        return float(v["value"]) if isinstance(v, dict) else (float(v) if v not in (None, "") else None)
    except (TypeError, ValueError, KeyError):
        return None


def american(p):
    """Price (prob) -> American odds string for familiarity."""
    if p is None or p <= 0 or p >= 1:
        return ""
    if p >= 0.5:
        return f"-{round(100 * p / (1 - p))}"
    return f"+{round(100 * (1 - p) / p)}"


# ----------------------------------------------------------------------------- Polymarket parsing

def parse_game(event):
    """Return game dict with normalized markets from a full Polymarket US event."""
    slug = event["slug"]
    m = re.match(r"^nfl-([a-z]+)-([a-z]+)-(\d{4}-\d{2}-\d{2})$", slug)
    if not m:
        return None
    away, home = POLY_TO_ABBR.get(m.group(1)), POLY_TO_ABBR.get(m.group(2))
    if not away or not home:
        return None
    kickoff = datetime.fromisoformat(event["startTime"].replace("Z", "+00:00"))
    game = {"slug": slug, "away": away, "home": home, "kickoff": kickoff, "markets": [],
            "label": f"{short(away)} @ {short(home)}"}
    for mk in event.get("markets", []):
        if mk.get("closed") or mk.get("hidden") or mk.get("status") != "MARKET_STATUS_OPEN":
            continue
        ms = mk.get("slug", "")
        if slug not in ms:
            continue
        rest = ms.split(slug, 1)[1].lstrip("-")
        bid, ask = q(mk.get("bestBidQuote")), q(mk.get("bestAskQuote"))
        if bid is None and ask is None:
            continue
        base = {"slug": ms, "bid": bid, "ask": ask, "fee": float(mk.get("feeCoefficient") or 0.07),
                "combo": bool(mk.get("comboEnabled")), "title": mk.get("title", "")}
        t = mk.get("sportsMarketTypeV2", "")
        rec = None
        if t.endswith("MONEYLINE") and rest == "":
            long_team = None
            for sd in mk.get("marketSides", []):
                if sd.get("long") and sd.get("team"):
                    long_team = POLY_TO_ABBR.get((sd["team"].get("abbreviation") or "").lower())
            long_team = long_team or away
            rec = {"type": "ML", "long_team": long_team}
        elif t.endswith("SPREAD"):
            mm = re.match(r"^(pos|neg)-(\d+pt\d+)$", rest)
            if mm:
                n = slug_num(mm.group(2))
                rec = {"type": "SPREAD", "away_line": n if mm.group(1) == "pos" else -n}
        elif t.endswith("TOTAL"):
            mm = re.match(r"^total-(\d+pt\d+)$", rest)
            mt = re.match(r"^tt-([a-z]+)-(\d+pt\d+)$", rest)
            if mm:
                rec = {"type": "TOTAL", "line": slug_num(mm.group(1))}
            elif mt and POLY_TO_ABBR.get(mt.group(1)) in (away, home):
                rec = {"type": "TEAM_TOTAL", "team": POLY_TO_ABBR[mt.group(1)], "line": slug_num(mt.group(2))}
        elif t.endswith("PROP"):
            mp = re.match(r"^(pyd|ryd|recyd|rec|td|ptd)-([a-z]+)-gte(\d+)$", rest)
            if mp:
                kind, n = mp.group(1), int(mp.group(3))
                nm = re.match(r"^(.*?)\s+\d+\+\s", mk.get("title", "") + " ")
                if nm:
                    rec = {"type": "TD" if kind == "td" else "PROP", "kind": kind, "player": nm.group(1).strip(),
                           "n": n}
        if rec:
            rec.update(base)
            game["markets"].append(rec)
    return game


# ----------------------------------------------------------------------------- sportsbook consensus

def build_consensus(odds_events, dists):
    """Map (away, home) -> consensus numbers from The Odds API mainlines."""
    out = {}
    for ev in odds_events:
        home = FULLNAME_TO_ABBR.get(ev.get("home_team"))
        away = FULLNAME_TO_ABBR.get(ev.get("away_team"))
        if not home or not away:
            continue
        ml, mus, tots, books, spreads_h, totals_l = [], [], [], set(), [], []
        for bk in ev.get("bookmakers", []):
            for mk in bk.get("markets", []):
                oc = {o["name"]: o for o in mk.get("outcomes", [])}
                try:
                    if mk["key"] == "h2h" and len(oc) == 2:
                        pa = devig2(oc[ev["away_team"]]["price"], oc[ev["home_team"]]["price"])
                        ml.append(pa)
                        books.add(bk["key"])
                    elif mk["key"] == "spreads" and len(oc) == 2:
                        h = oc[ev["home_team"]]
                        a = oc[ev["away_team"]]
                        p_home = devig2(h["price"], a["price"])
                        hp = float(h["point"])
                        # home covers if margin(home-away) + hp > 0  <=>  margin > -hp
                        mus.append(dists["margin"].solve_mu(-hp, p_home))
                        spreads_h.append(hp)
                        books.add(bk["key"])
                    elif mk["key"] == "totals" and len(oc) == 2:
                        o, u = oc["Over"], oc["Under"]
                        p_over = devig2(o["price"], u["price"])
                        L = float(o["point"])
                        tots.append(dists["total"].solve_mu(L, p_over))
                        totals_l.append(L)
                        books.add(bk["key"])
                except (KeyError, ValueError, ZeroDivisionError):
                    continue
        mu_m = median(mus)
        p_away_ml = median(ml)
        if mu_m is None and p_away_ml is not None:
            mu_m = dists["margin"].solve_mu(0, 1 - p_away_ml)
        out[(away, home)] = {
            "odds_id": ev.get("id"), "p_away_ml": p_away_ml, "mu_margin": mu_m, "mu_total": median(tots),
            "books": len(books), "main_home_spread": median(spreads_h), "main_total": median(totals_l),
            "commence": ev.get("commence_time"),
        }
    return out


def build_prop_consensus(props_json):
    """(norm player, kind) -> list of per-book (line, p_over) or for td: p_yes."""
    out = {}
    rev = {v[0]: k for k, v in PROP_KINDS.items()}
    for bk in props_json.get("bookmakers", []):
        for mk in bk.get("markets", []):
            kind = rev.get(mk.get("key"))
            if not kind:
                continue
            groups = {}
            for o in mk.get("outcomes", []):
                pl = norm_name(o.get("description") or "")
                key = (pl, o.get("point"))
                groups.setdefault(key, {})[o.get("name")] = o.get("price")
            for (pl, pt), sides in groups.items():
                if kind == "td":
                    if "Yes" in sides and "No" in sides:
                        p = devig2(sides["Yes"], sides["No"])
                    elif "Yes" in sides:
                        p = (1 / sides["Yes"]) * 0.92  # one-sided market: strip a typical ~8% hold
                    else:
                        continue
                    out.setdefault((pl, kind), []).append((None, p, bk["key"]))
                elif "Over" in sides and "Under" in sides and pt is not None:
                    p = devig2(sides["Over"], sides["Under"])
                    out.setdefault((pl, kind), []).append((float(pt), p, bk["key"]))
    return out


def prop_fair_prob(kind, n, book_lines):
    """Per-book fair P(stat >= n), then median. Returns (p, n_books, main_line, distance_in_sd)."""
    ps, lines = [], []
    for L, p_over, _ in book_lines:
        if kind == "td":
            lam = -math.log(max(1e-6, 1 - p_over))
            ps.append(poisson_sf(n, lam))
            continue
        lines.append(L)
        if PROP_KINDS[kind][2] == "cont":
            sd = PROP_SD[kind](L)
            mu = (L + 0.5) + sd * norm_ppf(p_over)  # P(X >= L+0.5) = p_over
            ps.append(1 - norm_cdf((n - 0.5 - mu) / sd))
        else:
            k = int(math.floor(L)) + 1
            lam = poisson_lambda_for(k, p_over)
            ps.append(poisson_sf(n, lam))
    p = median(ps)
    if p is None:
        return None
    main = median(lines)
    if kind == "td" or main is None:
        dist = 0.0 if n <= 1 else 1.0
    elif PROP_KINDS[kind][2] == "cont":
        dist = abs(n - (main + 0.5)) / PROP_SD[kind](main)
    else:
        dist = abs(n - (math.floor(main) + 1)) / max(1.0, math.sqrt(max(main, 0.5)))
    books = len({b for _, _, b in book_lines})
    return p, books, main, dist


# ----------------------------------------------------------------------------- evaluation

def describe(game, m, side):
    a, h = short(game["away"]), short(game["home"])
    t = m["type"]
    if t == "ML":
        team = m["long_team"] if side == "long" else (game["home"] if m["long_team"] == game["away"] else game["away"])
        return f"{short(team)} to win"
    if t == "SPREAD":
        line = m["away_line"]
        if side == "long":
            return f"{a} {line:+g}"
        return f"{h} {-line:+g}"
    if t == "TOTAL":
        return f"{'Over' if side == 'long' else 'Under'} {m['line']:g} ({a}-{h})"
    if t == "TEAM_TOTAL":
        return f"{short(m['team'])} team total {'Over' if side == 'long' else 'Under'} {m['line']:g}"
    noun = PROP_KINDS[m["kind"]][1]
    if side == "long":
        return f"{m['player']} {m['n']}+ {noun}"
    return f"NO on {m['player']} {m['n']}+ {noun}"


def evaluate_game(game, cons, dists, ratings, players, prop_cons, flags):
    """Yield candidate bet dicts for every market side in this game with a fair price."""
    away, home = game["away"], game["home"]
    neutral = flags.get("neutral", False)
    hp_m, ap_m = model_game(ratings, home, away, neutral)
    mu_margin_model, mu_total_model = hp_m - ap_m, hp_m + ap_m
    out, skipped = [], {"suspect": 0, "no_fair": 0}
    for m in game["markets"]:
        t = m["type"]
        pc = pm = None
        info = {}
        if t in ("ML", "SPREAD", "TOTAL", "TEAM_TOTAL"):
            if not cons or cons.get("mu_margin") is None:
                skipped["no_fair"] += 1
                continue
            mu_m = cons["mu_margin"]
            mu_t = cons.get("mu_total")
            if t == "ML":
                pa = cons["p_away_ml"] if cons["p_away_ml"] is not None else model_ml_prob_away(dists, mu_m)
                pma = model_ml_prob_away(dists, mu_margin_model)
                pc = pa if m["long_team"] == away else 1 - pa
                pm = pma if m["long_team"] == away else 1 - pma
                dist_sd = 0.0
            elif t == "SPREAD":
                pc = model_spread_prob(dists, mu_m, m["away_line"])
                pm = model_spread_prob(dists, mu_margin_model, m["away_line"])
                main = cons.get("main_home_spread")
                dist_sd = abs(m["away_line"] - (main if main is not None else m["away_line"])) / dists["margin"].sigma
            elif t == "TOTAL":
                if mu_t is None:
                    continue
                pc = model_total_prob(dists, mu_t, m["line"])
                pm = model_total_prob(dists, mu_total_model, m["line"])
                main = cons.get("main_total") or m["line"]
                dist_sd = abs(m["line"] - main) / dists["total"].sigma
            else:
                if mu_t is None:
                    continue
                mu_team = (mu_t + mu_m) / 2 if m["team"] == home else (mu_t - mu_m) / 2
                mu_team_model = hp_m if m["team"] == home else ap_m
                pc = model_team_total_prob(dists, mu_team, m["line"])
                pm = model_team_total_prob(dists, mu_team_model, m["line"])
                dist_sd = abs(m["line"] - mu_team) / dists["team"].sigma
            info["books"] = cons.get("books", 0)
        else:
            key = (norm_name(m["player"]), m["kind"])
            bl = prop_cons.get(key)
            if not bl:
                skipped["no_fair"] += 1
                continue
            res = prop_fair_prob(m["kind"], m["n"], bl)
            if not res:
                continue
            pc, nb, mainline, dist_sd = res
            team = players.team_of.get(norm_name(m["player"]))
            opp = home if team == away else away if team == home else None
            pm_res, pinfo = players.prob(m["kind"], m["player"], m["n"], opp)
            pm = pm_res
            inj = players.injury_status(m["player"])
            if inj.lower() in ("out", "doubtful"):
                continue
            info.update({"books": nb, "book_line": mainline, "model": pinfo, "injury": inj})
        mid = None
        if m["bid"] is not None and m["ask"] is not None:
            mid = (m["bid"] + m["ask"]) / 2
        if mid is not None and abs(pc - mid) > SANITY[t]:
            skipped["suspect"] += 1
            continue
        for side in ("long", "short"):
            price = m["ask"] if side == "long" else (1 - m["bid"] if m["bid"] is not None else None)
            if price is None or price < 0.03 or price > 0.97:
                continue
            p_c = pc if side == "long" else 1 - pc
            p_m = None if pm is None else (pm if side == "long" else 1 - pm)
            cost = price + m["fee"] * price * (1 - price)
            out.append({
                "game": game["label"], "game_slug": game["slug"], "kickoff": game["kickoff"], "away": away,
                "home": home, "type": t, "slug": m["slug"], "side": side, "label": describe(game, m, side),
                "price": price, "cost": cost, "p_c": p_c, "p_m": p_m, "edge_c": p_c - cost,
                "edge_m": None if p_m is None else p_m - cost, "dist_sd": dist_sd, "combo": m["combo"],
                "spread_width": None if mid is None else m["ask"] - m["bid"], "info": info,
                "player": m.get("player"), "kind": m.get("kind"), "dir": _direction(m, side),
                "mkt": {k: m[k] for k in ("long_team", "away_line", "line", "team") if k in m},
            })
    return out, skipped


def _direction(m, side):
    """Grouping key so we don't recommend five alt lines of the same idea."""
    t = m["type"]
    if t == "ML":
        return ("ML", m["long_team"], side)
    if t == "SPREAD":
        return ("SPREAD", side)
    if t == "TOTAL":
        return ("TOTAL", side)
    if t == "TEAM_TOTAL":
        return ("TT", m["team"], side)
    return ("PROP", norm_name(m["player"]), m["kind"], side)


def score_pick(c, flags):
    p = W_CONSENSUS * c["p_c"] + (1 - W_CONSENSUS) * c["p_m"]
    cost = c["cost"]
    ev10 = 10 * (p / cost - 1)
    risk = BASE_RISK[c["type"]]
    notes = []
    if p < 0.10:
        risk += 3
    elif p < 0.20:
        risk += 2
    elif p < 0.35:
        risk += 1
    dis = abs(c["p_c"] - c["p_m"])
    if dis > 0.15:
        risk += 2
        notes.append("model and books disagree on size")
    elif dis > 0.08:
        risk += 1
    if c["info"].get("books", 0) < 3:
        risk += 1
        notes.append("few sportsbooks quoting")
    if c["dist_sd"] > 0.7:
        risk += 1
        notes.append("alt line far from sportsbook main line")
    if c["spread_width"] is not None and c["spread_width"] > 0.06:
        risk += 1
        notes.append("wide Polymarket bid/ask")
    inj = c["info"].get("injury")
    if inj:
        risk += 1
        notes.append(f"player listed {inj}")
    gf = flags.get(c["game_slug"], {})
    for n in gf.get("notes", []):
        notes.append(n)
    if gf.get("weather_risk") and (c["type"] in ("TOTAL", "TEAM_TOTAL") or c.get("kind") in ("pyd", "recyd", "rec", "ptd")):
        risk += 1
    if gf.get("qb_risk"):
        risk += 1
    risk = int(min(10, max(1, risk)))
    kelly = max(0.0, (p - cost) / (1 - cost)) / 4
    c.update({"p": p, "ev10": ev10, "risk": risk, "notes": notes, "kelly": min(kelly, 0.03),
              "roi": p / cost - 1, "breakeven": cost, "american": american(cost)})
    if ev10 >= 0.50 and risk <= 6:
        c["verdict"] = "Worth it"
    elif ev10 >= 0.25 and risk <= 8:
        c["verdict"] = "Small edge"
    else:
        c["verdict"] = "Long shot / thin"
    return c


def select_picks(cands, flags, max_picks=15):
    picks = []
    for c in cands:
        if c["p_m"] is None:
            continue  # "both must agree" — no independent model view, no pick
        if c["edge_c"] < MIN_EDGE[c["type"]] or c["edge_m"] < 0:
            continue
        picks.append(score_pick(dict(c), flags))
    # keep the best line per idea (game, direction)
    best = {}
    for p in picks:
        k = (p["game_slug"], p["dir"])
        if k not in best or p["ev10"] * (1 - p["risk"] / 20) > best[k]["ev10"] * (1 - best[k]["risk"] / 20):
            best[k] = p
    out = sorted(best.values(), key=lambda p: p["ev10"] * (1 - p["risk"] / 20), reverse=True)
    return out[:max_picks]


def build_parlays(picks, max_out=5):
    legs = [p for p in picks if p["combo"] and p["risk"] <= 7]
    combos = []
    for n in (2, 3):
        for combo in itertools.combinations(legs, n):
            if len({c["game_slug"] for c in combo}) < n:
                continue  # different games only: same-game legs are correlated
            p = math.prod(c["p"] for c in combo)
            cost = math.prod(c["cost"] for c in combo)
            ev10 = 10 * (p / cost - 1)
            if ev10 <= 0:
                continue
            risk = min(10, max(c["risk"] for c in combo) + n)
            combos.append({"legs": combo, "p": p, "cost": cost, "ev10": ev10, "risk": risk,
                           "fair_mult": 1 / p, "min_mult": 1.05 / p, "est_mult": 1 / cost})
    combos.sort(key=lambda x: x["ev10"] * (1 - x["risk"] / 20), reverse=True)
    out, used = [], {}
    for c in combos:
        # don't let one leg dominate every parlay
        if any(used.get(l["slug"] + l["side"], 0) >= 2 for l in c["legs"]):
            continue
        out.append(c)
        for l in c["legs"]:
            used[l["slug"] + l["side"]] = used.get(l["slug"] + l["side"], 0) + 1
        if len(out) >= max_out:
            break
    return out


def book_liquidity(book, side, price):
    """Dollars available within 1 cent of our entry price."""
    if not book:
        return None
    if side == "long":
        levels = book.get("offers") or book.get("asks") or []
        tot = 0.0
        for lv in levels:
            px = q(lv.get("px"))
            qty = float(lv.get("qty") or 0)
            if px is not None and px <= price + 0.0101:
                tot += px * qty
        return tot
    levels = book.get("bids") or []
    tot = 0.0
    for lv in levels:
        px = q(lv.get("px"))
        qty = float(lv.get("qty") or 0)
        if px is not None and (1 - px) <= price + 0.0101:
            tot += (1 - px) * qty
    return tot
