"""
run_bridge.py
=============
Πιλοτική αναφορά για ΜΙΑ γέφυρα.

Ακολουθεί τα conventions των batch runners του project (run_noa_batches.py
κ.λπ.) και την ίδια λογική με το monthly batch: --dry-run, retry μία φορά,
issue log σε .xlsx, EXTRA_SAVE_DIR.

Usage (από τη ρίζα του project):
    py run_bridge.py ΚΜΑ_001_Β01               # παράγει (skip αν υπάρχει)
    py run_bridge.py ΚΜΑ_001_Β01 --force       # regen ακόμα κι αν υπάρχει
    py run_bridge.py ΚΜΑ_001_Β01 --dry-run     # τυπώνει το πλάνο, χωρίς data/αρχεία

Output:
    output/<ΠΕΡΙΦΕΡΕΙΑ>/<code_name>.docx
    + αντίγραφο στο Πιλοτικές Αναφορές/<ΠΕΡΙΦΕΡΕΙΑ>/ (από την generate_bridge_docx)
    + προαιρετικό αντίγραφο στο EXTRA_SAVE_DIR (env var)
    + batches/issue_log_<code_name>_<timestamp>.xlsx

Σε αποτυχία η αναφορά ξαναδοκιμάζεται ΜΙΑ φορά. Exit code 0/1.
"""
from __future__ import annotations

import argparse
import os
import shutil
import sys
import time
from datetime import datetime
from pathlib import Path

os.environ.setdefault("PYTHONIOENCODING", "utf-8")

# Same as run_full.py / test_run.py: make Greek output survive console
# codepage differences (Python configures stdio before this script runs).
sys.stdout.reconfigure(encoding="utf-8")
sys.stderr.reconfigure(encoding="utf-8")

import issue_log
from bridge_info import build_tag_map, fetch_bridge
from main import PILOT_REPORTS_DIR, generate_bridge_docx, safe_filename

BASE_DIR = Path(__file__).parent
TEMPLATE = BASE_DIR / "template.docx"
OUT_ROOT = BASE_DIR / "output"
BATCHES_DIR = BASE_DIR / "batches"

EXTRA_SAVE_DIR = Path(os.environ["EXTRA_SAVE_DIR"]) if os.getenv("EXTRA_SAVE_DIR") else None


def log(msg: str) -> None:
    try:
        print(msg, flush=True)
    except UnicodeEncodeError:
        print(msg.encode("ascii", "replace").decode("ascii"), flush=True)


def fetch_plan(code_name: str) -> dict | None:
    """Fetch bridge metadata ONLY (no sensor data): region, display name,
    pilot window, sensor count. Returns None on fetch failure (an issue
    is recorded here)."""
    try:
        data = fetch_bridge(code_name)
    except Exception as exc:
        issue_log.add_issue("fetch_failed", bridge=code_name, error=f"{type(exc).__name__}: {exc}")
        return None
    if not data:
        issue_log.add_issue("fetch_failed", bridge=code_name, error="κενή απάντηση από το bridge API")
        return None

    region_code = code_name.split("_")[0].strip()
    sensors: list[dict] = []
    for region in data.get("regions", []):
        for bridge in region.get("bridges", []):
            for site in bridge.get("sites", []):
                for sensor in site.get("sensors", []):
                    s = dict(sensor)
                    s["site_id"] = site.get("site_id")
                    sensors.append(s)

    pilot_start = pilot_finish = ""
    for s in sensors:
        pilot_start = pilot_start or (s.get("Pilot_Date_Start") or "")
        pilot_finish = pilot_finish or (s.get("Pilot_Date_Finish") or "")
        if pilot_start and pilot_finish:
            break

    try:
        tags = build_tag_map(data)
    except Exception:
        tags = {}
    display_name = tags.get("BRIDGE_INFO_NAME") or tags.get("NAME") or code_name

    if not sensors:
        issue_log.add_issue("no_sensors", bridge=code_name)
    elif not (pilot_start and pilot_finish):
        issue_log.add_issue("no_pilot_dates", bridge=code_name)

    return {
        "code_name": code_name,
        "region_code": region_code,
        "display_name": display_name,
        "pilot_start": pilot_start,
        "pilot_finish": pilot_finish,
        "n_sensors": len(sensors),
        "out_path": OUT_ROOT / region_code / f"{code_name}.docx",
        "pilot_copy": PILOT_REPORTS_DIR / region_code / f"{code_name}.docx",
        "data": data,
    }


def print_plan(plan: dict, force: bool, printer=None) -> None:
    p = printer or log
    p(f"  Γέφυρα:           {plan['code_name']}")
    p(f"  Όνομα:            {plan['display_name']}")
    p(f"  Περιφέρεια:       {plan['region_code']}")
    window = (f"{plan['pilot_start']} → {plan['pilot_finish']}"
              if plan["pilot_start"] else "(δεν βρέθηκαν πιλοτικές ημερομηνίες)")
    p(f"  Πιλοτική περίοδος: {window}")
    p(f"  Αισθητήρες:       {plan['n_sensors']}")
    p(f"  Παράγεται:        {plan['out_path']}")
    p(f"  Αντίγραφο:        {plan['pilot_copy']}")
    if EXTRA_SAVE_DIR:
        p(f"  EXTRA_SAVE_DIR:   {EXTRA_SAVE_DIR / plan['region_code'] / plan['out_path'].name}")
    exists = plan["out_path"].exists()
    note = "" if force or not exists else " (παραλείπεται χωρίς --force)"
    p(f"  Υπάρχει ήδη:      {'ναι' if exists else 'όχι'}{note}")


def generate_one(code_name: str, region_code: str, force: bool, data: dict | None = None) -> str | None:
    """Generate the pilot .docx for one bridge.

    Returns the output path (str) on success, 'skipped' when the file
    already exists (without --force), None on failure (issue recorded here).
    """
    out_dir = OUT_ROOT / region_code
    out_path = out_dir / f"{code_name}.docx"
    if out_path.exists() and not force:
        log(f"  [skip] {out_path} υπάρχει ήδη (--force για regen)")
        return "skipped"

    try:
        result = generate_bridge_docx(
            code_name=code_name,
            template=TEMPLATE,
            out_dir=out_dir,
            extra_tags={"CODE_NAME": code_name, "BRIDGE_NUMBER": "1"},
            bridge_number=1,
            region_code=region_code,
            data=data,
        )
    except Exception as exc:
        issue_log.add_issue("report_failed", bridge=code_name, error=f"{type(exc).__name__}: {exc}")
        return None

    if not result:
        issue_log.add_issue("report_failed", bridge=code_name, error="generate_bridge_docx επέστρεψε None")
        return None

    if EXTRA_SAVE_DIR:
        try:
            dest = EXTRA_SAVE_DIR / region_code
            dest.mkdir(parents=True, exist_ok=True)
            shutil.copy2(str(result), str(dest / result.name))
            log(f"  [OK] αντίγραφο -> {dest / result.name}")
        except Exception as exc:
            issue_log.add_issue("copy_failed", bridge=code_name, error=str(exc))
    return str(result)


def save_issue_log(code_name: str) -> None:
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    path = BATCHES_DIR / f"issue_log_{safe_filename(code_name)}_{stamp}.xlsx"
    saved = issue_log.save_issues(path)
    if saved:
        log(f"  Issue log: {saved}")


def print_issues() -> None:
    for iss in issue_log.issues():
        log(f"    - {iss['type']}: {iss.get('bridge', '')} {iss.get('error', '')}")


def main() -> None:
    ap = argparse.ArgumentParser(description="Πιλοτική αναφορά για μία γέφυρα.")
    ap.add_argument("code_name", help="Γέφυρα, π.χ. ΚΜΑ_001_Β01")
    ap.add_argument("--dry-run", action="store_true",
                    help="Τύπωσε το πλάνο χωρίς sensor data και χωρίς να γράψει αρχεία.")
    ap.add_argument("--force", action="store_true",
                    help="Regen ακόμα κι αν το .docx υπάρχει ήδη.")
    args = ap.parse_args()

    code_name = args.code_name.strip()
    issue_log.clear_issues()

    log(f"=== Πιλοτική αναφορά: {code_name} ===")
    plan = fetch_plan(code_name)
    if plan is None:
        log("  ! Δεν βρέθηκε η γέφυρα (ή το bridge API δεν απάντησε).")
        save_issue_log(code_name)
        print_issues()
        sys.exit(1)

    print_plan(plan, args.force)

    if args.dry_run:
        log("\n[dry-run] Δεν τραβήχτηκαν sensor data, δεν γράφτηκε κανένα αρχείο.")
        if issue_log.has_issues():
            log("  Issues που θα καταγραφούν:")
            print_issues()
        return

    t0 = time.time()
    result = generate_one(code_name, plan["region_code"], args.force, data=plan["data"])
    if result is None:
        log("  ! Αποτυχία — retry μία φορά...")
        result = generate_one(code_name, plan["region_code"], force=True, data=plan["data"])

    save_issue_log(code_name)
    ok = result is not None
    log(f"\n=== {'ΟΛΟΚΛΗΡΩΘΗΚΕ' if ok else 'ΑΠΕΤΥΧΕ'} σε {(time.time() - t0) / 60:.1f} min ===")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
