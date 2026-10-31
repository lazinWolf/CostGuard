"""Run persisted investigations without replaying a lost or cancelled lease."""

import threading

from . import db, gateway
from .execution import ExperimentSpec
from .investigation import AnalystSettings, EvidenceTools, investigate


def execute_investigation(investigation_id: str) -> None:
    stop = threading.Event()

    def heartbeat():
        while not stop.wait(15):
            db.update_investigation(investigation_id)

    thread = threading.Thread(target=heartbeat, daemon=True)
    thread.start()
    try:
        row = db.get_investigation(investigation_id)
        if row is None or row["state"] != "running":
            return
        saved = db.get_comparison(row["report_id"])
        left = db.get_artifact(saved["request"]["baseline_id"])
        right = db.get_artifact(saved["request"]["candidate_id"])
        source = row["request"].get("source_spec")
        tools = EvidenceTools(saved, left, right, ExperimentSpec.model_validate(source) if source else None)
        with gateway.client() as client:
            result = investigate(row["request"]["question"],
                AnalystSettings.model_validate(row["request"]["analyst"]), tools, client,
                progress=lambda result: db.update_investigation(investigation_id, result=result),
                should_continue=lambda: (db.get_investigation(investigation_id) or {}).get("state") == "running")
        db.update_investigation(investigation_id, result=result, state=result["state"], error=result["error"])
    except Exception as exc:
        db.update_investigation(investigation_id, state="failed",
                                error=f"Investigation failed ({type(exc).__name__}). Check analyst configuration and gateway.")
    finally:
        stop.set()
        thread.join(timeout=1)
