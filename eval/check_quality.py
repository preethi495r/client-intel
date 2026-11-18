"""Check the evidence links and totals in the manual briefing rubric."""

from pathlib import Path

from eval.check_snapshot import read_json


def main():
    root = Path(__file__).parent
    rubric = read_json(root / "quality-review.json")
    examples = root / "examples"
    runs = {run["fixture"]["id"]: run for run in read_json(examples / "live-runs.json")}
    assert set(rubric["cases"]) == set(runs)
    maximum = 2 * len(rubric["dimensions"])
    for case, review in rubric["cases"].items():
        path = examples / runs[case]["artifacts"]
        claims = {claim["claim_id"] for claim in read_json(path / "evidence.json")["claims"]}
        verification = read_json(path / "verification.json")
        assert not verification["render_checks"] and verification["status"] != "incomplete"
        assert set(review) == set(rubric["dimensions"])
        for score, reason, references in review.values():
            assert str(score) in rubric["levels"] and reason.strip() and references and set(references) <= claims
        print(f"{case}: {sum(entry[0] for entry in review.values())}/{maximum} | {verification['status']}")


if __name__ == "__main__":
    main()
