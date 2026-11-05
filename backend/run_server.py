import sys
from pathlib import Path

# Add project root to sys.path so 'backend' package resolves cleanly
project_root = Path(__file__).resolve().parent.parent
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

# Imports below deliberately follow the sys.path setup and the loop-policy
# choice: moving them up would let uvicorn resolve the Proactor default on
# Windows before the policy is installed (see Makefile's UVICORN_LOOP note).
import asyncio  # noqa: E402

if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

import uvicorn  # noqa: E402

if __name__ == "__main__":
    uvicorn.run("backend.app.main:app", host="127.0.0.1", port=8000, reload=False)
