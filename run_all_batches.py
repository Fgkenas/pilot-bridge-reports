"""
Parallel batch runner: per-bridge pilot reports for ALL regions.

7 balanced batches (batches/batch_1.txt ... batch_7.txt). Each batch is run
in its own terminal. Always regenerates every bridge (no resume/skip).

Usage (7 terminals in parallel):
    py run_all_batches.py 1
    py run_all_batches.py 2
    ...
    py run_all_batches.py 7

Output: output/<ΠΕΡΙΦΕΡΕΙΑ>/<code_name>.docx + copy in Πιλοτικές Αναφορές/<ΠΕΡΙΦΕΡΕΙΑ>/.
Log: batches/run_batch_<N>.log

After all 7 batches finish, assemble the 13 regional reports with:
    py run_all_regions.py
"""
from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path

os.environ.setdefault("MAX_API_WORKERS", "1")
os.environ.setdefault("PYTHONIOENCODING", "utf-8")

from main import generate_bridge_docx, read_bridges_txt

BASE_DIR = Path(__file__).parent
BATCHES_DIR = BASE_DIR / "batches"
TEMPLATE = BASE_DIR / "template.docx"
OUT_ROOT = BASE_DIR / "output"


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


def run_batch(batch_num: int) -> int:
    batch_file = BATCHES_DIR / f"batch_{batch_num}.txt"
    if not batch_file.exists():
        print(f"Batch file not found: {batch_file}")
        return 2

    log_path = BATCHES_DIR / f"run_batch_{batch_num}.log"
    if log_path.exists():
        log_path.unlink()

    code_names = read_bridges_txt(batch_file)
    if not code_names:
        log(log_path, f"No bridges found in {batch_file}")
        return 2

    log(log_path, f"=== Batch {batch_num} started {time.strftime('%Y-%m-%d %H:%M:%S')}: {len(code_names)} bridges ===")

    session, csrftoken = None, ""
    try:
        from bridge_info import resensys_login
        session, csrftoken = resensys_login()
        log(log_path, "  Resensys login OK (shared session)")
    except Exception as exc:
        log(log_path, f"  ! Resensys login failed (WLS charts will be skipped): {exc}")

    done, failed = [], []
    t0 = time.time()
    for idx, code_name in enumerate(code_names, start=1):
        region = code_name.split("_")[0].strip()
        out_dir = OUT_ROOT / region
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

    log(log_path, f"\n=== Batch {batch_num} done in {(time.time() - t0) / 60:.1f} min: {len(done)} OK, {len(failed)} failed ===")
    if failed:
        log(log_path, f"  Failed bridges: {', '.join(failed)}")
    return 1 if failed else 0


def main() -> None:
    ap = argparse.ArgumentParser(description="Run one balanced batch of pilot reports (1-7).")
    ap.add_argument("batch", type=int, help="Batch number (1-7).")
    args = ap.parse_args()

    if not 1 <= args.batch <= 7:
        print("Batch number must be between 1 and 7.")
        sys.exit(2)

    sys.exit(run_batch(args.batch))


if __name__ == "__main__":
    main()
