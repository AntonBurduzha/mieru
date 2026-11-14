-- One row per applied file in this directory, written by mieru.tools.ch_migrate.
-- The runner executes this file before every run, so it must stay safe to repeat.
CREATE TABLE IF NOT EXISTS schema_migrations
(
    version    UInt32,
    name       String,
    checksum   String,           -- sha256 of the file, to catch an applied file that was edited
    applied_at DateTime('UTC')
)
ENGINE = MergeTree
ORDER BY version
