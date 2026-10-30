"""NFL team reference data: names, abbreviations used by each data source, stadium coordinates."""

# nflverse abbr -> (full name used by The Odds API, Polymarket US abbr, lat, lon, roof)
# roof: "dome" = always indoors, "retract" = retractable (treated as indoors for weather), "open" = outdoors
TEAMS = {
    "ARI": ("Arizona Cardinals", "ari", 33.5276, -112.2626, "retract"),
    "ATL": ("Atlanta Falcons", "atl", 33.7554, -84.4008, "retract"),
    "BAL": ("Baltimore Ravens", "bal", 39.2780, -76.6227, "open"),
    "BUF": ("Buffalo Bills", "buf", 42.7738, -78.7870, "open"),
    "CAR": ("Carolina Panthers", "car", 35.2258, -80.8528, "open"),
    "CHI": ("Chicago Bears", "chi", 41.8623, -87.6167, "open"),
    "CIN": ("Cincinnati Bengals", "cin", 39.0954, -84.5160, "open"),
    "CLE": ("Cleveland Browns", "cle", 41.5061, -81.6995, "open"),
    "DAL": ("Dallas Cowboys", "dal", 32.7473, -97.0945, "retract"),
    "DEN": ("Denver Broncos", "den", 39.7439, -105.0201, "open"),
    "DET": ("Detroit Lions", "det", 42.3400, -83.0456, "dome"),
    "GB": ("Green Bay Packers", "gb", 44.5013, -88.0622, "open"),
    "HOU": ("Houston Texans", "hou", 29.6847, -95.4107, "retract"),
    "IND": ("Indianapolis Colts", "ind", 39.7601, -86.1639, "retract"),
    "JAX": ("Jacksonville Jaguars", "jax", 30.3239, -81.6373, "open"),
    "KC": ("Kansas City Chiefs", "kc", 39.0489, -94.4839, "open"),
    "LV": ("Las Vegas Raiders", "lv", 36.0909, -115.1833, "dome"),
    "LAC": ("Los Angeles Chargers", "lac", 33.9535, -118.3392, "dome"),
    "LA": ("Los Angeles Rams", "lar", 33.9535, -118.3392, "dome"),
    "MIA": ("Miami Dolphins", "mia", 25.9580, -80.2389, "open"),
    "MIN": ("Minnesota Vikings", "min", 44.9736, -93.2575, "dome"),
    "NE": ("New England Patriots", "ne", 42.0909, -71.2643, "open"),
    "NO": ("New Orleans Saints", "no", 29.9511, -90.0812, "dome"),
    "NYG": ("New York Giants", "nyg", 40.8135, -74.0745, "open"),
    "NYJ": ("New York Jets", "nyj", 40.8135, -74.0745, "open"),
    "PHI": ("Philadelphia Eagles", "phi", 39.9008, -75.1675, "open"),
    "PIT": ("Pittsburgh Steelers", "pit", 40.4468, -80.0158, "open"),
    "SF": ("San Francisco 49ers", "sf", 37.4030, -121.9700, "open"),
    "SEA": ("Seattle Seahawks", "sea", 47.5952, -122.3316, "open"),
    "TB": ("Tampa Bay Buccaneers", "tb", 27.9759, -82.5033, "open"),
    "TEN": ("Tennessee Titans", "ten", 36.1665, -86.7713, "open"),
    "WAS": ("Washington Commanders", "was", 38.9077, -76.8645, "open"),
}

FULLNAME_TO_ABBR = {v[0]: k for k, v in TEAMS.items()}
POLY_TO_ABBR = {v[1]: k for k, v in TEAMS.items()}
# A few aliases seen across feeds
POLY_TO_ABBR.update({"la": "LA", "wsh": "WAS", "jac": "JAX", "lvr": "LV", "gnb": "GB", "kan": "KC", "nwe": "NE", "nor": "NO", "sfo": "SF", "tam": "TB"})
NFLVERSE_ALIASES = {"LAR": "LA", "OAK": "LV", "SD": "LAC", "STL": "LA", "WSH": "WAS", "JAC": "JAX"}


def norm_abbr(a: str) -> str:
    a = (a or "").upper()
    return NFLVERSE_ALIASES.get(a, a)


def short(abbr: str) -> str:
    """Display abbreviation (LA -> LAR so it reads clearly)."""
    return "LAR" if abbr == "LA" else abbr
