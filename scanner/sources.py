"""Network fetchers: Polymarket US public gateway, The Odds API, nflverse, Open-Meteo, ESPN."""
import gzip
import json
import time
import urllib.error
import urllib.parse
import urllib.request

UA = "nfl-edge-scanner/1.0 (personal research tool)"
POLY = "https://gateway.polymarket.us/v1/"
ODDS = "https://api.the-odds-api.com/v4/sports/americanfootball_nfl/"


class FetchError(Exception):
    pass


def http_get(url, params=None, retries=3, timeout=60, want_headers=False):
    if params:
        url = url + ("&" if "?" in url else "?") + urllib.parse.urlencode(params)
    last = None
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept-Encoding": "gzip",
                                                       "Accept": "application/json,text/csv,*/*"})
            with urllib.request.urlopen(req, timeout=timeout) as r:
                raw = r.read()
                if r.headers.get("Content-Encoding") == "gzip":
                    raw = gzip.decompress(raw)
                if want_headers:
                    return raw, dict(r.headers)
                return raw
        except urllib.error.HTTPError as e:
            last = e
            if e.code in (400, 401, 403, 404, 422):
                break
        except Exception as e:  # network hiccup
            last = e
        time.sleep(2 * (attempt + 1))
    raise FetchError(f"GET failed {url.split('apiKey=')[0]}: {last}")


def get_json(url, params=None, **kw):
    return json.loads(http_get(url, params, **kw))


# ----------------------------------------------------------------------------- Polymarket US

def poly_nfl_events():
    """All open events in the NFL series (lightweight list: games carry only their moneyline)."""
    out, offset = [], 0
    for _ in range(20):
        j = get_json(POLY + "events", {"seriesId": 2, "closed": "false", "limit": 100, "offset": offset})
        evs = j.get("events", [])
        out.extend(evs)
        if len(evs) < 100:
            break
        offset += 100
    return out


def poly_event(slug):
    j = get_json(POLY + f"events/slug/{slug}", timeout=120)
    return j.get("event", j)


def poly_book(market_slug):
    j = get_json(POLY + f"markets/{market_slug}/book", retries=2, timeout=30)
    return j.get("marketData", j)


# ----------------------------------------------------------------------------- The Odds API

class OddsClient:
    def __init__(self, key):
        self.key = key
        self.remaining = None
        self.used = None
        self.calls = 0

    def _get(self, path, params):
        p = dict(params)
        p["apiKey"] = self.key
        raw, headers = http_get(ODDS + path, p, want_headers=True, timeout=60)
        self.calls += 1
        h = {k.lower(): v for k, v in headers.items()}
        if "x-requests-remaining" in h:
            try:
                self.remaining = int(float(h["x-requests-remaining"]))
                self.used = int(float(h.get("x-requests-used", 0)))
            except ValueError:
                pass
        return json.loads(raw)

    def mainlines(self, t_from, t_to):
        return self._get("odds", {"regions": "us", "markets": "h2h,spreads,totals", "oddsFormat": "decimal",
                                  "commenceTimeFrom": t_from, "commenceTimeTo": t_to})

    def event_props(self, event_id, markets):
        return self._get(f"events/{event_id}/odds", {"regions": "us", "markets": ",".join(markets),
                                                      "oddsFormat": "decimal"})


# ----------------------------------------------------------------------------- weather

def weather_at(lat, lon, when_utc):
    """Forecast at kickoff hour: (temp F, wind mph, precip mm). None if unavailable."""
    day = when_utc.strftime("%Y-%m-%d")
    try:
        j = get_json("https://api.open-meteo.com/v1/forecast", {
            "latitude": lat, "longitude": lon, "hourly": "temperature_2m,precipitation,wind_speed_10m",
            "wind_speed_unit": "mph", "temperature_unit": "fahrenheit", "timezone": "UTC",
            "start_date": day, "end_date": day}, retries=2, timeout=30)
        hrs = j["hourly"]["time"]
        target = when_utc.strftime("%Y-%m-%dT%H:00")
        i = hrs.index(target) if target in hrs else min(len(hrs) - 1, when_utc.hour)
        i2 = min(i + 2, len(hrs) - 1)  # look across the first ~3 hours of the game
        wind = max(j["hourly"]["wind_speed_10m"][i:i2 + 1])
        rain = sum(j["hourly"]["precipitation"][i:i2 + 1])
        temp = j["hourly"]["temperature_2m"][i]
        return temp, wind, rain
    except Exception:
        return None


# ----------------------------------------------------------------------------- ESPN (public, no key)

ESPN = "https://site.api.espn.com/apis/site/v2/sports/football/nfl/"


def espn_injuries():
    return get_json(ESPN + "injuries", retries=2, timeout=30)


def espn_news():
    return get_json(ESPN + "news", {"limit": 100}, retries=2, timeout=30)
