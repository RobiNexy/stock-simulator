from app.data.account_store import SqliteAccountRepository
from app.data.simulator import SimulationAccount
from app.db import Database


def _insert_user(database: Database, user_id: str, username: str) -> None:
    from app.auth import hash_password

    with database.connect() as connection:
        connection.execute(
            "INSERT INTO users (id, username, password_hash, is_admin, is_active, created_at)"
            " VALUES (?, ?, ?, 0, 1, datetime('now'))",
            (user_id, username, hash_password("password123")),
        )


def test_accounts_are_persisted_and_isolated_by_user(database: Database) -> None:
    _insert_user(database, "alice", "alice")
    _insert_user(database, "bob", "bob")
    repository = SqliteAccountRepository(database)
    account = SimulationAccount(initial_cash=50000, cash=42000)
    repository.save("alice", account)

    loaded = repository.load("alice")
    other = repository.load("bob")

    assert loaded.initial_cash == 50000
    assert loaded.cash == 42000
    assert other.initial_cash == 1_000_000
    assert other.cash == 1_000_000


def test_repository_can_be_recreated_without_losing_state(database: Database) -> None:
    _insert_user(database, "alice", "alice")
    SqliteAccountRepository(database).save(
        "alice", SimulationAccount(initial_cash=12345, cash=23456)
    )

    loaded = SqliteAccountRepository(database).load("alice")

    assert (loaded.initial_cash, loaded.cash) == (12345, 23456)
