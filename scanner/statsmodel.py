"""Independent stats model built from free nflverse data (no sportsbook input).

- Team strength: ridge-regressed offense/defense points ratings from this season's results
  (last season counted at reduced weight early in the year).
- Player props: recent per-game production adjusted for the opponent's defense, with injury status.
- Key-number distributions learned from historical results vs. closing lines.
"""
import csv
import io
import math
import re
from collections import defaultdict

from .dists import learn_keydist, norm_cdf, poisson_sf
from .teams import norm_abbr

NFLVERSE = "https://github.com/nflverse/nflverse-data/releases/download"
GAMES_URL = f"{NFLVERSE}/schedules/games.csv"


def stats_url(season):
    return f"{NFLVERSE}/stats_player/stats_player_week_{season}.csv"


def injuries_url(season):
    return f"{NFLVERSE}/injuries/injuries_{season}.csv"


def read_csv_text(text):
    return list(csv.DictReader(io.StringIO(text)))


def norm_name(s):
    s = (s or "").lower().replace("'", "").replace("’", "").replace(".", "")
    s = re.sub(r"[^a-z ]", " ", s)
    parts = [p for p in s.split() if p not in ("jr", "sr", "ii", "iii", "iv", "v")]
    return " ".join(parts)


# ----------------------------------------------------------------------------- distributions

def build_distributions(games):
    """Learn margin / total / team-score key-number distributions from historical games with lines."""
    marg, tot, team = [], [], []
    for g in games:
        try:
            season = int(g["season"])
            if season < 2002 or g["result"] == "" or g["spread_line"] == "" or g["total_line"] == "":
                continue
            res = int(float(g["result"]))  # home - away
            spread = float(g["spread_line"])  # nflverse: positive = home favoured by that many
            total_line = float(g["total_line"])
            tot_pts = int(float(g["total"]))
            hs, as_ = int(float(g["home_score"])), int(float(g["away_score"]))
        except (ValueError, KeyError):
            continue
        marg.append((res, spread))
        tot.append((tot_pts, total_line))
        team.append((hs, (total_line + spread) / 2))
        team.append((as_, (total_line - spread) / 2))
    return {
        "margin": learn_keydist(marg, -70, 70),
        "total": learn_keydist(tot, 0, 130),
        "team": learn_keydist(team, 0, 80),
    }


# ----------------------------------------------------------------------------- team ratings

def team_ratings(games, season, cur_week):
    """Fit points = base + off[team] - def[opp] + hfa*home. Ridge-shrunk toward 0. Returns dict."""
    rows = []
    for g in games:
        try:
            s = int(g["season"])
        except ValueError:
            continue
        if g["result"] == "" or g["game_type"] not in ("REG", "WC", "DIV", "CON", "SB"):
            continue
        if s == season:
            w = 1.0
        elif s == season - 1:
            # last season fades out as this season accumulates
            w = max(0.0, 0.35 - 0.04 * cur_week)
        else:
            continue
        if w <= 0:
            continue
        h, a = norm_abbr(g["home_team"]), norm_abbr(g["away_team"])
        neutral = (g.get("location") or "").lower() == "neutral"
        hs, as_ = float(g["home_score"]), float(g["away_score"])
        rows.append((h, a, hs, 0 if neutral else 1, w))
        rows.append((a, h, as_, 0, w))
    teams = sorted({r[0] for r in rows} | {r[1] for r in rows})
    off = {t: 0.0 for t in teams}
    de = {t: 0.0 for t in teams}
    base, hfa = 22.0, 1.5
    lam = 4.0  # ridge strength in "games"
    for _ in range(60):
        # base & hfa
        sw = sum(r[4] for r in rows)
        base = sum(r[4] * (r[2] - off[r[0]] + de[r[1]] - hfa * r[3]) for r in rows) / sw
        hw = sum(r[4] for r in rows if r[3])
        if hw:
            # shrink toward the modern-NFL home edge (~1.5 pts per team-score, i.e. ~1.5 on the margin)
            hfa = (sum(r[4] * (r[2] - base - off[r[0]] + de[r[1]]) for r in rows if r[3]) + 40 * 1.5) / (hw + 40)
        for t in teams:
            num = sum(r[4] * (r[2] - base - hfa * r[3] + de[r[1]]) for r in rows if r[0] == t)
            den = sum(r[4] for r in rows if r[0] == t) + lam
            off[t] = num / den
        for t in teams:
            num = sum(r[4] * (base + off[r[0]] + hfa * r[3] - r[2]) for r in rows if r[1] == t)
            den = sum(r[4] for r in rows if r[1] == t) + lam
            de[t] = num / den
    return {"off": off, "def": de, "base": base, "hfa": hfa, "n_games": len(rows) // 2}


def model_game(ratings, home, away, neutral=False):
    """Expected points for home and away team."""
    o, d = ratings["off"], ratings["def"]
    hfa = 0 if neutral else ratings["hfa"]
    hp = ratings["base"] + o.get(home, 0) - d.get(away, 0) + hfa
    ap = ratings["base"] + o.get(away, 0) - d.get(home, 0)
    return max(hp, 3.0), max(ap, 3.0)


# ----------------------------------------------------------------------------- players

STAT_COLS = {
    "pyd": "passing_yards",
    "ryd": "rushing_yards",
    "recyd": "receiving_yards",
    "rec": "receptions",
    "ptd": "passing_tds",
    "td": None,  # rushing_tds + receiving_tds
}
OPP_COLS = {
    "pyd": "passing_yards",
    "ryd": "rushing_yards",
    "recyd": "receiving_yards",
    "rec": "receptions",
    "ptd": "passing_tds",
    "td": None,
}


class PlayerModel:
    def __init__(self, stats_rows, injury_rows, cur_week):
        self.cur_week = cur_week
        self.games = defaultdict(list)  # norm name -> list of row dicts (sorted by week)
        self.team_of = {}
        allowed = defaultdict(lambda: defaultdict(float))  # opp team -> stat -> total
        allowed_games = defaultdict(set)
        for r in stats_rows:
            if r.get("season_type") not in ("REG", "POST"):
                continue
            try:
                wk = int(r["week"])
            except ValueError:
                continue
            nm = norm_name(r.get("player_display_name") or r.get("player_name"))
            self.games[nm].append(r)
            self.team_of[nm] = norm_abbr(r.get("team"))
            opp = norm_abbr(r.get("opponent_team"))
            allowed_games[opp].add(wk)
            for c in ("passing_yards", "rushing_yards", "receiving_yards", "receptions", "passing_tds",
                      "rushing_tds", "receiving_tds"):
                try:
                    allowed[opp][c] += float(r.get(c) or 0)
                except ValueError:
                    pass
        for nm in self.games:
            self.games[nm].sort(key=lambda r: int(r["week"]))
        # opponent factors relative to league average
        self.opp_factor = {}
        league = defaultdict(float)
        n_team_games = sum(len(v) for v in allowed_games.values()) or 1
        for opp, d in allowed.items():
            for c, v in d.items():
                league[c] += v
        for opp, d in allowed.items():
            g = len(allowed_games[opp]) or 1
            f = {}
            for c in league:
                avg = league[c] / n_team_games
                f[c] = (d.get(c, 0.0) / g) / avg if avg > 0 else 1.0
            f["tds"] = ((d.get("rushing_tds", 0) + d.get("receiving_tds", 0)) / g) / max(
                1e-6, (league["rushing_tds"] + league["receiving_tds"]) / n_team_games)
            self.opp_factor[opp] = f
        # injuries: latest report per player
        self.injury = {}
        latest = {}
        for r in injury_rows:
            try:
                wk = int(r["week"])
            except ValueError:
                continue
            nm = norm_name(r.get("full_name"))
            if wk >= latest.get(nm, -1):
                latest[nm] = wk
                self.injury[nm] = (wk, (r.get("report_status") or "").strip(),
                                   (r.get("report_primary_injury") or "").strip())

    def injury_status(self, name):
        nm = norm_name(name)
        rec = self.injury.get(nm)
        if not rec:
            return ""
        wk, status, inj = rec
        if wk < self.cur_week - 1:
            return ""
        return status

    def prob(self, kind, name, threshold, opp):
        """Model P(stat >= threshold). Returns (prob, info) or (None, reason)."""
        nm = norm_name(name)
        rows = self.games.get(nm)
        if not rows or len(rows) < 3:
            return None, "not enough games"
        rows = rows[-8:]
        if kind == "td":
            vals = [float(r.get("rushing_tds") or 0) + float(r.get("receiving_tds") or 0) for r in rows]
        else:
            vals = [float(r.get(STAT_COLS[kind]) or 0) for r in rows]
        # recency weights
        ws = [0.6 + 0.4 * (i + 1) / len(vals) for i in range(len(vals))]
        mean = sum(v * w for v, w in zip(vals, ws)) / sum(ws)
        var = sum(w * (v - mean) ** 2 for v, w in zip(vals, ws)) / sum(ws)
        f = 1.0
        of = self.opp_factor.get(norm_abbr(opp))
        if of:
            raw = of.get("tds") if kind == "td" else of.get(OPP_COLS[kind], 1.0)
            f = 1 + 0.5 * (raw - 1)  # half-weight the opponent adjustment
            f = min(max(f, 0.85), 1.15)
        mu = mean * f
        info = {"mean": round(mu, 1), "games": len(vals)}
        if kind in ("pyd", "ryd", "recyd"):
            floor = {"pyd": 55.0, "ryd": 18.0, "recyd": 18.0}[kind]
            sd = max(math.sqrt(var), floor, 0.35 * mu)
            p = 1 - norm_cdf((threshold - 0.5 - mu) / sd)
        elif kind in ("rec", "ptd"):
            # shrink small-sample count means toward a gentle prior
            lam = max(mu, 0.05)
            p = poisson_sf(int(threshold), lam)
        else:  # td
            lam = max(0.02, 0.65 * mu + 0.35 * 0.30)  # TDs are noisy: lean on a league-ish prior
            p = poisson_sf(int(threshold), lam)
        return min(max(p, 0.005), 0.995), info


def model_spread_prob(dists, mu_margin_home, away_line):
    """P(away covers away_line) where margin = home - away. away covers if margin < away_line."""
    pm = dists["margin"].pmf(mu_margin_home)
    return sum(p for k, p in pm.items() if k < away_line)


def model_total_prob(dists, mu_total, line):
    return dists["total"].p_greater(mu_total, line)


def model_team_total_prob(dists, mu_team, line):
    return dists["team"].p_greater(mu_team, line)


def model_ml_prob_away(dists, mu_margin_home):
    pm = dists["margin"].pmf(mu_margin_home)
    away = sum(p for k, p in pm.items() if k < 0)
    tie = pm.get(0, 0)
    return away + tie / 2  # ties are ~0.3%; split them
