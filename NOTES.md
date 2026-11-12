# Notes

## Testing with 15 million records

From the project root:

```bash
make setup
make db-up
make large-example
wc -l data/coffee_health_15m.csv
time make load-large
```

`make large-example` generates 15 million data records plus the header
(15,000,001 physical lines). The generator writes the CSV sequentially and
adds correctable values, invalid rows, and duplicates. A fixed random seed
makes the output reproducible. The file is about 1.2 GB; PostgreSQL also needs
space for its tables, indexes, and write-ahead log. Skip generation if the
file already exists.

`make load-large` prints counts of read, loaded, corrected, rejected, and
duplicate records. Rejections are exported to `errores.csv`. To inspect the
database, run `make db-shell`, then:

```sql
SELECT count(*) FROM coffee_health;
```

The table can contain fewer than 15 million rows because of rejections,
duplicates, and IDs loaded earlier. After an interruption, rerun
`make load-large` with the same CSV to resume from its checkpoint. Do not
regenerate or edit the CSV before resuming: its file signature must match the
checkpoint. For a small trial, run `make large-example COUNT=10000` followed
by `make load-large`.

### Block loading and benchmark

The current loader validates records in order, copies each 10,000-record block
to a temporary table with `COPY`, and classifies duplicates, conflicts, and
inserts with set-based SQL. Each block and its checkpoint commit in one
transaction. `--workers` parallelizes validation without changing commit
order. Checkpoints written by the previous loader remain resumable.

A local run with 400,000 generated records, four workers, and an isolated
database with all seven schema indexes took **215.46 seconds** (3 minutes
35 seconds). It inserted **396,241 rows**, rejected 3,035, found 362 exact
duplicates and 362 ID conflicts. The test database used 251 MB. Throughput
was about 1,839 inserted rows per second. An earlier, uncontrolled run was
reported to take more than five minutes for 400,000 records, so that
comparison is indicative rather than a controlled speedup measurement. The
isolated database was removed after the benchmark.

## Part 1 — Loading the coffee health dataset

### Schema

The table is `public.coffee_health` (`db/init/002_coffee_health.sql`). Column names
are snake_case and booleans use PostgreSQL `boolean` (`0 → false`, `1 → true`).

| Column | Type | Constraint |
|---|---|---|
| `id` | `integer` | primary key |
| `age` | `smallint` | 18–100 |
| `gender` | `text` | `Male`, `Female`, `Other` |
| `country` | `text` | non-empty |
| `coffee_intake` | `numeric(4,1)` | 0–10 cups/day |
| `caffeine_mg` | `numeric(6,1)` | 0–1000 mg/day |
| `sleep_hours` | `numeric(3,1)` | 0–16 hours |
| `sleep_quality` | `text` | `Poor`, `Fair`, `Good`, `Excellent` |
| `bmi` | `numeric(4,1)` | 10–60 |
| `heart_rate` | `smallint` | 30–220 bpm |
| `stress_level` | `text` | `Low`, `Medium`, `High` |
| `physical_activity_hours` | `numeric(4,1)` | 0–40 hours/week |
| `health_issues` | `text` | `None`, `Mild`, `Moderate`, `Severe` |
| `occupation` | `text` | `Office`, `Healthcare`, `Service`, `Student`, `Other` |
| `smoking` | `boolean` | not null |
| `alcohol_consumption` | `boolean` | not null |
| `loaded_at` | `timestamptz` | defaults to `now()` |

There is also a table-level consistency check:

```sql
(coffee_intake = 0 and caffeine_mg <= 10)
or (coffee_intake > 0 and caffeine_mg > 0)
```

The 10 mg tolerance accepts decaf/residual caffeine. A record such as
`Coffee_Intake = 0, Caffeine_mg = 400` is rejected.

Indexes: one primary key plus seven secondary indexes (`country`, `gender`,
`sleep_quality`, `stress_level`, `age`, `coffee_intake`, `bmi`, each followed by
`id`). They make the Part 2 filters and keyset pagination cheap at the cost of
write amplification: every insert updates all indexes. If writes became more
important than filtered reads, I would keep `country`, `age` and
`coffee_intake` and drop the rest.

### Code layout

- `app/models.py` — shared `CoffeeRecord` domain model.
- `app/coffee/constants.py` — CSV header, canonical values, ranges and DB columns.
- `app/coffee/normalization.py` — pure parsing, normalization and validation.
- `app/coffee/loader.py` — public loader interface and summary.
- `app/coffee/stream/` — CSV reader, ordered block orchestration and validation workers.
- `app/coffee/cli.py` — argparse entrypoint and the load summary printer.
- `app/coffee/schemas.py` — Pydantic response models and the validated query model.
- `app/coffee/repository/` — all PostgreSQL access: `reads.py` for filters,
  pagination, aggregates and single-record lookup; `imports.py` for the
  import transaction.
- `app/coffee/router.py` — `GET /coffee`, `GET /coffee/summary` and
  `GET /coffee/{record_id}`.
- `scripts/load_data.py` — thin CLI wrapper around `app.coffee.cli`.
- `tests/unit/` — tests that do not connect to the database.
- `tests/integration/` — tests that use the real PostgreSQL database.

### Loader design

`scripts/load_data.py` calls `app.coffee.cli` and the block importer:

1. `csv.reader` streams the file; at most one 10,000-record block is held for
   validation at a time.
2. The number of threads selected by `--workers` validates the records while
   preserving their original CSV order.
3. Each block is copied to a temporary table with `COPY`. PostgreSQL uses
   set-based operations to classify exact duplicates, ID conflicts, rows
   already loaded, and new rows.
4. Inserts, rejections, and the block checkpoint commit in one transaction.
   An interrupted block can be retried without duplicating results.
5. If a block causes a PostgreSQL data error, it is split until the offending
   record can be rejected individually.

The first valid occurrence of an ID wins across block boundaries.
`coffee_load_seen` stores its normalized values, `coffee_load_checkpoint`
stores progress, and `coffee_load_reject` supplies `errores.csv`. On resume,
the reader scans the CSV from the start but skips blocks before the checkpoint.

### Normalization and rejection rules

**Corrected:**

- Leading/trailing whitespace and inconsistent case:
  `" male "`, `"MALE"` → `Male`; `"south korea"` → `South Korea`.
- Decimal comma: `"7,5"` → `7.5`.
- Known unit suffixes: `"3.5 cups"` → `3.5`.
- Boolean synonyms: `true`, `t`, `yes`, `y`, `1` → `true`;
  `false`, `f`, `no`, `n`, `0` → `false`.
- Country overrides keep `UK` and `USA` as uppercase acronyms; other countries
  are Title Case.

**Rejected:**

- Empty required values.
- Non-numeric text that cannot be recovered: `thirty`, `4O`, `seven`, `n/a`.
- Negative numbers and values outside the ranges above.
- Unknown booleans such as `None`, `-1`, `2`.
- Rows with missing or extra columns.
- `Coffee_Intake = 0` with `Caffeine_mg > 10`.
- `Coffee_Intake > 0` with `Caffeine_mg = 0`.

A rejected row never aborts the load. The loader exits successfully after
processing the file, even when some rows were rejected.

### Duplicate and conflict policy

- **Exact duplicates:** skipped. The first occurrence wins.
- **Same ID, different values:** the first valid occurrence wins; later rows are
  reported as conflicts and skipped.
- **Previously loaded IDs:** skipped with `ON CONFLICT (id) DO NOTHING`.

I chose first-wins because the file order is the only deterministic tie-breaker.
Without a trustworthy field such as an update timestamp, choosing "the best"
record would be arbitrary. `DO NOTHING` also makes the loader non-destructive
and safe to run twice. If the source later changes for an existing ID, the
loader will not update it; reload from scratch with `make db-reset` when a
source correction must replace stored data.

### Actual run

First `make load`:

```text
Rows read:          10012
Blank lines skipped:   3
Loaded:             9975
Corrected:          56 (of loaded)
Rejected:           25
Exact duplicates:   8
ID conflicts:       4
Already loaded:     0
Accounted for:      10012
```

Second `make load`:

```text
Rows read:          10012
Blank lines skipped:   3
Loaded:             0
Corrected:          0 (of loaded)
Rejected:           25
Exact duplicates:   8
ID conflicts:       4
Already loaded:     9975
Accounted for:      10012
```

`select count(*) from coffee_health` returns **9975** after both runs.

The 56 corrected rows break down as: 18 countries, 13 sleep-quality values,
10 stress-level values, 5 genders, 4 occupations, 3 smoking values and 3
alcohol values (one correction per row).

### Tests

- `tests/unit/test_normalization.py` covers normalization and rejection without a
  database, including `"3.5 cups"`, `"7,5"`, whitespace/case, boolean synonyms,
  ranges, logical inconsistencies and structural errors.
- `tests/unit/test_mapping.py` checks the domain-to-database column order.
- `tests/unit/test_filters.py` checks query normalization and the 422 rules
  without a database.
- `tests/unit/test_chunks.py` checks line counting, chunk boundaries, byte
  offsets, worker resolution and reject-log merging without a database.
- `tests/fixtures/dirty_rows.csv` is the synthetic fixture for those edge cases.
- `tests/integration/test_loader.py` runs the loader twice against the real
  database and checks idempotency, exact-duplicate handling, conflict resolution
  and cleanup. It also compares `workers=1` with `workers=4` and asserts the
  summaries and reject logs are identical.
- `tests/integration/test_coffee_api.py` seeds four rows and exercises filters,
  keyset pagination, single-record reads, `404`/`422` responses and the summary
  endpoint.

Run them with `make test`; the database must be running and the schema applied.

### Scale notes for the loader

- Python memory is bounded by a 10,000-record block and the worker count;
  the full CSV is never loaded into memory.
- Imports update seven `coffee_health` indexes and `coffee_load_seen`.
  Both costs grow with stored data.
- Each block creates temporary tables, uses `COPY`, and commits one
  transaction. Set-based processing reduces per-row database round trips;
  throughput still needs to be measured on the target machine.
- Resume preserves database state but scans the CSV from the beginning to
  find the first pending block.

## Part 2 — API

### Endpoints

- `GET /coffee` — filtered, keyset-paginated records ordered by `id`.
- `GET /coffee/{record_id}` — one record by primary key; `404` when missing and
  `422` for `0`, negative or non-numeric IDs.
- `GET /coffee/summary` — aggregates over the same filters.
- `GET /health` — liveness plus a database round-trip.

`/coffee/{record_id}` is declared after `/coffee/summary`, so `summary` is never
interpreted as an ID.

Interactive documentation is available at `/docs`.

### Filters

Categorical filters accept the parameter more than once and match **any** of
the supplied values:

| Parameter | Values |
|---|---|
| `country` | Country name |
| `gender` | `Male`, `Female`, `Other` |
| `sleep_quality` | `Poor`, `Fair`, `Good`, `Excellent` |
| `stress_level` | `Low`, `Medium`, `High` |
| `health_issues` | `None`, `Mild`, `Moderate`, `Severe` |
| `occupation` | `Office`, `Healthcare`, `Service`, `Student`, `Other` |

Boolean filters: `smoking`, `alcohol_consumption`.

Inclusive numeric ranges: `min_age`/`max_age`, `min_coffee_intake`/
`max_coffee_intake`, `min_bmi`/`max_bmi`, `min_sleep_hours`/
`max_sleep_hours`.

Filters are optional and combine with `AND`. Repeated values in one
categorical filter combine with `OR` (`= any(%s)`) after deduplication.

The API reuses the loader's canonical maps and `normalize_country`, so
`country=south korea`, `gender=female` and `sleep_quality=GOOD` all work.

Validation lives in `app/coffee/schemas.py` as a Pydantic query model
(`CoffeeFilters` / `CoffeeListQuery`). It reuses the ranges from
`constants.py`, so `min_age=17`, `max_age=101`, `max_coffee_intake=20`,
`gender=alien` or `min_age > max_age` return `422` with the offending
parameter in the error. FastAPI builds `/docs` from the same model.

### SQL injection safety

- Every user-supplied **value** is bound as a query parameter (`%s`), never
  formatted into the SQL string.
- Every dynamic **column name** comes from a fixed allowlist and is emitted
  with `psycopg.sql.Identifier`, so it is quoted as an identifier even if the
  allowlist were ever bypassed.
- `group_by` is a Pydantic `Literal` and is additionally looked up in a dict in
  the repository; an unexpected value raises `ValueError` instead of reaching
  SQL.
- `after_id` and `limit` are validated integers, so `after_id=1 OR 1=1` is a
  `422` before any SQL runs.
- The loader has the same property: `COPY` rows are sent with `write_row` and
  the only interpolated identifiers are the fixed `COLUMNS`.

`tests/integration/test_coffee_api.py` includes a payload that passes country
validation (`Testland' OR true --`) and verifies it returns an empty result and
does not affect the table.

### Pagination

Keyset pagination, not `OFFSET`:

```text
GET /coffee?limit=50
GET /coffee?limit=50&after_id=12345
```

The response contains `items`, `limit`, `has_more` and `next_cursor`.
`next_cursor` is the `id` of the last item on the page; pass it as `after_id`
to fetch the next page. `limit` is between 1 and 200 (default 50).

Internally the query is `WHERE ... AND id > $cursor ORDER BY id LIMIT $limit + 1`.
The extra row decides `has_more` without a `COUNT(*)`. Page 1 and page 50,000
cost the same: PostgreSQL seeks to the cursor through the primary key instead
of reading and discarding all previous rows. The trade-off versus `OFFSET` is
that the cursor only moves forward and a deleted row can shift boundaries; for
this read-mostly dataset that is acceptable.

### Summary

```text
GET /coffee/summary?group_by=country
GET /coffee/summary?group_by=coffee_intake&gender=Female&min_age=30
```

`group_by` accepts `country`, `gender`, `sleep_quality`, `stress_level`,
`coffee_intake` or `occupation`. Each group returns `count`,
`avg_sleep_hours`, `avg_coffee_intake` and `avg_caffeine_mg`. It accepts exactly
the same filters as `/coffee`; an unknown `group_by` returns `422`.

### Scale notes for the API

- The list endpoint uses the `(filter_column, id)` indexes plus the primary key.
  With one categorical filter PostgreSQL can walk the composite index in `id`
  order. With several filters the planner may choose another index and sort the
  page; the keyset cursor still prevents deep `OFFSET` scans.
- Only one page is materialized: the repository fetches `limit + 1` rows and
  `limit` is capped at 200.
- The summary aggregates the filtered set with `GROUP BY`. That is linear in the
  matching rows, which is fine interactively; for a much larger table the next
  step would be pre-aggregated tables.
- `smoking` and `alcohol_consumption` have no dedicated indexes. They are
  low-cardinality filters, usually combined with a selective categorical filter,
  so an index would add write cost for little benefit.
- Requests borrow connections from the psycopg pool (`min_size=1`,
  `max_size=10`), so concurrency does not open an unbounded number of
  connections.

## Part 3 — Tests and code quality

### Tests

`make test-unit` runs normalization, filter, mapping, CSV reader, and worker
unit tests without PostgreSQL. `make test-integration` runs API and importer
tests against a real PostgreSQL instance. Importer integration tests cover
idempotency, rejection reporting, exact duplicates and conflicts across
blocks, equal results with one or multiple validation workers, interruption
and resume, and compatibility with previous checkpoints. `make test` runs both
suites. The latest full run passed **86 tests**.

### Pre-commit hook

`make setup` installs the versioned `.githooks/pre-commit` hook. It runs the
unit tests before each commit. The full suite and linter remain explicit
commands (`make test` and `make lint`).

### Type checking

`make lint` runs `ruff check`, `ruff format --check`, and strict `mypy` for
application code and tests.
