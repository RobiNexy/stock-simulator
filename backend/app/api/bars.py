from dataclasses import asdict
from datetime import date
from functools import lru_cache
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from app.config import get_settings
from app.data.baostock_source import BaostockSource, DataSourceError
from app.data.daily_bars import DailyBarNotFound, DailyBarStore

router = APIRouter(prefix="/api/bars", tags=["bars"])


class BarOut(BaseModel):
    date: str
    open: float
    high: float
    low: float
    close: float
    volume: float
    amount: float


@lru_cache
def get_store() -> DailyBarStore:
    return DailyBarStore(get_settings().cache_dir, BaostockSource())


@router.get("/daily", response_model=list[BarOut])
def get_daily_bars(
    code: Annotated[str, Query(pattern=r"^\d{6}$")],
    start: date,
    end: date,
    store: Annotated[DailyBarStore, Depends(get_store)],
    adjust: Literal["none", "forward"] = "forward",
) -> list[BarOut]:
    if start > end:
        raise HTTPException(status_code=422, detail="start 不能晚于 end")
    try:
        bars = store.get_daily(code, start.isoformat(), end.isoformat(), adjust)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except DailyBarNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except DataSourceError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return [BarOut(**asdict(b)) for b in bars]
