"""Deterministic end-to-end check against the Compose demo stack."""

import asyncio
import json
import os
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

import httpx
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from costguard.bootstrap_gateway import bootstrap
from costguard import db
from costguard.execution import ExperimentSpec

ROOT = Path(__file__).resolve().parents[1]
BASE = os.environ.get("COSTGUARD_URL", "http://app:8000")


def request(client, method, path, **kwargs):
    response = client.request(method, path, **kwargs)
    response.raise_for_status()
    return response.json()


def completed_job(client, spec, job_id=None):
    job_id = job_id or request(client, "POST", "/api/v1/jobs", json=spec)["job_id"]
    deadline = time.monotonic() + 90
    while time.monotonic() < deadline:
        job = request(client, "GET", f"/api/v1/jobs/{job_id}")
        if job["state"] == "completed":
            assert job["progress"] == job["total"] == len(spec["cases"])
            return job["artifact_id"]
        if job["state"] not in {"queued", "running"}:
            raise AssertionError(f"job ended unexpectedly: {job}")
        time.sleep(0.25)
    raise AssertionError(f"job did not finish: {job_id}")


async def check_mcp(report_id):
    server = StdioServerParameters(
        command=sys.executable, args=["-m", "costguard.mcp_server"],
        cwd=ROOT, env=os.environ.copy(),
    )
    async with stdio_client(server) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            names = {tool.name for tool in (await session.list_tools()).tools}
            assert {"list_runs", "get_report", "inspect_hotspots", "list_investigations", "get_investigation"} <= names
            result = await session.call_tool("inspect_hotspots", {"report_id": report_id})
            assert not result.is_error, result
            assert result.content


def check_workbench(client):
    """Exercise the real gateway/runner path, including adversarial fixture economics."""
    example=request(client,"GET","/api/v1/examples/triage")
    spec=example["spec"]
    demo=request(client,"POST","/api/v1/demo")
    baseline_id=completed_job(client,spec,demo["baseline_job_id"])
    longer_id=completed_job(client,spec,demo["candidate_job_id"])
    baseline=request(client,"GET",f"/api/v1/artifacts/{baseline_id}")
    assert baseline["schema_version"]==2 and baseline["suite_digest"]
    assert all(case["output_text"] and case["evaluation"]["score"]==1 for case in baseline["cases"])
    source=request(client,"GET",f"/api/v1/artifacts/{baseline_id}/source")["spec"]
    cloned=request(client,"POST","/api/v1/candidates",json={"baseline_id":baseline_id,
        "provider":source["provider"],"model":source["model"],"name":"Cheap but wrong triage",
        "system_prompt":example["variants"]["cheap_wrong"],"pricing":source["pricing"],"limits":source["limits"]})
    wrong_id=completed_job(client,spec,cloned["job_id"])
    def compare(candidate_id):
        return request(client,"POST","/api/v1/comparisons",json={"baseline_id":baseline_id,
            "candidate_id":candidate_id,"pricing":spec["pricing"],"policy":example["policy"]})
    longer=compare(longer_id);wrong=compare(wrong_id)
    assert longer["report"]["status"]==wrong["report"]["status"]=="fail"
    assert longer["report"]["economic"]["cost_per_request_usd"]["delta"]>0
    assert wrong["report"]["economic"]["cost_per_request_usd"]["delta"]<0
    assert wrong["report"]["economic"]["quality_score"]["candidate"]<1
    assert longer["report"]["partition_results"]["validation"]["scored_pairs"]==4
    # Fixtures must not call a real analyst even if one is configured globally.
    request(client,"PUT","/api/v1/analyst",json={"provider":"do-not-call","model":"real",
        "pricing_version":"test","input_per_million_usd":100,"output_per_million_usd":100})
    inv=request(client,"POST","/api/v1/investigations",json={"report_id":longer["id"],"question":"Investigate this fixture regression"})["investigation_id"]
    deadline=time.monotonic()+90
    while time.monotonic()<deadline:
        result=request(client,"GET",f"/api/v1/investigations/{inv}")
        if result["state"]=="completed":break
        assert result["state"] in {"queued","running"},result
        time.sleep(.25)
    else: raise AssertionError("triage fixture investigation did not complete")
    assert result["request"]["analyst"]["provider"]=="costguard-mock"
    assert request(client,"GET","/api/v1/analyst")["settings"]["provider"]=="do-not-call"
    approval={"system_prompt":example["variants"]["baseline"]}
    path=f"/api/v1/investigations/{inv}"
    approved=request(client,"POST",path+"/run-proposal",json=approval)["job_id"]
    assert request(client,"POST",path+"/run-proposal",json=approval)["job_id"]==approved
    completed_job(client,spec,approved)
    outcome=request(client,"POST",path+"/compare-proposal")
    assert outcome["candidate_report_id"]
    measured=request(client,"GET",f"/api/v1/comparisons/{outcome['report_id']}")
    assert measured["report"]["status"]=="pass",measured
    improved=request(client,"GET",f"/api/v1/comparisons/{outcome['candidate_report_id']}")
    assert improved["report"]["economic"]["cost_per_request_usd"]["delta"]<0
    bundle=request(client,"GET",f"/api/v1/comparisons/{outcome['report_id']}/export")
    assert bundle["baseline"]["suite_digest"]==bundle["candidate"]["suite_digest"]
    exported=client.get(f"/api/v1/comparisons/{outcome['report_id']}/export?format=markdown")
    exported.raise_for_status();assert "Pricing and policy snapshot" in exported.text
    # A lost workload worker keeps its in-flight receipt and produces partial evidence.
    checkpoint=json.loads(json.dumps(baseline));checkpoint["artifact_id"]=str(uuid4())
    checkpoint["cases"]=checkpoint["cases"][:1]
    active=checkpoint["cases"][0];active.update(status="error",quality_score=None,evaluator=None,evaluation=None,output_text=None)
    active["calls"][0].update(status="in_flight",usage_source="missing",input_tokens=None,output_tokens=None,response_text=None,finish_reason=None)
    stale_id=str(uuid4())
    with db.session_scope() as session:
        session.add(db.JobRow(id=stale_id,state="running",spec=spec,total=len(spec["cases"]),checkpoint=checkpoint,
            lease_until=datetime.now(timezone.utc)-timedelta(seconds=10)))
    assert db.claim_job() is None
    stale=request(client,"GET",f"/api/v1/jobs/{stale_id}")
    assert stale["state"]=="interrupted" and stale["artifact_id"]
    partial=request(client,"GET",f"/api/v1/artifacts/{stale['artifact_id']}")
    assert partial["cases"][0]["calls"][0]["status"]=="in_flight"
    assert client.put(f"/api/v1/baselines/{spec['suite_id']}",json={"artifact_id":stale["artifact_id"]}).status_code==422
    for page in ("/workbench",f"/workbench?baseline_id={baseline_id}&candidate_id={wrong_id}",f"/reports/{wrong['id']}",f"/investigations/{inv}"):
        response=client.get(page);response.raise_for_status()
        assert "<html" in response.text.lower()


def _main():
    bootstrap()
    baseline = json.loads((ROOT / "examples/experiment_baseline.json").read_text())
    candidate = json.loads((ROOT / "examples/experiment_candidate.json").read_text())
    with httpx.Client(base_url=BASE, timeout=10) as client:
        assert request(client, "GET", "/api/v1/ready")["status"] == "ok"
        gateway = request(client, "GET", "/api/v1/gateway")
        assert gateway["status"] == "connected", gateway
        assert request(client, "POST", "/api/v1/suites", json=baseline)["suite_id"] == "demo-suite"
        demo = {}
        baseline_id = completed_job(client, baseline, demo.get("baseline_job_id"))
        candidate_id = completed_job(client, candidate, demo.get("candidate_job_id"))
        left = request(client, "GET", f"/api/v1/artifacts/{baseline_id}")
        right = request(client, "GET", f"/api/v1/artifacts/{candidate_id}")
        for artifact in (left, right):
            assert len(artifact["cases"]) == 3
            assert all(case["status"] == "ok" for case in artifact["cases"])
            assert all(call["usage_source"] == "gateway"
                       for case in artifact["cases"] for call in case["calls"])
        assert request(client, "POST", "/api/v1/artifacts", json=left)["created"] is False
        request(client, "PUT", "/api/v1/baselines/demo-suite",
                json={"artifact_id": baseline_id})
        assert request(client, "GET", "/api/v1/baselines/demo-suite")["artifact_id"] == baseline_id
        saved = request(client, "POST", "/api/v1/comparisons", json={
            "baseline_id": baseline_id, "candidate_id": candidate_id,
            "pricing": baseline["pricing"],
            "policy": {"max_cost_increase_percent": 10, "min_candidate_quality_score": 1},
            "monthly_requests": 100000,
        })
        report = saved["report"]
        assert report["status"] == "fail", report
        assert report["economic"]["cost_per_request_usd"]["delta"] > 0
        assert report["economic"]["quality_score"]["delta"] == 0
        assert report["missing_usage_calls"] == 0
        assert request(client, "GET", f"/api/v1/comparisons/{saved['id']}")["report"]["status"] == "fail"
        # The investigation uses a separate analyst model, then proposes a measured experiment.
        request(client, "PUT", "/api/v1/analyst", json={
            "provider": "costguard-mock", "model": "analyst", "pricing_version": "mock-analyst",
            "input_per_million_usd": 0, "output_per_million_usd": 0,
        })
        investigation_id = request(client, "POST", "/api/v1/investigations", json={
            "report_id": saved["id"], "question": "Investigate the regression and propose a prompt experiment",
        })["investigation_id"]
        deadline = time.monotonic() + 90
        while time.monotonic() < deadline:
            investigation = request(client, "GET", f"/api/v1/investigations/{investigation_id}")
            if investigation["state"] == "completed":
                break
            assert investigation["state"] in {"queued", "running"}, investigation
            time.sleep(0.25)
        else:
            raise AssertionError("Investigation did not complete")
        assert investigation["result"]["conclusion"]["findings"]
        assert len(investigation["result"]["calls"]) == 3, investigation
        assert investigation["result"]["proposal"]
        assert investigation["proposal_job_id"] is None  # Proposal alone makes no workload calls.
        path = f"/api/v1/investigations/{investigation_id}"
        proposed_job = request(client, "POST", path + "/run-proposal")["job_id"]
        assert request(client, "POST", path + "/run-proposal")["job_id"] == proposed_job
        deadline = time.monotonic() + 90
        while time.monotonic() < deadline:
            job = request(client, "GET", f"/api/v1/jobs/{proposed_job}")
            if job["state"] == "completed":
                break
            assert job["state"] in {"queued", "running"}, job
            time.sleep(0.25)
        else:
            raise AssertionError("Proposed experiment did not complete")
        outcome_id = request(client, "POST", path + "/compare-proposal")["report_id"]
        assert request(client, "POST", path + "/compare-proposal")["report_id"] == outcome_id
        outcome = request(client, "GET", f"/api/v1/comparisons/{outcome_id}")
        assert outcome["report"]["status"] == "pass", outcome
        assert outcome["request"]["baseline_id"] == baseline_id
        assert outcome["report"]["economic"]["quality_score"]["delta"] == 0
        # Simulate a lost worker during a model call. It must not be replayed or
        # represented as fully accounted spend, even though earlier calls were known.
        stale_id = str(uuid4())
        with db.session_scope() as session:
            session.add(db.InvestigationRow(id=stale_id, report_id=saved["id"], state="running",
                request=investigation["request"], result={"cost_known": False, "cost_usd": None,
                    "known_cost_usd": "0.001", "calls": [{"status": "in_flight"}]},
                lease_until=datetime.now(timezone.utc) - timedelta(seconds=10)))
        assert db.claim_investigation() != stale_id
        stale = request(client, "GET", f"/api/v1/investigations/{stale_id}")
        assert stale["state"] == "interrupted"
        assert stale["result"]["cost_known"] is False
        scenario_pricing = json.loads(json.dumps(baseline["pricing"]))
        scenario_pricing["version"] = "hypothetical"
        scenario_pricing["entries"][0]["input_per_million_usd"] = 5
        scenario = request(client, "POST", "/api/v1/scenarios", json={
            "artifact_id": baseline_id, "pricing": scenario_pricing,
            "monthly_requests": 100000,
        })
        assert scenario["status"] == "hypothetical"
        assert scenario["cost_per_request_usd"]["candidate"] < scenario["cost_per_request_usd"]["baseline"]
        for page in ("/", "/connections", "/experiments", "/reports",
                     f"/reports/{saved['id']}", f"/artifacts/{baseline_id}", "/scenarios",
                     "/investigations", f"/investigations/{investigation_id}"):
            response = client.get(page)
            response.raise_for_status()
            assert "<html" in response.text.lower(), page
        if os.environ.get("COSTGUARD_DEMO") == "1":
            check_workbench(client)

    asyncio.run(check_mcp(saved["id"]))

    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        for name, data in (("baseline", left), ("candidate", right),
                           ("pricing", baseline["pricing"]),
                           ("policy", {"max_cost_increase_percent": 10})):
            (root / f"{name}.json").write_text(json.dumps(data))
        output = root / "gate.json"
        result = subprocess.run([
            sys.executable, "-m", "costguard", "gate",
            "--baseline", str(root / "baseline.json"),
            "--candidate", str(root / "candidate.json"),
            "--pricing", str(root / "pricing.json"),
            "--policy", str(root / "policy.json"),
            "--json-output", str(output),
        ], check=False, capture_output=True, text=True)
        assert result.returncode == 1, result.stderr
        assert json.loads(output.read_text())["status"] == "fail"
    print("CostGuard demo integration passed: gateway, jobs, artifacts, comparison, policy, scenario, investigation, proposed experiment, UI, CLI, MCP")


def main():
    from costguard.investigation import AnalystSettings
    original = db.get_analyst_settings()
    try:
        _main()
    finally:
        restored = original or {"provider":"costguard-mock","model":"analyst","pricing_version":"fixture-free",
                                "input_per_million_usd":0,"output_per_million_usd":0}
        db.save_analyst_settings(AnalystSettings.model_validate(restored))


if __name__ == "__main__":
    main()
