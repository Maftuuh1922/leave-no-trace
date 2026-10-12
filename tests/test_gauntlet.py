import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "bench"))

import gauntlet  # noqa: E402


def test_gauntlet_catches_and_restores_everything_it_plants():
    res = gauntlet.run_gauntlet(idle_seconds=1.0)
    n = res["planted_tested"]
    assert n >= 18, res["rows"]
    missed = [r["id"] for r in res["rows"] if r.get("caught") is False]
    unrestored = [r["id"] for r in res["rows"] if "caught" in r and not r.get("restored_exact")]
    assert not missed, missed
    assert not unrestored, unrestored
    assert res["false_positives"] == 0, res["fp_ids"]
    assert res["idle"]["false_positives"] == 0, res["idle"]["fp_ids"]
    assert res["diff_exit_code"] == 1 and res["idle"]["exit_code"] == 0
