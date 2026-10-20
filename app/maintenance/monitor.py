"""Overvågning og alarmer (specifikation 14.9).

Kører hvert 15. minut fra sukker-monitor.timer. Tjekker backupstatus, certifikatudløb, diskplads, fejlrate
og fejlende sukker-*-enheder. Alarmer sendes til ALERT_EMAIL. Den samme fejl sendes højst én gang i døgnet,
og der sendes besked, når en fejl er væk igen.
"""
import json
import os
import shutil
import socket
import ssl
import subprocess
from dataclasses import dataclass
from datetime import datetime, timedelta

from flask import current_app

from ..brands import current_brand
from ..core.mail import send_mail
from ..core.timeutil import now_utc
from . import backup

BACKUP_MAX_AGE = timedelta(hours=26)
CERT_WARN_DAYS = 14
DISK_MIN_FREE_RATIO = 0.10
DISK_MIN_FREE_BYTES = 2 * 1024**3
ERROR_WINDOW = timedelta(minutes=15)
ERROR_MIN_REQUESTS = 20
ERROR_MAX_RATIO = 0.05
ERROR_MAX_COUNT = 50
REALERT_AFTER = timedelta(hours=24)


@dataclass(frozen=True)
class Problem:
    key: str
    message: str


def check_backup(now=None):
    if current_app.config["ENV_NAME"] != "production":
        return []
    now = now or now_utc()
    status = backup.read_status()
    if not status.get("last_success"):
        return [Problem("backup", "Der er endnu ikke taget en vellykket backup.")]
    problems = []
    if status.get("ok") is False:
        problems.append(Problem("backup", f"Seneste backup fejlede: {status.get('error')}"))
    age = now - datetime.fromisoformat(status["last_success"])
    if age > BACKUP_MAX_AGE:
        problems.append(Problem("backup-age", f"Seneste vellykkede backup er {age.total_seconds() / 3600:.0f} timer gammel."))
    if not status.get("encrypted"):
        problems.append(Problem("backup-encryption", "Seneste backup er ikke krypteret."))
    if status.get("offsite") != "ok":
        problems.append(Problem("backup-offsite", f"Backup uden for serveren: {status.get('offsite')}."))
    return problems


def check_disk(paths=None):
    problems = []
    for path in paths or {current_app.config["MEDIA_ROOT"], backup.backup_dir()}:
        if not os.path.exists(path):
            continue
        usage = shutil.disk_usage(path)
        if usage.free < DISK_MIN_FREE_BYTES or usage.free / usage.total < DISK_MIN_FREE_RATIO:
            problems.append(Problem(f"disk:{path}", f"Lidt diskplads ved {path}: {usage.free / 1024**3:.1f} GB fri "
                                                     f"({100 * usage.free / usage.total:.0f} %)."))
    return problems


def certificate_expiry(domain, port=443, timeout=10):
    context = ssl.create_default_context()
    with socket.create_connection((domain, port), timeout=timeout) as sock:
        with context.wrap_socket(sock, server_hostname=domain) as tls:
            not_after = tls.getpeercert()["notAfter"]
    return datetime.fromtimestamp(ssl.cert_time_to_seconds(not_after)).astimezone()


def check_certificate(now=None, expiry_lookup=certificate_expiry):
    domain = current_app.config["SITE_DOMAIN"]
    if not domain:
        return []
    now = now or now_utc()
    try:
        expires = expiry_lookup(domain)
    except (OSError, ssl.SSLError) as error:
        return [Problem("certificate", f"Certifikatet for {domain} kunne ikke kontrolleres: {error}")]
    days = (expires - now).days
    if days < CERT_WARN_DAYS:
        return [Problem("certificate", f"Certifikatet for {domain} udløber om {days} dage. Fornyelsen virker måske ikke.")]
    return []


def _tail_lines(path, max_bytes=4 * 1024**2):
    with open(path, "rb") as fh:
        fh.seek(0, os.SEEK_END)
        size = fh.tell()
        fh.seek(max(0, size - max_bytes))
        data = fh.read().decode("utf-8", errors="replace")
    lines = data.splitlines()
    return lines[1:] if size > max_bytes else lines


def check_error_rate(now=None, path=None):
    """Andel 5xx-svar i Nginx' adgangslog de seneste 15 minutter. Loggen er JSON uden IP-adresser."""
    path = path or current_app.config["ACCESS_LOG"]
    if not path or not os.path.exists(path):
        return []
    since = (now or now_utc()) - ERROR_WINDOW
    total = errors = 0
    for line in _tail_lines(path):
        try:
            entry = json.loads(line)
            if datetime.fromisoformat(entry["t"]) < since:
                continue
        except (ValueError, KeyError, TypeError):
            continue
        total += 1
        errors += int(entry.get("s", 0)) >= 500
    if errors >= ERROR_MAX_COUNT or (total >= ERROR_MIN_REQUESTS and errors / total > ERROR_MAX_RATIO):
        return [Problem("error-rate", f"{errors} af {total} forespørgsler gav en serverfejl de seneste 15 minutter.")]
    return []


def check_failed_units(runner=subprocess.run):
    if not shutil.which("systemctl"):
        return []
    prefix = current_app.config["SERVICE_NAME"]
    result = runner(["systemctl", "list-units", "--failed", "--plain", "--no-legend", f"{prefix}*"],
                    capture_output=True, text=True)
    units = [line.split()[0] for line in result.stdout.splitlines() if line.strip()]
    return [Problem(f"unit:{u}", f"systemd-enheden {u} er fejlet.") for u in units if belongs_to(u, prefix)]


def belongs_to(unit, prefix):
    """Produktion (fx sukker eller nogen) og staging (sukker-staging, nogen-staging) deler præfiks, men hvert miljø
    overvåger kun sit eget."""
    if not (unit.startswith(prefix + ".") or unit.startswith(prefix + "-") or unit.startswith(prefix + "@")):
        return False
    return prefix.endswith("-staging") or not unit.startswith(prefix + "-staging")


def collect_problems():
    return check_backup() + check_disk() + check_certificate() + check_error_rate() + check_failed_units()


def _state_path():
    return os.path.join(backup.backup_dir(), "monitor-state.json")


def _load_state():
    try:
        with open(_state_path(), encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return {}


def _save_state(state):
    with open(_state_path() + ".tmp", "w", encoding="utf-8") as fh:
        json.dump(state, fh)
    os.replace(_state_path() + ".tmp", _state_path())


def run(now=None, problems=None):
    """Tjekker alt og sender alarmer. Returnerer (aktuelle problemer, om der blev sendt mail)."""
    now = now or now_utc()
    problems = collect_problems() if problems is None else problems
    state = _load_state()
    current = {p.key: p for p in problems}
    due = [p for p in problems
           if p.key not in state or now - datetime.fromisoformat(state[p.key]) > REALERT_AFTER]
    resolved = [key for key in state if key not in current]
    sent = False
    recipient = current_app.config["ALERT_EMAIL"]
    if (due or resolved) and recipient:
        subject = (f"{current_brand().name} ({current_app.config['ENV_NAME']}): {len(problems)} problem(er)" if problems
                   else f"{current_brand().name} ({current_app.config['ENV_NAME']}): alt er i orden igen")
        sent = send_mail(recipient, subject, "mail/alert.txt", problems=problems, resolved=resolved,
                         environment=current_app.config["ENV_NAME"])
    for problem in problems:
        current_app.logger.warning("Overvågning: %s", problem.message)
    if sent or not recipient:
        state = {key: (now.isoformat() if any(p.key == key for p in due) else state.get(key, now.isoformat()))
                 for key in current}
        _save_state(state)
    return problems, sent


def alert_unit_failure(unit):
    """Kaldes af sukker-alert@.service, når en timer-opgave eller servicen fejler (OnFailure=)."""
    recipient = current_app.config["ALERT_EMAIL"]
    problem = Problem(f"unit:{unit}", f"systemd-enheden {unit} fejlede. Se: journalctl -u {unit} -n 50")
    current_app.logger.error(problem.message)
    if not recipient:
        return False
    return send_mail(recipient, f"{current_brand().name} ({current_app.config['ENV_NAME']}): {unit} fejlede", "mail/alert.txt",
                     problems=[problem], resolved=[], environment=current_app.config["ENV_NAME"])
