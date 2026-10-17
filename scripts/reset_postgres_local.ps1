# Nulstiller postgres-brugerens password på den LOKALE udviklingsdatabase og opretter Sukker.nu's
# databaser og roller. Skal køres som administrator.
#
# Fremgangsmåde:
#   1. Tager en kopi af pg_hba.conf.
#   2. Tillader midlertidigt postgres at logge ind fra denne maskine (127.0.0.1 og ::1) uden password.
#   3. Kører setup_local_db.sql og sætter et nyt postgres-password, som du selv taster.
#   4. Gendanner altid den oprindelige pg_hba.conf og genstarter PostgreSQL, også ved fejl.
param(
    [string]$PgRoot = "C:\Program Files\PostgreSQL\18",
    [string]$ServiceName = "postgresql-x64-18"
)

$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot
$logDir = Join-Path $projectRoot "instance"
New-Item -ItemType Directory -Force $logDir | Out-Null
Start-Transcript -Path (Join-Path $logDir "db-setup.log") -Force | Out-Null

$principal = New-Object Security.Principal.WindowsPrincipal([Security.Principal.WindowsIdentity]::GetCurrent())
if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    throw "Scriptet skal køres som administrator."
}

$psql = Join-Path $PgRoot "bin\psql.exe"
$hba = Join-Path $PgRoot "data\pg_hba.conf"
$backup = "$hba.sukker-backup"
$envFile = Join-Path $projectRoot ".env"
if (-not (Test-Path $envFile)) { throw "Filen .env mangler i $projectRoot" }

function Get-PasswordFromUrl([string]$name) {
    $line = Get-Content $envFile | Where-Object { $_ -match "^$name=" } | Select-Object -First 1
    if (-not $line -or $line -notmatch "://[^:]+:([^@]+)@") { throw "$name mangler eller har intet password i .env" }
    return $Matches[1]
}

Copy-Item $hba $backup -Force
try {
    $original = Get-Content $hba
    $trust = @(
        "# Midlertidigt indsat af Sukker.nu reset_postgres_local.ps1 - fjernes automatisk",
        "host    all             postgres        127.0.0.1/32            trust",
        "host    all             postgres        ::1/128                 trust"
    )
    Set-Content -Path $hba -Value ($trust + $original) -Encoding ascii
    Restart-Service $ServiceName
    Write-Host "PostgreSQL genstartet med midlertidig adgang." -ForegroundColor Yellow

    $appPw = Get-PasswordFromUrl "SUKKER_DATABASE_URL"
    $migratorPw = Get-PasswordFromUrl "SUKKER_MIGRATOR_DATABASE_URL"
    & $psql -h 127.0.0.1 -U postgres -d postgres -v ON_ERROR_STOP=1 `
        -v "app_pw=$appPw" -v "migrator_pw=$migratorPw" `
        -f (Join-Path $PSScriptRoot "setup_local_db.sql")
    if ($LASTEXITCODE -ne 0) { throw "setup_local_db.sql fejlede med kode $LASTEXITCODE" }

    Write-Host ""
    Write-Host "Vælg et nyt password til postgres-brugeren (det vises ikke, mens du skriver)." -ForegroundColor Cyan
    $secure = Read-Host "Nyt postgres-password" -AsSecureString
    $plain = [Runtime.InteropServices.Marshal]::PtrToStringAuto([Runtime.InteropServices.Marshal]::SecureStringToBSTR($secure))
    if ([string]::IsNullOrWhiteSpace($plain)) {
        Write-Host "Intet password indtastet. postgres-passwordet er uændret." -ForegroundColor Yellow
    } else {
        # psql erstatter kun variabler i input fra fil eller stdin, ikke i -c. :'pw' citerer passwordet sikkert.
        "ALTER ROLE postgres PASSWORD :'pw';" | & $psql -h 127.0.0.1 -U postgres -d postgres -v ON_ERROR_STOP=1 -q -v "pw=$plain"
        $code = $LASTEXITCODE
        $plain = $null
        if ($code -ne 0) { throw "Kunne ikke sætte nyt postgres-password" }
        Write-Host "Nyt postgres-password er sat." -ForegroundColor Green
    }
}
finally {
    Copy-Item $backup $hba -Force
    Remove-Item $backup -Force
    Restart-Service $ServiceName
    Write-Host "Den oprindelige pg_hba.conf er gendannet, og PostgreSQL er genstartet." -ForegroundColor Green
    Stop-Transcript | Out-Null
}

Write-Host ""
Write-Host "Færdig. Du kan lukke vinduet." -ForegroundColor Green
