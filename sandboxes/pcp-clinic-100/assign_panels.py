"""Assign Synthea patients to PCP panels and trim each record to years of service.

Population-level sandbox: 100-PCP adult primary care clinic.

Reads the FHIR R4 transaction bundles Synthea wrote (one per patient), and writes:
  <out>/patients/<same file name>   trimmed bundle, Patient.generalPractitioner set
  <out>/clinic-practitioners.json   transaction bundle: clinic Organization + PCPs
  <out>/roster.csv                  one row per patient
  <out>/panels.csv                  one row per PCP (target vs assigned panel size)
  <out>/summary.json                run parameters and counts

Trimming rule (per patient):
  clinic_start = reference_date - U(min_years, max_years) years of service.
  Encounters that start before clinic_start are dropped, together with every
  resource that references a dropped resource (observations, procedures,
  claims, reports and so on), repeated until nothing else references a dropped
  resource. Carried-over history is kept and its links to dropped encounters
  are removed: Condition, AllergyIntolerance and Immunization always, plus
  MedicationRequest, CarePlan and CareTeam while status is "active", because a
  new patient arrives with a problem list, allergy list, immunization history
  and current medications. Provenance keeps only targets that survive.

Input files are never modified. Standard library only.
"""

import argparse
import csv
import datetime as dt
import json
import multiprocessing
import os
import random
import sys
from pathlib import Path

PCP_SYSTEM = "urn:eocs:sandbox:pcp"
ORG_SYSTEM = "urn:eocs:sandbox:clinic"
CARRY_ALWAYS = {"Condition", "AllergyIntolerance", "Immunization"}
CARRY_IF_ACTIVE = {"MedicationRequest", "CarePlan", "CareTeam"}


def parse_args(argv):
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--input", help="Synthea FHIR output folder")
    p.add_argument("--out", help="output folder (created; must be empty)")
    p.add_argument("--reference-date",
                   help="YYYY-MM-DD; use the same date passed to Synthea with -r")
    p.add_argument("--pcp-range", help="e.g. 1-10: fill only these PCPs (batch builds)")
    p.add_argument("--plan", type=int, metavar="BATCH",
                   help="print panel targets per batch of BATCH PCPs and exit")
    p.add_argument("--workers", type=int, default=max(1, (os.cpu_count() or 2) - 2))
    p.add_argument("--seed", type=int, default=20261126)
    p.add_argument("--pcps", type=int, default=100)
    p.add_argument("--panel-mean", type=float, default=2000)
    p.add_argument("--panel-sd", type=float, default=350)
    p.add_argument("--panel-min", type=int, default=1200)
    p.add_argument("--panel-max", type=int, default=2800)
    p.add_argument("--min-years", type=float, default=1)
    p.add_argument("--max-years", type=float, default=10)
    p.add_argument("--pilot", action="store_true",
                   help="assign every patient to a single PCP (PCP-001)")
    return p.parse_args(argv)


def draw_panel_sizes(rng, n, mean, sd, lo, hi):
    """Normal(mean, sd) truncated to [lo, hi] by rejection, rounded to whole patients."""
    sizes = []
    while len(sizes) < n:
        x = round(rng.gauss(mean, sd))
        if lo <= x <= hi:
            sizes.append(x)
    return sizes


def refs_in(obj, found):
    """Collect every 'reference' string anywhere inside a resource."""
    if isinstance(obj, dict):
        ref = obj.get("reference")
        if isinstance(ref, str):
            found.add(ref)
        for v in obj.values():
            refs_in(v, found)
    elif isinstance(obj, list):
        for v in obj:
            refs_in(v, found)
    return found


def scrub(obj, dropped):
    """Remove any reference element pointing at a dropped resource. Returns False
    when obj itself is such a reference and should be removed by the caller."""
    if isinstance(obj, dict):
        if obj.get("reference") in dropped:
            return False
        for k in list(obj.keys()):
            v = obj[k]
            if isinstance(v, (dict, list)) and not scrub(v, dropped):
                del obj[k]
            elif isinstance(v, list) and not v:
                del obj[k]
        return True
    if isinstance(obj, list):
        obj[:] = [v for v in obj if scrub(v, dropped)]
        obj[:] = [v for v in obj if not (isinstance(v, dict) and not v)]
        return True
    return True


def is_carry_over(res):
    rt = res.get("resourceType")
    if rt in CARRY_ALWAYS:
        return True
    return rt in CARRY_IF_ACTIVE and res.get("status") == "active"


def trim_bundle(bundle, start_date):
    """Drop pre-service history in place. Returns (kept_enc, dropped_enc)."""
    entries = bundle.get("entry", [])
    start = start_date.isoformat()
    dropped = set()
    enc_total = 0
    for e in entries:
        r = e["resource"]
        if r.get("resourceType") == "Encounter":
            enc_total += 1
            if r.get("period", {}).get("start", "9999")[:10] < start:
                dropped.add(e["fullUrl"])
    enc_dropped = len(dropped)

    refs = {e["fullUrl"]: refs_in(e["resource"], set()) for e in entries}
    changed = True
    while changed:
        changed = False
        for e in entries:
            fu = e["fullUrl"]
            r = e["resource"]
            if fu in dropped or is_carry_over(r) or r.get("resourceType") in ("Patient", "Provenance"):
                continue
            if refs[fu] & dropped:
                dropped.add(fu)
                changed = True

    kept = []
    for e in entries:
        if e["fullUrl"] in dropped:
            continue
        r = e["resource"]
        if refs[e["fullUrl"]] & dropped:
            scrub(r, dropped)
        if r.get("resourceType") == "Provenance" and not r.get("target"):
            continue
        kept.append(e)
    bundle["entry"] = kept
    return enc_total - enc_dropped, enc_dropped


def practitioner_bundle(n):
    org_url = "urn:uuid:" + "00000000-0000-4000-8000-000000000000"
    entries = [{
        "fullUrl": org_url,
        "resource": {
            "resourceType": "Organization",
            "identifier": [{"system": ORG_SYSTEM, "value": "CLINIC-001"}],
            "active": True,
            "name": "Sandbox Adult Primary Care Clinic (synthetic)",
        },
        "request": {"method": "POST", "url": "Organization",
                    "ifNoneExist": f"identifier={ORG_SYSTEM}|CLINIC-001"},
    }]
    for i in range(1, n + 1):
        pid = f"PCP-{i:03d}"
        entries.append({
            "fullUrl": f"urn:uuid:00000000-0000-4000-8000-{i:012d}",
            "resource": {
                "resourceType": "Practitioner",
                "identifier": [{"system": PCP_SYSTEM, "value": pid}],
                "active": True,
                "name": [{"text": f"Synthetic PCP {i:03d}"}],
            },
            "request": {"method": "POST", "url": "Practitioner",
                        "ifNoneExist": f"identifier={PCP_SYSTEM}|{pid}"},
        })
    return {"resourceType": "Bundle", "type": "transaction", "entry": entries}


def process_patient(job):
    """Trim one patient file and set its PCP. Runs in a worker process."""
    src, dest, pid, start_iso, years = job
    bundle = json.loads(Path(src).read_text(encoding="utf-8"))
    patient = next((e["resource"] for e in bundle.get("entry", [])
                    if e["resource"].get("resourceType") == "Patient"), None)
    if patient is None:
        return None
    kept_enc, dropped_enc = trim_bundle(bundle, dt.date.fromisoformat(start_iso))
    patient["generalPractitioner"] = [{
        "reference": f"Practitioner?identifier={PCP_SYSTEM}|{pid}", "display": pid}]
    Path(dest).write_text(json.dumps(bundle), encoding="utf-8")
    return [patient.get("id"), Path(src).name, pid, start_iso, years, kept_enc, dropped_enc]


def parse_range(text, n):
    lo, hi = (int(x) for x in text.split("-"))
    if not 1 <= lo <= hi <= n:
        sys.exit(f"--pcp-range must lie within 1-{n}")
    return lo, hi


def main(argv):
    a = parse_args(argv)
    targets_all = draw_panel_sizes(random.Random(a.seed), a.pcps, a.panel_mean,
                                   a.panel_sd, a.panel_min, a.panel_max)
    if a.plan:
        print(f"panel target total: {sum(targets_all)}")
        for lo in range(1, a.pcps + 1, a.plan):
            hi = min(lo + a.plan - 1, a.pcps)
            print(f"PCPs {lo}-{hi}: {sum(targets_all[lo - 1:hi])} patients")
        return
    if not (a.input and a.out and a.reference_date):
        sys.exit("--input, --out and --reference-date are required unless --plan is given")
    src = Path(a.input)
    out = Path(a.out)
    if out.exists() and any(out.iterdir()):
        sys.exit(f"output folder is not empty: {out}")
    (out / "patients").mkdir(parents=True, exist_ok=True)
    ref_date = dt.date.fromisoformat(a.reference_date)

    patient_files = [f for f in sorted(src.glob("*.json")) if not f.name.startswith(
        ("hospitalInformation", "practitionerInformation", "payerInformation"))]

    if a.pilot:
        first, targets = 1, [len(patient_files)]
    else:
        lo, hi = parse_range(a.pcp_range or f"1-{a.pcps}", a.pcps)
        first, targets = lo, targets_all[lo - 1:hi]
    n_pcps = len(targets)

    # Plan every assignment first (deterministic), then trim in parallel.
    # Years of service come from a per-patient generator keyed on the file name,
    # so the result does not depend on worker count or processing order.
    jobs, assigned, slot, unassigned = [], [0] * n_pcps, 0, 0
    for f in patient_files:
        while slot < n_pcps and assigned[slot] >= targets[slot]:
            slot += 1
        if slot >= n_pcps:
            unassigned += 1
            continue
        days = random.Random(f"{a.seed}:{f.name}").uniform(
            a.min_years * 365.25, a.max_years * 365.25)
        start = ref_date - dt.timedelta(days=round(days))
        pid = f"PCP-{first + slot:03d}"
        jobs.append((str(f), str(out / "patients" / f.name), pid, start.isoformat(),
                     round(days / 365.25, 2)))
        assigned[slot] += 1

    with multiprocessing.Pool(a.workers) as pool:
        results = pool.map(process_patient, jobs, chunksize=8)
    roster_rows = [r for r in results if r is not None]
    no_encounters = sum(1 for r in roster_rows if r[5] == 0)

    (out / "clinic-practitioners.json").write_text(
        json.dumps(practitioner_bundle(1 if a.pilot else a.pcps), indent=1), encoding="utf-8")
    with open(out / "roster.csv", "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["patient_id", "source_file", "pcp_id", "clinic_start_date",
                    "years_of_service", "encounters_kept", "encounters_dropped"])
        w.writerows(roster_rows)
    with open(out / "panels.csv", "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["pcp_id", "target_panel_size", "assigned"])
        for i, (t, n) in enumerate(zip(targets, assigned), first):
            w.writerow([f"PCP-{i:03d}", t, n])

    summary = {
        "reference_date": a.reference_date, "seed": a.seed, "pilot": a.pilot,
        "pcp_range": None if a.pilot else [first, first + n_pcps - 1],
        "pcps": n_pcps, "panel_target_total": sum(targets),
        "patients_in_input": len(patient_files), "patients_assigned": len(roster_rows),
        "patients_unassigned_panels_full": unassigned,
        "panels_short": sum(1 for t, n in zip(targets, assigned) if n < t),
        "patients_with_no_encounters_in_service": no_encounters,
        "panel_params": None if a.pilot else {
            "mean": a.panel_mean, "sd": a.panel_sd, "min": a.panel_min, "max": a.panel_max},
        "years_of_service_range": [a.min_years, a.max_years],
    }
    (out / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main(sys.argv[1:])
