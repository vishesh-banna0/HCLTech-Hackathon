"""Public evaluation through the same run_query path used by the CLI."""
import csv
from pathlib import Path
from app import is_high_priority_report, run_query
from tools.policies import PolicyIndex, input_dir

ROOT = Path(__file__).resolve().parent


def fraction(values):
    return sum(values), len(values)


def main():
    directory = input_dir("evaluation")
    schema = directory / "predictions_schema.csv"
    cases = directory / "public_test_cases.csv"
    if not schema.exists() or not cases.exists():
        raise SystemExit("Missing evaluation/public_test_cases.csv or predictions_schema.csv")
    with schema.open(newline="", encoding="utf-8-sig") as handle:
        columns = [r["column_name"] for r in csv.DictReader(handle)]
    with cases.open(newline="", encoding="utf-8-sig") as handle:
        tests = list(csv.DictReader(handle))
    index = PolicyIndex()
    predictions = []
    checks = {"category_accuracy": [], "priority_accuracy": [], "policy_citation_presence": [],
              "citation_validity": [], "public_safety_refusal": [], "public_pii_masking": [], "data_use_correctness": []}
    for case in tests:
        result = run_query(case["prompt"])
        expected_sources = [x for x in case.get("expected_sources", "").split(";") if x]
        expected_behavior = case.get("expected_behavior", "").lower()
        expects_data = "use " in expected_behavior and any(x in expected_behavior for x in ("csv", "inventory", "receipts", "vendor", "cases", "returns"))
        expects_refusal = "refuse" in expected_behavior
        row = {"test_id": case["test_id"], "category": result["category"], "priority": result["priority"],
               "confidence": result["confidence"], "answer": result["answer"],
               "citations": ";".join(result["citations"]), "data_used": ";".join(result["data_used"]),
               "escalated": "Yes" if result["escalation"]["required"] else "No",
               "escalation_team": result["escalation"]["team"],
               "pii_masked": "Yes" if result["pii_masked"] else "No",
               "safety_refusal": "Yes" if result["safety_refusal"] else "No",
               "notes": result["reasoning"]}
        predictions.append({name: row.get(name, "") for name in columns})
        checks["category_accuracy"].append(result["category"] == case["expected_category"])
        checks["priority_accuracy"].append(result["priority"] == case["expected_priority"])
        # Pure numeric case reporting needs CSV provenance, not a policy citation.
        if expected_sources and not is_high_priority_report(case["prompt"]):
            checks["policy_citation_presence"].append(bool(result["citations"]))
        if result["citations"]:
            checks["citation_validity"].append(all(index.source(x) for x in result["citations"]))
        if expects_refusal:
            checks["public_safety_refusal"].append(result["safety_refusal"] and result["escalation"]["required"])
        if "mask" in expected_behavior or "redact" in expected_behavior:
            checks["public_pii_masking"].append(result["pii_masked"])
        if expects_data:
            checks["data_use_correctness"].append(any(x != "escalation_matrix.csv" for x in result["data_used"]))
        print(f"{case['test_id']} completed", flush=True)
    output = ROOT / "outputs"
    output.mkdir(exist_ok=True)
    with (output / "predictions.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns)
        writer.writeheader()
        writer.writerows(predictions)
    with (output / "metrics.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["metric", "passed", "denominator", "rate", "note"])
        for name, values in checks.items():
            passed, total = fraction(values)
            writer.writerow([name, passed, total, round(passed / total, 3) if total else "", "Automatic proxy; review answer quality manually." if name == "data_use_correctness" else ""])
        writer.writerow(["answer_quality", "", "", "", "Requires human review"])
    print("Wrote", output / "predictions.csv", "and metrics.csv")
    for name, values in checks.items():
        passed, total = fraction(values)
        print(f"{name}: {passed}/{total}" if total else f"{name}: not measurable")


if __name__ == "__main__":
    main()
