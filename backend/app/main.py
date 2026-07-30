from fastapi import FastAPI

from app.api.cases import router as cases_router

app = FastAPI(title="Industry Evidence Workspace")
app.include_router(cases_router)


@app.get("/health")
def health() -> dict[str, str]:
    return {"service": "industry-evidence-workspace", "status": "ok"}
