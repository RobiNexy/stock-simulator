from dataclasses import asdict
from datetime import date
from functools import lru_cache
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.api.bars import BarOut, get_store
from app.data.baostock_source import DataSourceError
from app.data.custom_indices import (
    CustomIndex,
    CustomIndexNotFound,
    CustomIndexStore,
)
from app.data.daily_bars import DailyBarNotFound

INDEX_DIR = Path(__file__).resolve().parents[2] / "data" / "indices"
router = APIRouter(prefix="/api/indices", tags=["custom indices"])


class ComponentIn(BaseModel):
    code: str = Field(pattern=r"^\d{6}$")
    weight: float | None = Field(default=None, gt=0)


class CustomIndexIn(BaseModel):
    name: str = Field(min_length=1, max_length=40)
    components: list[ComponentIn] = Field(min_length=1, max_length=100)


class ComponentOut(BaseModel):
    code: str
    weight: float


class CustomIndexOut(BaseModel):
    id: str
    name: str
    base_date: str
    base_point: float
    components: list[ComponentOut]
    description: str = "自定义指数，非官方口径；仅可用于本地模拟"


@lru_cache
def get_index_store() -> CustomIndexStore:
    return CustomIndexStore(INDEX_DIR, get_store())


def _to_out(index: CustomIndex) -> CustomIndexOut:
    payload = asdict(index)
    return CustomIndexOut(**payload)


@router.post("", response_model=CustomIndexOut, status_code=201)
def create_index(
    request: CustomIndexIn,
    store: Annotated[CustomIndexStore, Depends(get_index_store)],
) -> CustomIndexOut:
    all_weights = [component.weight for component in request.components]
    if any(weight is not None for weight in all_weights) and any(
        weight is None for weight in all_weights
    ):
        raise HTTPException(status_code=422, detail="请为所有成分股填写权重，或全部留空使用等权")
    try:
        index = store.create(
            request.name,
            [component.code for component in request.components],
            [float(weight) for weight in all_weights if weight is not None]
            if all_weights and all(weight is not None for weight in all_weights)
            else None,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except DataSourceError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return _to_out(index)


@router.get("", response_model=list[CustomIndexOut])
def list_indices(
    store: Annotated[CustomIndexStore, Depends(get_index_store)],
) -> list[CustomIndexOut]:
    return [_to_out(index) for index in store.list()]


@router.get("/{index_id}", response_model=CustomIndexOut)
def get_index(
    index_id: str,
    store: Annotated[CustomIndexStore, Depends(get_index_store)],
) -> CustomIndexOut:
    try:
        return _to_out(store.get(index_id))
    except CustomIndexNotFound as exc:
        raise HTTPException(status_code=404, detail="自定义指数不存在") from exc


@router.get("/{index_id}/daily", response_model=list[BarOut])
def get_index_bars(
    index_id: str,
    start: date,
    end: date,
    store: Annotated[CustomIndexStore, Depends(get_index_store)],
) -> list[BarOut]:
    if start > end:
        raise HTTPException(status_code=422, detail="start 不能晚于 end")
    try:
        bars = store.daily_bars(index_id, start.isoformat(), end.isoformat())
    except CustomIndexNotFound as exc:
        raise HTTPException(status_code=404, detail="自定义指数不存在") from exc
    except DailyBarNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except DataSourceError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return [BarOut(**asdict(bar)) for bar in bars]

