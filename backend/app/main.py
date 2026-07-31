from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.cases import router as cases_router

app = FastAPI(title="Industry Evidence Workspace")
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5173",
        "http://127.0.0.1:5173",
        "http://localhost:5174",
        "http://127.0.0.1:5174",
    ],
    allow_methods=["*"],
    allow_headers=["*"],
)
app.include_router(cases_router)


@app.get("/health")
def health() -> dict[str, str]:
    return {"service": "industry-evidence-workspace", "status": "ok"}
