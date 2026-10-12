"""End-to-end checks: the generator is reproducible and the detectors recover the
patterns that were planted, without flagging the benign decoys."""
import hashlib
from pathlib import Path

import pytest

from aml_graph.detect import evaluate, load_truth, run_all
from aml_graph.generate import generate


def digest(directory: Path) -> str:
    h = hashlib.sha256()
    for f in sorted(directory.glob("*.csv")):
        h.update(f.name.encode())
        h.update(f.read_bytes())
    return h.hexdigest()


def test_generator_is_deterministic(tmp_path):
    a, b = tmp_path / "a", tmp_path / "b"
    generate(a, seed=7)
    generate(b, seed=7)
    assert digest(a) == digest(b)


def test_different_seeds_differ(tmp_path):
    a, b = tmp_path / "a", tmp_path / "b"
    generate(a, seed=1)
    generate(b, seed=2)
    assert digest(a) != digest(b)


@pytest.mark.parametrize("seed", [42, 1, 2, 3, 4])
def test_detectors_recover_planted_patterns(tmp_path, seed):
    generate(tmp_path, seed=seed)
    res = run_all(tmp_path)
    metrics = evaluate(load_truth(tmp_path), res["rings"], res["clusters"], res["structuring"], res["hubs"])
    for name, m in metrics.items():
        assert m["recall"] == 1.0, f"{name} missed planted entities: {m}"
        assert m["precision"] == 1.0, f"{name} flagged benign entities: {m}"


def test_expected_counts_on_default_seed(tmp_path):
    generate(tmp_path, seed=42)
    res = run_all(tmp_path)
    assert len(res["rings"]) == 5
    assert len(res["clusters"]) == 4
    assert len(res["structuring"]) == 6
    assert len(res["hubs"]) == 4


def test_every_flagged_account_exists(tmp_path):
    generate(tmp_path, seed=42)
    res = run_all(tmp_path)
    known = {a["account_id"] for a in res["ds"]["accounts"]}
    assert {r["account_id"] for r in res["scores"]} <= known
