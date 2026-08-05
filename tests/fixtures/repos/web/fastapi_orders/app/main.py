from fastapi import FastAPI

app = FastAPI()


@app.post("/v1/orders")
def create_order() -> dict[str, str]:
    return {"status": "created"}
