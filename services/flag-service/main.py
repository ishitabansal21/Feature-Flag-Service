from fastapi import FastAPI

app = FastAPI(title="Feature Flag Service")


@app.get("/")
def root():
    return {
        "message": "Feature Flag Service Running"
    }


@app.get("/health")
def health():
    return {
        "status": "healthy"
    }