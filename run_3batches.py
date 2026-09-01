"""
Parallel batch runner for the 8 pilot regions: per-bridge reports + regional reports.

3 balanced batches (whole regions), run each in its own terminal:
    Batch 1: ΝΟΑ (18) + ΗΠΕ (16) + ΒΟΑ (13) = 47 bridges
    Batch 2: ΠΕΛ (21) + ΔΜΑ (16) + ΙΟΝ (12) = 49 bridges
    Batch 3: ΔΕ (20) + ΑΜΘ (20)              = 40 bridges

Phase 1: per-bridge .docx reports for every bridge of the batch's regions
         (output/<ΠΕΡΙΦΕΡΕΙΑ>/<code_name>.docx + copy in Πιλοτικές Αναφορές/<ΠΕΡΙΦΕΡΕΙΑ>/).
Phase 2: assemble each region's combined report (region_<ΠΕΡΙΦΕΡΕΙΑ>.docx)
         from the freshly generated per-bridge files.

Usage (3 terminals in parallel):
    py run_3batches.py 1
    py run_3batches.py 2
    py run_3batches.py 3

Options:
    --resume   skip per-bridge .docx files that already exist (default: full regeneration)
    --no-regional   skip Phase 2 (regional report assembly)

Log: batches/run_3batch_<N>.log
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

from main import generate_bridge_docx, read_bridges_txt

BASE_DIR = Path(__file__).parent
BATCHES_DIR = BASE_DIR / "batches"
REGIONS_DIR = BASE_DIR / "regions"
TEMPLATE = BASE_DIR / "template.docx"
REGION_TEMPLATE = BASE_DIR / "region_template.docx"
OUT_ROOT = BASE_DIR / "output"

PYTHON_EXE = r"C:\Users\fgkenas\AppData\Local\Python\bin\python.exe"

BATCHES: dict[int, list[str]] = {
    1: ["ΝΟΑ", "ΗΠΕ", "ΒΟΑ"],
    2: ["ΠΕΛ", "ΔΜΑ", "ΙΟΝ"],
    3: ["ΔΕ", "ΑΜΘ"],
}


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


def phase1_bridges(batch_num: int, log_path: Path, resume: bool) -> tuple[list[str], list[str]]:
    regions = BATCHES[batch_num]
    session, csrftoken = None, ""
    try:
        from bridge_info import resensys_login
        session, csrftoken = resensys_login()
        log(log_path, "  Resensys login OK (shared session)")
    except Exception as exc:
        log(log_path, f"  ! Resensys login failed (WLS charts will be skipped): {exc}")

    done, failed = [], []
    for region in regions:
        bridges_file = REGIONS_DIR / f"region_{region}.txt"
        if not bridges_file.exists():
            log(log_path, f"\n  ! {region}: {bridges_file} not found, skipping region")
            continue
        code_names = read_bridges_txt(bridges_file)
        if not code_names:
            log(log_path, f"\n  ! {region}: no bridges found in {bridges_file}")
            continue

        out_dir = OUT_ROOT / region
        out_dir.mkdir(parents=True, exist_ok=True)
        log(log_path, f"\n=== Phase 1 [{region}]: {len(code_names)} bridges -> {out_dir} ===")

        for idx, code_name in enumerate(code_names, start=1):
            out_path = out_dir / f"{code_name}.docx"
            if resume and out_path.exists():
                log(log_path, f"  [{idx}/{len(code_names)}] [skip] {code_name}.docx already exists")
                done.append(code_name)
                continue

            log(log_path, f"\n[{idx}/{len(code_names)}] Processing {code_name} ({region})...")
            t_bridge = time.time()
            try:
                result = generate_bridge_docx(
                    code_name=code_name,
                    template=TEMPLATE,
                    out_dir=out_dir,
                    extra_tags={"CODE_NAME": code_name, "BRIDGE_NUMBER": str(idx)},
                    bridge_number=idx,
                    session=session,
                    csrftoken=csrftoken,
                    region_code=region,
                )
                if result:
                    log(log_path, f"  [OK] {code_name} ({time.time() - t_bridge:.0f}s)")
                    done.append(code_name)
                else:
                    log(log_path, f"  [FAIL] {code_name} (generate_bridge_docx returned None)")
                    failed.append(code_name)
            except Exception as exc:
                log(log_path, f"  [FAIL] {code_name}: {exc}")
                failed.append(code_name)

    return done, failed


def phase2_regional(batch_num: int, log_path: Path) -> list[str]:
    regions = BATCHES[batch_num]
    failed = []
    for region in regions:
        bridges_file = REGIONS_DIR / f"region_{region}.txt"
        out_path = OUT_ROOT / region / f"region_{region}.docx"
        if not bridges_file.exists():
            log(log_path, f"\n  ! {region}: {bridges_file} not found, skipping regional report")
            continue

        log(log_path, f"\n=== Phase 2 [{region}]: assembling regional report {out_path} ===")
        cmd = [
            PYTHON_EXE, str(BASE_DIR / "region_report.py"),
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
            log(log_path, f"  ! region_report.py exited with code {result.returncode}")
            failed.append(region)
        else:
            log(log_path, f"  [OK] {region} -> {out_path}")
    return failed


def run_batch(batch_num: int, resume: bool, no_regional: bool) -> int:
    log_path = BATCHES_DIR / f"run_3batch_{batch_num}.log"
    BATCHES_DIR.mkdir(parents=True, exist_ok=True)
    if log_path.exists():
        log_path.unlink()

    regions = BATCHES[batch_num]
    log(log_path, f"=== Batch {batch_num} started {time.strftime('%Y-%m-%d %H:%M:%S')} (resume={resume}) ===")
    log(log_path, f"  Regions: {', '.join(regions)}")

    t0 = time.time()
    done, failed = phase1_bridges(batch_num, log_path, resume)

    log(log_path, f"\n=== Phase 1 done in {(time.time() - t0) / 60:.1f} min: {len(done)} OK, {len(failed)} failed ===")
    if failed:
        log(log_path, f"  Failed bridges: {', '.join(failed)}")

    regional_failed: list[str] = []
    if not no_regional:
        if failed:
            log(log_path, "  Some bridges failed - regional reports will assemble only the available files.")
        regional_failed = phase2_regional(batch_num, log_path)

    log(log_path, f"\n=== Batch {batch_num} finished in {(time.time() - t0) / 60:.1f} min ===")
    if failed:
        log(log_path, f"  Failed bridges: {len(failed)}")
    if regional_failed:
        log(log_path, f"  Failed regional reports: {', '.join(regional_failed)}")
    return 1 if (failed or regional_failed) else 0


def main() -> None:
    ap = argparse.ArgumentParser(description="Run one of the 3 balanced batches (8 pilot regions).")
    ap.add_argument("batch", type=int, help="Batch number (1-3).")
    ap.add_argument("--resume", action="store_true",
                    help="Skip per-bridge .docx files that already exist.")
    ap.add_argument("--no-regional", action="store_true",
                    help="Skip Phase 2 (regional report assembly).")
    args = ap.parse_args()

    if args.batch not in BATCHES:
        print(f"Batch number must be one of {sorted(BATCHES)}.")
        sys.exit(2)

    sys.exit(run_batch(args.batch, args.resume, args.no_regional))


if __name__ == "__main__":
    main()
