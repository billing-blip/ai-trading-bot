import os
import shutil
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
TMP = Path(tempfile.mkdtemp(prefix="botroot-"))
shutil.copytree(REPO / "config", TMP / "config")
os.environ["BOT_ROOT"] = str(TMP)
sys.path.insert(0, str(REPO))
