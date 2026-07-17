from fastapi import FastAPI

app = FastAPI()


@app.get("/health/live")
def live():
    return {"status": "ok"}
