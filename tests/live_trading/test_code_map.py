"""qlib ↔ QMT 股票代码转换测试。"""
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from live_trading.modules.code_map import qlib_to_qmt, qmt_to_qlib


def test_qlib_to_qmt_basic():
    assert qlib_to_qmt("SH600000") == "600000.SH"
    assert qlib_to_qmt("SZ000001") == "000001.SZ"
    assert qlib_to_qmt("BJ835185") == "835185.BJ"


def test_qmt_to_qlib_basic():
    assert qmt_to_qlib("600000.SH") == "SH600000"
    assert qmt_to_qlib("000001.SZ") == "SZ000001"
    assert qmt_to_qlib("835185.BJ") == "BJ835185"


def test_roundtrip():
    for code in ["SH600000", "SZ300750", "BJ430047"]:
        assert qmt_to_qlib(qlib_to_qmt(code)) == code


@pytest.mark.parametrize("bad", [
    "sh600000",      # 小写市场
    "SH60000",       # 5 位数字
    "SH6000000",     # 7 位数字
    "XX600000",      # 未知市场
    "600000.SH",     # 已是 QMT 格式
    "600000",        # 无市场
    "",
])
def test_qlib_to_qmt_rejects_bad_input(bad):
    with pytest.raises(ValueError):
        qlib_to_qmt(bad)


@pytest.mark.parametrize("bad", [
    "600000.sh",     # 小写市场
    "SH600000",      # 已是 qlib 格式
    "60000.SH",      # 5 位数字
    "600000.XX",     # 未知市场
    "600000",        # 无后缀
    "",
])
def test_qmt_to_qlib_rejects_bad_input(bad):
    with pytest.raises(ValueError):
        qmt_to_qlib(bad)
