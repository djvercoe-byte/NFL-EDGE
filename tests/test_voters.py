"""Offline test for the extra voters (line movement, news/injury, weather) and their learning.
Needs no network or data files. Run: python -m tests.test_voters"""
import random
from datetime import datetime, timedelta, timezone

from scanner import voters
from scanner.dists import learn_keydist

rng = random.Random(3)
marg = [(round(rng.gauss(s, 13.5)), s) for s in (rng.uniform(-10, 10) for _ in range(4000))]
tot = [(max(3, round(rng.gauss(t, 13))), t) for t in (rng.uniform(37, 53) for _ in range(4000))]
team = [(max(0, round(rng.gauss(t, 9))), t) for t in (rng.uniform(17, 28) for _ in range(4000))]
dists = {"margin": learn_keydist(marg, -70, 70), "total": learn_keydist(tot, 0, 130), "team": learn_keydist(team, 0, 80)}
now = datetime(2026, 11, 8, 20, tzinfo=timezone.utc)


def cand(type_, side, mkt, player=None, kind=None):
    return {"type": type_, "side": side, "mkt": mkt, "away": "BAL", "home": "BUF", "game_slug": "nfl-bal-buf",
            "player": player, "kind": kind, "p_c": 0.5, "p_m": 0.5, "cost": 0.48, "edge_c": 0.02, "edge_m": 0.02}


class Players:
    team_of = {voters.norm_name(n): t for n, t in (("Josh Allen", "BUF"), ("Khalil Shakir", "BUF"),
                                                   ("Dawson Knox", "BUF"), ("Derrick Henry", "BAL"))}


st = voters.load_state("/nonexistent/voters.json")
# Thursday: BUF -3, total 47.  Sunday: BUF -6, total 44.5 (money on BUF and the under)
voters.record_lines(st, {("BAL", "BUF"): {"p_away_ml": 0.40, "mu_margin": 3.0, "mu_total": 47.0,
                                          "commence": "2026-11-09T01:20:00Z"}}, now - timedelta(days=3))
voters.record_lines(st, {("BAL", "BUF"): {"p_away_ml": 0.32, "mu_margin": 6.0, "mu_total": 44.5,
                                          "commence": "2026-11-09T01:20:00Z"}}, now)
assert voters.vote_line_move(cand("ML", "long", {"long_team": "BAL"}), st, dists) < 0, "BAL lost support"
assert voters.vote_line_move(cand("TOTAL", "short", {"line": 45.5}), st, dists) > 0, "under gained support"
print("line move: BAL ML", round(voters.vote_line_move(cand("ML", "long", {"long_team": "BAL"}), st, dists), 3),
      "| under 45.5", round(voters.vote_line_move(cand("TOTAL", "short", {"line": 45.5}), st, dists), 3))

# ESPN injuries + a late headline
espn = {"injuries": [{"displayName": "Buffalo Bills", "injuries": [
    {"athlete": {"displayName": "Khalil Shakir", "position": {"abbreviation": "WR"}}, "status": "Out"},
    {"athlete": {"displayName": "Dawson Knox", "position": {"abbreviation": "TE"}}, "status": "Questionable"}]},
    {"displayName": "Baltimore Ravens", "injuries": [
        {"athlete": {"displayName": "Lamar Jackson", "position": {"abbreviation": "QB"}}, "status": "Questionable"}]}]}
inj = voters.parse_espn_injuries(espn)
news = {"articles": [{"published": (now - timedelta(hours=3)).isoformat(),
                      "headline": "Ravens' Lamar Jackson ruled out for Sunday night", "description": ""},
                     {"published": (now - timedelta(hours=5)).isoformat(),
                      "headline": "Dawson Knox (ankle) logs full practice, expected to play", "description": ""}]}
nn = voters.norm_name
voters.apply_espn_news(inj, news, now, {nn("Lamar Jackson"): "Lamar Jackson", nn("Dawson Knox"): "Dawson Knox"})
assert inj[nn("Lamar Jackson")]["status"] == "Out" and inj[nn("Dawson Knox")]["status"] == "", inj
team_qb = {"BAL": "Lamar Jackson", "BUF": "Josh Allen"}
v_bal_ml = voters.vote_injury(cand("ML", "long", {"long_team": "BAL"}), inj, team_qb, Players)
v_knox = voters.vote_injury(cand("PROP", "long", {}, "Dawson Knox", "rec"), inj, team_qb, Players)
v_shakir = voters.vote_injury(cand("PROP", "long", {}, "Khalil Shakir", "recyd"), inj, team_qb, Players)
assert v_bal_ml < 0 and v_knox > 0 and v_shakir is None, (v_bal_ml, v_knox, v_shakir)
print("injury: BAL ML (QB out)", v_bal_ml, "| Knox rec over (WR1 out)", v_knox, "| Shakir prop dropped")

# Weather: 24 mph wind and rain at kickoff
wx = (38.0, 24.0, 3.0)
cons = {"mu_total": 44.5, "mu_margin": 6.0}
over = voters.vote_weather(cand("TOTAL", "long", {"line": 44.5}), wx, dists, cons)
pyd = voters.vote_weather(cand("PROP", "long", {}, "Josh Allen", "pyd"), wx, dists, cons)
assert over < 0 and pyd < 0
print("weather: over 44.5", round(over, 3), "| Allen pass yds over", round(pyd, 3))

# apply + learn
cands = [cand("ML", "long", {"long_team": "BAL"}), cand("PROP", "long", {}, "Khalil Shakir", "recyd"),
         cand("TOTAL", "short", {"line": 44.5})]
out = voters.apply(cands, st, dists, {("BAL", "BUF"): cons}, inj, team_qb, Players, {"nfl-bal-buf": wx})
assert len(out) == 2
for c in out:
    print(f"  {c['type']:<6} {c['side']:<5} p_c 0.500 -> {c['p_c']:.3f}  votes {c['voters']}")
    print("   ", voters.describe(c))
led = {"bets": [{"kind": "single", "result": "loss", "voters": out[0]["voters"]},
                {"kind": "single", "result": "win", "voters": out[1]["voters"]}]}
w0 = dict(st["weights"])
n = voters.learn(st, led)
assert n > 0 and voters.learn(st, led) == 0, "each bet scored once"
print("learned from", n, "voter calls; weights", {k: (round(w0[k], 2), round(st["weights"][k], 2)) for k in w0})
print("record", st["record"])
print("OK")
