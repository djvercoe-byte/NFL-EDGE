"""Probability tools.

NFL scores cluster on key numbers (3, 7, 10 for margins; 41/44/47 for totals, 17/20/24 for team scores).
A plain bell curve misprices lines near those numbers, which creates fake "edges". So we learn
key-number weights from 25+ years of real results vs. closing lines (nflverse games.csv) and use a
discrete distribution:  P(k) ∝ Normal(k; mu, sigma) * w(k).
"""
import math
from functools import lru_cache

SQRT2 = math.sqrt(2.0)


def norm_cdf(x):
    return 0.5 * (1.0 + math.erf(x / SQRT2))


def norm_pdf(x):
    return math.exp(-0.5 * x * x) / math.sqrt(2 * math.pi)


def norm_ppf(p):
    p = min(max(p, 1e-9), 1 - 1e-9)
    lo, hi = -10.0, 10.0
    for _ in range(80):
        mid = (lo + hi) / 2
        if norm_cdf(mid) < p:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2


def poisson_sf(k, lam):
    """P(X >= k) for Poisson(lam)."""
    if k <= 0:
        return 1.0
    term = math.exp(-lam)
    cdf = term
    for i in range(1, k):
        term *= lam / i
        cdf += term
    return max(0.0, 1.0 - cdf)


def poisson_lambda_for(k, p_ge):
    """Find lam such that P(X >= k) = p_ge."""
    p_ge = min(max(p_ge, 1e-4), 1 - 1e-4)
    lo, hi = 1e-4, 60.0
    for _ in range(80):
        mid = (lo + hi) / 2
        if poisson_sf(k, mid) < p_ge:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2


class KeyDist:
    """Discrete integer distribution with learned key-number weights."""

    def __init__(self, sigma, weights, lo, hi):
        self.sigma = sigma
        self.w = weights  # dict int -> weight
        self.lo, self.hi = lo, hi

    def pmf(self, mu):
        return _pmf_cached(self, round(mu, 3))

    def p_greater(self, mu, x):
        """P(X > x) for half-point x (no push)."""
        return sum(p for k, p in self.pmf(mu).items() if k > x)

    def p_cover_nopush(self, mu, x):
        """P(X > x | X != x) — how sportsbooks settle whole-number lines (pushes refunded)."""
        pm = self.pmf(mu)
        gt = sum(p for k, p in pm.items() if k > x)
        eq = pm.get(int(x), 0.0) if float(x).is_integer() else 0.0
        return gt / max(1e-9, 1 - eq)

    def solve_mu(self, x, p_over, lo=-40.0, hi=110.0):
        """Find mu such that P(X > x | no push) = p_over."""
        for _ in range(60):
            mid = (lo + hi) / 2
            if self.p_cover_nopush(mid, x) < p_over:
                lo = mid
            else:
                hi = mid
        return (lo + hi) / 2

    def __hash__(self):
        return id(self)


@lru_cache(maxsize=20000)
def _pmf_cached(dist, mu):
    out = {}
    tot = 0.0
    for k in range(dist.lo, dist.hi + 1):
        v = norm_pdf((k - mu) / dist.sigma) * dist.w.get(k, dist.w.get("default", 1.0))
        out[k] = v
        tot += v
    return {k: v / tot for k, v in out.items()}


def learn_keydist(samples, lo, hi, smooth=1.0):
    """samples: list of (actual_int, expected_mu). Returns KeyDist with weights = observed / normal-expected."""
    resid = [a - m for a, m in samples]
    n = len(resid)
    mean = sum(resid) / n
    sigma = math.sqrt(sum((r - mean) ** 2 for r in resid) / n)
    obs = {}
    for a, _ in samples:
        obs[a] = obs.get(a, 0) + 1
    exp = {k: 0.0 for k in range(lo, hi + 1)}
    for _, m in samples:
        # expected mass of a normal at each integer (ignore far tails for speed)
        c = int(round(m))
        for k in range(max(lo, c - 60), min(hi, c + 60) + 1):
            exp[k] += norm_pdf((k - m) / sigma) / sigma
    w = {}
    for k in range(lo, hi + 1):
        w[k] = (obs.get(k, 0) + smooth) / (exp[k] + smooth)
    w["default"] = 1.0
    return KeyDist(sigma, w, lo, hi)


def devig2(dec_a, dec_b):
    """Two-way decimal odds -> fair probability of side a (proportional method)."""
    qa, qb = 1.0 / dec_a, 1.0 / dec_b
    return qa / (qa + qb)


def median(xs):
    xs = sorted(x for x in xs if x is not None)
    if not xs:
        return None
    n = len(xs)
    return xs[n // 2] if n % 2 else 0.5 * (xs[n // 2 - 1] + xs[n // 2])
