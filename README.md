# Sukker.nu

Mobilorienteret datingside bygget med Flask og PostgreSQL. Specifikationen ligger i
[specs/sukker.nu_v1.0.1.docx](specs/sukker.nu_v1.0.1.docx).

## Status

| Milepæl | Indhold | Status |
|---|---|---|
| M0 Fundament | Flask-app, konfiguration, PostgreSQL-skema `sukker`, migrationer, tests | Færdig |
| M1 Konto | Oprettelse, e-mailverificering, login, glemt password, sessioner, 3 måneders Premium | Færdig |
| M2 Profiler og billeder | Profilfelter, skjulte felter, synlighed, pause, forhåndsvisning, upload med omkodning og beskyttet levering | Færdig |
| M3 Udforsk | Kommune ud fra postnummer, omtrentlig afstand, sortering efter nærhed, Premium-filtre, bundnavigation | Færdig, med DAGI-data i `data/geo/` |
| M4 Likes og matches | Likes, matches, hvem har liket dig (Premium), fjern match, match igen, match-loft | Færdig |
| M5 Beskeder | Samtaler mellem matches, låsning af Free/Free, pause, skjult historik ved fjernet match, slet for mig, arkiv, slet konto og "Slettet bruger" | Færdig |
| M6–M9 | Tryghed og moderation, administration, offentlige sider, drift | Ikke startet |

## Kom i gang lokalt (Windows)

Forudsætninger: Python 3.14 og PostgreSQL 18.

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
copy .env.example .env        # udfyld passwords og SUKKER_SECRET_KEY
powershell -ExecutionPolicy Bypass -File .\scripts\setup_local_db.ps1   # spørger efter postgres-password
# Kender du ikke postgres-passwordet, så kør i stedet som administrator:
#   scripts\reset_postgres_local.ps1  (sætter et nyt postgres-password og gendanner pg_hba.conf bagefter)
.\.venv\Scripts\flask.exe db upgrade
.\.venv\Scripts\flask.exe run
```

Mails sendes ikke lokalt. De gemmes i `instance/outbox/`, hvor du finder bekræftelses- og nulstillingslinks.
Uploadede billeder gemmes lokalt i `instance/media/`. I produktion ligger de i `/var/lib/sukker` og leveres af
Nginx via `X-Accel-Redirect`, efter at Flask har kontrolleret adgangen.

## Tests

```powershell
.\.venv\Scripts\python.exe -m pytest
```

Testene bruger databasen `sukker_test`, som tømmes ved hver kørsel.

## Database

- Applikationen kører som `sukker_app` med kun læse- og skriverettigheder.
- Migrationer kører som `sukker_migrator`, som ejer skemaet `sukker`.
- Ny migration: `flask db migrate -m "beskrivelse"`. Gennemgå filen i `migrations/versions/` før `flask db upgrade`.

## Kommuner og afstande

Afstande beregnes fra kommune til kommune ud fra DAGI-data fra Klimadatastyrelsen via
[Datafordeleren](https://datafordeler.dk). DAWA lukkede 1. oktober 2026, så data hentes med en API-nøgle.

1. Opret en bruger og et IT-system med API-nøgle i Datafordelerens administration.
2. Sæt `SUKKER_DATAFORDELER_APIKEY` i `.env`.
3. Byg CSV-filerne i `data/geo/` (kræver `shapely` fra requirements-dev.txt) og indlæs dem:

```powershell
.\.venv\Scripts\flask.exe sukker build-geo
.\.venv\Scripts\flask.exe sukker load-geo
```

CSV-filerne kan committes, så serveren kun behøver `load-geo`. Indeholder data fra Klimadatastyrelsen (DAGI).
Uden kommunedata kører appen stadig, men uden kommune og afstand.

## Administration fra kommandolinjen

Indtil administrationspanelet findes (M7):

```powershell
.\.venv\Scripts\flask.exe sukker grant-premium anna@example.test --months 3 --note "Testbruger"
```
