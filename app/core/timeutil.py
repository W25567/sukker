import calendar
from datetime import UTC, date, datetime
from zoneinfo import ZoneInfo

DANISH_TZ = ZoneInfo("Europe/Copenhagen")


def now_utc():
    return datetime.now(UTC)


def today_local():
    """Dagens dato i dansk tid."""
    return datetime.now(DANISH_TZ).date()


def age_on(birthdate: date, on: date) -> int:
    had_birthday = (on.month, on.day) >= (birthdate.month, birthdate.day)
    return on.year - birthdate.year - (0 if had_birthday else 1)


def add_months(moment: datetime, months: int) -> datetime:
    """Lægger hele måneder til. Dagen afkortes til månedens sidste dag ved behov (31. jan + 1 = 28./29. feb).

    Regnes altid i UTC, så resultatet ikke afhænger af, hvilken tidszone tidspunktet kom ind med.
    """
    moment = moment.astimezone(UTC)
    month_index = moment.month - 1 + months
    year = moment.year + month_index // 12
    month = month_index % 12 + 1
    day = min(moment.day, calendar.monthrange(year, month)[1])
    return moment.replace(year=year, month=month, day=day)
