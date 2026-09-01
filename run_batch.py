"""
Parallel batch runner for per-region bridge reports.

First run:  python split_by_region.py --input bridges.txt
Then run:   python run_batch.py 1    (in each of 5 terminals)

Usage:
    python run_batch.py 1    # runs batch 1: ΑΤΤ (36), ΗΠΕ (16) = 52 bridges
    python run_batch.py 2    # runs batch 2: ΚΜΑ (31), ΔΕ (20) = 51 bridges
    python run_batch.py 3    # runs batch 3: ΘΕΣ (21), ΣΤΕ (21), ΙΟΝ (12) = 54 bridges
    python run_batch.py 4    # runs batch 4: ΠΕΛ (21), ΑΜΘ (20), ΒΟΑ (13) = 54 bridges
    python run_batch.py 5    # runs batch 5: ΚΡ (18), ΝΟΑ (18), ΔΜΑ (16) = 52 bridges
"""
from __future__ import annotations

import os
import subprocess
import sys


BATCHES = {
    1: ["ΑΤΤ", "ΗΠΕ"],
    2: ["ΚΜΑ", "ΔΕ"],
    3: ["ΘΕΣ", "ΣΤΕ", "ΙΟΝ"],
    4: ["ΠΕΛ", "ΑΜΘ", "ΒΟΑ"],
    5: ["ΚΡ", "ΝΟΑ", "ΔΜΑ"],
}

PYTHON = r"C:\Users\fgkenas\AppData\Local\Python\bin\python.exe"


def run_region(region: str) -> int:
    bridges_file = f"regions\\region_{region}.txt"
    out_path = f"output\\{region}\\region_{region}.docx"

    if not os.path.exists(bridges_file):
        print(f"\n  {region}: {bridges_file} not found, skipping")
        return 0

    cmd = [
        PYTHON, "region_report.py",
        "--bridges", bridges_file,
        "--template", "template.docx",
        "--region-template", "region_template.docx",
        "--out", out_path,
        "--force",
    ]

    print(f"\n  {region}: {' '.join(cmd)}")
    result = subprocess.run(cmd, env={
        **os.environ,
        "PYTHONIOENCODING": "utf-8",
        "MAX_API_WORKERS": "1",
    })
    return result.returncode


def run_batch(batch_num: int) -> None:
    regions = BATCHES.get(batch_num)
    if not regions:
        print(f"Invalid batch number: {batch_num}. Use 1-5.")
        sys.exit(1)

    print(f"=== Batch {batch_num}: {', '.join(regions)} ===")
    failed = []

    for region in regions:
        rc = run_region(region)
        if rc != 0:
            failed.append(region)
            print(f"  ! {region} FAILED (exit code {rc})")
            input("  Press Enter to continue...")
        else:
            print(f"  [OK] {region}")

    print(f"\n=== Batch {batch_num} DONE ===")
    if failed:
        print(f"  Failed regions: {', '.join(failed)}")
    print(f"  Output files in: output\\<region>\\ (per-region subfolders)")
    print(f"  Copies also in: C:\\Users\\fgkenas\\Globiled\\Marina Bagourdi - Μεταβλητές\\Πιλοτικές Αναφορές\\<region>\\")
    print(f"  Regional copies in: C:\\Users\\fgkenas\\Globiled\\Marina Bagourdi - Μεταβλητές\\Περιφερειακές Πιλοτικές Αναφορές\\")
    input("Press Enter to exit...")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python run_batch.py <batch_number>")
        for num, regions in BATCHES.items():
            print(f"  batch {num}: {', '.join(regions)}")
        sys.exit(1)
    run_batch(int(sys.argv[1]))
