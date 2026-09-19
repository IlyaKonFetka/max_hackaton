import os
import sys
import tempfile
from pathlib import Path

# Тесты: отдельная БД и папка данных, бот не запускается, авторизация через X-Debug-User.
_tmp = tempfile.mkdtemp(prefix="engine-test-")
os.environ["DATA_DIR"] = _tmp
os.environ["RUN_BOT"] = "0"
os.environ["DEBUG_AUTH"] = "1"
os.environ.setdefault("MAX_BOT_TOKEN", "test-token")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
