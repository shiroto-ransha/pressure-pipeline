import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))


def load(name):
    return json.loads((ROOT / "config" / name).read_text(encoding="utf-8"))
