"""Run the real FastAPI app as its own process (so tests can signal it). Env: MODELS, RESULTS, PORT."""
import os
import sys

import uvicorn

sys.path.insert(0, os.environ["PROJECT_ROOT"])
from backend.main import create_app  # noqa: E402

uvicorn.run(create_app(os.environ["MODELS"], os.environ["RESULTS"]), host="127.0.0.1",
            port=int(os.environ["PORT"]), log_level="error")
