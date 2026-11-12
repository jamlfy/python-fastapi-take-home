"""PostgreSQL access for the coffee health dataset.

Safety model:

- Every user-supplied **value** is passed as a query parameter (``%s``).
- Every dynamic **identifier** comes from a fixed allowlist and is emitted
  through ``psycopg.sql.Identifier``, so it is always quoted as an identifier.
- ``group_by`` is also a closed ``Literal`` and is looked up in a dict.

That means neither filter values nor filter names are ever concatenated into
SQL text.

This package is also the owner of the import transaction: the loader validates
CSV rows and hands them to :func:`copy_records`, while :func:`create_records`
performs the deterministic merge and insert into the destination table.

Reads live in :mod:`app.coffee.repository.reads`; the import pipeline lives in
:mod:`app.coffee.repository.imports`.
"""

from app.coffee.repository.imports import (
    ImportReport,
    copy_records,
    create_records,
    import_session,
)
from app.coffee.repository.reads import get_record, list_records, summarize

__all__ = [
    "ImportReport",
    "copy_records",
    "create_records",
    "get_record",
    "import_session",
    "list_records",
    "summarize",
]
