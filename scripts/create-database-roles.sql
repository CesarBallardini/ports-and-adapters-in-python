-- Two roles and one schema, because migrations and the application need different powers
-- (ADR-0018).
--
-- Run this once, as a superuser, against the database for the environment being set up, before
-- the first migration. It is not a migration itself: a migration runs *as* the migrator, so it
-- cannot be the thing that creates the migrator.
--
-- Databases are named for their environment -- academy_production, academy_staging,
-- academy_test -- so the name in a connection string says which one it is, and a restore into
-- the wrong database is a visible mistake rather than a silent one. Everything below is
-- identical in each; only the database differs.
--
-- PostgreSQL only. SQLite has no permission system and no schemas -- access is file
-- permissions and nothing else -- so on a developer's machine the two URLs point at one file
-- and the separation is documentation. It is real in CI and in production, which is where it
-- matters.

-- The role that owns the schema. Migrations connect as this one, and nothing else does.
CREATE ROLE academy_migrator LOGIN PASSWORD :'migrator_password';

-- The role the application connects as. It can change rows and cannot change the schema.
CREATE ROLE academy_app LOGIN PASSWORD :'app_password';

GRANT CONNECT ON DATABASE :"database" TO academy_migrator, academy_app;

-- A schema of our own, owned by the migrator.
--
-- Not `public`, and that is a security decision rather than tidiness: PostgreSQL historically
-- grants CREATE on `public` to every role, so an application role that "cannot do DDL" can
-- still create tables there until someone remembers to revoke it. Owning a dedicated schema
-- makes the separation structural.
CREATE SCHEMA academy AUTHORIZATION academy_migrator;
REVOKE CREATE ON SCHEMA public FROM PUBLIC;

GRANT USAGE ON SCHEMA academy TO academy_app;

-- The schema is selected per role rather than written into the table names. Qualifying the
-- metadata as `academy.people` would produce DDL that SQLite cannot run, and the same
-- migrations have to work on both databases (ADR-0007). This way the SQL is identical
-- everywhere and only the connection differs.
ALTER ROLE academy_migrator IN DATABASE :"database" SET search_path = academy;
ALTER ROLE academy_app      IN DATABASE :"database" SET search_path = academy;

-- Existing tables, for a database that already has some.
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA academy TO academy_app;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA academy TO academy_app;

-- The line everything hinges on. Without it, the *next* migration creates a table the
-- application cannot read, and the failure appears at run time in production rather than at
-- deploy time -- every future migration is a latent outage.
ALTER DEFAULT PRIVILEGES FOR ROLE academy_migrator IN SCHEMA academy
    GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO academy_app;
ALTER DEFAULT PRIVILEGES FOR ROLE academy_migrator IN SCHEMA academy
    GRANT USAGE, SELECT ON SEQUENCES TO academy_app;

-- Deliberately NOT granted to academy_app: CREATE on the schema, and ownership of any table.
-- Those are what make DDL possible, and the application never issues any.
