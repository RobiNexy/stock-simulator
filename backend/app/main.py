from fastapi import FastAPI

from app.api.bars import router as bars_router
from app.api.indices import router as indices_router
from app.api.simulator import router as simulator_router
from app.api.stocks import router as stocks_router

app = FastAPI(title="stock-learn")
app.include_router(bars_router)
app.include_router(indices_router)
app.include_router(simulator_router)
app.include_router(stocks_router)


@app.get("/api/health")
def health() -> dict[str, str]:
    return {"status": "ok"}
