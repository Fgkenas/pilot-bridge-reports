"""
Phase 2: assemble the 13 regional combined reports (region_<code>.docx)
from the per-bridge .docx files produced by run_all_batches.py (batches 1-7).

Run ONLY after all 7 batches have finished:
    py run_all_regions.py              # all 13 regions
    py run_all_regions.py ΚΡ           # only one region

Output: output/<code>/region_<code>.docx + copy in Περιφερειακές Πιλοτικές Αναφορές/.
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time
from pathlib import Path

BASE_DIR = Path(__file__).parent
REGIONS_DIR = BASE_DIR / "regions"
TEMPLATE = BASE_DIR / "template.docx"
REGION_TEMPLATE = BASE_DIR / "region_template.docx"

PYTHON_EXE = r"C:\Users\fgkenas\AppData\Local\Python\bin\python.exe"


def region_codes() -> list[str]:
    codes = [p.stem.replace("region_", "") for p in sorted(REGIONS_DIR.glob("region_*.txt"))]
    return codes


def run_region(region: str) -> int:
    bridges_file = REGIONS_DIR / f"region_{region}.txt"
    out_path = BASE_DIR / "output" / region / f"region_{region}.docx"

    if not bridges_file.exists():
        print(f"\n  {region}: {bridges_file} not found, skipping")
        return 0

    cmd = [
        PYTHON_EXE, str(BASE_DIR / "region_report.py"),
        "--bridges", str(bridges_file),
        "--template", str(TEMPLATE),
        "--region-template", str(REGION_TEMPLATE),
        "--out", str(out_path),
        "--skip-bridge-gen",
    ]
    print(f"\n  {region}: {' '.join(cmd)}")
    result = subprocess.run(
        cmd,
        cwd=str(BASE_DIR),
        env={**os.environ, "PYTHONIOENCODING": "utf-8", "MAX_API_WORKERS": "1"},
    )
    if result.returncode != 0:
        print(f"  ! {region} FAILED (exit code {result.returncode})")
    else:
        print(f"  [OK] {region} -> {out_path}")
    return result.returncode


def main() -> None:
    ap = argparse.ArgumentParser(description="Assemble regional reports from per-bridge files (Phase 2).")
    ap.add_argument("regions", nargs="*", help="Region codes to assemble (default: all).")
    args = ap.parse_args()

    codes = args.regions or region_codes()
    if not codes:
        print("No region files found.")
        sys.exit(1)

    print(f"=== Phase 2: assembling {len(codes)} regional reports ===")
    t0 = time.time()
    failed = []
    for code in codes:
        rc = run_region(code)
        if rc != 0:
            failed.append(code)

    print(f"\n=== Phase 2 done in {(time.time() - t0) / 60:.1f} min ===")
    if failed:
        print(f"  Failed regions: {', '.join(failed)}")
        sys.exit(1)


if __name__ == "__main__":
    main()
