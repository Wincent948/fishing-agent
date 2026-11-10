"""天气、潮汐、最佳出钓时段。

评测模式（FIXTURE_MODE=1）读 eval/fixtures/conditions.json；否则调用 Open-Meteo（免 key）。
best_window 是「计算」类工具：固定函数，不开放任意代码执行。
"""
from __future__ import annotations

import datetime as dt
import json
from functools import lru_cache

from app import config
from app.tools.base import ToolResult, ctx, tool_wrapper

NO_GO_WIND_KMH, NO_GO_PRECIP_MM = 30, 10
TIDE_HALF_WINDOW_MIN, INLAND_WINDOW_MIN = 60, 120


@lru_cache(maxsize=1)
def _fx() -> dict:
    return json.loads(config.FIXTURES.read_text(encoding="utf-8"))


def _validate(location: str, date: str) -> ToolResult | None:
    if location not in _fx()["locations"]:
        return ToolResult(False, error=f"【钓况】未知地点「{location}」，请提供支持的地点或坐标。", source="validation")
    try:
        delta = (dt.date.fromisoformat(date) - dt.date.fromisoformat(ctx().today)).days
    except ValueError:
        return ToolResult(False, error="【钓况】日期格式应为 YYYY-MM-DD。", source="validation")
    if delta < 0:
        return ToolResult(False, error="【钓况】不支持查询历史日期。", source="validation")
    if delta > 6:
        return ToolResult(False, error="【钓况】只支持今天起 7 天内的预报。", source="validation")
    return None


@tool_wrapper("get_weather")
def get_weather(location: str, date: str) -> ToolResult:
    """查询某地某天的天气摘要：天气、最低/最高气温、风速风向、降水、气压趋势。"""
    if bad := _validate(location, date):
        return bad
    if config.FIXTURE_MODE:
        return ToolResult(True, {"location": location, "date": date, **_fx()["weather"][location][date]}, source="fixture")
    return _open_meteo_weather(location, date)  # pragma: no cover


@tool_wrapper("get_tide")
def get_tide(location: str, date: str) -> ToolResult:
    """查询沿海地点某天的高/低潮时间与潮高（以平均海平面为基准，近岸精度有限）。内陆地点返回不适用。"""
    if bad := _validate(location, date):
        return bad
    L = _fx()["locations"][location]
    if not L["coastal"]:
        return ToolResult(False, error=f"【潮汐】{L['name']} 是内陆水域，没有潮汐数据。", source="not_applicable")
    t = _fx()["tide"][location][date] if config.FIXTURE_MODE else _open_meteo_tide(location, date)  # pragma: no cover
    if t["status"] != "ok":
        return ToolResult(False, error="【潮汐】上游接口超时，潮汐数据暂不可用。", source="upstream")
    return ToolResult(True, {"location": location, "date": date, "extremes": t["extremes"]}, source="fixture")


def _m(t):
    h, m = t.split(":")
    return int(h) * 60 + int(m)


def _t(m):
    return f"{m // 60:02d}:{m % 60:02d}"


@tool_wrapper("best_window")
def best_window(location: str, date: str) -> ToolResult:
    """计算最佳出钓时段：风速≥30km/h 或降水≥10mm 不建议；沿海取高潮前后 60 分钟 ∩ 白天；内陆取日出后 / 日落前 120 分钟。"""
    if bad := _validate(location, date):
        return bad
    L, w = _fx()["locations"][location], _fx()["weather"][location][date]
    if w["wind_kmh"] >= NO_GO_WIND_KMH or w["precip_mm"] >= NO_GO_PRECIP_MM:
        why = "、".join(x for x in [f"风速 {w['wind_kmh']} km/h" if w["wind_kmh"] >= NO_GO_WIND_KMH else "",
                                   f"降水 {w['precip_mm']} mm" if w["precip_mm"] >= NO_GO_PRECIP_MM else ""] if x)
        return ToolResult(True, {"status": "no_go", "windows": [], "reason": why}, source="computed")
    sr, ss = _m(L["sunrise"]), _m(L["sunset"])
    if not L["coastal"]:
        return ToolResult(True, {"status": "ok", "windows": [[_t(sr), _t(sr + INLAND_WINDOW_MIN)],
                                                             [_t(ss - INLAND_WINDOW_MIN), _t(ss)]], "reason": None},
                          source="computed")
    t = _fx()["tide"][location][date]
    if t["status"] != "ok":
        return ToolResult(True, {"status": "tide_unavailable", "windows": [], "reason": "潮汐数据暂不可用"}, source="computed")
    wins = []
    for e in t["extremes"]:
        if e["type"] == "high":
            a, b = max(_m(e["time"]) - TIDE_HALF_WINDOW_MIN, sr), min(_m(e["time"]) + TIDE_HALF_WINDOW_MIN, ss)
            if b > a:
                wins.append([_t(a), _t(b)])
    return ToolResult(True, {"status": "ok", "windows": wins, "reason": None}, source="computed")


def location_name(loc: str) -> str:
    return _fx()["locations"][loc]["name"]


def is_coastal(loc: str) -> bool:
    return _fx()["locations"][loc]["coastal"]


def _open_meteo_weather(location, date):  # pragma: no cover - 需联网
    import requests
    L = _fx()["locations"][location]
    r = requests.get("https://api.open-meteo.com/v1/forecast", timeout=5, params={
        "latitude": L["lat"], "longitude": L["lon"], "start_date": date, "end_date": date, "timezone": "auto",
        "daily": "temperature_2m_min,temperature_2m_max,wind_speed_10m_max,precipitation_sum"}).json()["daily"]
    return ToolResult(True, {"location": location, "date": date, "temp_min_c": r["temperature_2m_min"][0],
                             "temp_max_c": r["temperature_2m_max"][0], "wind_kmh": r["wind_speed_10m_max"][0],
                             "precip_mm": r["precipitation_sum"][0]}, source="open-meteo")


def _open_meteo_tide(location, date):  # pragma: no cover - 需联网
    import requests
    L = _fx()["locations"][location]
    h = requests.get("https://marine-api.open-meteo.com/v1/marine", timeout=5, params={
        "latitude": L["lat"], "longitude": L["lon"], "hourly": "sea_level_height_msl", "start_date": date,
        "end_date": date, "timezone": "auto"}).json()["hourly"]
    v, ts = h["sea_level_height_msl"], h["time"]
    ext = [{"type": "high" if v[i] > v[i - 1] else "low", "time": ts[i][11:16], "height_m": v[i]}
           for i in range(1, len(v) - 1) if (v[i] - v[i - 1]) * (v[i + 1] - v[i]) < 0]
    return {"status": "ok", "extremes": ext}
