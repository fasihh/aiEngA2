"""Build adapters from backend/models.json."""
import json
import os
from pathlib import Path

from .chat import ChatAdapter
from .decisions import DecisionsAdapter
from .mock import MockAdapter

MODELS_FILE = Path(__file__).resolve().parent.parent / "models.json"


def load_model_configs(path=MODELS_FILE):
    return json.loads(Path(path).read_text())["models"]


def build_adapter(cfg, api_key=None):
    kind = cfg["interface"]
    label = cfg.get("label")
    if kind == "mock":
        opts = {k: cfg[k] for k in ("strategy", "latency_s", "malformed_rate", "seed") if k in cfg}
        return MockAdapter(cfg["id"], label=label, **opts)
    key = api_key or os.environ.get("OPENROUTER_API_KEY")
    if not key:
        raise RuntimeError("OPENROUTER_API_KEY is not set (see .env.example)")
    cls = {"decisions": DecisionsAdapter, "chat": ChatAdapter}[kind]
    return cls(cfg["id"], key, label=label)


def build_adapters(path=MODELS_FILE, api_key=None):
    return [build_adapter(c, api_key) for c in load_model_configs(path)]
