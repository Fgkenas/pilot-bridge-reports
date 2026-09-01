"""
Split a bridges.txt file into N equal batches for parallel execution.

Usage:
    python split_bridges.py --input bridges.txt --batches 4
    python split_bridges.py --input pilot.txt --batches 3 --out-dir batches
"""
from __future__ import annotations

import argparse
import math
from pathlib import Path


def read_bridges(path: Path) -> list[str]:
    lines = path.read_text(encoding="utf-8").splitlines()
    return [l.strip() for l in lines if l.strip() and not l.strip().startswith("#")]


def split_into_batches(items: list[str], n_batches: int) -> list[list[str]]:
    batch_size = math.ceil(len(items) / n_batches)
    return [items[i : i + batch_size] for i in range(0, len(items), batch_size)]


def main() -> None:
    ap = argparse.ArgumentParser(description="Split a bridges list into N equal batches.")
    ap.add_argument("--input", type=Path, required=True, help="Path to bridges .txt file")
    ap.add_argument("--batches", type=int, required=True, help="Number of batches")
    ap.add_argument("--out-dir", type=Path, default=Path("."), help="Output directory (default: current dir)")
    args = ap.parse_args()

    bridges = read_bridges(args.input)
    if not bridges:
        print(f"No bridges found in {args.input}")
        return

    batches = split_into_batches(bridges, args.batches)
    args.out_dir.mkdir(parents=True, exist_ok=True)

    for i, batch in enumerate(batches, start=1):
        out_path = args.out_dir / f"batch_{i}.txt"
        out_path.write_text("\n".join(batch) + "\n", encoding="utf-8")
        print(f"  batch_{i}.txt: {len(batch)} bridges")

    print(f"\nDone. {len(bridges)} bridges split into {len(batches)} batches.")
    print(f"Run each batch in a separate terminal:")
    for i in range(1, len(batches) + 1):
        print(f"  Terminal {i}: python main.py --bridges batch_{i}.txt --template template.docx --out output")


if __name__ == "__main__":
    main()
