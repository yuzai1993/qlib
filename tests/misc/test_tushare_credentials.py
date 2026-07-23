import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
PRODUCTION_FILES = (
    ROOT / "scripts/data_collector/utils.py",
    ROOT / "scripts/data_collector/tushare/collector.py",
    ROOT / "scripts/data_collector/tushare/fill_missing_from_index.py",
    ROOT / "scripts/data_collector/csindex_v2/puller_tushare.py",
)


def test_production_collectors_never_assign_tushare_token():
    assignment = re.compile(r"os\.environ\[['\"]TUSHARE_TOKEN['\"]\]\s*=")
    offenders = [
        str(path.relative_to(ROOT))
        for path in PRODUCTION_FILES
        if assignment.search(path.read_text())
    ]
    assert offenders == []
