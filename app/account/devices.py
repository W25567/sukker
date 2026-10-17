"""Gør en user agent-streng læsbar på kontosiden, fx "Chrome på Windows"."""

_BROWSERS = [("Edg/", "Edge"), ("OPR/", "Opera"), ("Firefox/", "Firefox"), ("Chrome/", "Chrome"), ("Safari/", "Safari")]
_SYSTEMS = [("iPhone", "iPhone"), ("iPad", "iPad"), ("Android", "Android"), ("Windows", "Windows"),
            ("Mac OS X", "Mac"), ("Linux", "Linux")]


def describe_device(user_agent):
    if not user_agent:
        return "Ukendt enhed"
    browser = next((name for marker, name in _BROWSERS if marker in user_agent), None)
    system = next((name for marker, name in _SYSTEMS if marker in user_agent), None)
    if browser and system:
        return f"{browser} på {system}"
    return browser or system or "Ukendt enhed"
