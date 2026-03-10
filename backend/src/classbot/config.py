# config.py
# Parâmetros base (podem ser alterados para o teu ambiente)

# Modelo a usar no Ollama
DEFAULT_MODEL = "llama3.1:8b"

# Temperatura default
DEFAULT_TEMPERATURE = 0.1

# Host do servidor Ollama. Mantém None para localhost.
# Exemplo remoto: "http://127.0.0.1:11434"
OLLAMA_HOST = None

# === Integração do avaliador remoto (FSD) ===
import os

# URL pública do teu FastAPI (/grade), por ex.: https://xxx.ngrok-free.app
FSD_GRADER_URL = os.getenv("FSD_GRADER_URL", "")

# Chave do header X-API-Key (deves defini-la também no Colab)
FSD_API_KEY = os.getenv("FSD_API_KEY", "")

# Timeout (segundos) para esperar resposta do serviço remoto
REQUEST_TIMEOUT_S = int(os.getenv("REQUEST_TIMEOUT_S", 90))