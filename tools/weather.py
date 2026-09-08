"""示例 tool: 查询天气 (mock 数据, 用于演示)。"""
from __future__ import annotations


def get_weather(city: str) -> str:
    """查询指定城市的当前天气 (本实现返回 mock 数据)。

    生产环境应替换为和风天气 / OpenWeatherMap / 彩云天气 等真实 API。
    """
    city = (city or "").strip()
    if not city:
        return "城市名不能为空"

    # 一些固定 mock, 让 demo 有可观察的输出
    mock_db = {
        "北京": {"temp": 22, "cond": "晴", "humidity": 35, "wind": "北风 3 级"},
        "上海": {"temp": 26, "cond": "多云", "humidity": 70, "wind": "东风 2 级"},
        "深圳": {"temp": 30, "cond": "雷阵雨", "humidity": 80, "wind": "南风 4 级"},
    }
    data = mock_db.get(city, {"temp": 20, "cond": "未知", "humidity": 50, "wind": "微风"})
    return (
        f"{city} 当前天气: 温度 {data['temp']}°C, {data['cond']}, "
        f"湿度 {data['humidity']}%, {data['wind']} (mock 数据)"
    )
