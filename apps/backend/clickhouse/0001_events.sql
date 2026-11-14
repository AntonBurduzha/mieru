-- Every page view and custom event. The design: docs/architecture/data-model.md, "ClickHouse (events)"
CREATE TABLE IF NOT EXISTS events
(
    site_id        UInt32,                            -- sites.id in Postgres
    timestamp      DateTime('UTC') CODEC(Delta, ZSTD),
    name           LowCardinality(String),            -- 'pageview' or a custom event name
    visitor_id     UInt64,                            -- the daily rotating hash
    session_id     UInt64,
    session_start  DateTime('UTC'),                   -- the same on every event of a session
    hostname       LowCardinality(String),
    pathname       String CODEC(ZSTD(3)),
    -- session attributes, copied onto every event by the writer
    source         LowCardinality(String),            -- 'Google', 'Direct', ...
    referrer       String CODEC(ZSTD(3)),
    utm_source     LowCardinality(String),
    utm_medium     LowCardinality(String),
    utm_campaign   LowCardinality(String),
    country        LowCardinality(FixedString(2)),    -- 'ZZ' when unknown
    device         LowCardinality(String),            -- desktop | mobile | tablet
    browser        LowCardinality(String),
    os             LowCardinality(String),
    props          Map(LowCardinality(String), String)
)
ENGINE = MergeTree
PARTITION BY toYYYYMM(timestamp)
ORDER BY (site_id, toDate(timestamp), name, visitor_id, timestamp)
-- remember the last 1000 inserted blocks, so a retried batch is stored once
SETTINGS non_replicated_deduplication_window = 1000
