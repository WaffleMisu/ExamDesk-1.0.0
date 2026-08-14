from __future__ import annotations

from datetime import UTC, datetime, time, timedelta, tzinfo

DISPLAY_DATETIME_FORMAT = "%Y-%m-%d %H:%M:%S"
EXCEL_DATETIME_FORMAT = "yyyy-mm-dd hh:mm:ss"


def format_local_datetime(
    value,
    *,
    empty: str = "",
    target_timezone: tzinfo | None = None,
) -> str:
    if value in (None, ""):
        return empty
    parsed = _parse_datetime(value)
    if parsed is None:
        return str(value)
    return _to_local(parsed, target_timezone).strftime(DISPLAY_DATETIME_FORMAT)


def excel_local_datetime(value, *, target_timezone: tzinfo | None = None):
    if value in (None, ""):
        return ""
    parsed = _parse_datetime(value)
    if parsed is None:
        return value
    return _to_local(parsed, target_timezone).replace(tzinfo=None, microsecond=0)


def local_date_utc_bounds(
    value: str,
    *,
    target_timezone: tzinfo | None = None,
) -> tuple[str, str]:
    cleaned = value.strip()
    try:
        selected_date = datetime.strptime(cleaned, "%Y-%m-%d").date()
    except ValueError as exc:
        raise ValueError("日期格式错误，请输入 YYYY-MM-DD") from exc
    if cleaned != selected_date.strftime("%Y-%m-%d"):
        raise ValueError("日期格式错误，请输入 YYYY-MM-DD")
    local_timezone = target_timezone or _local_timezone()
    start_local = datetime.combine(selected_date, time.min, tzinfo=local_timezone)
    end_local = datetime.combine(selected_date + timedelta(days=1), time.min, tzinfo=local_timezone)
    return start_local.astimezone(UTC).isoformat(), end_local.astimezone(UTC).isoformat()


def _parse_datetime(value) -> datetime | None:
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str):
        cleaned = value.strip()
        if not cleaned:
            return None
        try:
            parsed = datetime.fromisoformat(cleaned.replace("Z", "+00:00"))
        except ValueError:
            return None
    else:
        return None
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)


def _to_local(value: datetime, target_timezone: tzinfo | None) -> datetime:
    return value.astimezone(target_timezone) if target_timezone is not None else value.astimezone()


def _local_timezone() -> tzinfo:
    return datetime.now().astimezone().tzinfo or UTC
