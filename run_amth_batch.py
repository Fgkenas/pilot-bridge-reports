"""
AMTH batch runner: per-bridge pilot reports + regional report.

Phase 1: Generate per-bridge .docx reports for all AMTH bridges
         sequentially (resume: skip existing files unless --force).
Phase 2: Assemble the combined regional report (region_ΑΜΘ.docx)
         from the per-bridge files.

Usage:
    py run_amth_batch.py             # resume (skip existing bridges)
    py run_amth_batch.py --force     # regenerate everything
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time
from pathlib import Path

os.environ.setdefault("MAX_API_WORKERS", "1")
os.environ.setdefault("PYTHONIOENCODING", "utf-8")

from main import generate_bridge_docx, read_bridges_txt, PILOT_REPORTS_DIR

BASE_DIR = Path(__file__).parent
REGION = "ΑΜΘ"
BRIDGES_FILE = BASE_DIR / "regions" / f"region_{REGION}.txt"
TEMPLATE = BASE_DIR / "template.docx"
REGION_TEMPLATE = BASE_DIR / "region_template.docx"
OUT_DIR = BASE_DIR / "output" / REGION
REGION_OUT = OUT_DIR / f"region_{REGION}.docx"
LOG_FILE = BASE_DIR / "run_amth_batch.log"

PYTHON_EXE = r"C:\Users\fgkenas\AppData\Local\Python\bin\python.exe"


def log(msg: str) -> None:
    try:
        print(msg, flush=True)
    except UnicodeEncodeError:
        print(msg.encode("ascii", "replace").decode("ascii"), flush=True)
    try:
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(msg + "\n")
    except Exception:
        pass


def phase1_bridges(force: bool) -> tuple[list[str], list[str]]:
    code_names = read_bridges_txt(BRIDGES_FILE)
    if not code_names:
        log(f"No bridges found in {BRIDGES_FILE}")
        sys.exit(1)

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    log(f"=== Phase 1: {len(code_names)} AMTH bridges -> {OUT_DIR} ===")

    session, csrftoken = None, ""
    try:
        from bridge_info import resensys_login
        session, csrftoken = resensys_login()
        log("  Resensys login OK (shared session)")
    except Exception as exc:
        log(f"  ! Resensys login failed (WLS charts will be skipped): {exc}")

    done, failed = [], []
    t0 = time.time()
    for idx, code_name in enumerate(code_names, start=1):
        out_path = OUT_DIR / f"{code_name}.docx"
        if out_path.exists() and not force:
            log(f"\n[{idx}/{len(code_names)}] [skip] {code_name}.docx already exists")
            done.append(code_name)
            continue

        log(f"\n[{idx}/{len(code_names)}] Processing {code_name}...")
        t_bridge = time.time()
        try:
            result = generate_bridge_docx(
                code_name=code_name,
                template=TEMPLATE,
                out_dir=OUT_DIR,
                extra_tags={"CODE_NAME": code_name, "BRIDGE_NUMBER": str(idx)},
                bridge_number=idx,
                session=session,
                csrftoken=csrftoken,
                region_code=REGION,
            )
            if result:
                log(f"  [OK] {code_name} ({time.time() - t_bridge:.0f}s)")
                done.append(code_name)
            else:
                log(f"  [FAIL] {code_name} (generate_bridge_docx returned None)")
                failed.append(code_name)
        except Exception as exc:
            log(f"  [FAIL] {code_name}: {exc}")
            failed.append(code_name)

    log(f"\n=== Phase 1 done in {(time.time() - t0) / 60:.1f} min: {len(done)} OK, {len(failed)} failed ===")
    if failed:
        log(f"  Failed bridges: {', '.join(failed)}")
    return done, failed


def phase2_region() -> int:
    log(f"\n=== Phase 2: assembling regional report {REGION_OUT} ===")
    cmd = [
        PYTHON_EXE, "region_report.py",
        "--bridges", str(BRIDGES_FILE),
        "--template", str(TEMPLATE),
        "--region-template", str(REGION_TEMPLATE),
        "--out", str(REGION_OUT),
        "--skip-bridge-gen",
    ]
    log(f"  {' '.join(cmd)}")
    result = subprocess.run(
        cmd,
        cwd=str(BASE_DIR),
        env={**os.environ, "PYTHONIOENCODING": "utf-8", "MAX_API_WORKERS": "1"},
    )
    if result.returncode != 0:
        log(f"  ! region_report.py exited with code {result.returncode}")
    return result.returncode


def main() -> None:
    ap = argparse.ArgumentParser(description="Run all AMTH pilot reports + regional report in one batch.")
    ap.add_argument("--force", action="store_true",
                    help="Regenerate per-bridge .docx files even if they already exist.")
    args = ap.parse_args()

    if LOG_FILE.exists():
        LOG_FILE.unlink()

    log(f"=== AMTH batch started {time.strftime('%Y-%m-%d %H:%M:%S')} (force={args.force}) ===")

    done, failed = phase1_bridges(args.force)

    if failed:
        log("  Some bridges failed - regional report will assemble only the available files.")
    if not done and not failed:
        log("Nothing to do.")
        sys.exit(1)

    rc = phase2_region()
    if rc == 0:
        log(f"\n=== Batch complete. Regional report: {REGION_OUT} ===")
        log(f"  Per-bridge reports: {OUT_DIR}")
        log(f"  Copies also in: {PILOT_REPORTS_DIR / REGION}")
    else:
        log(f"\n=== Batch finished with regional phase error (code {rc}) ===")
        sys.exit(rc)


if __name__ == "__main__":
    main()
