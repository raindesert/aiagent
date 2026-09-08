"""工具: 实时天气查询, 调 wttr.in 公开 API (免费, 无需 key)。

API: GET https://wttr.in/<city>?format=j1&lang=zh
"""
from __future__ import annotations

import requests

_API_URL = "https://wttr.in"
_DEFAULT_TIMEOUT = 10  # 秒
_HEADERS = {
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    "User-Agent": "aiagent/0.1",
}


def get_weather(city: str, timeout: int = _DEFAULT_TIMEOUT) -> str:
    """查询指定城市的实时天气。

    Args:
        city: 城市名 (中英文皆可, 例如 "北京" / "Shanghai" / "Tokyo")。
        timeout: HTTP 请求超时秒数, 默认 10。
    """
    if not city or not city.strip():
        return "城市名不能为空"
    if timeout <= 0:
        return "timeout 必须为正整数"

    city_q = city.strip()
    url = f"{_API_URL}/{requests.utils.quote(city_q)}?format=j1&lang=zh"

    # ---- 1. 拉数据 ----
    try:
        resp = requests.get(url, headers=_HEADERS, timeout=timeout)
    except requests.exceptions.Timeout:
        return f"请求超时 ({timeout}s)"
    except requests.exceptions.ConnectionError as e:
        return f"无法连接 {_API_URL}: {e}"
    except requests.exceptions.RequestException as e:
        return f"网络错误: {e}"

    if resp.status_code != 200:
        return f"天气 API 返回 HTTP {resp.status_code}: {resp.reason}"

    # ---- 2. 解析 JSON ----
    try:
        data = resp.json()
    except ValueError as e:
        return f"返回数据不是 JSON: {e}"

    # ---- 3. 校验结构 ----
    current_list = data.get("current_condition") or []
    if not current_list:
        return f"未找到城市 {city!r} 的天气数据 (可能城市名拼写有误)"

    cur = current_list[0]
    try:
        temp = int(cur.get("temp_C", "0"))
        feels = int(cur.get("FeelsLikeC", "0"))
        humidity = int(cur.get("humidity", "0"))
        wind_kmh = int(cur.get("windspeedKmph", "0"))
        pressure = int(cur.get("pressure", "0"))
        cloud = int(cur.get("cloudcover", "0"))
    except (TypeError, ValueError):
        return "天气 API 返回字段异常"

    # ---- 4. 提取位置 ----
    area_list = data.get("nearest_area") or []
    area = area_list[0] if area_list else {}
    area_name = _dig(area, "areaName", 0, "value") or city.strip()
    country = _dig(area, "country", 0, "value") or ""
    region = _dig(area, "region", 0, "value") or ""
    location = area_name
    if country and country.lower() != area_name.lower():
        location = f"{area_name}, {country}"
    if region and region not in location:
        location = f"{location} ({region})"

    desc = (_dig(cur, "weatherDesc", 0, "value") or "未知").strip()
    wind_dir = (cur.get("winddir16Point") or "?")
    obs_time = cur.get("observation_time") or "?"

    return (
        f"{location} 当前天气: {desc}\n"
        f"  温度: {temp}°C (体感 {feels}°C)\n"
        f"  湿度: {humidity}%, 云量: {cloud}%\n"
        f"  风: {wind_dir} {wind_kmh} km/h\n"
        f"  气压: {pressure} hPa\n"
        f"  观测时间 (UTC): {obs_time}"
    )


def _dig(d: dict, *path) -> str | None:
    """安全地深入 dict/list 嵌套取值, 任意一段缺失返回 None。"""
    cur = d
    for key in path:
        try:
            cur = cur[key]
        except (KeyError, IndexError, TypeError):
            return None
    return cur if isinstance(cur, str) else None
