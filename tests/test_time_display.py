from datetime import UTC, datetime, timedelta, timezone

import pytest

from examdesk.time_display import (
    EXCEL_DATETIME_FORMAT,
    excel_local_datetime,
    format_local_datetime,
    local_date_utc_bounds,
)

CHINA_TIME = timezone(timedelta(hours=8))


def test_utc_time_is_displayed_as_local_time_across_midnight() -> None:
    value = "2026-08-05T16:37:39.679405+00:00"

    assert format_local_datetime(value, target_timezone=CHINA_TIME) == "2026-08-06 00:37:39"
    assert excel_local_datetime(value, target_timezone=CHINA_TIME) == datetime(2026, 8, 6, 0, 37, 39)
    assert EXCEL_DATETIME_FORMAT == "yyyy-mm-dd hh:mm:ss"


def test_naive_legacy_time_is_treated_as_utc() -> None:
    assert format_local_datetime(
        "2026-08-05T16:37:39",
        target_timezone=CHINA_TIME,
    ) == "2026-08-06 00:37:39"


def test_empty_and_malformed_values_do_not_break_display() -> None:
    assert format_local_datetime(None, empty="-") == "-"
    assert format_local_datetime("旧记录异常") == "旧记录异常"
    assert excel_local_datetime("") == ""
    assert excel_local_datetime("旧记录异常") == "旧记录异常"


def test_local_date_is_converted_to_utc_query_bounds() -> None:
    assert local_date_utc_bounds("2026-08-06", target_timezone=CHINA_TIME) == (
        "2026-08-05T16:00:00+00:00",
        "2026-08-06T16:00:00+00:00",
    )


@pytest.mark.parametrize("value", ["2026-8-6", "2026/08/06", "2026-02-30"])
def test_invalid_filter_date_is_rejected(value: str) -> None:
    with pytest.raises(ValueError, match="YYYY-MM-DD"):
        local_date_utc_bounds(value, target_timezone=UTC)
