"""
run_region.py
=============
Πιλοτικές αναφορές για ΟΛΟΚΛΗΡΗ περιφέρεια.

Phase 1: per-bridge .docx για κάθε γέφυρα της περιφέρειας.
Phase 2: συναρμολόγηση του συνολικού region_<ΠΕΡΙΦΕΡΕΙΑ>.docx.

Usage (από τη ρίζα του project):
    py run_region.py ΚΜΑ                  # skip υπαρχόντων + συναρμολόγηση
    py run_region.py ΚΜΑ --force          # regen όλων των γεφυρών
    py run_region.py ΚΜΑ --no-regional    # μόνο Phase 1
    py run_region.py ΚΜΑ --dry-run        # πλάνο χωρίς data/αρχεία

Γέφυρες: regions/region_<ΠΕΡΙΦΕΡΕΙΑ>.txt· αν λείπει, δημιουργείται με
φιλτράρισμα του bridges.txt με το prefix της περιφέρειας (σε --dry-run
δεν γράφεται τίποτα).

Output:
    output/<ΠΕΡΙΦΕΡΕΙΑ>/<code_name>.docx         (+ αντίγραφα στο Πιλοτικές Αναφορές)
    output/<ΠΕΡΙΦΕΡΕΙΑ>/region_<ΠΕΡΙΦΕΡΕΙΑ>.docx (+ αντίγραφο στο Περιφερειακές Πιλοτικές Αναφορές)
    batches/run_region_<ΠΕΡΙΦΕΡΕΙΑ>.log
    batches/issue_log_<ΠΕΡΙΦΕΡΕΙΑ>_<timestamp>.xlsx

Αποτυχημένες γέφυρες ξαναδοκιμάζονται ΜΙΑ φορά στο τέλος. Exit code 0/1.
"""
from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

os.environ.setdefault("MAX_API_WORKERS", "1")
os.environ.setdefault("PYTHONIOENCODING", "utf-8")

# Same as run_full.py / test_run.py: make Greek output survive console
# codepage differences (Python configures stdio before this script runs).
sys.stdout.reconfigure(encoding="utf-8")
sys.stderr.reconfigure(encoding="utf-8")

import issue_log
from main import read_bridges_txt
from run_bridge import EXTRA_SAVE_DIR, fetch_plan, generate_one, print_plan

BASE_DIR = Path(__file__).parent
REGIONS_DIR = BASE_DIR / "regions"
BATCHES_DIR = BASE_DIR / "batches"
BRIDGES_TXT = BASE_DIR / "bridges.txt"
TEMPLATE = BASE_DIR / "template.docx"
REGION_TEMPLATE = BASE_DIR / "region_template.docx"
OUT_ROOT = BASE_DIR / "output"

# Same cloud folder that region_report.py copies the combined report into.
REGIONAL_REPORTS_DIR = Path(
    "C:/Users/fgkenas/Globiled/Marina Bagourdi - Μεταβλητές/Περιφερειακές Πιλοτικές Αναφορές"
)


def log(log_path: Path, msg: str) -> None:
    try:
        print(msg, flush=True)
    except UnicodeEncodeError:
        print(msg.encode("ascii", "replace").decode("ascii"), flush=True)
    try:
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(msg + "\n")
    except Exception:
        pass


def region_bridges(region_code: str, log_path: Path, allow_create: bool = True) -> tuple[Path, list[str]]:
    """The region's bridge list from regions/region_<code>.txt, falling back
    to filtering bridges.txt by the region prefix. With allow_create the
    fallback file is written (so region_report.py --bridges can use it)."""
    bridges_file = REGIONS_DIR / f"region_{region_code}.txt"
    if bridges_file.exists():
        return bridges_file, read_bridges_txt(bridges_file)

    code_names = [b for b in read_bridges_txt(BRIDGES_TXT)
                  if b.split("_")[0].strip() == region_code]
    if not code_names:
        log(log_path, f"  ! Καμία γέφυρα με prefix {region_code} στο {BRIDGES_TXT}")
        return bridges_file, []
    if allow_create:
        REGIONS_DIR.mkdir(parents=True, exist_ok=True)
        bridges_file.write_text("\n".join(code_names) + "\n", encoding="utf-8")
        log(log_path, f"  Δημιουργήθηκε {bridges_file} ({len(code_names)} γέφυρες από το bridges.txt)")
    return bridges_file, code_names


def phase1(code_names: list[str], region_code: str, force: bool, log_path: Path) -> tuple[list, list, list]:
    done, failed, skipped = [], [], []
    for idx, code_name in enumerate(code_names, start=1):
        log(log_path, f"\n[{idx}/{len(code_names)}] {code_name}...")
        plan = fetch_plan(code_name)
        if plan is None:
            log(log_path, f"  [FAIL] fetch απέτυχε για {code_name}")
            failed.append(code_name)
            continue
        log(log_path, f"  {plan['display_name']} | {plan['pilot_start']} → {plan['pilot_finish']} | "
                      f"{plan['n_sensors']} sensors")
        t_bridge = time.time()
        result = generate_one(code_name, region_code, force, data=plan["data"])
        if result == "skipped":
            log(log_path, f"  [skip] {code_name} υπάρχει ήδη")
            skipped.append(code_name)
        elif result:
            log(log_path, f"  [OK] {code_name} ({time.time() - t_bridge:.0f}s)")
            done.append(code_name)
        else:
            log(log_path, f"  [FAIL] {code_name}")
            failed.append(code_name)
    return done, failed, skipped


def retry_failed(failed: list[str], region_code: str, log_path: Path) -> tuple[list, list]:
    if not failed:
        return [], failed
    log(log_path, f"\n=== Retry {len(failed)} αποτυχημένων γεφυρών (μία φορά) ===")
    done, still_failed = [], []
    for code_name in failed:
        log(log_path, f"\n[retry] {code_name}...")
        t_bridge = time.time()
        result = generate_one(code_name, region_code, force=True)
        if result and result != "skipped":
            log(log_path, f"  [OK] {code_name} ({time.time() - t_bridge:.0f}s)")
            done.append(code_name)
        else:
            log(log_path, f"  [FAIL] {code_name} (και στο retry)")
            still_failed.append(code_name)
    return done, still_failed


def phase2(region_code: str, bridges_file: Path, log_path: Path) -> int:
    out_path = OUT_ROOT / region_code / f"region_{region_code}.docx"
    log(log_path, f"\n=== Phase 2: συναρμολόγηση {out_path} ===")
    cmd = [
        sys.executable, str(BASE_DIR / "region_report.py"),
        "--bridges", str(bridges_file),
        "--template", str(TEMPLATE),
        "--region-template", str(REGION_TEMPLATE),
        "--out", str(out_path),
        "--skip-bridge-gen",
    ]
    log(log_path, f"  {' '.join(cmd)}")
    result = subprocess.run(
        cmd,
        cwd=str(BASE_DIR),
        env={**os.environ, "PYTHONIOENCODING": "utf-8", "MAX_API_WORKERS": "1"},
    )
    if result.returncode != 0:
        log(log_path, f"  ! region_report.py exit code {result.returncode}")
        return 1
    log(log_path, f"  [OK] {out_path}")
    if EXTRA_SAVE_DIR:
        try:
            dest = EXTRA_SAVE_DIR / region_code
            dest.mkdir(parents=True, exist_ok=True)
            shutil.copy2(str(out_path), str(dest / out_path.name))
            log(log_path, f"  [OK] αντίγραφο -> {dest / out_path.name}")
        except Exception as exc:
            issue_log.add_issue("copy_failed", bridge=f"region_{region_code}", error=str(exc))
    return 0


def dry_run(code_names: list[str], region_code: str, no_regional: bool, log_path: Path) -> None:
    printer = lambda msg: log(log_path, msg)
    log(log_path, f"=== [dry-run] {region_code}: {len(code_names)} γέφυρες ===")
    missing = 0
    for idx, code_name in enumerate(code_names, start=1):
        log(log_path, f"\n[{idx}/{len(code_names)}] {code_name}")
        plan = fetch_plan(code_name)
        if plan is None:
            missing += 1
            continue
        print_plan(plan, force=False, printer=printer)
    if not no_regional:
        log(log_path, f"\n  Θα παραχθεί επίσης: {OUT_ROOT / region_code / f'region_{region_code}.docx'}")
        log(log_path, f"  Αντίγραφο: {REGIONAL_REPORTS_DIR / f'region_{region_code}.docx'}")
    log(log_path, "\n[dry-run] Δεν τραβήχτηκαν sensor data, δεν γράφτηκε κανένα αρχείο.")
    if missing:
        log(log_path, f"  ! {missing} γέφυρες δεν βρέθηκαν στο bridge API")
    if issue_log.has_issues():
        log(log_path, "  Issues που θα καταγραφούν:")
        for iss in issue_log.issues():
            log(log_path, f"    - {iss['type']}: {iss.get('bridge', '')} {iss.get('error', '')}")


def main() -> None:
    ap = argparse.ArgumentParser(description="Πιλοτικές αναφορές για ολόκληρη περιφέρεια.")
    ap.add_argument("region", help="Κωδικός περιφέρειας, π.χ. ΚΜΑ")
    ap.add_argument("--dry-run", action="store_true",
                    help="Τύπωσε το πλάνο χωρίς sensor data και χωρίς να γράψει αρχεία.")
    ap.add_argument("--force", action="store_true",
                    help="Regen όλων των per-bridge .docx.")
    ap.add_argument("--no-regional", action="store_true",
                    help="Παράλειψη Phase 2 (συνολικό regional report).")
    args = ap.parse_args()
    region_code = args.region.strip()

    BATCHES_DIR.mkdir(parents=True, exist_ok=True)
    log_path = BATCHES_DIR / f"run_region_{region_code}.log"
    if log_path.exists():
        log_path.unlink()

    issue_log.clear_issues()
    log(log_path, f"=== Περιφέρεια {region_code} — {time.strftime('%Y-%m-%d %H:%M:%S')} ===")
    if EXTRA_SAVE_DIR:
        log(log_path, f"  EXTRA_SAVE_DIR: {EXTRA_SAVE_DIR}")

    bridges_file, code_names = region_bridges(region_code, log_path, allow_create=not args.dry_run)
    if not code_names:
        log(log_path, f"  ! Καμία γέφυρα για την περιφέρεια {region_code}")
        sys.exit(2)

    if args.dry_run:
        dry_run(code_names, region_code, args.no_regional, log_path)
        return

    t0 = time.time()
    done, failed, skipped = phase1(code_names, region_code, args.force, log_path)
    log(log_path, f"\n=== Phase 1 σε {(time.time() - t0) / 60:.1f} min: "
                  f"{len(done)} OK, {len(skipped)} skipped, {len(failed)} failed ===")
    if failed:
        log(log_path, f"  Αποτυχημένες: {', '.join(failed)}")
        retried, failed = retry_failed(failed, region_code, log_path)
        if retried:
            done += retried
            log(log_path, f"  Σώθηκαν στο retry: {', '.join(retried)}")

    regional_rc = 0
    if not args.no_regional:
        if failed:
            log(log_path, "  Κάποιες γέφυρες απέτυχαν — το regional θα συναρμολογήσει όσα αρχεία υπάρχουν.")
        regional_rc = phase2(region_code, bridges_file, log_path)

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    saved = issue_log.save_issues(BATCHES_DIR / f"issue_log_{region_code}_{stamp}.xlsx")
    if saved:
        log(log_path, f"  Issue log: {saved}")

    log(log_path, f"\n=== Περιφέρεια {region_code} ολοκληρώθηκε σε {(time.time() - t0) / 60:.1f} min ===")
    sys.exit(1 if (failed or regional_rc) else 0)


if __name__ == "__main__":
    main()
