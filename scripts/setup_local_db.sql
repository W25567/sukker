-- Opretter lokale databaser og roller til Sukker.nu (specifikation 14.4).
-- Køres som postgres via scripts/setup_local_db.ps1, som sender passwordene ind som psql-variabler.
-- Scriptet kan køres igen. Det opdaterer passwords og rettigheder uden at slette data.

-- Roller
SELECT 'CREATE ROLE sukker_migrator LOGIN'
WHERE NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'sukker_migrator') \gexec
SELECT 'CREATE ROLE sukker_app LOGIN'
WHERE NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'sukker_app') \gexec

ALTER ROLE sukker_migrator WITH LOGIN PASSWORD :'migrator_pw';
ALTER ROLE sukker_app WITH LOGIN PASSWORD :'app_pw';
ALTER ROLE sukker_migrator SET search_path = sukker;
ALTER ROLE sukker_app SET search_path = sukker;

-- Databaser
SELECT 'CREATE DATABASE sukker_dev'
WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = 'sukker_dev') \gexec
SELECT 'CREATE DATABASE sukker_test'
WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = 'sukker_test') \gexec

-- Udviklingsdatabase: migrator ejer skemaet, app får kun læse- og skriverettigheder.
\connect sukker_dev
REVOKE ALL ON DATABASE sukker_dev FROM PUBLIC;
GRANT CONNECT ON DATABASE sukker_dev TO sukker_migrator, sukker_app;
REVOKE CREATE ON SCHEMA public FROM PUBLIC;
CREATE SCHEMA IF NOT EXISTS sukker AUTHORIZATION sukker_migrator;
GRANT USAGE ON SCHEMA sukker TO sukker_app;
ALTER DEFAULT PRIVILEGES FOR ROLE sukker_migrator IN SCHEMA sukker
  GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO sukker_app;
ALTER DEFAULT PRIVILEGES FOR ROLE sukker_migrator IN SCHEMA sukker
  GRANT USAGE, SELECT ON SEQUENCES TO sukker_app;
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA sukker TO sukker_app;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA sukker TO sukker_app;

-- Testdatabase: pytest kører som migrator og genopretter tabellerne ved hver kørsel.
\connect sukker_test
REVOKE ALL ON DATABASE sukker_test FROM PUBLIC;
GRANT CONNECT ON DATABASE sukker_test TO sukker_migrator;
REVOKE CREATE ON SCHEMA public FROM PUBLIC;
CREATE SCHEMA IF NOT EXISTS sukker AUTHORIZATION sukker_migrator;

\echo 'Sukker.nu: lokale databaser og roller er klar.'
