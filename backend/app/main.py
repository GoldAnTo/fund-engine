from fastapi import FastAPI

app = FastAPI(title="Industry Evidence Workspace")


@app.get("/health")
def health() -> dict[str, str]:
    return {"service": "industry-evidence-workspace", "status": "ok"}
