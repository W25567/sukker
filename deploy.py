"""Udgiver Sukker.nu fra Windows til Ubuntu-serveren (specifikation 15).

    python deploy.py                       udgiv til produktion (spørger om bekræftelse)
    python deploy.py --env staging         udgiv til staging
    python deploy.py --env staging --dry-run
                                           vis hvad der ville ske, uden at ændre noget
    python deploy.py --rollback            skift tilbage til den seneste fungerende version
    python deploy.py --env staging --run "sukker create-test-accounts"
                                           kør en flask-kommando på serveren
    python deploy.py --set-secret SUKKER_SMTP_PASSWORD
                                           sæt eller skift en hemmelighed (tastes skjult)

Forudsætninger: SSH-nøgle til serveren, sudo-rettigheder, DNS for domænet og udfyldt
deployment/environments.json. Selve installationen udføres af deployment/server_setup.py på serveren.
"""
import argparse
import base64
import getpass
import hashlib
import json
import os
import shlex
import ssl
import subprocess
import sys
import tarfile
import tempfile
import urllib.error
import urllib.request
from datetime import datetime

ROOT = os.path.dirname(os.path.abspath(__file__))
ENVIRONMENTS_FILE = os.path.join(ROOT, "deployment", "environments.json")
SERVER_SCRIPT = os.path.join(ROOT, "deployment", "server_setup.py")

# Det, der kommer med i en udgivelse. Tests, specifikationer, .env og lokale data kommer aldrig med.
PACKAGE_ITEMS = ["app", "migrations", "deployment", "data/geo", "wsgi.py", "requirements.txt"]
EXCLUDE_DIRS = {"__pycache__", ".pytest_cache"}
EXCLUDE_SUFFIXES = (".pyc", ".pyo")

REPORT_ORDER = ["SSH-forbindelse", "Sudo-adgang", "Serverkontrol", "Pakker", "Systembruger og mapper",
                "Miljøkonfiguration", "Database", "Version", "Python-afhængigheder", "Kodekontrol",
                "Backup før migration", "Migrationer", "Kommunedata", "Nginx-konfiguration", "Let's Encrypt",
                "Aktivering", "systemd", "Timere", "Sukker service", "Health-check", "HTTPS", "Oprydning"]


CONNECTION_HINT = ("Kontrollér at din SSH-nøgle er installeret på serveren. Ved 'timed out' kan serverens "
                   "firewall midlertidigt have spærret efter mange forbindelser – vent et minut og prøv igen.")


class DeployAbort(RuntimeError):
    pass


# --- Konfiguration ------------------------------------------------------------------------------------------


# Brands (app/brands): navnet bruges i Nginx' adgangskode-dialog og på vedligeholdelsessiden.
BRAND_NAMES = {"sukker": ("Sukker", ".nu"), "nogen": ("nogen", ".nu")}


def is_production(env_name, raw):
    """Et miljø er produktion, hvis det hedder "production" eller har "production": true (fx Nogen.nu)."""
    return bool(raw.get("production", env_name == "production"))


def service_name(env_name, raw):
    """Sukker.nu: "sukker" og "sukker-staging". Andre brands: "<brand>" og "<brand>-staging"."""
    brand = raw.get("brand", "sukker")
    if brand == "sukker":
        return "sukker" if env_name == "production" else f"sukker-{env_name}"
    if is_production(env_name, raw):
        return brand
    return env_name if env_name.startswith(f"{brand}-") else f"{brand}-{env_name}"


def resolve(env_name, raw):
    """Alle navne og stier udledes af miljøets navn og brand, så miljøerne aldrig kan dele ressourcer."""
    brand = raw.get("brand", "sukker")
    if brand not in BRAND_NAMES:
        raise DeployAbort(f"{env_name}.brand er ukendt: {brand!r}. Findes: {', '.join(BRAND_NAMES)}.")
    production = is_production(env_name, raw)
    name = service_name(env_name, raw)
    ident = name.replace("-", "_")
    cfg = {
        "env": env_name,
        "brand": brand,
        "brand_main": BRAND_NAMES[brand][0],
        "brand_tld": BRAND_NAMES[brand][1],
        "production": production,
        "id": ident,
        "service": name,
        "user": name,
        "app_root": f"/opt/{name}",
        "env_file": f"/etc/sukker/{env_name}.env",
        "htpasswd_file": f"/etc/sukker/{env_name}.htpasswd",
        "data_root": f"/var/lib/{name}",
        "log_dir": f"/var/log/{name}",
        "backup_dir": f"/var/backups/{name}",
        "acme_root": f"/var/www/{name}-acme",
        "database": ident,
        "db_app_role": f"{ident}_app",
        "db_migrator_role": f"{ident}_migrator",
        "nginx_site": f"/etc/nginx/sites-available/{raw['domain']}.conf",
        "domain": raw["domain"],
        "aliases": list(raw.get("aliases", [])),
        "basic_auth": not production,
        "backup_enabled": production,
        "letsencrypt_email": raw.get("letsencrypt_email") or raw.get("alert_email", ""),
        "smtp_username": raw.get("smtp_username", ""),
        "backup_age_recipients": list(raw.get("backup_age_recipients", [])),
    }
    cfg["env_settings"] = {
        "SUKKER_ENV": env_name,
        "SUKKER_BRAND": brand,
        "SUKKER_BASE_URL": f"https://{raw['domain']}",
        "SUKKER_SITE_DOMAIN": raw["domain"],
        "SUKKER_SERVICE_NAME": name,
        "SUKKER_MEDIA_ROOT": cfg["data_root"],
        "SUKKER_MEDIA_X_ACCEL_PREFIX": "/_protected_media/",
        "SUKKER_INSTANCE_PATH": f"{cfg['data_root']}/instance",
        "SUKKER_BACKUP_DIR": cfg["backup_dir"],
        "SUKKER_ACCESS_LOG": f"{cfg['log_dir']}/access.log",
        "SUKKER_MAIL_BACKEND": "smtp" if raw.get("smtp_host") else "console",
        "SUKKER_MAIL_FROM": raw.get("mail_from", ""),
        "SUKKER_SMTP_HOST": raw.get("smtp_host", ""),
        "SUKKER_SMTP_PORT": str(raw.get("smtp_port", 587)),
        "SUKKER_SMTP_USERNAME": raw.get("smtp_username", ""),
        "SUKKER_CONTACT_EMAIL": raw.get("contact_email", ""),
        "SUKKER_COMPANY_NAME": raw.get("company_name", ""),
        "SUKKER_COMPANY_CVR": raw.get("company_cvr", ""),
        "SUKKER_COMPANY_ADDRESS": raw.get("company_address", ""),
        "SUKKER_ALERT_EMAIL": raw.get("alert_email", ""),
        "SUKKER_BACKUP_AGE_RECIPIENTS": " ".join(raw.get("backup_age_recipients", [])),
        "SUKKER_BACKUP_REMOTE": raw.get("backup_remote", ""),
        "SUKKER_BACKUP_REMOTE_PORT": str(raw.get("backup_remote_port", 22)),
        "SUKKER_GUNICORN_WORKERS": str(raw.get("gunicorn_workers", 3)),
    }
    return cfg


def load_environments(path=ENVIRONMENTS_FILE):
    with open(path, encoding="utf-8") as fh:
        data = json.load(fh)
    return {name: value for name, value in data.items() if not name.startswith("_")}


def validate(env_name, environments):
    """Fejl, der skal rettes, før der udgives. Tjekker også, at miljøerne ikke deler noget."""
    if env_name not in environments:
        return [f"Ukendt miljø {env_name!r}. Findes: {', '.join(environments)}."]
    raw = environments[env_name]
    errors = []
    for key in ("host", "ssh_user", "domain", "alert_email", "smtp_host", "smtp_username"):
        if not str(raw.get(key) or "").strip():
            errors.append(f"{env_name}.{key} mangler i deployment/environments.json.")
    if not (raw.get("letsencrypt_email") or raw.get("alert_email")):
        errors.append(f"{env_name}.letsencrypt_email (eller alert_email) mangler.")
    if raw.get("brand", "sukker") not in BRAND_NAMES:
        return errors + [f"{env_name}.brand er ukendt: {raw.get('brand')!r}. Findes: {', '.join(BRAND_NAMES)}."]
    if is_production(env_name, raw):
        if not raw.get("backup_age_recipients"):
            errors.append(f"{env_name}.backup_age_recipients mangler: backup skal være krypteret (specifikation 14.8).")
        if any(not r.startswith("age1") for r in raw.get("backup_age_recipients", [])):
            errors.append("backup_age_recipients skal være offentlige age-nøgler (age1...), aldrig private nøgler.")
        if not raw.get("backup_remote"):
            errors.append(f"{env_name}.backup_remote mangler: backuppen skal også ligge uden for serveren, ellers "
                          "mistes både data og backup, hvis serveren går tabt (specifikation 14.8).")
        if "staging" in str(raw.get("mail_from", "")).lower():
            errors.append(f"{env_name}.mail_from indeholder \"staging\". Brugerne vil se det som afsender.")
    mine = resolve(env_name, raw) if raw.get("domain") else None
    for other_name, other_raw in environments.items():
        if other_name == env_name or not other_raw.get("domain") or not mine:
            continue
        other = resolve(other_name, other_raw)
        shared = [k for k in ("domain", "app_root", "data_root", "database", "service", "env_file", "nginx_site",
                              "backup_dir")
                  if mine[k] == other[k]]
        # To miljøer må heller ikke tage backup til samme mappe uden for serveren (--delete ville slette den andens).
        if (mine["backup_enabled"] and other["backup_enabled"] and raw.get("backup_remote")
                and raw.get("backup_remote") == other_raw.get("backup_remote")):
            shared.append("backup_remote")
        overlap = set([mine["domain"]] + mine["aliases"]) & set([other["domain"]] + other["aliases"])
        if shared or overlap:
            errors.append(f"{env_name} og {other_name} deler {', '.join(shared) or ', '.join(overlap)}.")
    return errors


# --- Pakke ---------------------------------------------------------------------------------------------------


def package_files(root=ROOT):
    files = []
    for item in PACKAGE_ITEMS:
        path = os.path.join(root, item)
        if os.path.isfile(path):
            files.append(item.replace("\\", "/"))
            continue
        for directory, dirs, names in os.walk(path):
            dirs[:] = sorted(d for d in dirs if d not in EXCLUDE_DIRS)
            for name in sorted(names):
                if name.endswith(EXCLUDE_SUFFIXES):
                    continue
                files.append(os.path.relpath(os.path.join(directory, name), root).replace("\\", "/"))
    return sorted(files)


def content_hash(root, files):
    """Ændringsdetektering: samme filer med samme indhold giver samme hash (specifikation 15.1)."""
    digest = hashlib.sha256()
    for rel in files:
        digest.update(rel.encode() + b"\0")
        with open(os.path.join(root, rel), "rb") as fh:
            digest.update(hashlib.sha256(fh.read()).digest())
    return digest.hexdigest()


def requirements_hash(root=ROOT):
    with open(os.path.join(root, "requirements.txt"), "rb") as fh:
        return hashlib.sha256(fh.read().replace(b"\r\n", b"\n")).hexdigest()


def build_package(target, root=ROOT):
    files = package_files(root)

    def normalize(info):
        info.uid = info.gid = 0
        info.uname = info.gname = "root"
        info.mode = 0o755 if info.isdir() else 0o644
        return info

    with tarfile.open(target, "w:gz") as tar:
        for rel in files:
            tar.add(os.path.join(root, rel), arcname=rel, filter=normalize, recursive=False)
    return {"code_hash": content_hash(root, files), "requirements_hash": requirements_hash(root), "files": len(files)}


# --- SSH -----------------------------------------------------------------------------------------------------


class Remote:
    def __init__(self, raw):
        self.target = f"{raw['ssh_user']}@{raw['host']}"
        self.port = str(raw.get("ssh_port", 22))
        self.options = ["-o", "BatchMode=yes", "-o", "ConnectTimeout=15", "-o", "ServerAliveInterval=30"]

    def ssh(self, command, **kwargs):
        # -n og en tom stdin: Windows' OpenSSH kan ellers hænge på terminalens stdin, efter kommandoen er færdig.
        return subprocess.run(["ssh", *self.options, "-n", "-p", self.port, self.target, command],
                              stdin=subprocess.DEVNULL, **kwargs)

    def prepare(self, script):
        """Én forbindelse: rydder gamle midlertidige mapper, opretter en ny og lægger server_setup.py i den.

        Udgivelsen bruger kun fire SSH-forbindelser i alt, så en firewall, der begrænser nye forbindelser
        (fx ufw limit: 6 pr. 30 sekunder), ikke afbryder den."""
        remote_cmd = ("find /tmp -maxdepth 1 -name 'sukker-deploy-*' -user \"$(id -un)\" -mmin +10 "
                      "-exec rm -rf {} + 2>/dev/null; "
                      "d=$(mktemp -d /tmp/sukker-deploy-XXXXXXXX) && cat > \"$d/server_setup.py\" && echo \"$d\"")
        with open(script, "rb") as fh:
            result = subprocess.run(["ssh", *self.options, "-p", self.port, self.target, remote_cmd],
                                    input=fh.read(), capture_output=True)
        if result.returncode != 0:
            raise DeployAbort(f"SSH til {self.target} fejlede: {result.stderr.decode(errors='replace').strip()}\n"
                              + CONNECTION_HINT)
        return result.stdout.decode().strip().splitlines()[-1]

    def sudo_setup(self, remote_dir, command, payload, password, on_line, extra_args=()):
        """Kører server_setup.py som root. Passwordet og konfigurationen sendes på stdin, aldrig som argumenter.

        sudo -k glemmer en eventuel gemt adgang, så sudo altid læser passwordet fra den første linje."""
        args = " ".join(shlex.quote(a) for a in extra_args)
        remote_cmd = f"sudo -k -S -p '' python3 {shlex.quote(remote_dir)}/server_setup.py {command} {args}".strip()
        process = subprocess.Popen(["ssh", *self.options, "-p", self.port, self.target, remote_cmd],
                                   stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                   text=True, encoding="utf-8", errors="replace")
        process.stdin.write(password + "\n" + json.dumps(payload))
        process.stdin.close()
        for line in process.stdout:
            on_line(line.rstrip("\n"))
        return process.wait()


# --- Rapport ---------------------------------------------------------------------------------------------------


def format_report(steps, release, success, restored=None, error=None):
    lines = ["", "SUKKER.NU DEPLOY", "=" * 45]
    ordered = [n for n in REPORT_ORDER if n in steps] + [n for n in steps if n not in REPORT_ORDER]
    for name in ordered:
        lines.append(f"{name} ".ljust(29, ".") + f" {steps[name]}")
    if release:
        lines.append(f"Version: {release}")
    if error:
        lines.append(f"Fejl i trinnet {error['step']}:")
        lines += ["  " + line for line in str(error["message"]).splitlines()[-15:]]
    if restored is not None:
        if restored.get("release") is None:
            lines.append("Der var ingen tidligere version at genetablere (første installation). "
                         "Ret fejlen og kør udgivelsen igen – trin, der allerede er gennemført, springes over.")
        elif restored.get("working"):
            lines.append(f"Tidligere version {restored['release']} kører og svarer korrekt.")
        else:
            lines.append("ADVARSEL: Systemet er IKKE genetableret. Se driftsvejledningen (deployment/README.md).")
    lines.append(f"RESULTAT: {'SUCCES' if success else 'FEJL'}")
    lines.append("=" * 45)
    return "\n".join(lines)


def _https_via_address(domain, address):
    """HTTPS mod en bestemt adresse, men med certifikatkontrol for domænet (når lokal DNS ikke kender navnet endnu)."""
    import http.client
    import socket

    context = ssl.create_default_context()
    with context.wrap_socket(socket.create_connection((address, 443), timeout=20), server_hostname=domain) as sock:
        conn = http.client.HTTPConnection(domain)
        conn.sock = sock
        conn.request("GET", "/health/ready", headers={"Host": domain})
        response = conn.getresponse()
        return response.status == 200 and json.loads(response.read()).get("status") == "ok"


def check_https(domain, fallback_address=None):
    """Kontrollerer udefra: gyldigt certifikat, health via Nginx og omdirigering fra http."""
    context = ssl.create_default_context()
    try:
        with urllib.request.urlopen(f"https://{domain}/health/ready", timeout=20, context=context) as resp:
            if json.loads(resp.read()).get("status") != "ok":
                return False, "health svarer ikke ok"
    except urllib.error.URLError as error:
        if isinstance(error.reason, OSError) and "getaddrinfo" in str(error.reason) and fallback_address:
            try:
                ok = _https_via_address(domain, fallback_address)
            except (OSError, ValueError) as inner:
                return False, str(inner)
            return ok, ("OK via serverens adresse (din lokale DNS kender ikke navnet endnu)" if ok
                        else "health svarer ikke ok")
        return False, str(error)
    except (ValueError, OSError) as error:
        return False, str(error)

    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, *args, **kwargs):
            return None

    try:
        urllib.request.build_opener(NoRedirect).open(f"http://{domain}/health", timeout=20)
        return False, "http omdirigerer ikke til https"
    except urllib.error.HTTPError as error:
        if error.code == 301 and error.headers.get("Location", "").startswith(f"https://{domain}/"):
            return True, "OK"
        return False, f"http svarede {error.code}"
    except OSError as error:
        return False, str(error)


# --- Hovedprogram ------------------------------------------------------------------------------------------------


class StepCollector:
    def __init__(self, verbose=True):
        self.steps, self.result, self.inspect, self.verbose = {}, None, None, verbose

    def __call__(self, line):
        if line.startswith("@@STEP "):
            data = json.loads(line[7:])
            self.steps[data["name"]] = data["status"]
            print(f"  {data['name']}: {data['status']}" + (f" ({data['detail']})" if data["detail"]
                                                            and data["status"] != "FEJL" else ""))
        elif line.startswith("@@RESULT "):
            self.result = json.loads(line[9:])
        elif line.startswith("@@JSON "):
            self.inspect = json.loads(line[7:])
        elif self.verbose and line.strip():
            print(line)


def run_tests():
    print("Kører testene først (spring over med --skip-tests) ...")
    result = subprocess.run([sys.executable, "-m", "pytest", "-q", "-x"], cwd=ROOT)
    if result.returncode != 0:
        raise DeployAbort("Testene fejler. Der udgives ikke.")


def ask_secrets(inspect_result, args):
    secrets, staging_auth = {}, None
    names = set(inspect_result["missing_secrets"]) | ({args.set_secret} if args.set_secret else set())
    for name in sorted(names - {"staging_auth"}):
        value = getpass.getpass(f"{name} (vises ikke): ")
        if not value:
            raise DeployAbort(f"{name} er påkrævet.")
        secrets[name] = value
    if "staging_auth" in names:
        print("Staging kræver brugernavn og adgangskode i browseren (specifikation 12.5).")
        user = input("Brugernavn til staging: ").strip()
        password = getpass.getpass("Adgangskode til staging (vises ikke): ")
        if not user or len(password) < 10:
            raise DeployAbort("Brugernavn og en adgangskode på mindst 10 tegn er påkrævet.")
        staging_auth = {"user": user, "password": password}
    return secrets, staging_auth


def main(argv=None):
    parser = argparse.ArgumentParser(description="Udgiv Sukker.nu.", formatter_class=argparse.RawDescriptionHelpFormatter,
                                     epilog=__doc__)
    parser.add_argument("--env", default="production",
                        help="miljøet i deployment/environments.json: production (standard), staging, nogen-staging …")
    parser.add_argument("--yes", action="store_true", help="spring bekræftelsen af produktion over")
    parser.add_argument("--dry-run", action="store_true", help="vis status og hvad der ville ske, uden ændringer")
    parser.add_argument("--skip-tests", action="store_true", help="kør ikke testene før udgivelsen")
    parser.add_argument("--rollback", action="store_true", help="skift tilbage til den seneste fungerende version")
    parser.add_argument("--run", metavar="KOMMANDO", help='kør en flask-kommando på serveren, fx "sukker set-role a@b.dk admin"')
    parser.add_argument("--set-secret", metavar="NAVN", help="sæt eller skift en hemmelighed, fx SUKKER_SMTP_PASSWORD")
    parser.add_argument("--quiet", action="store_true", help="vis kun trin, ikke serverens detaljer")
    args = parser.parse_args(argv)

    environments = load_environments()
    errors = validate(args.env, environments)
    if errors:
        print("deployment/environments.json skal rettes først:")
        for error in errors:
            print(f"  - {error}")
        return 2
    raw = environments[args.env]
    cfg = resolve(args.env, raw)

    if cfg["production"] and not (args.yes or args.dry_run or args.run):
        answer = input(f"Udgiv til PRODUKTION ({cfg['domain']}) på {raw['host']}? Skriv ja: ").strip().lower()
        if answer != "ja":
            print("Afbrudt.")
            return 1

    steps = {}
    remote = Remote(raw)
    remote_dir = None
    collector = StepCollector(verbose=not args.quiet)
    try:
        if not (args.run or args.rollback or args.dry_run or args.skip_tests):
            run_tests()
        remote_dir = remote.prepare(SERVER_SCRIPT)
        steps["SSH-forbindelse"] = "OK"
        print("SSH-forbindelse OK")
        password = getpass.getpass(f"sudo-password for {raw['ssh_user']}@{raw['host']} (vises ikke): ")

        if remote.sudo_setup(remote_dir, "inspect", {"config": cfg}, password, collector) != 0 or not collector.inspect:
            raise DeployAbort("sudo eller serverkontrollen fejlede. Er passwordet rigtigt, og har brugeren sudo-rettigheder?")
        steps["Sudo-adgang"] = "OK"
        state = collector.inspect
        print(f"Sudo-adgang OK. Installeret: {'ja' if state['installed'] else 'nej'}, "
              f"aktiv version: {state['current_release'] or 'ingen'}")

        if args.run:
            code = remote.sudo_setup(remote_dir, "flask", {"config": cfg}, password, print, shlex.split(args.run))
            return code
        if args.rollback:
            code = remote.sudo_setup(remote_dir, "rollback", {"config": cfg}, password, collector)
            steps.update(collector.steps)
            result = collector.result or {}
            print(format_report(steps, result.get("release"), bool(result.get("success")), error=result.get("error")))
            return code

        with tempfile.TemporaryDirectory() as tmp:
            package = os.path.join(tmp, "sukker.tar.gz")
            info = build_package(package)
            unchanged = state.get("code_hash") == info["code_hash"]
            print(f"Pakke: {info['files']} filer. Kode {'uændret' if unchanged else 'ændret'} i forhold til serveren.")
            if args.dry_run:
                print(json.dumps(state, indent=2, ensure_ascii=False))
                print("Tørløb: intet er ændret.")
                return 0
            secrets, staging_auth = ask_secrets(state, args)
            with open(package, "rb") as fh:
                package_b64 = base64.b64encode(fh.read()).decode("ascii")

        # Pakken sendes med på stdin i samme forbindelse som installationen (færre SSH-forbindelser).
        payload = {
            "config": cfg,
            "package_b64": package_b64,
            "release": datetime.now().strftime("%Y%m%d_%H%M%S"),
            "code_hash": info["code_hash"],
            "requirements_hash": info["requirements_hash"],
            "secrets": secrets,
            "staging_auth": staging_auth,
        }
        print("Udgiver ...")
        code = remote.sudo_setup(remote_dir, "apply", payload, password, collector)
        steps.update(collector.steps)
        result = collector.result or {"success": False, "error": {
            "step": "Server", "message": f"afsluttede med kode {code}" + (f". {CONNECTION_HINT}" if code == 255 else "")}}
        success = bool(result.get("success"))
        if success:
            ok, detail = check_https(cfg["domain"], raw["host"])
            steps["HTTPS"] = (detail if detail.startswith("OK") else "OK") if ok else f"FEJL: {detail}"
            success = ok
        print(format_report(steps, result.get("release"), success, result.get("restored"), result.get("error")))
        if result.get("changes"):
            print("Ændrede filer på serveren: " + ", ".join(result["changes"]))
        if result.get("backup_public_key"):
            print("Backupnøgle (skal stå i authorized_keys på backupserveren):\n  " + result["backup_public_key"])
        return 0 if success else 1
    except DeployAbort as error:
        print(format_report(steps, None, False, error={"step": "Forberedelse", "message": str(error)}))
        return 1
    finally:
        if remote_dir:
            remote.ssh(f"rm -rf {shlex.quote(remote_dir)}", capture_output=True)


if __name__ == "__main__":
    sys.exit(main())
