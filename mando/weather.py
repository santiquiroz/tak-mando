from __future__ import annotations

import json
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone


@dataclass
class Hour:
    time: datetime
    temp_c: float | None
    rain_prob: int | None
    rain_mm: float | None
    cloud: int | None
    visibility_m: float | None
    gust_kmh: float | None


def forecast_url(lat: float, lon: float) -> str:
    return (
        "https://api.open-meteo.com/v1/forecast"
        f"?latitude={lat:.4f}&longitude={lon:.4f}"
        "&hourly=temperature_2m,precipitation_probability,precipitation,"
        "cloud_cover,visibility,wind_gusts_10m"
        "&timezone=UTC&forecast_days=3"
    )


def _parse_time(value: object) -> datetime | None:
    if not isinstance(value, str):
        return None
    text = value.strip()
    if text.endswith(("Z", "z")):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _as_float(value: object) -> float | None:
    if value is None:
        return None
    try:
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


def _as_int(value: object) -> int | None:
    if value is None:
        return None
    try:
        return int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


def _column(hourly: dict, key: str, index: int) -> object:
    values = hourly.get(key)
    if not isinstance(values, (list, tuple)) or index >= len(values):
        return None
    return values[index]


def parse_forecast(data: dict) -> list[Hour]:
    hourly = data.get("hourly") if isinstance(data, dict) else None
    if not isinstance(hourly, dict):
        return []
    times = hourly.get("time")
    if not isinstance(times, (list, tuple)):
        return []
    hours = []
    for index, raw in enumerate(times):
        when = _parse_time(raw)
        if when is None:
            continue
        hours.append(
            Hour(
                time=when,
                temp_c=_as_float(_column(hourly, "temperature_2m", index)),
                rain_prob=_as_int(_column(hourly, "precipitation_probability", index)),
                rain_mm=_as_float(_column(hourly, "precipitation", index)),
                cloud=_as_int(_column(hourly, "cloud_cover", index)),
                visibility_m=_as_float(_column(hourly, "visibility", index)),
                gust_kmh=_as_float(_column(hourly, "wind_gusts_10m", index)),
            )
        )
    return hours


def next_hours(hours: list[Hour], now_utc: datetime, n: int) -> list[Hour]:
    start = now_utc.replace(minute=0, second=0, microsecond=0)
    return [h for h in hours if h.time >= start][:n]


def fetch_forecast(lat: float, lon: float, timeout: float = 10) -> list[Hour]:
    req = urllib.request.Request(forecast_url(lat, lon), headers={"User-Agent": "tak-mando"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return parse_forecast(json.loads(resp.read()))


class ForecastCache:
    def __init__(self, fetcher: Callable[[], list[Hour]], ttl_s: float = 1800) -> None:
        self._fetcher = fetcher
        self._ttl_s = ttl_s
        self._data: list[Hour] | None = None
        self._failed_at: datetime | None = None
        self.fetched_at: datetime | None = None
        self.last_error: str | None = None

    def _in_backoff(self, now_utc: datetime) -> bool:
        return (
            self._failed_at is not None
            and (now_utc - self._failed_at).total_seconds() < self._ttl_s / 6.0
        )

    def get(self, now_utc: datetime) -> list[Hour]:
        if self._data is not None and self.fetched_at is not None:
            if (now_utc - self.fetched_at).total_seconds() < self._ttl_s:
                return self._data
            # A failed refetch must not hammer the API on every call.
            if self._in_backoff(now_utc):
                return self._data
        elif self._in_backoff(now_utc):
            return []
        try:
            data = self._fetcher()
        except Exception as exc:
            self._failed_at = now_utc
            self.last_error = str(exc)
            return self._data if self._data is not None else []
        self._data = data
        self.fetched_at = now_utc
        self._failed_at = None
        self.last_error = None
        return data
