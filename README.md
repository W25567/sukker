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
| M6 Tryghed og moderation | Skjul, blokér, anmeld med kopi, moderationssager, afgørelser, begrænsning og suspendering, klagevej, moderatoradgang med uændrelig revisionslog | Færdig |
| M7 Rettigheder og administration | Central rettighedsmodel (niveau → funktioner, global slukning, individuelle tildelinger med udløb), admin-indstillinger, brugere, abonnementer, roller og status, dashboard uden testkonti, testkonti pr. niveau, uændrelig administrationslog | Færdig |
| M8 Offentlige sider og SEO | Forside, Sådan virker det, Tryghed, Premium, FAQ, Om og kontakt, vilkår, privatlivspolitik og cookiepolitik. Metadata, canonical, Open Graph, JSON-LD, sitemap.xml og robots.txt. Vilkårsversioner med ny accept, hent egne data | Færdig (teksterne er udkast til juridisk gennemgang) |
| M9 Drift | `deploy.py` og `deployment/server_setup.py` (idempotent udgivelse med health-check og rollback), staging, systemd-timere, krypteret backup med rotation og kopi uden for serveren, sletning efter 12-månedersreglen, overvågning med alarmer | Bygget og testet lokalt. Mangler afprøvning på en rigtig server (tjekliste i [deployment/README.md](deployment/README.md)) |

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
.\.venv\Scripts\python.exe -m flask run
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

Det meste klares i administrationspanelet under `/admin/`. Den første administrator skal dog oprettes fra kommandolinjen:

```powershell
.\.venv\Scripts\python.exe -m flask sukker set-role anna@example.com admin   # user, moderator eller admin
.\.venv\Scripts\python.exe -m flask sukker grant-premium anna@example.com --months 3 --note "Testbruger"
.\.venv\Scripts\python.exe -m flask sukker create-test-accounts   # Test_free, Test_premium, Test_premiumplus
```

Moderator og administrator er adskilte roller. Moderatorer behandler sager under `/moderation/`.
Administratorer ser moderatorernes revisionslog under `/moderation/log` og administrationsloggen under `/admin/log`.
Testkonti kan kun se andre testkonti og tæller ikke med i målingerne. Passwordene vises kun, når kontiene oprettes.

## Offentlige sider og SEO

De offentlige sider står i `app/pages/content.py` (titel, beskrivelse og senest ændret). Registret styrer
metadata, `sitemap.xml` og sidefoden. Alt andet sendes med `X-Robots-Tag: noindex, nofollow`, og `robots.txt`
udleder selv stierne bag login fra URL-reglerne. En ny offentlig side kræver `@public_page` på view'et og en
linje i registret.

- **Vilkår og privatlivspolitik:** Virksomhedsoplysninger og kontaktmail sættes i `.env` (se `.env.example`).
- **Pre-release:** Indtil `SUKKER_LAUNCH_AT` (1. november 2026) viser siden en nedtælling, og besøgende skal acceptere,
  at platformen endnu ikke er officielt i luften (`app/pages/prerelease.py`). Efter tidspunktet forsvinder det af sig selv.
- **Ændres vilkårene væsentligt**, hæves `TERMS_VERSION` i `app/pages/legal.py`. Alle skal så acceptere igen ved
  næste besøg. Konti, der fandtes før M8, skal acceptere de første vilkår.
- **Logo og ikoner** ligger alle i `app/static/images`. Favicons, app-ikoner og `site.webmanifest` kommer fra
  RealFaviconGenerator, og originalerne af logoet ligger i `images/full/`. De afledte filer (`logo-64.webp`,
  `full-logo-280.webp`, en mørk variant og delingsbilledet `og.jpg`) laves med
  `python scripts\make_brand_images.py`. Brugerbilleder indgår aldrig.
- **Staging**: `SUKKER_NOINDEX_ALL=1` giver noindex på alt og `Disallow: /` i robots.txt.

Nginx-sitet (M9) sender http og www videre til `https://sukker.nu` med 301, komprimerer med gzip og cacher
statiske filer i et år. Det er sikkert, fordi alle statiske URL'er har `?v=<indholdshash>`. Staging kræver
adgangskode. Google Search Console og evt. cookiefri besøgsstatistik sættes op ved lancering.

## Drift og udgivelse

Udgivelse, backup, overvågning og gendannelse er beskrevet i [deployment/README.md](deployment/README.md).

```powershell
python deploy.py --env staging     # udgiv til staging
python deploy.py                    # udgiv til produktion
```

Baggrundsopgaverne kan også køres lokalt:

```powershell
.\.venv\Scripts\python.exe -m flask sukker maintenance    # sletning efter opbevaringsreglen og oprydning
.\.venv\Scripts\python.exe -m flask sukker monitor        # overvågning (alarmer kræver SUKKER_ALERT_EMAIL)
.\.venv\Scripts\python.exe -m flask sukker backup --label manual   # kræver pg_dump (SUKKER_PG_BIN_DIR)
.\.venv\Scripts\python.exe -m flask sukker verify-media   # passer billedposter og -filer sammen?
```

Kendte begrænsninger: Rate limiting gemmes i hukommelsen pr. Gunicorn-proces, så grænserne er reelt
lidt højere med flere processer. E-mail før Premium-perioden udløber (specifikation 10.1) og
notifikationer (12.4) er endnu ikke bygget.
