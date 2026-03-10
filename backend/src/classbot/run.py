import os
import sys
from pathlib import Path
import uvicorn

HERE = Path(__file__).resolve().parent
BACKEND_SRC = HERE.parent
PROJECT_ROOT = BACKEND_SRC.parent.parent

sys.path.insert(0, str(HERE))
sys.path.insert(0, str(BACKEND_SRC))
sys.path.insert(0, str(PROJECT_ROOT))

from main import app

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port, reload=False)