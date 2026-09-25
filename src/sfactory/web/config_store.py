"""File-backed configuration store: one JSON file per document, atomic writes, validated on every save."""
from __future__ import annotations

import json
from pathlib import Path

from sfactory.web.schemas import COLLECTIONS, PlatformSettings


class ConfigStore:
    def __init__(self, root: str | Path):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        for c in COLLECTIONS:
            (self.root / c).mkdir(exist_ok=True)

    # --- platform settings --------------------------------------------------------------------------------
    def settings(self) -> PlatformSettings:
        p = self.root / "platform.json"
        return PlatformSettings(**json.loads(p.read_text(encoding="utf-8"))) if p.exists() else PlatformSettings()

    def save_settings(self, s: PlatformSettings) -> PlatformSettings:
        self._write(self.root / "platform.json", s.model_dump())
        return s

    # --- collections --------------------------------------------------------------------------------------
    def _model(self, collection: str):
        if collection not in COLLECTIONS:
            raise KeyError(collection)
        return COLLECTIONS[collection]

    def list(self, collection: str) -> list[dict]:
        self._model(collection)
        return [json.loads(p.read_text(encoding="utf-8")) for p in sorted((self.root / collection).glob("*.json"))]

    def get(self, collection: str, doc_id: str) -> dict:
        self._model(collection)
        p = self.root / collection / f"{doc_id}.json"
        if not p.exists():
            raise FileNotFoundError(doc_id)
        return json.loads(p.read_text(encoding="utf-8"))

    def put(self, collection: str, doc: dict, create: bool = False) -> dict:
        model = self._model(collection)
        obj = model(**doc)
        p = self.root / collection / f"{obj.id}.json"
        if create and p.exists():
            raise FileExistsError(obj.id)
        data = obj.model_dump()
        self._write(p, data)
        return data

    def delete(self, collection: str, doc_id: str) -> None:
        self._model(collection)
        p = self.root / collection / f"{doc_id}.json"
        if not p.exists():
            raise FileNotFoundError(doc_id)
        p.unlink()

    @staticmethod
    def _write(path: Path, data: dict) -> None:
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, indent=1, ensure_ascii=False, default=str), encoding="utf-8")
        tmp.replace(path)
