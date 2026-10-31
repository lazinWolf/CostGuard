"""Recorded examples: a real session (check, analyst run, measured fixes) exported once and replayed.

A replay needs no gateway, model or GPU, so a demo cannot fail on hardware. Only the observations are
stored (runs, the analyst's result, the comparison requests); verdicts are recomputed on load, so a
replayed check always agrees with the current rules.
"""

import json
from datetime import datetime, timezone
from pathlib import Path
from uuid import NAMESPACE_URL, uuid5

from . import db
from .economics import compare_artifacts
from .execution import ExecutionArtifact, ExperimentSpec
from .models import ComparisonPolicy, PricingCatalog

DIRECTORY = Path(__file__).resolve().parent.parent / "examples" / "recorded"
KEY = "recorded:"


def job_id_for(artifact_id: str) -> str:
    """A replayed run keeps the same job ID however often it is loaded."""
    return str(uuid5(NAMESPACE_URL, f"costguard:run:{artifact_id}"))


def read(name: str) -> dict | None:
    path = DIRECTORY / f"{Path(name).name}.json"
    return json.loads(path.read_text()) if path.exists() else None


def export(name: str, report_id: str, investigation_id: str) -> dict:
    """Everything needed to replay one check and the analyst session that investigated it."""
    check, investigation = db.get_comparison(report_id), db.get_investigation(investigation_id)
    if check is None or investigation is None or investigation["report_id"] != report_id:
        raise ValueError("the investigation must belong to the check")
    saved = [check] + [db.get_comparison(fix["report_id"]) for fix in investigation["fixes"] if fix["report_id"]]
    runs = {}
    for item in saved:
        for artifact_id in (item["request"]["baseline_id"], item["request"]["candidate_id"]):
            runs[artifact_id] = {"artifact": json.loads(db.get_artifact(artifact_id).model_dump_json()),
                                 "spec": json.loads(db.source_experiment(artifact_id).model_dump_json())}
    fixes = [{**fix, "job_id": job_id_for(db.get_comparison(fix["report_id"])["request"]["candidate_id"])}
             for fix in investigation["fixes"] if fix["report_id"]]
    candidate = next(iter(runs.values()))["spec"]
    return {"name": name, "check_id": report_id,
            "recorded": {"model": f"{candidate['provider']}/{candidate['model']}", "at": datetime.now(timezone.utc).isoformat(),
                         "analyst": "{provider}/{model}".format(**investigation["request"]["analyst"])},
            "runs": list(runs.values()),
            "comparisons": [{"id": item["id"], "request": item["request"]} for item in saved],
            "investigation": {**{key: investigation[key] for key in ("id", "report_id", "state", "request", "result", "error")},
                              "fixes": fixes}}


def replay(example: dict) -> str:
    """Load a recorded example into the database. Idempotent: loading it again changes nothing."""
    for run in example["runs"]:
        artifact, spec = ExecutionArtifact.model_validate(run["artifact"]), ExperimentSpec.model_validate(run["spec"])
        db.import_artifact(artifact)
        db.import_job(job_id_for(str(artifact.artifact_id)), spec, str(artifact.artifact_id))
    db.save_suite(ExperimentSpec.model_validate(example["runs"][0]["spec"]))
    for item in example["comparisons"]:
        if db.get_comparison(item["id"]) is None:
            request = item["request"]
            report = compare_artifacts(
                db.get_artifact(request["baseline_id"]), db.get_artifact(request["candidate_id"]),
                PricingCatalog.model_validate(request["pricing"]),
                ComparisonPolicy.model_validate(request["policy"]) if request.get("policy") else None,
                request.get("monthly_requests"))
            db.save_comparison(request["baseline_id"], request["candidate_id"], request,
                               json.loads(report.model_dump_json()), report_id=item["id"])
    db.import_investigation(example["investigation"])
    db.save_setting(KEY + example["name"], {**example["recorded"], "report_ids": [item["id"] for item in example["comparisons"]]})
    return example["check_id"]
