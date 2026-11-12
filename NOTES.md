# Notes

## Prueba con 15 millones de registros

Desde la raíz del proyecto:

```bash
make setup
make db-up
make large-example
wc -l data/coffee_health_15m.csv
time make load-large
```

`make large-example` genera 15 millones de registros de datos más la cabecera
(15.000.001 líneas físicas). El generador escribe el CSV de forma secuencial y
añade casos corregibles, inválidos y duplicados; usa una semilla fija para que
el resultado sea reproducible. El archivo ocupa aproximadamente 1,2 GB, así
que hace falta espacio adicional para las tablas, índices y registros de
PostgreSQL. Si el CSV ya existe, se puede omitir la generación.

`make load-large` carga el archivo en PostgreSQL y muestra el resumen de filas
leídas, cargadas, corregidas, rechazadas y duplicadas. Los rechazos se exportan
a `errores.csv`. Para comprobar el resultado en la base de datos:

```bash
make db-shell
```

Dentro de `psql`:

```sql
SELECT count(*) FROM coffee_health;
```

El conteo en la tabla puede ser menor que 15 millones por los registros
rechazados, duplicados o ya cargados. La carga guarda puntos de control: si se
interrumpe, se puede ejecutar `make load-large` otra vez con el mismo CSV para
reanudarla. No regenerar ni modificar el archivo antes de reanudar, porque la
firma del archivo debe coincidir con la del punto de control. Para una prueba
rápida, usar `make large-example COUNT=10000` y luego `make load-large`.

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
- `app/coffee/chunks.py` — line counting, chunk planning and byte offsets.
- `app/coffee/normalization.py` — pure parsing, normalization and validation.
- `app/coffee/loader.py` — streaming, staging, COPY and parallel workers.
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

`app/coffee/loader.py`, invoked through `app/coffee/cli.py` and
`scripts/load_data.py`:

1. Streams the CSV with `csv.reader`; the file is never loaded into Python memory.
2. Validates and normalizes each row before it reaches PostgreSQL.
3. Rejected rows are skipped, printed with their physical line number and
   reason, and written to `data/rejected_rows.jsonl`.
4. Valid rows go into a temporary staging table through psycopg's
   `COPY ... FROM STDIN`.
5. A single `INSERT ... SELECT DISTINCT ON (id) ... ON CONFLICT (id) DO NOTHING`
   inserts the first valid occurrence of each ID.
6. The summary distinguishes loaded, corrected, rejected, exact duplicates, ID
   conflicts and rows that were already in the table.

The whole load runs in one transaction. The staging table is `UNLOGGED` and is
dropped at the end of both successful and failed runs, so a crash leaves no
partial rows in the target table.

### Parallel loading

The same loader can split the file across processes:

1. `app/coffee/chunks.py` counts physical lines and plans contiguous ranges,
   one per worker. Counting and offset location use binary blocks, so memory
   stays constant with respect to file size.
2. The parent creates the shared `UNLOGGED` staging table and takes
   `pg_advisory_lock(hashtext('coffee_health.load'))`, so two loads cannot
   overlap.
3. Each worker (`multiprocessing` **spawn**, never `fork`, to avoid inheriting
   psycopg connections) seeks to its range, streams and validates it, and
   `COPY`s valid rows into the shared staging table with their global
   `source_line`.
4. The parent merges once with `DISTINCT ON (id) ... ORDER BY id, source_line`,
   so **first occurrence in the file wins** regardless of worker count or
   scheduling.
5. Each worker writes its own reject log; the parent concatenates them in chunk
   order, keeping the merged JSONL sorted by physical line.

`--workers auto` (default) stays sequential below **50,000** data lines and
otherwise uses `min(CPU, 8)`. `--workers N` forces a count, including `1`.
Capping auto mode bounds the number of database connections. A file containing
an embedded newline inside a quoted field falls back to one worker, because
splitting by physical line is only safe when quoted records stay on one line.
Quoted single-line fields do not force a fallback.

The target insert remains a single transaction in the parent. Workers commit
only their own staging rows; if any worker fails, the parent drops staging, so
the destination table is never partially loaded.

All of that SQL lives in `app/coffee/repository/imports.py`:

- `import_session` takes the lock and creates the `UNLOGGED` staging table,
  then drops it and releases the lock on success or failure.
- `copy_records` streams rows through `COPY`.
- `create_records` performs the deterministic merge, classifies duplicates and
  conflicts, inserts the new rows and returns an `ImportReport`.

The loader only parses, validates and orchestrates; it no longer contains SQL
for the import.

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

- The CSV is processed as a stream, so file size is not limited by Python memory.
  The parallel planner also keeps memory constant: it counts in 1 MiB blocks and
  stores only one offset per worker.
- `COPY` is the fastest PostgreSQL bulk-ingest path; validation stays in Python
  as required.
- The staging table is `UNLOGGED` and lives in the database, so it does not grow
  Python memory. At tens of millions of rows it will use database disk space;
  PostgreSQL handles that better than a Python `set` of IDs. Parallel workers
  write to the same table, and the parent drops it at the end.
- Automatic parallelism is capped at `min(CPU, 8)` connections. More workers
  would increase contention on the same staging table and exhaust the server's
  connection budget.
- The final `INSERT ... SELECT` is set-based and only touches each unique ID
  once. `ON CONFLICT (id) DO NOTHING` makes reruns cheap and idempotent. It runs
  once in the parent, so the destination-table transaction is still atomic.
- Measured on this machine with the 10,012-row file: sequential `make load`
  takes ~1.12 s and `make load-parallel` (4 workers) takes ~1.42 s. At this size
  the process startup dominates, which is why auto mode stays sequential below
  50,000 lines. The parallel path is designed for files where validation or I/O
  per row outweighs process startup.
- One very large transaction is simpler and atomic. If transaction size became
  a problem, the next step would be batch commits into the target table while
  keeping the same conflict policy; the trade-off is losing whole-file
  atomicity and making duplicate reporting more complex.

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

### Tests by file

**Unit tests (no database).** `make test-unit` runs only `tests/unit`; these
tests pass with an unreachable `DATABASE_URL`. **70 tests.**

| Archivo | Funcionalidad | Posibilidades |
|---|---|---|
| `tests/unit/test_normalization.py` (30) | Valida, normaliza y rechaza filas del CSV usando solo funciones puras. | Fila limpia sin correcciones; espacios y mayúsculas; booleanos `YES`/`no` y `0`/`1`; decimales con coma `7,5`; sufijos `3.5 cups`; números inválidos `seven`, `4O`, vacío; no enteros; booleanos inválidos `-1`, `2`; café sin cafeína; cafeína residual ≤ 10 mg; café 0 con cafeína alta; rangos de edad, BMI y pulso; categóricos inválidos; columnas faltantes o extra; fixture sucio completo. |
| `tests/unit/test_filters.py` (17) | Valida el query model Pydantic reutilizando la normalización del loader. | Normalización de caso y espacios; deduplicación; defaults `limit=50` y `after_id=None`; límites `limit` 0/201 y `after_id` 0; categórico inválido; valores fuera de los rangos de la base; `min > max` para edad, café, BMI y sueño. |
| `tests/unit/test_chunks.py` (22) | Planifica la carga paralela: conteo de líneas, rangos y offsets de bytes. | Cobertura exacta sin solapes para 1–1000 líneas y 1–16 workers; reparto del resto; archivo vacío o solo cabecera; línea final sin salto; detección de registros multilínea (comillas en una sola línea no fuerzan secuencial); offsets al inicio de cada chunk; tope de workers por número de líneas; resolución `auto` bajo/sobre el umbral y tope de CPU; merge de logs en orden. |
| `tests/unit/test_mapping.py` (1) | Comprueba que `to_db_row` respeta el orden de columnas de la base. | Registro completo con decimales y booleanos. |

**Integration tests (real PostgreSQL).** `make test-integration` runs
`tests/integration`; `make test` runs both suites. **30 tests.**

| Archivo | Funcionalidad | Posibilidades |
|---|---|---|
| `tests/integration/test_coffee_api.py` (26) | Prueba `/coffee`, `/coffee/{record_id}` y `/coffee/summary` con `TestClient` contra PostgreSQL real. | País case-insensitive; filtros categóricos + rango; rangos numéricos y booleanos; keyset pagination; página vacía; inyección SQL; forma completa del registro; lectura de un registro y coincidencia con el elemento de la lista; `404` inexistente; `422` por ID `0`, negativo o no numérico, rangos invertidos, valores fuera de rango, categórico inválido, `limit` y `after_id`; error que nombra `min_age`; resumen por género y país con filtros; `group_by` inválido. |
| `tests/integration/test_health.py` (2) | Verifica la salud de la app y el montaje del router. | `/health` responde `200` con base de datos; `/coffee` responde con la estructura esperada. |
| `tests/integration/test_loader.py` (2) | Verifica idempotencia, conflictos y equivalencia del modo paralelo. | Primera carga; segunda carga sin cambios; duplicado exacto; conflicto de ID (gana la primera); filas rechazadas; log JSONL; limpieza de filas de prueba; `workers=1` vs `workers=4` con el mismo resumen y el mismo log. |

Total: **70 unitarios + 30 de integración = 100 tests**.

### Pre-commit hook

The hook is versioned at `.githooks/pre-commit`. `make setup` (or `make hooks`)
configures it with:

```bash
git config core.hooksPath .githooks
chmod +x .githooks/pre-commit
```

Before every commit it runs:

```bash
uv run pytest tests/unit -q
```

So a commit is blocked if normalization, filter validation, chunk planning or
mapping breaks, and the hook never needs the database. The full suite and the
linter remain explicit commands (`make test`, `make lint`).

### Type checking

`make lint` runs `ruff check`, `ruff format --check` and `mypy` in strict mode.
Both the application and the tests are typed.
