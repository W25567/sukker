# Opretter de lokale PostgreSQL-databaser og -roller til Sukker.nu.
# Kør fra projektmappen:  .\scripts\setup_local_db.ps1
# psql spørger efter postgres-brugerens password. Det gemmes ikke.
param(
    [string]$Psql = "C:\Program Files\PostgreSQL\18\bin\psql.exe",
    [string]$PgHost = "localhost",
    [int]$Port = 5432,
    [string]$SuperUser = "postgres"
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$envFile = Join-Path $root ".env"
if (-not (Test-Path $envFile)) { throw "Filen .env mangler. Kopiér .env.example til .env og udfyld den." }
if (-not (Test-Path $Psql)) { throw "psql blev ikke fundet: $Psql" }

function Get-PasswordFromUrl([string]$name) {
    $line = Get-Content $envFile | Where-Object { $_ -match "^$name=" } | Select-Object -First 1
    if (-not $line) { throw "$name mangler i .env" }
    if ($line -notmatch "://[^:]+:([^@]+)@") { throw "$name indeholder ikke et password" }
    return $Matches[1]
}

$appPw = Get-PasswordFromUrl "SUKKER_DATABASE_URL"
$migratorPw = Get-PasswordFromUrl "SUKKER_MIGRATOR_DATABASE_URL"

& $Psql -h $PgHost -p $Port -U $SuperUser -d postgres -v ON_ERROR_STOP=1 `
    -v "app_pw=$appPw" -v "migrator_pw=$migratorPw" `
    -f (Join-Path $PSScriptRoot "setup_local_db.sql")
if ($LASTEXITCODE -ne 0) { throw "psql fejlede med kode $LASTEXITCODE" }
