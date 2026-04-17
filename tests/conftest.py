from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"

src_str = str(SRC)
if src_str not in sys.path:
    sys.path.insert(0, src_str)
