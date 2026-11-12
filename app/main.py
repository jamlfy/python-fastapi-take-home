from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from pydantic import BaseModel

from app.coffee.router import router as coffee_router
from app.db import DbConn, create_pool


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    app.state.pool = create_pool()
    yield
    app.state.pool.close()


app = FastAPI(title="Coffee Health API", lifespan=lifespan)


class Health(BaseModel):
    status: str
    database: str


@app.get("/health")
def health(conn: DbConn) -> Health:
    conn.execute("select 1")
    return Health(status="ok", database="ok")


app.include_router(coffee_router)
