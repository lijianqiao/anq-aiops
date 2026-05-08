"""主机分级查询

production_hosts / staging_hosts 是逗号分隔的 IP 字符串（在 settings 中），
不在两者中的主机默认归 'dev'。同一 IP 同时出现在两者时，production 优先。
"""

from src.config import settings


def _parse_list(raw: str) -> set[str]:
    """逗号分隔字符串 → set，去空白；空字符串返回空 set"""
    if not raw:
        return set()
    return {item.strip() for item in raw.split(",") if item.strip()}


def lookup_tier(host_ip: str | None) -> str:
    """查询主机所属 tier

    Returns:
        "production" / "staging" / "dev"
    """
    if not host_ip:
        return "dev"
    if host_ip in _parse_list(settings.production_hosts):
        return "production"
    if host_ip in _parse_list(settings.staging_hosts):
        return "staging"
    return "dev"
