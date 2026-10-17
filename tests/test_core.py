from app.account.devices import describe_device
from app.core.timeutil import add_months, age_on


def test_describe_device():
    chrome_windows = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/152.0 Safari/537.36"
    safari_iphone = "Mozilla/5.0 (iPhone; CPU iPhone OS 19_0 like Mac OS X) AppleWebKit/605.1.15 Version/19.0 Mobile Safari/604.1"
    assert describe_device(chrome_windows) == "Chrome på Windows"
    assert describe_device(safari_iphone) == "Safari på iPhone"
    assert describe_device(None) == "Ukendt enhed"


def test_health(client):
    response = client.get("/health")
    assert response.status_code == 200
    assert response.get_json() == {"status": "ok"}


def test_ready_checks_database(client):
    response = client.get("/health/ready")
    assert response.status_code == 200
    assert response.get_json()["database"] == "ok"


def test_public_home_is_indexable_but_other_pages_are_not(client):
    assert "X-Robots-Tag" not in client.get("/").headers
    assert client.get("/log-ind").headers["X-Robots-Tag"] == "noindex, nofollow"
    assert client.get("/health").headers["X-Robots-Tag"] == "noindex, nofollow"


def test_security_headers(client):
    headers = client.get("/").headers
    assert headers["X-Content-Type-Options"] == "nosniff"
    assert headers["X-Frame-Options"] == "DENY"
    assert "frame-ancestors 'none'" in headers["Content-Security-Policy"]


def test_age_on():
    from datetime import date

    assert age_on(date(2000, 6, 15), date(2018, 6, 14)) == 17
    assert age_on(date(2000, 6, 15), date(2018, 6, 15)) == 18


def test_add_months_clamps_to_month_end():
    from datetime import UTC, datetime

    assert add_months(datetime(2026, 1, 31, tzinfo=UTC), 1) == datetime(2026, 2, 28, tzinfo=UTC)
    assert add_months(datetime(2026, 11, 15, tzinfo=UTC), 3) == datetime(2027, 2, 15, tzinfo=UTC)


def test_add_months_gives_same_result_regardless_of_input_timezone():
    from datetime import UTC, datetime
    from zoneinfo import ZoneInfo

    # Hen over skiftet til vintertid må resultatet ikke afhænge af, hvilken tidszone tidspunktet har.
    utc_moment = datetime(2026, 10, 16, 15, 0, tzinfo=UTC)
    local_moment = utc_moment.astimezone(ZoneInfo("Europe/Copenhagen"))
    assert add_months(local_moment, 3) == add_months(utc_moment, 3)
