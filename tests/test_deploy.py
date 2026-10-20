"""Tests for udgivelsen (M9): konfiguration, pakke, skabeloner og serverscriptets rene funktioner.

Selve installationen kræver en Ubuntu-server og testes efter tjeklisten i deployment/README.md.
"""
import importlib.util
import os
import re
import tarfile

import pytest

import deploy

spec = importlib.util.spec_from_file_location("server_setup", os.path.join(deploy.ROOT, "deployment", "server_setup.py"))
server_setup = importlib.util.module_from_spec(spec)
spec.loader.exec_module(server_setup)

COMPLETE = {
    "host": "server.example.com", "ssh_user": "deployer", "domain": "sukker.nu", "aliases": ["www.sukker.nu"],
    "alert_email": "drift@example.com", "smtp_host": "smtp.example.com", "smtp_username": "noreply@example.com",
    "backup_age_recipients": ["age1qyqszqgpqyqszqgpqyqszqgpqyqszqgpqyqszqgpqyqszqgpqyqs3290gq"],
    "backup_remote": "u1@u1.your-storagebox.de:sukker/", "backup_remote_port": 23,
}


def environments(**overrides):
    production = {**COMPLETE, **overrides.get("production", {})}
    staging = {**COMPLETE, "domain": "staging.sukker.nu", "aliases": [], "backup_age_recipients": [],
               **overrides.get("staging", {})}
    return {"production": production, "staging": staging}


def templates():
    directory = os.path.join(deploy.ROOT, "deployment", "templates")
    return {name: open(os.path.join(directory, name), encoding="utf-8").read() for name in os.listdir(directory)}


# --- Konfiguration --------------------------------------------------------------------------------------------


def test_committed_environments_file_is_valid_json_without_secrets():
    data = deploy.load_environments()
    assert {"production", "staging", "nogen-staging"} <= set(data)
    assert all(deploy.validate(name, data) == [] for name in data)
    text = open(deploy.ENVIRONMENTS_FILE, encoding="utf-8").read().lower()
    assert "password" not in text and "secret" not in text and "age-secret-key" not in text


def test_incomplete_environment_is_refused():
    errors = deploy.validate("production", environments(production={"host": "", "alert_email": ""}))
    assert any("host mangler" in e for e in errors) and any("alert_email mangler" in e for e in errors)


def test_complete_environments_are_valid():
    envs = environments()
    assert deploy.validate("production", envs) == [] and deploy.validate("staging", envs) == []


def test_production_requires_public_age_keys():
    assert any("backup_age_recipients mangler" in e
               for e in deploy.validate("production", environments(production={"backup_age_recipients": []})))
    private = environments(production={"backup_age_recipients": ["AGE-SECRET-KEY-1ABC"]})
    assert any("aldrig private nøgler" in e for e in deploy.validate("production", private))


def test_other_brands_get_their_own_resources():
    envs = environments()
    envs["nogen-staging"] = {**COMPLETE, "brand": "nogen", "domain": "staging.nogen.nu", "aliases": [],
                             "backup_age_recipients": []}
    envs["nogen"] = {**COMPLETE, "brand": "nogen", "production": True, "domain": "nogen.nu", "aliases": [],
                     "backup_remote": "u1@u1.your-storagebox.de:nogen/"}
    staging, production = deploy.resolve("nogen-staging", envs["nogen-staging"]), deploy.resolve("nogen", envs["nogen"])
    assert (staging["service"], staging["database"], staging["basic_auth"]) == ("nogen-staging", "nogen_staging", True)
    assert (production["service"], production["app_root"], production["backup_enabled"]) == ("nogen", "/opt/nogen", True)
    assert production["env_settings"]["SUKKER_BRAND"] == "nogen" and not production["basic_auth"]
    # Appen kender kun konfigurationerne production og staging (app/config.py)
    assert staging["env_settings"]["SUKKER_ENV"] == "staging" and production["env_settings"]["SUKKER_ENV"] == "production"
    from app.config import CONFIGS
    assert all(deploy.resolve(n, e)["env_settings"]["SUKKER_ENV"] in CONFIGS for n, e in envs.items())
    assert all(deploy.validate(name, envs) == [] for name in envs)
    # Produktion for et andet brand kræver også krypteret backup uden for serveren
    envs["nogen"]["backup_remote"] = ""
    assert any("nogen.backup_remote mangler" in e for e in deploy.validate("nogen", envs))
    # To produktionsmiljøer må ikke dele mappe på backupserveren
    envs["nogen"]["backup_remote"] = COMPLETE["backup_remote"]
    assert any("backup_remote" in e and "deler" in e for e in deploy.validate("nogen", envs))
    envs["nogen"]["brand"] = "findes-ikke"
    assert any("brand er ukendt" in e for e in deploy.validate("nogen", envs))


def test_production_requires_offsite_backup_and_proper_sender():
    errors = deploy.validate("production", environments(production={"backup_remote": "",
                                                                      "mail_from": "Sukker.nu staging <x@sukker.nu>"}))
    assert any("backup_remote mangler" in e for e in errors) and any("mail_from" in e for e in errors)
    assert deploy.validate("staging", environments(staging={"backup_remote": ""})) == []  # kun produktion
    cfg = deploy.resolve("production", environments()["production"])
    assert cfg["env_settings"]["SUKKER_BACKUP_REMOTE_PORT"] == "23"


def test_environments_cannot_share_a_domain():
    envs = environments(staging={"domain": "sukker.nu"})
    assert any("deler" in e for e in deploy.validate("staging", envs))
    envs = environments(staging={"aliases": ["www.sukker.nu"]})
    assert any("deler" in e for e in deploy.validate("staging", envs))


def test_production_and_staging_never_share_resources():
    production = deploy.resolve("production", environments()["production"])
    staging = deploy.resolve("staging", environments()["staging"])
    for key in ("service", "user", "app_root", "env_file", "data_root", "log_dir", "backup_dir", "database",
                "db_app_role", "db_migrator_role", "nginx_site", "acme_root", "id"):
        assert production[key] != staging[key], key
    assert production["service"] == "sukker" and production["app_root"] == "/opt/sukker"
    assert staging["basic_auth"] and not production["basic_auth"]
    assert production["backup_enabled"] and not staging["backup_enabled"]
    assert staging["env_settings"]["SUKKER_ENV"] == "staging"


# --- Pakke ------------------------------------------------------------------------------------------------------


def test_package_contains_the_app_but_never_tests_or_secrets(tmp_path):
    target = tmp_path / "sukker.tar.gz"
    info = deploy.build_package(str(target))
    with tarfile.open(target) as tar:
        names = tar.getnames()
        members = tar.getmembers()
    assert {"wsgi.py", "requirements.txt", "deployment/server_setup.py", "deployment/templates/sukker.service",
            "app/__init__.py", "migrations/env.py"} <= set(names)
    assert any(n.startswith("data/geo/") for n in names)
    assert not any(n.startswith(("tests/", "specs/", "instance/", ".venv/")) or n.endswith((".env", ".pyc"))
                   or "__pycache__" in n for n in names)
    assert all(m.uid == 0 and m.mode in (0o644, 0o755) for m in members)
    assert info["files"] == len(names)


def test_content_hash_detects_changes(tmp_path):
    for rel, text in {"wsgi.py": "a", "requirements.txt": "flask\n", "app/x.py": "1"}.items():
        os.makedirs(tmp_path / os.path.dirname(rel), exist_ok=True)
        (tmp_path / rel).write_text(text)
    files = deploy.package_files(str(tmp_path))
    first = deploy.content_hash(str(tmp_path), files)
    assert deploy.content_hash(str(tmp_path), files) == first
    (tmp_path / "app/x.py").write_text("2")
    assert deploy.content_hash(str(tmp_path), files) != first


# --- Skabeloner -------------------------------------------------------------------------------------------------


@pytest.mark.parametrize("env_name", ["production", "staging"])
def test_all_templates_render_completely(env_name):
    cfg = deploy.resolve(env_name, environments()[env_name])
    t = templates()
    nginx = server_setup.render(t["sukker.nginx.conf"], server_setup.nginx_values(cfg, server_setup.cert_dir(cfg)))
    units = server_setup.render_units(cfg, t)
    for text in [nginx, *units.values()]:
        assert "{{" not in text and "\r" not in text

    assert f"server unix:/run/{cfg['service']}/gunicorn.sock" in nginx
    assert re.search(r"location \^~ /_protected_media/ \{\s+internal;", nginx)
    assert "/.well-known/acme-challenge/" in nginx and "return 301 https://" in nginx
    assert ("auth_basic_user_file" in nginx) == (env_name == "staging")
    assert ("www.sukker.nu" in nginx) == (env_name == "production")

    service = units[f"{cfg['service']}.service"]
    assert "ProtectSystem=strict" in service and f"User={cfg['user']}" in service
    assert f"OnFailure={cfg['service']}-alert@%n.service" in service
    timers = sorted(n for n in units if n.endswith(".timer"))
    expected = ["maintenance", "monitor", "notify"]
    if env_name == "production":
        expected = ["backup"] + expected
    assert timers == [f"{cfg['service']}-{t}.timer" for t in expected]
    assert all(name.startswith(cfg["service"]) for name in units)


def test_unknown_placeholder_is_an_error():
    with pytest.raises(KeyError):
        server_setup.render("hej {{findes_ikke}}", {})


# --- Serverscriptets hjælpefunktioner ---------------------------------------------------------------------------


def test_env_file_roundtrip_with_quotes_and_spaces():
    values = {"SUKKER_MAIL_FROM": 'Sukker.nu <noreply@sukker.nu>', "X": 'med "citat" og \\', "TOM": ""}
    text = server_setup.format_env_file(values, "production")
    assert server_setup.parse_env_file(text) == values


def test_conflicting_nginx_sites_are_detected(tmp_path):
    cfg = deploy.resolve("production", environments()["production"])
    (tmp_path / "andet-site").write_text("server {\n  server_name example.org www.sukker.nu;\n}\n")
    (tmp_path / "uskyldigt").write_text("server {\n  server_name sukker.nu.example.org;\n}\n")
    own = tmp_path / "sukker.nu.conf"
    own.write_text("server { server_name sukker.nu; }")
    conflicts = server_setup.conflicting_sites(cfg, str(own), directories=(str(tmp_path),))
    assert conflicts == [str(tmp_path / "andet-site")]


def test_release_cleanup_protects_current_and_previous():
    releases = ["20261001_100000", "20261002_100000", "20261003_100000", "20261004_100000", "20261005_100000",
                "20261006_100000", "20261007_100000", "20261008_100000.partial"]
    removed = server_setup.releases_to_remove(releases, 5, {"20261001_100000"})
    assert removed == ["20261002_100000", "20261008_100000.partial"]


def test_package_is_received_from_stdin_payload(tmp_path, monkeypatch):
    monkeypatch.setattr(server_setup, "__file__", str(tmp_path / "server_setup.py"))
    payload = server_setup.receive_package({"package_b64": "aGVq", "config": {}})
    assert payload["package"] == str(tmp_path / "sukker.tar.gz") and "package_b64" not in payload
    assert (tmp_path / "sukker.tar.gz").read_bytes() == b"hej"


def test_alembic_revisions_are_parsed():
    output = "INFO  [alembic.runtime.migration] Context impl\nec5aaac6d16d (head)\n"
    assert server_setup.revisions(output) == {"ec5aaac6d16d"}
    assert server_setup.revisions("INFO nothing\n") == set()


# --- Rapport ------------------------------------------------------------------------------------------------------


def test_report_shows_steps_in_order_and_restoration():
    report = deploy.format_report({"Health-check": "FEJL", "SSH-forbindelse": "OK"}, "20261017_120000", False,
                                  restored={"release": "20261016_090000", "working": True},
                                  error={"step": "Health-check", "message": "HTTP 503"})
    assert report.index("SSH-forbindelse") < report.index("Health-check")
    assert "Tidligere version 20261016_090000 kører" in report and "RESULTAT: FEJL" in report


def test_step_collector_parses_protocol_lines(capsys):
    collector = deploy.StepCollector(verbose=False)
    collector('@@STEP {"name": "Database", "status": "OK", "detail": ""}')
    collector('@@RESULT {"success": true, "release": "x"}')
    collector('@@JSON {"installed": false}')
    collector("almindelig logtekst")
    assert collector.steps == {"Database": "OK"} and collector.result["success"] and collector.inspect == {"installed": False}
    assert "almindelig logtekst" not in capsys.readouterr().out
