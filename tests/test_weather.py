from datetime import datetime, timedelta, timezone

import mando.weather as weather_mod
from mando.weather import ForecastCache, fetch_forecast, forecast_url, next_hours, parse_forecast

UTC = timezone.utc


def _fixture():
    return {
        "hourly": {
            "time": ["2026-10-10T21:00", "2026-10-10T22:00", "not-a-time"],
            "temperature_2m": [18.5, 17.0, 16.0],
            "precipitation_probability": [10, 80, 50],
            "precipitation": [0.0, 2.5, 0.0],
            "cloud_cover": [20, 90, 10],
            "visibility": [24000.0, None, 1000.0],
            "wind_gusts_10m": [12.6, 30.2, 5.0],
        }
    }


def test_parse_forecast_values():
    hours = parse_forecast(_fixture())
    assert len(hours) == 2
    first, second = hours
    assert first.time == datetime(2026, 10, 10, 21, 0, tzinfo=UTC)
    assert first.temp_c == 18.5
    assert first.rain_prob == 10
    assert first.rain_mm == 0.0
    assert first.cloud == 20
    assert first.visibility_m == 24000.0
    assert first.gust_kmh == 12.6
    assert second.time == datetime(2026, 10, 10, 22, 0, tzinfo=UTC)
    assert second.visibility_m is None
    assert second.rain_mm == 2.5
    assert second.rain_prob == 80


def test_parse_forecast_missing_arrays_give_none():
    hours = parse_forecast({"hourly": {"time": ["2026-10-10T21:00"]}})
    assert len(hours) == 1
    only = hours[0]
    assert only.time == datetime(2026, 10, 10, 21, 0, tzinfo=UTC)
    assert only.temp_c is None
    assert only.rain_prob is None
    assert only.rain_mm is None
    assert only.cloud is None
    assert only.visibility_m is None
    assert only.gust_kmh is None


def test_next_hours_floors_now_to_hour():
    hours = parse_forecast(_fixture())
    now = datetime(2026, 10, 10, 21, 37, 12, tzinfo=UTC)
    got = next_hours(hours, now, 2)
    assert [h.time.hour for h in got] == [21, 22]
    assert next_hours(hours, now, 1) == hours[:1]


def test_forecast_url_format():
    url = forecast_url(5.1606, -75.4918)
    assert "latitude=5.1606" in url
    assert "longitude=-75.4918" in url
    assert "timezone=UTC" in url
    assert "latitude=5.1600" in forecast_url(5.16, 0)


def test_fetch_forecast_sends_user_agent(monkeypatch):
    seen = {}

    class FakeResp:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def read(self):
            return b'{"hourly": {"time": ["2026-10-10T21:00"], "temperature_2m": [18.5]}}'

    def fake_urlopen(req, timeout=10):
        seen["agent"] = req.get_header("User-agent")
        seen["timeout"] = timeout
        seen["url"] = req.full_url
        return FakeResp()

    monkeypatch.setattr(weather_mod.urllib.request, "urlopen", fake_urlopen)
    hours = fetch_forecast(5.1606, -75.4918)
    assert seen["agent"] == "tak-mando"
    assert seen["timeout"] == 10
    assert "latitude=5.1606" in seen["url"]
    assert len(hours) == 1
    assert hours[0].temp_c == 18.5


def test_cache_refetches_after_ttl():
    calls = []

    def fetcher():
        calls.append(1)
        return ["d"]

    cache = ForecastCache(fetcher, ttl_s=1800)
    t0 = datetime(2026, 10, 10, 12, 0, tzinfo=UTC)
    assert cache.get(t0) == ["d"]
    assert len(calls) == 1
    assert cache.fetched_at == t0
    assert cache.get(t0 + timedelta(seconds=100)) == ["d"]
    assert len(calls) == 1
    assert cache.get(t0 + timedelta(seconds=1801)) == ["d"]
    assert len(calls) == 2


def test_cache_failure_keeps_old_data_with_backoff():
    state = {"fail": False, "calls": 0}

    def fetcher():
        state["calls"] += 1
        if state["fail"]:
            raise RuntimeError("boom")
        return ["ok"]

    cache = ForecastCache(fetcher, ttl_s=600)
    t0 = datetime(2026, 10, 10, 12, 0, tzinfo=UTC)
    assert cache.get(t0) == ["ok"]
    state["fail"] = True
    t1 = t0 + timedelta(seconds=601)
    assert cache.get(t1) == ["ok"]
    assert cache.last_error == "boom"
    assert cache.fetched_at == t0
    frozen = state["calls"]
    assert cache.get(t1 + timedelta(seconds=10)) == ["ok"]
    assert state["calls"] == frozen
    assert cache.get(t1 + timedelta(seconds=101)) == ["ok"]
    assert state["calls"] == frozen + 1


def test_cache_failure_without_data_returns_empty():
    def fetcher():
        raise RuntimeError("down")

    cache = ForecastCache(fetcher, ttl_s=600)
    t0 = datetime(2026, 10, 10, 12, 0, tzinfo=UTC)
    assert cache.get(t0) == []
    assert cache.last_error == "down"
    assert cache.fetched_at is None
