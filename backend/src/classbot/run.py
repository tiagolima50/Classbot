import sys
from pathlib import Path
import webbrowser
import uvicorn

HERE = Path(__file__).resolve().parent          # .../backend/src/classbot
BACKEND_SRC = HERE.parent                        # .../backend/src
PROJECT_ROOT = BACKEND_SRC.parent.parent         # .../CLASSBOTT

# permitir "import main" (se o teu main.py está no mesmo folder do run.py)
sys.path.insert(0, str(HERE))

# permitir imports tipo "from classbot...." (precisa do backend/src no path)
sys.path.insert(0, str(BACKEND_SRC))

# opcional: se o main.py precisa de referenciar front/dist com paths absolutos
sys.path.insert(0, str(PROJECT_ROOT))

from main import app  # importa backend/src/classbot/main.py

if __name__ == "__main__":
    webbrowser.open("http://127.0.0.1:8000/login")
    uvicorn.run(app, host="127.0.0.1", port=8000, reload=False)