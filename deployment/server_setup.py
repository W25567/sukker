#!/usr/bin/env python3
"""Installerer og opdaterer Sukker.nu på Ubuntu. Køres som root af deploy.py (specifikation 14.3–15.6).

Scriptet bruger kun Pythons standardbibliotek og modtager hele konfigurationen som JSON på stdin, så
hemmeligheder aldrig står på kommandolinjen eller i logfiler.

    server_setup.py inspect      Læser installationens tilstand (ændrer intet)
    server_setup.py apply        Installerer eller opdaterer til pakken i payload["package"]
    server_setup.py rollback     Skifter tilbage til den seneste fungerende version
    server_setup.py flask ...    Kører en flask-kommando som servicebrugeren, fx "sukker set-role ..."

Alle trin er idempotente: Kører man scriptet igen uden ændringer, ændres og genstartes intet.
Scriptet rører kun Sukker.nu's egne filer, service, database og Nginx-site. Fælles ressourcer
(Nginx, PostgreSQL, www-data) kontrolleres, før de ændres, og Nginx genindlæses kun efter `nginx -t`.

Linjer, der starter med @@, læses af deploy.py: @@STEP {...} for hvert trin og @@RESULT {...} til sidst.
"""
import base64
import hashlib
import http.client
import json
import os
import re
import secrets
import shutil
import socket
import subprocess
import sys
import tarfile
import time
import urllib.request
from datetime import datetime

RUNUSER = shutil.which("runuser") or "/usr/sbin/runuser"
KEEP_RELEASES = 5
HEALTH_TIMEOUT = 45
PACKAGES = ["python3-venv", "nginx", "certbot", "postgresql-client", "rsync", "openssl"]
MANAGED_KEYS = ("SUKKER_SECRET_KEY", "SUKKER_DATABASE_URL", "SUKKER_MIGRATOR_DATABASE_URL")

HARDENING = """NoNewPrivileges=yes
PrivateTmp=yes
PrivateDevices=yes
ProtectSystem=strict
ProtectHome=yes
ProtectKernelTunables=yes
ProtectKernelModules=yes
ProtectKernelLogs=yes
ProtectControlGroups=yes
ProtectClock=yes
ProtectHostname=yes
RestrictSUIDSGID=yes
RestrictRealtime=yes
RestrictNamespaces=yes
LockPersonality=yes
RestrictAddressFamilies=AF_UNIX AF_INET AF_INET6
SystemCallArchitectures=native
CapabilityBoundingSet="""

# Baggrundsopgaver (specifikation 14.6). Backup kun hvor det er slået til (ikke staging).
TASKS = {
    "maintenance": {"description": "oprydning og sletning efter opbevaringsreglen", "command": "maintenance",
                    "calendar": "*-*-* 03:30:00", "delay": "10min", "timeout": "1h"},
    "backup": {"description": "backup af database og billeder", "command": "backup",
               "calendar": "*-*-* 02:30:00", "delay": "10min", "timeout": "3h"},
    "monitor": {"description": "overvågning", "command": "monitor",
                "calendar": "*:0/15", "delay": "1min", "timeout": "5min"},
    "notify": {"description": "notifikationer pr. e-mail", "command": "notify",
               "calendar": "*:0/5", "delay": "30s", "timeout": "4min"},
}


class DeployError(RuntimeError):
    def __init__(self, step, message):
        super().__init__(message)
        self.step = step


# --- Udskrift -------------------------------------------------------------------------------------------


def log(message):
    print(f"  · {message}", flush=True)


def report_step(name, status, detail=""):
    print("@@STEP " + json.dumps({"name": name, "status": status, "detail": detail}, ensure_ascii=False), flush=True)


def report_result(result):
    print("@@RESULT " + json.dumps(result, ensure_ascii=False), flush=True)


# --- Hjælpefunktioner -------------------------------------------------------------------------------------


def run(cmd, *, input=None, env=None, user=None, cwd=None, check=True, timeout=1800):
    if user:
        # Fuld sti: med et eget env bruges dets PATH til at finde programmet, og runuser ligger i /usr/sbin.
        cmd = [RUNUSER, "-u", user, "--"] + cmd
    result = subprocess.run(cmd, input=input, env=env, cwd=cwd, capture_output=True, text=True, timeout=timeout)
    if check and result.returncode != 0:
        raise subprocess.CalledProcessError(result.returncode, cmd, result.stdout, result.stderr)
    return result


def render(template, values):
    """Erstatter {{navn}}. Ukendte pladsholdere er en fejl, så en skabelon aldrig installeres halvt udfyldt."""
    def replace(match):
        key = match.group(1)
        if key not in values:
            raise KeyError(f"Ukendt pladsholder i skabelon: {key}")
        return str(values[key])

    text = re.sub(r"\{\{([a-z_]+)\}\}", replace, template.replace("\r\n", "\n"))
    return text


def sha256_text(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def read_text(path):
    try:
        with open(path, encoding="utf-8") as fh:
            return fh.read()
    except FileNotFoundError:
        return None


class Changes:
    """Holder styr på ændrede filer, så de kan gendannes ved fejl og vises i rapporten."""

    def __init__(self):
        self.previous = {}  # sti -> tidligere indhold (None = fandtes ikke)
        self.notes = []

    def write(self, path, content, mode=0o644, owner="root", group="root"):
        old = read_text(path)
        if old == content:
            return False
        self.previous.setdefault(path, old)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = f"{path}.sukker-tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            fh.write(content)
        os.chmod(tmp, mode)
        shutil.chown(tmp, owner, group)
        os.replace(tmp, path)
        self.notes.append(path)
        return True

    def restore(self, paths=None):
        for path, old in self.previous.items():
            if paths is not None and path not in paths:
                continue
            if old is None:
                if os.path.exists(path):
                    os.remove(path)
            else:
                with open(path, "w", encoding="utf-8") as fh:
                    fh.write(old)


def parse_env_file(text):
    values = {}
    for line in (text or "").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] == '"':
            value = value[1:-1].replace('\\"', '"').replace("\\\\", "\\")
        values[key.strip()] = value
    return values


def format_env_file(values, env_name):
    lines = [f"# Sukker.nu ({env_name}). Skrevet af deploy.py. Hemmelighederne her forlader aldrig serveren.",
             "# Værdier, deploy.py ikke styrer, bevares. Rediger kun, hvis du ved, hvad du gør."]
    for key in sorted(values):
        value = str(values[key]).replace("\\", "\\\\").replace('"', '\\"')
        lines.append(f'{key}="{value}"')
    return "\n".join(lines) + "\n"


def server_names(cfg):
    return " ".join([cfg["domain"]] + cfg.get("aliases", []))


def nginx_values(cfg, cert_dir):
    alias_block = ""
    if cfg.get("aliases"):
        alias_block = (
            "\nserver {\n    listen 443 ssl;\n    listen [::]:443 ssl;\n    http2 on;\n"
            f"    server_name {' '.join(cfg['aliases'])};\n"
            f"    ssl_certificate {cert_dir}/fullchain.pem;\n    ssl_certificate_key {cert_dir}/privkey.pem;\n"
            f"    return 301 https://{cfg['domain']}$request_uri;\n}}\n"
        )
    basic_auth = ""
    if cfg.get("basic_auth"):
        basic_auth = f'\n    auth_basic "Sukker.nu {cfg["env"]}";\n    auth_basic_user_file {cfg["htpasswd_file"]};\n'
    return {
        "env": cfg["env"], "id": cfg["id"], "service": cfg["service"], "domain": cfg["domain"],
        "server_names": server_names(cfg), "acme_root": cfg["acme_root"], "cert_dir": cert_dir,
        "log_dir": cfg["log_dir"], "app_root": cfg["app_root"], "data_root": cfg["data_root"],
        "alias_block": alias_block, "basic_auth": basic_auth,
    }


def unit_values(cfg):
    return {"env": cfg["env"], "service": cfg["service"], "user": cfg["user"], "app_root": cfg["app_root"],
            "env_file": cfg["env_file"], "data_root": cfg["data_root"], "backup_dir": cfg["backup_dir"],
            "hardening": HARDENING}


def enabled_tasks(cfg):
    return {name: task for name, task in TASKS.items() if name != "backup" or cfg.get("backup_enabled")}


def render_units(cfg, templates):
    """Alle systemd-enheder for miljøet: {filnavn: indhold}."""
    values = unit_values(cfg)
    units = {
        f"{cfg['service']}.service": render(templates["sukker.service"], values),
        f"{cfg['service']}-alert@.service": render(templates["sukker-alert@.service"], values),
    }
    for name, task in enabled_tasks(cfg).items():
        task_values = {**values, **task, "task": name}
        units[f"{cfg['service']}-{name}.service"] = render(templates["sukker-task.service"], task_values)
        units[f"{cfg['service']}-{name}.timer"] = render(templates["sukker-task.timer"], task_values)
    return units


def conflicting_sites(cfg, own_site, directories=("/etc/nginx/sites-enabled", "/etc/nginx/conf.d")):
    """Andre Nginx-sites, der allerede bruger vores domæner. Dem rører vi ikke – vi stopper i stedet."""
    names = set(server_names(cfg).split())
    conflicts = []
    for directory in directories:
        if not os.path.isdir(directory):
            continue
        for entry in sorted(os.listdir(directory)):
            path = os.path.join(directory, entry)
            if os.path.realpath(path) == os.path.realpath(own_site) or not os.path.isfile(path):
                continue
            text = read_text(path) or ""
            for match in re.finditer(r"^\s*server_name\s+([^;]+);", text, re.M):
                if names & set(match.group(1).split()):
                    conflicts.append(path)
                    break
    return conflicts


def releases_to_remove(releases, keep, protected):
    """Ældste versioner ud over `keep`. Den aktive og den seneste fungerende slettes aldrig."""
    complete = sorted(r for r in releases if not r.endswith(".partial"))
    candidates = [r for r in complete[:-keep] if r not in protected] if len(complete) > keep else []
    return candidates + [r for r in releases if r.endswith(".partial")]


# --- Tilstand --------------------------------------------------------------------------------------------


def state_path(cfg):
    return os.path.join(cfg["app_root"], "shared", "deploy-state.json")


def load_state(cfg):
    try:
        with open(state_path(cfg), encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return {}


def save_state(cfg, state):
    path = state_path(cfg)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path + ".tmp", "w", encoding="utf-8") as fh:
        json.dump(state, fh, indent=2)
    os.replace(path + ".tmp", path)


def current_release(cfg):
    link = os.path.join(cfg["app_root"], "current")
    return os.path.basename(os.path.realpath(link)) if os.path.islink(link) else None


def cert_dir(cfg):
    return f"/etc/letsencrypt/live/{cfg['domain']}"


def cert_names(cfg):
    path = os.path.join(cert_dir(cfg), "cert.pem")
    if not os.path.exists(path):
        return None
    out = run(["openssl", "x509", "-in", path, "-noout", "-ext", "subjectAltName", "-enddate"], check=False).stdout
    names = set(re.findall(r"DNS:([^,\s]+)", out))
    end = re.search(r"notAfter=(.+)", out)
    return names, end.group(1).strip() if end else ""


def missing_secrets(cfg, env_values):
    missing = []
    if cfg.get("smtp_username") and not env_values.get("SUKKER_SMTP_PASSWORD"):
        missing.append("SUKKER_SMTP_PASSWORD")
    if cfg.get("basic_auth") and not os.path.exists(cfg["htpasswd_file"]):
        missing.append("staging_auth")
    return missing


def inspect(cfg):
    marker = read_text(os.path.join(cfg["app_root"], ".sukker-env"))
    env_values = parse_env_file(read_text(cfg["env_file"]))
    state = load_state(cfg)
    names = cert_names(cfg)
    return {
        "installed": marker is not None,
        "env_marker": marker.strip() if marker else None,
        "current_release": current_release(cfg),
        "code_hash": state.get("code_hash"),
        "requirements_hash": state.get("requirements_hash"),
        "previous_ok": state.get("previous_ok"),
        "deployed_at": state.get("deployed_at"),
        "missing_secrets": missing_secrets(cfg, env_values),
        "certificate": {"names": sorted(names[0]), "expires": names[1]} if names else None,
        "service_active": run(["systemctl", "is-active", cfg["service"]], check=False).stdout.strip(),
    }


# --- Trin i en udgivelse ---------------------------------------------------------------------------------


def preflight(cfg):
    if os.geteuid() != 0:
        raise DeployError("Serverkontrol", "server_setup.py skal køres som root (via sudo).")
    os_release = parse_env_file(read_text("/etc/os-release"))
    if os_release.get("ID") != "ubuntu":
        raise DeployError("Serverkontrol", f"Forventede Ubuntu, fandt {os_release.get('PRETTY_NAME')}.")
    marker = read_text(os.path.join(cfg["app_root"], ".sukker-env"))
    if marker is not None and marker.strip() != cfg["env"]:
        raise DeployError("Serverkontrol", f"{cfg['app_root']} tilhører miljøet {marker.strip()!r}, ikke "
                                           f"{cfg['env']!r}. Udgivelsen stoppes for at undgå forveksling.")
    conflicts = conflicting_sites(cfg, cfg["nginx_site"])
    if conflicts:
        raise DeployError("Serverkontrol", f"Domænet bruges allerede af: {', '.join(conflicts)}")
    return os_release.get("PRETTY_NAME", "Ubuntu")


def acquire_lock(cfg):
    import fcntl

    handle = open(f"/run/lock/{cfg['service']}-deploy.lock", "w")
    try:
        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        raise DeployError("Serverkontrol", "En anden udgivelse af samme miljø kører allerede.")
    return handle


def ensure_packages(cfg):
    wanted = PACKAGES + (["age"] if cfg.get("backup_age_recipients") else [])
    missing = [p for p in wanted
               if "install ok installed" not in run(["dpkg-query", "-W", "-f=${Status}", p], check=False).stdout]
    if missing:
        log(f"Installerer manglende pakker: {', '.join(missing)}")
        env = dict(os.environ, DEBIAN_FRONTEND="noninteractive")
        run(["apt-get", "update", "-q"], env=env)
        run(["apt-get", "install", "-y", "-q", "--no-install-recommends"] + missing, env=env)
    if run(["runuser", "-u", "postgres", "--", "psql", "-tAc", "SELECT 1"], check=False).returncode != 0:
        raise DeployError("Pakker", "PostgreSQL svarer ikke. Sukker.nu bruger den eksisterende PostgreSQL-instans.")
    return f"Installeret: {', '.join(missing)}" if missing else "OK"


def ensure_user_and_dirs(cfg, changes):
    import grp
    import pwd

    user = cfg["user"]
    try:
        pwd.getpwnam(user)
    except KeyError:
        run(["useradd", "--system", "--user-group", "--home-dir", f"{cfg['app_root']}/shared/home",
             "--shell", "/usr/sbin/nologin", user])
        changes.notes.append(f"systembruger {user}")
    # Nginx skal kunne læse billederne (X-Accel-Redirect) og forbinde til Gunicorns socket.
    if "www-data" not in grp.getgrnam(user).gr_mem:
        run(["usermod", "-aG", user, "www-data"])
        changes.notes.append(f"www-data tilføjet gruppen {user}")
    dirs = [
        (cfg["app_root"], "root", "root", 0o755), (f"{cfg['app_root']}/releases", "root", "root", 0o755),
        (f"{cfg['app_root']}/venvs", "root", "root", 0o755), (f"{cfg['app_root']}/shared", "root", "root", 0o755),
        (f"{cfg['app_root']}/shared/maintenance", "root", "root", 0o755),
        (f"{cfg['app_root']}/shared/home", user, user, 0o700),
        ("/etc/sukker", "root", "root", 0o755),
        (cfg["data_root"], user, user, 0o750), (f"{cfg['data_root']}/public", user, user, 0o750),
        (f"{cfg['data_root']}/private", user, user, 0o750), (f"{cfg['data_root']}/evidence", user, user, 0o750),
        (f"{cfg['data_root']}/instance", user, user, 0o750),
        (cfg["log_dir"], "root", user, 0o750), (cfg["backup_dir"], user, user, 0o700),
        (cfg["acme_root"], "root", "root", 0o755),
    ]
    for path, owner, group, mode in dirs:
        if not os.path.isdir(path):
            os.makedirs(path)
            changes.notes.append(path)
        shutil.chown(path, owner, group)
        os.chmod(path, mode)
    return "OK"


def ensure_backup_ssh(cfg, remote):
    """SSH-nøgle til backup uden for serveren. Den offentlige nøgle skal lægges på backupserveren."""
    if not remote or ":" not in remote:
        return None
    home = f"{cfg['app_root']}/shared/home"
    ssh_dir = f"{home}/.ssh"
    key = f"{ssh_dir}/id_ed25519"
    host = remote.split(":", 1)[0].split("@")[-1]
    if not os.path.isdir(ssh_dir):
        os.makedirs(ssh_dir, mode=0o700)
        shutil.chown(ssh_dir, cfg["user"], cfg["user"])
    if not os.path.exists(key):
        run(["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-C", f"{cfg['service']}-backup", "-f", key], user=cfg["user"])
    known_hosts = f"{ssh_dir}/known_hosts"
    if host not in (read_text(known_hosts) or ""):
        scan = run(["ssh-keyscan", "-T", "10", host], check=False)
        if scan.stdout.strip():
            with open(known_hosts, "a", encoding="utf-8") as fh:
                fh.write(scan.stdout)
            shutil.chown(known_hosts, cfg["user"], cfg["user"])
    return read_text(key + ".pub").strip()


def ensure_env_file(cfg, payload, changes):
    """Miljøfilen. Hemmeligheder genereres her første gang og forlader aldrig serveren."""
    values = parse_env_file(read_text(cfg["env_file"]))
    generated = {}
    if not values.get("SUKKER_SECRET_KEY"):
        values["SUKKER_SECRET_KEY"] = secrets.token_urlsafe(48)
    for key, role in (("SUKKER_DATABASE_URL", cfg["db_app_role"]), ("SUKKER_MIGRATOR_DATABASE_URL", cfg["db_migrator_role"])):
        if not values.get(key):
            password = secrets.token_hex(24)
            generated[role] = password
            values[key] = f"postgresql+psycopg://{role}:{password}@localhost:5432/{cfg['database']}"
    values.update(cfg["env_settings"])
    for key, value in (payload.get("secrets") or {}).items():
        if key.startswith("SUKKER_") and value:
            values[key] = value
    changed = changes.write(cfg["env_file"], format_env_file(values, cfg["env"]), 0o640, "root", cfg["user"])
    return values, generated, changed


def psql(sql, database="postgres"):
    return run(["runuser", "-u", "postgres", "--", "psql", "-v", "ON_ERROR_STOP=1", "-X", "-q", "-d", database],
               input=sql)


def psql_value(query, database="postgres"):
    return run(["runuser", "-u", "postgres", "--", "psql", "-X", "-tA", "-d", database, "-c", query]).stdout.strip()


def ensure_database(cfg, generated):
    """Egen database, eget skema og egne roller (specifikation 14.4). Rører aldrig en database, vi ikke selv har oprettet."""
    db, app, mig = cfg["database"], cfg["db_app_role"], cfg["db_migrator_role"]
    marker = f"sukker.nu:{cfg['env']}"
    for name in (db, app, mig):
        if not re.fullmatch(r"[a-z_][a-z0-9_]*", name):
            raise DeployError("Database", f"Ugyldigt navn: {name}")
    exists = psql_value(f"SELECT 1 FROM pg_database WHERE datname = '{db}'") == "1"
    if exists:
        comment = psql_value(f"SELECT shobj_description(oid, 'pg_database') FROM pg_database WHERE datname = '{db}'")
        if comment != marker:
            raise DeployError("Database", f"Databasen {db} findes, men er ikke oprettet af Sukker.nu ({cfg['env']}). "
                                          "Scriptet bruger aldrig en eksisterende database. Vælg et andet navn.")
    lines = []
    for role, password_key in ((mig, "mig_pw"), (app, "app_pw")):
        if role in generated:
            lines.append(f"\\set {password_key} '{generated[role]}'")
            lines.append(f"SELECT format('CREATE ROLE %I LOGIN', '{role}') "
                         f"WHERE NOT EXISTS (SELECT FROM pg_roles WHERE rolname = '{role}') \\gexec")
            lines.append(f"ALTER ROLE {role} WITH LOGIN PASSWORD :'{password_key}';")
        lines.append(f"ALTER ROLE {role} SET search_path = sukker;")
    lines += [
        f"SELECT 'CREATE DATABASE {db}' WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = '{db}') \\gexec",
        f"COMMENT ON DATABASE {db} IS '{marker}';",
        f"\\connect {db}",
        f"REVOKE ALL ON DATABASE {db} FROM PUBLIC;",
        f"GRANT CONNECT ON DATABASE {db} TO {mig}, {app};",
        "REVOKE CREATE ON SCHEMA public FROM PUBLIC;",
        f"CREATE SCHEMA IF NOT EXISTS sukker AUTHORIZATION {mig};",
        f"GRANT USAGE ON SCHEMA sukker TO {app};",
        f"ALTER DEFAULT PRIVILEGES FOR ROLE {mig} IN SCHEMA sukker GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO {app};",
        f"ALTER DEFAULT PRIVILEGES FOR ROLE {mig} IN SCHEMA sukker GRANT USAGE, SELECT ON SEQUENCES TO {app};",
        f"GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA sukker TO {app};",
        f"GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA sukker TO {app};",
    ]
    psql("\n".join(lines) + "\n")
    return "Oprettet" if not exists else ("Nye passwords" if generated else "OK")


def install_release(cfg, payload):
    """Pakker versionen ud i sin egen mappe. Samme kode som den aktive version giver ingen ny version."""
    state = load_state(cfg)
    current = current_release(cfg)
    if current and state.get("code_hash") == payload["code_hash"] and os.path.isdir(release_dir(cfg, current)):
        return current, False
    name = payload["release"]
    target = release_dir(cfg, name)
    if os.path.exists(target):
        name = f"{name}_{secrets.token_hex(2)}"
        target = release_dir(cfg, name)
    partial = target + ".partial"
    shutil.rmtree(partial, ignore_errors=True)
    os.makedirs(partial)
    with tarfile.open(payload["package"]) as tar:
        tar.extractall(partial, filter="data")
    with open(os.path.join(partial, "RELEASE.json"), "w", encoding="utf-8") as fh:
        json.dump({"release": name, "code_hash": payload["code_hash"],
                   "requirements_hash": payload["requirements_hash"], "created": datetime.now().isoformat()}, fh)
    os.rename(partial, target)
    return name, True


def release_dir(cfg, name):
    return os.path.join(cfg["app_root"], "releases", name)


def ensure_venv(cfg, release, requirements_hash):
    """Ét virtuelt miljø pr. sæt afhængigheder. Nye afhængigheder giver et nyt miljø (specifikation 15.4)."""
    venv = os.path.join(cfg["app_root"], "venvs", requirements_hash[:16])
    created = False
    if not os.path.exists(os.path.join(venv, ".complete")):
        shutil.rmtree(venv, ignore_errors=True)
        log("Opretter Python-miljø og installerer afhængigheder ...")
        run(["python3", "-m", "venv", venv])
        run([f"{venv}/bin/pip", "install", "--quiet", "--no-cache-dir", "--upgrade", "pip"])
        run([f"{venv}/bin/pip", "install", "--quiet", "--no-cache-dir", "-r",
             os.path.join(release_dir(cfg, release), "requirements.txt")])
        open(os.path.join(venv, ".complete"), "w").close()
        created = True
    link = os.path.join(release_dir(cfg, release), ".venv")
    if os.path.realpath(link) != os.path.realpath(venv):
        if os.path.lexists(link):
            os.remove(link)
        os.symlink(venv, link)
    return venv, created


def app_env(cfg, env_values):
    return {"PATH": "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin", "LANG": "C.UTF-8", **env_values}


def flask(cfg, release, env_values, *args, check=True, timeout=1800):
    return run([os.path.join(release_dir(cfg, release), ".venv", "bin", "flask"), "--app", "wsgi:app", *args],
               env=app_env(cfg, env_values), user=cfg["user"], cwd=release_dir(cfg, release), check=check,
               timeout=timeout)


def check_code(cfg, release, env_values):
    python = os.path.join(release_dir(cfg, release), ".venv", "bin", "python")
    result = run([python, "-c", "from app import create_app; create_app()"], env=app_env(cfg, env_values),
                 user=cfg["user"], cwd=release_dir(cfg, release), check=False)
    if result.returncode != 0:
        raise DeployError("Kodekontrol", "Applikationen kan ikke indlæses:\n" + result.stderr[-2000:])


def revisions(output):
    return set(re.findall(r"^([0-9a-f]{12})\b", output, re.M))


def migrate(cfg, release, env_values):
    current = revisions(flask(cfg, release, env_values, "db", "current").stdout)
    heads = revisions(flask(cfg, release, env_values, "db", "heads").stdout)
    if current == heads:
        return "Ingen nye", None
    backup_note = None
    if current:
        # Målrettet sikkerhedskopi før risikable ændringer (specifikation 15.3).
        log("Tager backup af databasen før migrationen ...")
        result = flask(cfg, release, env_values, "sukker", "backup", "--label", "pre-migrate", "--db-only", check=False)
        if result.returncode != 0:
            raise DeployError("Backup før migration", (result.stderr or result.stdout)[-1500:])
        backup_note = result.stdout.strip().splitlines()[-1] if result.stdout.strip() else "OK"
    result = flask(cfg, release, env_values, "db", "upgrade", check=False)
    if result.returncode != 0:
        raise DeployError("Migrationer", (result.stderr or result.stdout)[-2000:])
    after = revisions(flask(cfg, release, env_values, "db", "current").stdout)
    if after != heads:
        raise DeployError("Migrationer", f"Databasen står på {sorted(after)}, forventede {sorted(heads)}.")
    return f"Udført ({', '.join(sorted(heads))})", backup_note


def geo_hash(cfg, release):
    directory = os.path.join(release_dir(cfg, release), "data", "geo")
    digest = hashlib.sha256()
    for name in sorted(os.listdir(directory)) if os.path.isdir(directory) else []:
        with open(os.path.join(directory, name), "rb") as fh:
            digest.update(name.encode() + fh.read())
    return digest.hexdigest()


def load_geo(cfg, release, env_values, state):
    current = geo_hash(cfg, release)
    if state.get("geo_hash") == current:
        return "Uændret", current
    result = flask(cfg, release, env_values, "sukker", "load-geo", check=False)
    if result.returncode != 0:
        raise DeployError("Kommunedata", (result.stderr or result.stdout)[-1500:])
    return "Indlæst", current


def write_support_files(cfg, release, templates, payload, changes):
    values = {"env": cfg["env"], "log_dir": cfg["log_dir"], "user": cfg["user"]}
    changes.write(f"{cfg['app_root']}/shared/maintenance/_vedligeholdelse.html", templates["maintenance.html"])
    changes.write(f"/etc/logrotate.d/{cfg['service']}", render(templates["sukker.logrotate"], values))
    auth = payload.get("staging_auth")
    if cfg.get("basic_auth") and auth:
        hashed = run(["openssl", "passwd", "-apr1", "-stdin"], input=auth["password"] + "\n").stdout.strip()
        changes.write(cfg["htpasswd_file"], f"{auth['user']}:{hashed}\n", 0o640, "root", "www-data")
    if cfg.get("basic_auth") and not os.path.exists(cfg["htpasswd_file"]):
        raise DeployError("Nginx-konfiguration", "Staging kræver adgangskode, men den er ikke sat.")


def apply_nginx(cfg, content, changes):
    """Skriver sitet, validerer med nginx -t og genindlæser. Ved fejl gendannes den tidligere konfiguration."""
    site, enabled = cfg["nginx_site"], f"/etc/nginx/sites-enabled/{os.path.basename(cfg['nginx_site'])}"
    changed = changes.write(site, content)
    if not os.path.islink(enabled):
        os.symlink(site, enabled)
        changed = True
    if not changed:
        return False
    test = run(["nginx", "-t"], check=False)
    if test.returncode != 0:
        changes.restore([site])
        if changes.previous.get(site) is None and os.path.islink(enabled):
            os.remove(enabled)
        raise DeployError("Nginx-konfiguration", "nginx -t fejlede, den tidligere konfiguration er gendannet:\n"
                          + test.stderr[-1500:])
    run(["systemctl", "reload", "nginx"])
    return True


def probe_http(cfg):
    """Kontrollerer DNS og netværk, før der bestilles et certifikat: kan Let's Encrypt nå os via HTTP?"""
    token = secrets.token_hex(16)
    challenge_dir = os.path.join(cfg["acme_root"], ".well-known", "acme-challenge")
    os.makedirs(challenge_dir, exist_ok=True)
    path = os.path.join(challenge_dir, token)
    with open(path, "w") as fh:
        fh.write(token)
    try:
        for name in server_names(cfg).split():
            try:
                socket.getaddrinfo(name, 80)
                with urllib.request.urlopen(f"http://{name}/.well-known/acme-challenge/{token}", timeout=15) as resp:
                    if resp.read().decode().strip() != token:
                        raise OSError("forkert svar")
            except OSError as error:
                raise DeployError("Let's Encrypt", f"{name} peger ikke på denne server, eller port 80 er lukket ({error}).")
    finally:
        os.remove(path)


def ensure_certificate(cfg, templates, changes):
    wanted = set(server_names(cfg).split())
    existing = cert_names(cfg)
    if existing and wanted <= existing[0]:
        return f"Genbrugt (udløber {existing[1]})"
    if not cfg.get("letsencrypt_email"):
        raise DeployError("Let's Encrypt", "letsencrypt_email mangler i deployment/environments.json.")
    if not existing:
        apply_nginx(cfg, render(templates["sukker-bootstrap.nginx.conf"], nginx_values(cfg, cert_dir(cfg))), changes)
    probe_http(cfg)
    cmd = ["certbot", "certonly", "--webroot", "-w", cfg["acme_root"], "--cert-name", cfg["domain"],
           "--non-interactive", "--agree-tos", "-m", cfg["letsencrypt_email"],
           "--deploy-hook", "systemctl reload nginx"]
    for name in sorted(wanted, key=lambda n: n != cfg["domain"]):
        cmd += ["-d", name]
    if existing:
        cmd.append("--expand")
    result = run(cmd, check=False, timeout=600)
    if result.returncode != 0:
        raise DeployError("Let's Encrypt", (result.stderr or result.stdout)[-1500:])
    renewal = [t for t in ("certbot.timer", "snap.certbot.renew.timer")
               if run(["systemctl", "is-enabled", t], check=False).stdout.strip() == "enabled"]
    note = "" if renewal else " – ADVARSEL: ingen certbot-fornyelsestimer fundet"
    return ("Udvidet" if existing else "Oprettet") + note


def activate(cfg, release):
    """Skifter current atomisk til versionen."""
    link = os.path.join(cfg["app_root"], "current")
    tmp = link + ".new"
    if os.path.lexists(tmp):
        os.remove(tmp)
    os.symlink(release_dir(cfg, release), tmp)
    os.replace(tmp, link)


def apply_units(cfg, templates, state, changes):
    units = render_units(cfg, templates)
    changed = {name for name, content in units.items()
               if changes.write(f"/etc/systemd/system/{name}", content)}
    stale = [u for u in state.get("units", []) if u not in units]
    for name in stale:
        if name.endswith(".timer"):
            run(["systemctl", "disable", "--now", name], check=False)
        path = f"/etc/systemd/system/{name}"
        changes.previous.setdefault(path, read_text(path))
        if os.path.exists(path):
            os.remove(path)
        changes.notes.append(f"fjernet {path}")
    if changed or stale:
        run(["systemctl", "daemon-reload"])
        verify = run(["systemd-analyze", "verify"] + [f"/etc/systemd/system/{n}" for n in sorted(changed)
                                                      if not n.endswith("@.service")], check=False)
        # verify kan også klage over andre programmers enheder. Kun fejl i vores egne stopper udgivelsen.
        own = [line for line in verify.stderr.splitlines() if cfg["service"] in line]
        if verify.returncode != 0 and own:
            raise DeployError("systemd", "systemd-analyze verify fejlede:\n" + "\n".join(own[-20:]))
        if verify.stderr.strip() and not own:
            log("systemd-analyze verify har bemærkninger til andre enheder end Sukker.nu's (ignoreres).")
    return units, changed, stale


def apply_timers(cfg, units, changed):
    notes = []
    for name in sorted(u for u in units if u.endswith(".timer")):
        enabled = run(["systemctl", "is-enabled", name], check=False).stdout.strip() == "enabled"
        active = run(["systemctl", "is-active", name], check=False).stdout.strip() == "active"
        if not enabled or not active:
            run(["systemctl", "enable", "--now", name])
            notes.append(f"{name} aktiveret")
        elif name in changed:
            run(["systemctl", "restart", name])
            notes.append(f"{name} opdateret")
    return ", ".join(notes) or "OK"


def health(cfg, timeout=HEALTH_TIMEOUT):
    """Spørger den nye version direkte via Gunicorns socket. Et 'active' fra systemd er ikke nok (15.5)."""
    socket_path = f"/run/{cfg['service']}/gunicorn.sock"
    deadline = time.time() + timeout
    last = "ingen forbindelse"
    while time.time() < deadline:
        try:
            conn = http.client.HTTPConnection("localhost", timeout=5)
            conn.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            conn.sock.settimeout(5)
            conn.sock.connect(socket_path)
            conn.request("GET", "/health/ready", headers={"Host": cfg["domain"], "X-Forwarded-Proto": "https"})
            response = conn.getresponse()
            body = response.read().decode("utf-8", "replace")
            if response.status == 200 and json.loads(body).get("status") == "ok":
                return True, body
            last = f"HTTP {response.status}: {body[:200]}"
        except (OSError, ValueError) as error:
            last = str(error)
        time.sleep(2)
    return False, last


def restart_service(cfg):
    run(["systemctl", "enable", cfg["service"]], check=False)
    run(["systemctl", "restart", cfg["service"]])


def cleanup(cfg, protected):
    releases_root = os.path.join(cfg["app_root"], "releases")
    removed = releases_to_remove(os.listdir(releases_root), KEEP_RELEASES, protected)
    for name in removed:
        shutil.rmtree(os.path.join(releases_root, name), ignore_errors=True)
    used = {os.path.realpath(os.path.join(releases_root, r, ".venv")) for r in os.listdir(releases_root)}
    venvs_root = os.path.join(cfg["app_root"], "venvs")
    for name in os.listdir(venvs_root):
        path = os.path.join(venvs_root, name)
        if os.path.realpath(path) not in used:
            shutil.rmtree(path, ignore_errors=True)
    return len(removed)


def load_templates(cfg, release):
    directory = os.path.join(release_dir(cfg, release), "deployment", "templates")
    return {name: read_text(os.path.join(directory, name)) for name in os.listdir(directory)}


# --- Kommandoer ------------------------------------------------------------------------------------------


def receive_package(payload):
    """Pakken kommer base64-kodet på stdin og gemmes ved siden af scriptet i den midlertidige mappe."""
    if payload.get("package_b64"):
        path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "sukker.tar.gz")
        with open(path, "wb") as fh:
            fh.write(base64.b64decode(payload.pop("package_b64")))
        payload["package"] = path
    return payload


def apply(payload):
    payload = receive_package(payload)
    cfg = payload["config"]
    changes = Changes()
    steps = {}
    result = {"success": False, "release": None, "restored": None, "changes": changes.notes}
    current_step = "Serverkontrol"

    def step(name, status, detail=""):
        steps[name] = status
        report_step(name, status, detail)

    previous_release = current_release(cfg)
    activated = False
    try:
        lock = acquire_lock(cfg)  # noqa: F841 – holdes til processen slutter
        step("Serverkontrol", "OK", preflight(cfg))
        current_step = "Pakker"
        step("Pakker", ensure_packages(cfg))
        current_step = "Systembruger og mapper"
        step(current_step, ensure_user_and_dirs(cfg, changes))
        current_step = "Miljøkonfiguration"
        env_values, generated, env_changed = ensure_env_file(cfg, payload, changes)
        step(current_step, "Opdateret" if env_changed else "Uændret")
        public_key = ensure_backup_ssh(cfg, env_values.get("SUKKER_BACKUP_REMOTE"))
        if public_key:
            result["backup_public_key"] = public_key
        current_step = "Database"
        step(current_step, ensure_database(cfg, generated))

        state = load_state(cfg)
        current_step = "Version"
        release, new_release = install_release(cfg, payload)
        result["release"] = release
        step(current_step, f"Ny: {release}" if new_release else f"Uændret: {release}")
        current_step = "Python-afhængigheder"
        _, venv_created = ensure_venv(cfg, release, payload["requirements_hash"])
        step(current_step, "Nyt miljø" if venv_created else "OK")
        current_step = "Kodekontrol"
        check_code(cfg, release, env_values)
        step(current_step, "OK")
        current_step = "Migrationer"
        migration_status, backup_note = migrate(cfg, release, env_values)
        if backup_note:
            step("Backup før migration", "OK", backup_note)
        step(current_step, migration_status)
        current_step = "Kommunedata"
        geo_status, geo = load_geo(cfg, release, env_values, state)
        step(current_step, geo_status)

        templates = load_templates(cfg, release)
        current_step = "Nginx-konfiguration"
        write_support_files(cfg, release, templates, payload, changes)
        current_step = "Let's Encrypt"
        step(current_step, ensure_certificate(cfg, templates, changes))
        current_step = "Nginx-konfiguration"
        nginx_changed = apply_nginx(cfg, render(templates["sukker.nginx.conf"], nginx_values(cfg, cert_dir(cfg))), changes)
        step(current_step, "Opdateret og genindlæst" if nginx_changed else "Uændret")

        current_step = "Aktivering"
        if release != previous_release:
            activate(cfg, release)
            activated = True
        step(current_step, "Skiftet" if activated else "Uændret")
        current_step = "systemd"
        units, units_changed, stale = apply_units(cfg, templates, state, changes)
        step(current_step, "Opdateret" if units_changed or stale else "Uændret")
        current_step = "Timere"
        step(current_step, apply_timers(cfg, units, units_changed))

        current_step = "Sukker service"
        service_unit = f"{cfg['service']}.service"
        service_active = run(["systemctl", "is-active", cfg["service"]], check=False).stdout.strip() == "active"
        needs_restart = activated or venv_created or env_changed or service_unit in units_changed or not service_active
        if needs_restart:
            restart_service(cfg)
        step(current_step, "Genstartet" if needs_restart else "Ikke genstartet (ingen ændringer)")

        current_step = "Health-check"
        ok, detail = health(cfg)
        if not ok:
            raise DeployError("Health-check", f"Den nye version svarer ikke korrekt: {detail}")
        step(current_step, "OK")

        # Markerer installationen med miljøets navn, så en senere udgivelse ikke kan forveksle miljøerne.
        changes.write(os.path.join(cfg["app_root"], ".sukker-env"), cfg["env"] + "\n")
        state.update(
            current=release, previous_ok=state.get("current") if activated else state.get("previous_ok"),
            code_hash=payload["code_hash"], requirements_hash=payload["requirements_hash"], geo_hash=geo,
            units=sorted(units), deployed_at=datetime.now().isoformat(timespec="seconds"),
        )
        save_state(cfg, state)
        current_step = "Oprydning"
        removed = cleanup(cfg, {release, state.get("previous_ok")})
        step(current_step, f"{removed} gamle versioner fjernet" if removed else "OK")
        result["success"] = True
    except Exception as error:  # rapporten og gendannelsen skal altid ske, uanset fejltype
        name = error.step if isinstance(error, DeployError) else current_step
        detail = str(error)
        if isinstance(error, subprocess.CalledProcessError):
            detail = f"{' '.join(map(str, error.cmd))[:300]}\n{(error.stderr or error.stdout or '')[-1500:]}"
        step(name, "FEJL", detail)
        result["error"] = {"step": name, "message": detail}
        result["restored"] = restore_after_failure(cfg, changes, previous_release, activated)
    report_result(result)
    return 0 if result["success"] else 1


def restore_after_failure(cfg, changes, previous_release, activated):
    """Gendanner tidligere konfiguration og version, når det kan ske sikkert. Databasen røres ikke (15.6)."""
    try:
        unit_paths = [p for p in changes.previous if p.startswith("/etc/systemd/system/")]
        nginx_paths = [p for p in changes.previous if p.startswith("/etc/nginx/")]
        changes.restore(unit_paths + nginx_paths)
        if unit_paths:
            run(["systemctl", "daemon-reload"], check=False)
        if nginx_paths and run(["nginx", "-t"], check=False).returncode == 0:
            run(["systemctl", "reload", "nginx"], check=False)
        if activated and previous_release:
            activate(cfg, previous_release)
            run(["systemctl", "restart", cfg["service"]], check=False)
        if previous_release:
            ok, _ = health(cfg, timeout=30)
            return {"release": previous_release, "working": ok}
        return {"release": None, "working": False}
    except Exception as error:  # rapporten skal altid komme frem
        return {"release": previous_release, "working": False, "error": str(error)}


def rollback(payload):
    cfg = payload["config"]
    state = load_state(cfg)
    target = state.get("previous_ok")
    if not target or not os.path.isdir(release_dir(cfg, target)):
        report_result({"success": False, "error": {"step": "Rollback", "message": "Ingen tidligere fungerende version."}})
        return 1
    current = current_release(cfg)
    activate(cfg, target)
    restart_service(cfg)
    ok, detail = health(cfg)
    if ok:
        state.update(current=target, previous_ok=current)
        save_state(cfg, state)
    report_step("Rollback", "OK" if ok else "FEJL", f"{current} -> {target}" if ok else detail)
    report_result({"success": ok, "release": target})
    return 0 if ok else 1


def flask_command(payload, args):
    cfg = payload["config"]
    release = current_release(cfg)
    if not release:
        print("Sukker.nu er ikke installeret i dette miljø.")
        return 1
    env_values = parse_env_file(read_text(cfg["env_file"]))
    result = flask(cfg, release, env_values, *args, check=False)
    sys.stdout.write(result.stdout)
    sys.stderr.write(result.stderr)
    return result.returncode


def main(argv):
    if len(argv) < 2 or argv[1] not in {"inspect", "apply", "rollback", "flask"}:
        print(__doc__)
        return 2
    payload = json.loads(sys.stdin.read())
    if argv[1] == "inspect":
        print("@@JSON " + json.dumps(inspect(payload["config"])), flush=True)
        return 0
    if argv[1] == "apply":
        return apply(payload)
    if argv[1] == "rollback":
        return rollback(payload)
    return flask_command(payload, argv[2:])


if __name__ == "__main__":
    sys.exit(main(sys.argv))
