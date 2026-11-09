import json
import random
from dataclasses import asdict, dataclass, field
from datetime import date
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path
from typing import Literal, Protocol
from uuid import uuid4

from app.data.adjust import Bar
from app.data.custom_indices import CustomIndexStore
from app.data.daily_bars import HISTORY_START, DailyBarNotFound, DailyBarStore

AssetType = Literal["stock", "custom_index"]
PriceMode = Literal["open", "close", "mid", "random"]
OrderSide = Literal["buy", "sell"]


class SimulationNotFound(Exception):
    pass


class AssetProvider(Protocol):
    def get_daily(
        self, asset_type: AssetType, asset_id: str, start: str, end: str, adjust: str
    ) -> list[Bar]: ...


@dataclass
class FeeSettings:
    commission_rate: float = 0.00025
    minimum_commission: float = 5.0
    stamp_duty_rate: float = 0.0005
    transfer_fee_rate: float = 0.0001


@dataclass
class PositionLot:
    quantity: int
    cost_per_unit: float
    buy_date: str


@dataclass
class PendingOrder:
    id: str
    side: OrderSide
    quantity: int
    submitted_date: str


@dataclass
class SimulationAccount:
    initial_cash: float = 1_000_000.0
    cash: float = 1_000_000.0
    asset_type: AssetType | None = None
    asset_id: str | None = None
    start_date: str | None = None
    current_date: str | None = None
    adjust: Literal["none", "forward"] = "forward"
    price_mode: PriceMode = "open"
    fee_settings: FeeSettings = field(default_factory=FeeSettings)
    lots: list[PositionLot] = field(default_factory=list)
    pending_order: PendingOrder | None = None
    trades: list[dict[str, object]] = field(default_factory=list)
    random_seed: int = 0


class PaperSimulator:
    def __init__(
        self, account_path: Path, assets: AssetProvider, indices: CustomIndexStore
    ) -> None:
        self._path = account_path
        self._assets = assets
        self._indices = indices

    def start(
        self,
        asset_type: AssetType,
        asset_id: str,
        start_date: str,
        adjust: Literal["none", "forward"] = "forward",
    ) -> dict[str, object]:
        self._validate_asset(asset_type, asset_id)
        if start_date > date.today().isoformat():
            raise ValueError("模拟起始日不能晚于今天")
        account = self._read()
        if account.lots and (account.asset_type, account.asset_id) != (
            asset_type,
            asset_id,
        ):
            raise ValueError("当前标的仍有持仓，请先卖出或重置账户")
        if account.pending_order is not None:
            raise ValueError("有待成交订单，请先推进或重置账户")
        bars = self._assets.get_daily(
            asset_type, asset_id, start_date, date.today().isoformat(), adjust
        )
        first_bar = next((bar for bar in bars if bar.date >= start_date), None)
        if first_bar is None:
            raise DailyBarNotFound("起始日之后没有可用行情")
        if (
            account.lots
            and account.asset_type == asset_type
            and account.asset_id == asset_id
            and account.current_date is not None
            and first_bar.date < account.current_date
        ):
            raise ValueError("持仓期间不能把模拟日期倒回，请先推进或重置账户")
        account.asset_type = asset_type
        account.asset_id = asset_id
        account.start_date = first_bar.date
        account.current_date = first_bar.date
        account.adjust = adjust
        account.random_seed = random.SystemRandom().randrange(0, 2**63)
        self._write(account)
        return self.state()

    def step(self) -> dict[str, object]:
        account = self._read()
        bars = self._current_bars(account)
        future = [bar for bar in bars if bar.date > (account.current_date or "")]
        if not future:
            raise ValueError("没有更多交易日可推进")
        bar = future[0]
        account.current_date = bar.date
        if account.pending_order is not None:
            self._fill_order(account, bar)
        self._write(account)
        return self.state()

    def submit_order(self, side: OrderSide, quantity: int) -> dict[str, object]:
        account = self._read()
        if account.asset_id is None or account.current_date is None:
            raise SimulationNotFound("请先启动模拟")
        if account.pending_order is not None:
            raise ValueError("已有待成交订单，请先推进一根 K 线")
        if quantity <= 0:
            raise ValueError("数量必须为正整数")
        if account.asset_type == "stock" and quantity % 100 != 0:
            raise ValueError("A 股数量必须为 100 股的整数倍")
        if side == "sell":
            sellable = sum(
                lot.quantity for lot in account.lots if lot.buy_date < account.current_date
            )
            if quantity > sellable:
                raise ValueError(f"可卖数量不足（当前可卖 {sellable}）")
        account.pending_order = PendingOrder(
            id=uuid4().hex,
            side=side,
            quantity=quantity,
            submitted_date=account.current_date,
        )
        self._write(account)
        return self.state()

    def reset(self, initial_cash: float | None = None) -> dict[str, object]:
        account = self._read()
        cash = account.initial_cash if initial_cash is None else initial_cash
        if cash <= 0:
            raise ValueError("起始资金必须大于 0")
        replacement = SimulationAccount(
            initial_cash=cash,
            cash=cash,
            fee_settings=account.fee_settings,
            price_mode=account.price_mode,
        )
        self._write(replacement)
        return self.state()

    def update_settings(
        self,
        price_mode: PriceMode | None = None,
        fees: FeeSettings | None = None,
        adjust: Literal["none", "forward"] | None = None,
    ) -> dict[str, object]:
        account = self._read()
        if price_mode is not None:
            account.price_mode = price_mode
        if adjust is not None:
            account.adjust = adjust
        if fees is not None:
            if min(
                fees.commission_rate,
                fees.minimum_commission,
                fees.stamp_duty_rate,
                fees.transfer_fee_rate,
            ) < 0:
                raise ValueError("费用参数不能为负数")
            account.fee_settings = fees
        self._write(account)
        return self.state()

    def state(self) -> dict[str, object]:
        account = self._read()
        if account.asset_id is None or account.current_date is None:
            return self._serialize(account, [], None)
        bars = self._current_bars(account)
        visible = [bar for bar in bars if bar.date <= account.current_date][-2000:]
        current = next((bar for bar in reversed(visible) if bar.date == account.current_date), None)
        quantity = sum(lot.quantity for lot in account.lots)
        cost_basis = sum(lot.quantity * lot.cost_per_unit for lot in account.lots)
        market_value = quantity * current.close if current is not None else 0.0
        available = sum(
            lot.quantity for lot in account.lots if lot.buy_date < account.current_date
        )
        value = self._serialize(account, visible, current)
        value["portfolio"] = {
            "cash": round(account.cash, 2),
            "quantity": quantity,
            "available_to_sell": available,
            "average_cost": round(cost_basis / quantity, 6) if quantity else 0.0,
            "market_value": round(market_value, 2),
            "unrealized_pnl": round(market_value - cost_basis, 2),
            "total_asset": round(account.cash + market_value, 2),
        }
        value["recent_trades"] = account.trades[-100:]
        return value

    def _current_bars(self, account: SimulationAccount) -> list[Bar]:
        if account.asset_type is None or account.asset_id is None or account.start_date is None:
            raise SimulationNotFound("请先启动模拟")
        history_start = (
            HISTORY_START
            if account.asset_type == "stock"
            else self._indices.get(account.asset_id).base_date
        )
        return self._assets.get_daily(
            account.asset_type,
            account.asset_id,
            history_start,
            date.today().isoformat(),
            account.adjust,
        )

    def _fill_order(self, account: SimulationAccount, bar: Bar) -> None:
        order = account.pending_order
        assert order is not None
        price = self._fill_price(account, bar)
        gross = price * order.quantity
        fees = self._fees(account, order.side, gross)
        event: dict[str, object] = {
            "order_id": order.id,
            "date": bar.date,
            "side": order.side,
            "quantity": order.quantity,
            "price": round(price, 6),
            "gross": round(gross, 2),
            "fees": round(fees, 2),
            "asset_type": account.asset_type,
            "asset_id": account.asset_id,
        }
        if order.side == "buy":
            total = gross + fees
            if total > account.cash:
                event["status"] = "rejected"
                event["reason"] = "可用资金不足"
            else:
                account.cash -= total
                account.lots.append(
                    PositionLot(order.quantity, (gross + fees) / order.quantity, bar.date)
                )
                event["status"] = "filled"
        else:
            if account.asset_type == "stock":
                pass
            remaining = order.quantity
            sellable_lots = sorted(
                (lot for lot in account.lots if lot.buy_date < bar.date),
                key=lambda lot: lot.buy_date,
            )
            if remaining > sum(lot.quantity for lot in sellable_lots):
                event["status"] = "rejected"
                event["reason"] = "推进后的可卖持仓不足（T+1）"
            else:
                for lot in sellable_lots:
                    take = min(remaining, lot.quantity)
                    lot.quantity -= take
                    remaining -= take
                    if not remaining:
                        break
                account.lots = [lot for lot in account.lots if lot.quantity > 0]
                account.cash += gross - fees
                event["status"] = "filled"
        account.trades.append(event)
        account.pending_order = None

    def _fill_price(self, account: SimulationAccount, bar: Bar) -> float:
        if account.price_mode == "open":
            return bar.open
        if account.price_mode == "close":
            return bar.close
        if account.price_mode == "mid":
            return (bar.open + bar.close) / 2
        rng = random.Random(account.random_seed)
        price = rng.uniform(bar.low, bar.high)
        account.random_seed = rng.getrandbits(63)
        return price

    @staticmethod
    def _fees(account: SimulationAccount, side: OrderSide, gross: float) -> float:
        settings = account.fee_settings
        commission = max(settings.minimum_commission, gross * settings.commission_rate)
        stamp = gross * settings.stamp_duty_rate if side == "sell" else 0.0
        transfer = (
            gross * settings.transfer_fee_rate
            if account.asset_type == "stock"
            and account.asset_id is not None
            and account.asset_id.startswith("6")
            else 0.0
        )
        return float(Decimal(str(commission + stamp + transfer)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))

    def _validate_asset(self, asset_type: AssetType, asset_id: str) -> None:
        if asset_type == "stock":
            if len(asset_id) != 6 or not asset_id.isdigit():
                raise ValueError("股票代码须为 6 位数字")
            if not (asset_id.startswith(("0", "3", "6"))):
                raise ValueError("暂不支持该股票代码段")
        elif asset_type == "custom_index":
            self._indices.get(asset_id)
        else:
            raise ValueError("不支持的标的类型")

    def _read(self) -> SimulationAccount:
        if not self._path.exists():
            return SimulationAccount()
        payload = json.loads(self._path.read_text(encoding="utf-8"))
        return SimulationAccount(
            initial_cash=payload["initial_cash"],
            cash=payload["cash"],
            asset_type=payload.get("asset_type"),
            asset_id=payload.get("asset_id"),
            start_date=payload.get("start_date"),
            current_date=payload.get("current_date"),
            adjust=payload.get("adjust", "forward"),
            price_mode=payload.get("price_mode", "open"),
            fee_settings=FeeSettings(**payload.get("fee_settings", {})),
            lots=[PositionLot(**item) for item in payload.get("lots", [])],
            pending_order=(
                PendingOrder(**payload["pending_order"])
                if payload.get("pending_order")
                else None
            ),
            trades=payload.get("trades", []),
            random_seed=payload.get("random_seed", 0),
        )

    def _write(self, account: SimulationAccount) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        payload = asdict(account)
        self._path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    @staticmethod
    def _serialize(
        account: SimulationAccount, bars: list[Bar], current: Bar | None
    ) -> dict[str, object]:
        return {
            "active": account.asset_id is not None,
            "initial_cash": account.initial_cash,
            "asset_type": account.asset_type,
            "asset_id": account.asset_id,
            "start_date": account.start_date,
            "current_date": account.current_date,
            "adjust": account.adjust,
            "price_mode": account.price_mode,
            "pending_order": asdict(account.pending_order) if account.pending_order else None,
            "current_bar": asdict(current) if current else None,
            "bars": [asdict(bar) for bar in bars],
            "fee_settings": asdict(account.fee_settings),
        }
