"""示例 tool: 获取当前时间。"""
from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


def get_current_time(timezone: str = "") -> str:
    """返回 ISO8601 格式的当前时间。

    Args:
        timezone: IANA 时区名 (例如 Asia/Shanghai), 留空用本地时区。
    """
    if timezone:
        try:
            tz = ZoneInfo(timezone)
            now = datetime.now(tz)
            return now.strftime("%Y-%m-%d %H:%M:%S %Z")
        except ZoneInfoNotFoundError:
            return f"未知时区: {timezone}"
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")
