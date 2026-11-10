import json
import threading
from collections import defaultdict
from typing import Protocol

from app.data.simulator import (
    FeeSettings,
    PendingOrder,
    PositionLot,
    SimulationAccount,
)
from app.db import Database


class AccountRepository(Protocol):
    def load(self, user_id: str) -> SimulationAccount: ...

    def save(self, user_id: str, account: SimulationAccount) -> None: ...


def account_from_payload(payload: dict[str, object]) -> SimulationAccount:
    return SimulationAccount(
        initial_cash=payload["initial_cash"],  # type: ignore[arg-type]
        cash=payload["cash"],  # type: ignore[arg-type]
        asset_type=payload.get("asset_type"),  # type: ignore[arg-type]
        asset_id=payload.get("asset_id"),  # type: ignore[arg-type]
        start_date=payload.get("start_date"),  # type: ignore[arg-type]
        current_date=payload.get("current_date"),  # type: ignore[arg-type]
        adjust=payload.get("adjust", "forward"),  # type: ignore[arg-type]
        price_mode=payload.get("price_mode", "open"),  # type: ignore[arg-type]
        fee_settings=FeeSettings(**payload.get("fee_settings", {})),  # type: ignore[arg-type]
        lots=[
            PositionLot(**item)  # type: ignore[arg-type]
            for item in payload.get("lots", [])  # type: ignore[union-attr]
        ],
        pending_order=(
            PendingOrder(**payload["pending_order"])  # type: ignore[arg-type]
            if payload.get("pending_order")
            else None
        ),
        trades=payload.get("trades", []),  # type: ignore[arg-type]
        random_seed=payload.get("random_seed", 0),  # type: ignore[arg-type]
    )


def account_to_payload(account: SimulationAccount) -> dict[str, object]:
    from dataclasses import asdict

    return asdict(account)


class InMemoryAccountRepository:
    def __init__(self) -> None:
        self._accounts: dict[str, SimulationAccount] = {}

    def load(self, user_id: str) -> SimulationAccount:
        account = self._accounts.get(user_id)
        return account_from_payload(account_to_payload(account)) if account else SimulationAccount()

    def save(self, user_id: str, account: SimulationAccount) -> None:
        self._accounts[user_id] = account_from_payload(account_to_payload(account))


class SqliteAccountRepository:
    def __init__(self, database: Database) -> None:
        self._database = database
        self._locks: defaultdict[str, threading.Lock] = defaultdict(threading.Lock)
        self._locks_guard = threading.Lock()

    def _lock_for(self, user_id: str) -> threading.Lock:
        with self._locks_guard:
            return self._locks[user_id]

    def load(self, user_id: str) -> SimulationAccount:
        with self._database.connect() as connection:
            row = connection.execute(
                "SELECT payload FROM paper_accounts WHERE user_id = ?", (user_id,)
            ).fetchone()
        if row is None:
            return SimulationAccount()
        return account_from_payload(json.loads(row["payload"]))

    def save(self, user_id: str, account: SimulationAccount) -> None:
        payload = json.dumps(
            account_to_payload(account), ensure_ascii=False, separators=(",", ":")
        )
        with self._lock_for(user_id), self._database.connect() as connection:
            connection.execute(
                "INSERT INTO paper_accounts (user_id, payload, updated_at)"
                " VALUES (?, ?, datetime('now'))"
                " ON CONFLICT(user_id) DO UPDATE SET"
                " payload = excluded.payload, updated_at = excluded.updated_at",
                (user_id, payload),
            )
