import sys, os, time
sys.stdout.reconfigure(encoding='utf-8')
sys.path.insert(0, '.')

from pathlib import Path
from main import generate_bridge_docx

code_name = "ΑΤΤ_004_ΠΙΚ"
template = Path("template.docx")
out_dir = Path("output")

print(f"=== Full pipeline: {code_name} ===")
t0 = time.time()
sys.stdout.flush()

out_path = generate_bridge_docx(
    code_name=code_name,
    template=template,
    out_dir=out_dir,
    bridge_number=1,
)

elapsed = time.time() - t0
if out_path:
    size = os.path.getsize(out_path)
    print(f"\n=== SUCCESS! {out_path} ({size:,} bytes) in {elapsed:.0f}s ===")
else:
    print(f"\n=== FAILED after {elapsed:.0f}s ===")
