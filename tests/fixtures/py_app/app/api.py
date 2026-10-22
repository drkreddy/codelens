from fastapi import FastAPI

from . import service
from .models import Item

app = FastAPI()


@app.get("/items/{item_id}")
def read_item(item_id: int) -> Item:
    return service.load(item_id)


@app.route("/items", methods=["POST", "PUT"])
def write_item(payload: dict) -> Item:
    item = Item(**payload)
    service.store(item)
    return item
