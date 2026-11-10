from functools import lru_cache
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.api.bars import get_store
from app.api.indices import get_index_store
from app.auth import User
from app.data.account_store import SqliteAccountRepository
from app.data.adjust import Bar
from app.data.baostock_source import DataSourceError
from app.data.custom_indices import CustomIndexNotFound, CustomIndexStore
from app.data.daily_bars import DailyBarNotFound, DailyBarStore
from app.data.simulator import (
    AssetType,
    FeeSettings,
    OrderSide,
    PaperSimulator,
    PriceMode,
    SimulationNotFound,
)
from app.db import get_database
from app.deps import get_current_user

router = APIRouter(prefix="/api/sim", tags=["simulator"])


class LocalAssetProvider:
    def __init__(
        self, stocks: DailyBarStore, indices: CustomIndexStore, user_id: str
    ) -> None:
        self._stocks = stocks
        self._indices = indices
        self._user_id = user_id

    def get_daily(
        self, asset_type: AssetType, asset_id: str, start: str, end: str, adjust: str
    ) -> list[Bar]:
        if asset_type == "stock":
            return self._stocks.get_daily(asset_id, start, end, adjust)  # type: ignore[arg-type]
        return self._indices.daily_bars(self._user_id, asset_id, start, end)


@lru_cache
def get_account_repository() -> SqliteAccountRepository:
    return SqliteAccountRepository(get_database())


def get_simulator(
    user: Annotated[User, Depends(get_current_user)],
) -> PaperSimulator:
    index_store = get_index_store()
    return PaperSimulator(
        get_account_repository(),
        user.id,
        LocalAssetProvider(get_store(), index_store, user.id),
        index_store,
    )


class StartIn(BaseModel):
    asset_type: AssetType
    asset_id: str = Field(min_length=1)
    start_date: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")
    adjust: Literal["none", "forward"] = "forward"


class OrderIn(BaseModel):
    side: OrderSide
    quantity: int = Field(gt=0)


class ResetIn(BaseModel):
    initial_cash: float | None = Field(default=None, gt=0)


class FeeSettingsIn(BaseModel):
    commission_rate: float = Field(ge=0, le=0.1)
    minimum_commission: float = Field(ge=0)
    stamp_duty_rate: float = Field(ge=0, le=0.1)
    transfer_fee_rate: float = Field(ge=0, le=0.1)


class SettingsIn(BaseModel):
    price_mode: PriceMode | None = None
    adjust: Literal["none", "forward"] | None = None
    fees: FeeSettingsIn | None = None


@router.post("/start")
def start_simulation(
    request: StartIn,
    simulator: Annotated[PaperSimulator, Depends(get_simulator)],
) -> dict[str, object]:
    try:
        return simulator.start(
            request.asset_type,
            request.asset_id,
            request.start_date,
            request.adjust,
        )
    except SimulationNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except CustomIndexNotFound as exc:
        raise HTTPException(status_code=404, detail="自定义指数不存在") from exc
    except DailyBarNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except DataSourceError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post("/step")
def step_simulation(
    simulator: Annotated[PaperSimulator, Depends(get_simulator)],
) -> dict[str, object]:
    try:
        return simulator.step()
    except SimulationNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except CustomIndexNotFound as exc:
        raise HTTPException(status_code=404, detail="自定义指数不存在") from exc
    except DailyBarNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except DataSourceError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/orders")
def submit_order(
    request: OrderIn,
    simulator: Annotated[PaperSimulator, Depends(get_simulator)],
) -> dict[str, object]:
    try:
        return simulator.submit_order(request.side, request.quantity)
    except SimulationNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except CustomIndexNotFound as exc:
        raise HTTPException(status_code=404, detail="自定义指数不存在") from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("/state")
def get_simulation_state(
    simulator: Annotated[PaperSimulator, Depends(get_simulator)],
) -> dict[str, object]:
    try:
        return simulator.state()
    except (SimulationNotFound, CustomIndexNotFound, DailyBarNotFound) as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except DataSourceError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.post("/reset")
def reset_simulation(
    request: ResetIn,
    simulator: Annotated[PaperSimulator, Depends(get_simulator)],
) -> dict[str, object]:
    try:
        return simulator.reset(request.initial_cash)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.patch("/settings")
def update_settings(
    request: SettingsIn,
    simulator: Annotated[PaperSimulator, Depends(get_simulator)],
) -> dict[str, object]:
    fees = FeeSettings(**request.fees.model_dump()) if request.fees else None
    try:
        return simulator.update_settings(request.price_mode, fees, request.adjust)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
