import requests
import sqlite3

from app.models import Item


def load(item_id: int) -> Item:
    row = _db().execute("select id, name from items where id = ?", (item_id,)).fetchone()
    return Item(*row)


def store(item: Item) -> None:
    _db().execute("insert into items values (?, ?)", (item.id, item.name))
    requests.post("https://audit.example/items", json={"id": item.id})


def _db():
    return sqlite3.connect("items.db")


class Cache:
    def get(self, key):
        return self._lookup(key)

    def _lookup(self, key):
        return None
