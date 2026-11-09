from functools import lru_cache
from typing import Annotated, Literal

from fastapi import APIRouter, HTTPException, Query

from app.data.baostock_source import DataSourceError
from app.data.stock_universe import StockUniverse

router = APIRouter(prefix="/api/stocks", tags=["stocks"])


@lru_cache
def get_universe() -> StockUniverse:
    return StockUniverse()


@router.get("/random")
def random_stock(
    pool: Annotated[Literal["all", "hs300", "sz50", "zz500"], Query()] = "all",
) -> dict[str, str]:
    try:
        code, name = get_universe().random_stock(pool)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except DataSourceError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return {"code": code, "name": name, "pool": pool}
