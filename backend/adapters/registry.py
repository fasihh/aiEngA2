"""Build adapters from backend/models.json."""
import json
import os
from pathlib import Path

from .chat import ChatAdapter
from .claude_code import ClaudeCodeMCPAdapter
from .decisions import DecisionsAdapter
from .mock import MockAdapter

MODELS_FILE = Path(__file__).resolve().parent.parent / "models.json"


def load_model_configs(path=MODELS_FILE):
    """Enabled models from the config; entries with "enabled": false are skipped."""
    return [m for m in json.loads(Path(path).read_text())["models"] if m.get("enabled", True)]


def build_adapter(cfg, api_key=None):
    kind = cfg["interface"]
    label = cfg.get("label")
    if kind == "mock":
        opts = {k: cfg[k] for k in ("strategy", "latency_s", "malformed_rate", "seed") if k in cfg}
        return MockAdapter(cfg["id"], label=label, **opts)
    if kind == "claude_code":
        opts = {k: cfg[k] for k in ("launch", "claude_model", "claude_bin", "api_url", "extra_args",
                                    "use_subscription", "effort") if k in cfg}
        return ClaudeCodeMCPAdapter(cfg["id"], label=label, **opts)
    key = api_key or os.environ.get("OPENROUTER_API_KEY")
    if not key:
        raise RuntimeError("OPENROUTER_API_KEY is not set (see .env.example)")
    if kind == "chat":
        return ChatAdapter(cfg["id"], key, label=label, structured_output=cfg.get("structured_output", True))
    return DecisionsAdapter(cfg["id"], key, label=label)


def build_adapters(path=MODELS_FILE, api_key=None):
    return [build_adapter(c, api_key) for c in load_model_configs(path)]
