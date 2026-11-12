"""FastAPI routes for the coffee health dataset."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, HTTPException, Path, Query

from app.coffee import repository
from app.coffee.schemas import (
    CoffeeFilters,
    CoffeeListQuery,
    CoffeePage,
    CoffeeRecordOut,
    CoffeeSummary,
    SummaryGroupBy,
)
from app.db import DbConn

router = APIRouter(prefix="/coffee", tags=["coffee"])


@router.get(
    "",
    response_model=CoffeePage,
    summary="List coffee health records",
    description=(
        "Return one page of records ordered by id. Use the response's "
        "`next_cursor` as `after_id` to fetch the next page."
    ),
)
def list_coffee(
    conn: DbConn,
    filters: Annotated[CoffeeListQuery, Query()],
) -> CoffeePage:
    return repository.list_records(conn, filters)


@router.get(
    "/summary",
    response_model=CoffeeSummary,
    summary="Aggregate coffee health records",
    description=(
        "Group the filtered records by one categorical or numeric column and "
        "return record counts and averages."
    ),
)
def coffee_summary(
    conn: DbConn,
    filters: Annotated[CoffeeFilters, Query()],
    group_by: SummaryGroupBy = "country",
) -> CoffeeSummary:
    return repository.summarize(conn, filters, group_by)


@router.get(
    "/{record_id}",
    response_model=CoffeeRecordOut,
    summary="Get one coffee health record",
    description="Return one record by its primary key.",
    responses={404: {"description": "Coffee record not found"}},
)
def get_coffee(
    conn: DbConn,
    record_id: Annotated[int, Path(ge=1, description="Record ID")],
) -> CoffeeRecordOut:
    record = repository.get_record(conn, record_id)
    if record is None:
        raise HTTPException(status_code=404, detail=f"Coffee record {record_id} not found")
    return record
