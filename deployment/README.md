# Drift af Sukker.nu

Vejledningen er skrevet, så en anden person med de nødvendige adgange kan udgive, overvåge og
genetablere Sukker.nu, hvis udvikleren ikke er tilgængelig (specifikation 14.8).

## Overblik

| Ressource | Produktion | Staging |
|---|---|---|
| Domæne | sukker.nu (+ www) | staging.sukker.nu (kræver adgangskode) |
| Applikation | `/opt/sukker/releases/<version>`, aktiv: `/opt/sukker/current` | `/opt/sukker-staging/...` |
| Python-miljøer | `/opt/sukker/venvs/<hash>` (ét pr. sæt afhængigheder) | `/opt/sukker-staging/venvs/` |
| Konfiguration og hemmeligheder | `/etc/sukker/production.env` (0640 root:sukker) | `/etc/sukker/staging.env` |
| Billeder og beviser | `/var/lib/sukker/{public,private,evidence}` | `/var/lib/sukker-staging/` |
| Backup | `/var/backups/sukker` (+ kopi uden for serveren) | ingen |
| Nginx-logge | `/var/log/sukker/` (JSON, uden IP-adresser) | `/var/log/sukker-staging/` |
| Database | `sukker`, roller `sukker_app` og `sukker_migrator` | `sukker_staging`, `sukker_staging_app`, ... |
| systemd | `sukker.service`, `sukker-{maintenance,backup,monitor}.timer`, `sukker-alert@.service` | `sukker-staging*` |
| Nginx-site | `/etc/nginx/sites-available/sukker.nu.conf` | `.../staging.sukker.nu.conf` |

Navnene udledes af miljøets navn i `deploy.py`. Miljøerne kan derfor ikke dele filer, database eller
service, og `deploy.py` afviser en konfiguration, hvor de deler domæne.

## Før første udgivelse

1. **Server:** Ubuntu med PostgreSQL kørende. Din bruger skal have sudo-rettigheder, og din SSH-nøgle
   skal ligge i `~/.ssh/authorized_keys` på serveren. Manglende pakker (nginx, certbot, python3-venv,
   postgresql-client, rsync, age) installeres af scriptet.
2. **DNS:** `sukker.nu` og `www.sukker.nu` (og `staging.sukker.nu`) peger på serveren. Port 80 og 443 er åbne.
3. **Backupnøgle (produktion):** På din egen maskine, ikke på serveren. På Windows installeres age med
   `winget install FiloSottile.age` (åbn derefter en ny terminal):
   ```
   age-keygen -o sukker-backup.key
   ```
   Den offentlige nøgle (`age1...`, står også i filen) skrives i `backup_age_recipients`. Den private nøgle
   (hele filen) opbevares sikkert uden for serveren, mindst to steder: fx i en passwordmanager og på en
   USB-nøgle eller papir i et skab. Uden den kan ingen backup gendannes, og serveren har den aldrig.
4. **Backup uden for serveren:** Produktion og staging kører på samme server, så uden en kopi et andet sted
   mistes alt, hvis serveren går tabt. Brug fx en Hetzner Storage Box i EU (rsync over SSH på port 23):
   - Slå "SSH-support" og automatiske **snapshots** til (fx dagligt, 14 dage). Snapshots er beskyttelsen
     mod, at en angriber med adgang til serveren også sletter kopierne.
   - Sæt `backup_remote` til fx `u123456@u123456.your-storagebox.de:sukker/` og `backup_remote_port` til `23`.
     Opret mappen `sukker` på boksen.
   - Efter første udgivelse viser rapporten servicebrugerens offentlige SSH-nøgle. Læg den i
     `.ssh/authorized_keys` på boksen (Storage Box: via `ssh-copy-id -p 23 -s u123456@...` fra din egen maskine
     med nøglen i en fil, eller i webkonsollen).
   - Kontrollér første kopi: `python deploy.py --run "sukker backup"` og se `offsite: ok` i
     `status.json` (eller vent på næste nat). Overvågningen sender en alarm, hvis kopien fejler.

   `deploy.py` nægter at udgive produktion uden `backup_age_recipients` og `backup_remote`.
5. **Udfyld `deployment/environments.json`:** host, ssh_user, alert_email (den navngivne ansvarlige for
   alarmer), smtp_host, smtp_username og virksomhedsoplysninger. Ingen hemmeligheder i filen.

## Udgivelse

```
python deploy.py --env staging        # først staging
python deploy.py                       # derefter produktion (spørger om bekræftelse)
```

Kør kommandoen i en almindelig terminal, så du kan taste passwords. En udgivelse bruger kun fire
SSH-forbindelser, så serverens firewall-begrænsning (fx `ufw limit`) ikke afbryder den.

Scriptet kører testene, bygger en pakke, forbinder med SSH, beder om dit sudo-password (skjult, gemmes
aldrig) og kører `deployment/server_setup.py` på serveren. Mangler en hemmelighed (SMTP-passwordet eller
staging-adgangskoden), spørges der skjult om den, og den gemmes kun i miljøfilen på serveren.

Forløb: serverkontrol → pakker → bruger og mapper → miljøfil → database → ny versionsmappe →
Python-miljø → kodekontrol → backup før migration → migrationer → kommunedata → Nginx og HTTPS →
skift af `current` → systemd og timere → genstart → health-check → HTTPS-kontrol udefra → rapport.

Kører du scriptet igen uden ændringer, genstartes intet og databasen røres ikke.

### Andre kommandoer

```
python deploy.py --env staging --dry-run                 # vis status, ændr intet
python deploy.py --rollback                              # tilbage til seneste fungerende version
python deploy.py --run "sukker set-role dig@example.com admin"
python deploy.py --env staging --run "sukker create-test-accounts"
python deploy.py --set-secret SUKKER_SMTP_PASSWORD       # skift en hemmelighed
```

## Fejl under udgivelse

- Fejler et trin før skiftet af `current`, kører den gamle version videre uberørt.
- Fejler health-check efter skiftet, skifter scriptet tilbage til den tidligere version, gendanner
  systemd- og Nginx-filerne og genstarter. Rapporten viser, om den tidligere version svarer korrekt.
- Databasen rulles aldrig tilbage automatisk, da det kan koste nye brugerdata (specifikation 15.6).
  Migrationer skal derfor være bagudkompatible, så den tidligere version kan køre mod det nye skema.
- Før hver migration tages en backup af databasen: `/var/backups/sukker/sukker-production-*-pre-migrate.tar.age`.
- Et afbrudt forløb kan altid køres igen. Halvfærdige versioner (`*.partial`) ryddes op.

## Baggrundsopgaver og overvågning

| Timer | Hvornår | Opgave |
|---|---|---|
| `sukker-backup.timer` | 02:30 | Krypteret backup af database og billeder, 7 i rotation, kopi uden for serveren |
| `sukker-maintenance.timer` | 03:30 | Sletning efter 12-månedersreglen, udløbne sessioner og links, forældreløse filer |
| `sukker-monitor.timer` | hvert 15. min | Backupstatus, certifikatudløb, diskplads, fejlrate, fejlede sukker-enheder |

Fejler en opgave, sender `sukker-alert@.service` en mail til `alert_email`. Overvågningen melder samme fejl
højst én gang i døgnet og giver besked, når den er løst. Status på serveren:

```
systemctl list-timers 'sukker*'
journalctl -u sukker -n 100
journalctl -u sukker-backup -n 50
sudo -u sukker cat /var/backups/sukker/status.json
```

Certifikatet fornyes af den certbot-timer, der allerede findes på serveren. Scriptet opretter ingen ny.
Ved fornyelse genindlæses Nginx (`--deploy-hook`).

## Gendannelse (restore)

RTO: 15 minutter ved nedbrud eller fejlet udgivelse, 4 timer ved datafejl, 24 timer ved tab af serveren,
regnet fra den ansvarlige kan gå i gang (specifikation 14.8).

**Applikationen er nede eller en udgivelse fejlede:** `python deploy.py --rollback`. Nginx viser imens en
vedligeholdelsesside.

**Ødelagte data på en server, der virker** (eller ny server – kør så først `python deploy.py`, så alt er installeret):

1. Hent den nyeste backup (fra `/var/backups/sukker` eller backupserveren) og dekrypter den på en maskine
   med den private nøgle:
   ```
   age -d -i sukker-backup.key sukker-production-<tid>-nightly.tar.age > backup.tar
   tar -xf backup.tar          # manifest.json, database.dump, media.tar
   ```
2. Kopier `database.dump` og `media.tar` til serveren, fx til `/tmp/restore/`.
3. Stop applikationen og timerne:
   ```
   sudo systemctl stop sukker sukker-backup.timer sukker-maintenance.timer
   ```
4. Gendan databasen som migrator (passwordet står i `/etc/sukker/production.env`):
   ```
   sudo -u sukker pg_restore --clean --if-exists --no-owner --role=sukker_migrator \
       -h localhost -U sukker_migrator -d sukker /tmp/restore/database.dump
   ```
5. Gendan billederne:
   ```
   sudo -u sukker tar -xf /tmp/restore/media.tar -C /var/lib/sukker
   ```
6. Kontroller, at databaseposter og billedfiler passer sammen:
   ```
   python deploy.py --run "sukker verify-media"
   ```
   Billeder i databasen uden filer listes. Sammenlign med `images_in_database` i `manifest.json`.
7. Start igen og kontroller:
   ```
   sudo systemctl start sukker sukker-backup.timer sukker-maintenance.timer
   python deploy.py --dry-run
   ```
8. Slet `/tmp/restore` og den dekrypterede `backup.tar`.

### Afprøv en backup (hver måned, tager få minutter)

En backup er først god, når den er gendannet. Afprøvningen sker på din egen maskine, ikke på serveren:

1. Hent den nyeste natlige backup, fx fra backupserveren:
   ```
   scp -P 23 u123456@u123456.your-storagebox.de:sukker/sukker-production-<tid>-nightly.tar.age .
   ```
2. Kontrollér filerne (dekryptering, kontrolsummer, databasedump og billedarkiv):
   ```
   .\.venv\Scripts\python.exe -m flask sukker restore-check sukker-production-<tid>-nightly.tar.age --identity sukker-backup.key
   ```
3. Vil du også gendanne databasen: opret en tom database (fx `sukker_restore_test` i pgAdmin) og tilføj
   `--database postgresql://bruger:password@localhost/sukker_restore_test`. Antallet af brugere, profiler,
   billeder, beskeder, matches og sager sammenlignes med manifestet. Slet databasen bagefter.

Kommandoen sletter selv den dekrypterede kopi. Slet også den hentede `.tar.age`-fil, når du er færdig.

Mindst én gang i kvartalet gendannes en backup på en tom server efter trinene ovenfor. Notér den faktiske
tid og juster RTO, hvis den ikke kan overholdes.

| Dato | Backup | Afprøvning | Resultat |
|---|---|---|---|
| | | | |

## Acceptkriterier (specifikation 15.8)

Udgivelsesløsningen er først færdig, når scenarierne er afprøvet på en rigtig server:

- [x] Installation på en server uden Sukker.nu (staging, 17-10-2026)
- [x] Gentagen kørsel uden ændringer (ingen genstart, ingen databaseændringer) (staging, 17-10-2026)
- [ ] Ændret Flask-kode (ny version og genstart)
- [ ] Nye Python-afhængigheder (nyt miljø under `/opt/sukker/venvs`)
- [ ] Databasemigration (med backup før)
- [ ] Ændret Nginx-konfiguration (validering og reload)
- [ ] Ændret systemd-service (daemon-reload og genstart)
- [ ] Ny eller ændret timer
- [x] Genbrug af eksisterende certifikat (staging, 17-10-2026)
- [x] Afbrudt udgivelse og genkørsel (staging, 17-10-2026: første forsøg stoppede efter databasen, genkørsel fuldførte)
- [ ] Fejlende applikationsstart, hvor den tidligere version genetableres
- [ ] Andre applikationers services, skemaer og filer er uændrede (sammenlign `systemctl list-units`,
      `\dn` i psql og `/etc/nginx/sites-enabled` før og efter; rapporten lister alle ændrede filer)
- [ ] Udgivelse til staging uden påvirkning af produktion
- [ ] Kopi uden for serveren (`offsite: ok` i `status.json`)
- [ ] `restore-check` af en natlig backup uden fejl
- [ ] Gendannelse af en backup med `verify-media` uden fejl
