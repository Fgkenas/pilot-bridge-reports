"""
Split a bridges list into per-region .txt files for region_report.py --bridges.

Usage:
    python split_by_region.py --input bridges.txt --out-dir regions
"""
from __future__ import annotations

import argparse
from pathlib import Path


REGIONS: dict[str, list[str]] = {}


def main() -> None:
    ap = argparse.ArgumentParser(description="Split bridges.txt into per-region files.")
    ap.add_argument("--input", type=Path, required=True, help="Path to bridges .txt")
    ap.add_argument("--out-dir", type=Path, default=Path("regions"), help="Output directory")
    args = ap.parse_args()

    lines = args.input.read_text(encoding="utf-8-sig").splitlines()
    bridges = [l.strip() for l in lines if l.strip() and not l.strip().startswith("#")]

    regions: dict[str, list[str]] = {}
    for b in bridges:
        parts = b.replace("  ", " ").split("_")  # handle "ΔΕ  ΔΕ_001_ΑΒΟ" -> ["ΔΕ", "ΔΕ", "001", "ΑΒΟ"]
        region_code = parts[0].strip()
        if len(parts) >= 3 and parts[1].strip() == region_code:
            region_code = parts[0].strip()
        elif len(b.split(None, 1)) > 1:
            # Handle space-prefixed codes like "ΑΜΘ ΑΜΘ_001_ΣΤΑ"
            region_code = b.split(None, 1)[0].strip()

        regions.setdefault(region_code, []).append(b)

    args.out_dir.mkdir(parents=True, exist_ok=True)

    print(f"Found {len(regions)} regions in {len(bridges)} bridges:\n")
    for code, bridge_list in sorted(regions.items()):
        out_path = args.out_dir / f"region_{code}.txt"
        out_path.write_text("\n".join(bridge_list) + "\n", encoding="utf-8")
        print(f"  {code}: {len(bridge_list)} bridges -> {out_path}")

    print(f"\nDone. Now run each batch with:")
    print(f"  python run_batch.py <batch_number>")


if __name__ == "__main__":
    main()
