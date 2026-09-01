"""
Region report generator.

Fetches all bridges in a region (via API or bridges.txt), generates per-bridge
.docx reports (using main.py logic), then assembles them into a single region
report using region_template.docx.

Usage:
    # All bridges in a region via API:
    python region_report.py --region ΚΡ --template template.docx

    # Bridges from a .txt file:
    python region_report.py --bridges bridges.txt --template template.docx

    # With explicit output path:
    python region_report.py --region ΔΕ --template template.docx --out output/region_ΔΕ.docx
"""
from __future__ import annotations

from resensys_hosts import BRIDGES_API_URL

import argparse
import re
import shutil
import sys
from pathlib import Path
from typing import Dict, Optional

import requests
from docx import Document
from docx.shared import Pt, Cm, RGBColor
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml.shared import qn
from docxcompose.composer import Composer

# ---------------------------------------------------------------------------
# Re-use all helpers from main.py
# ---------------------------------------------------------------------------
from main import (
    ELLADA2_LOGO_PATH,
    _copy_styles_from_source,
    _iter_paragraphs,
    _replace_in_paragraph,
    _set_doc_font,
    _strip_trailing_logo,
    add_page_break,
    add_ellada2_logo,
    generate_bridge_docx,
    read_bridges_txt,
    safe_filename,
)
from bridge_info import fetch_bridge

PLACEHOLDER_RE = re.compile(r"\{([A-Z0-9_]+)\}")

REGION_API_URL = BRIDGES_API_URL

# ---------------------------------------------------------------------------
# Logo mapping (from logos/logos.txt)
# ---------------------------------------------------------------------------
REGION_LOGO_MAP: Dict[str, str] = {
    "ΑΤΤΙΚΗ": "nuro",
    "ΚΡΗΤΗ": "terna",
    "ΔΥΤΙΚΗ ΕΛΛΑΔΑ": "ote",
    "ΘΕΣΣΑΛΙΑΣ": "nuro",
    "ΔΥΤΙΚΗ ΜΑΚΕΔΟΝΙΑ": "nuro",
    "ΝΟΤΙΟ ΑΙΓΑΙΟ": "nuro",
    "ΑΜΘ": "terna",
    "ΗΠΕΙΡΟΣ": "terna",
    "ΚΕΝΤΡΙΚΗ ΜΑΚΕΔΟΝΙΑ": "terna",
    "ΙΟΝΙΩΝ ΝΗΣΩΝ": "ote",
    "ΣΤΕΡΕΑ ΕΛΛΑΔΑ": "ote",
    "ΠΕΛΟΠΟΝΝΗΣΟΣ": "ote",
    "ΒΟΡΕΙΟΥ ΑΙΓΑΙΟΥ": "ote",
}

LOGO_FILENAME_MAP: Dict[str, str] = {
    "nuro": "nuro.png",
    "terna": "terna.png",
    "ote": "cosmote.png",
}

LOGOS_DIR = Path(__file__).parent / "logos"

REGIONAL_REPORTS_DIR = Path("C:/Users/fgkenas/Globiled/Marina Bagourdi - Μεταβλητές/Περιφερειακές Πιλοτικές Αναφορές")


def _get_logo_path(region_name_el: str | None) -> Optional[Path]:
    """Return path to logo image for a region, or None if not found."""
    if not region_name_el:
        return None
    # Normalise: strip accents-like characters and compare upper
    name_upper = region_name_el.upper().strip()
    for key, logo_key in REGION_LOGO_MAP.items():
        if key in name_upper or name_upper in key:
            filename = LOGO_FILENAME_MAP.get(logo_key)
            if filename:
                p = LOGOS_DIR / filename
                return p if p.exists() else None
    return None


# ---------------------------------------------------------------------------
# Fetch bridges from region API
# ---------------------------------------------------------------------------
def fetch_region_bridges(region_name: str, timeout: int = 30) -> tuple[list[str], dict]:
    """
    Call the bridges API with ?region_code=<region_code>.
    Returns (list_of_code_names, region_meta_dict).
    region_meta_dict has keys like region_name_el_report, etc.
    """
    resp = requests.get(REGION_API_URL, params={"region_code": region_name}, timeout=timeout)
    resp.raise_for_status()
    data = resp.json()

    code_names: list[str] = []
    region_meta: dict = {}

    # Expect structure: {"regions": [{"region_code": ..., "region_name_el_report": ..., "bridges": [...]}]}
    for region in data.get("regions", []):
        if not region_meta:
            region_meta = {
                "region_code": region.get("region_code", ""),
                "region_name": region.get("region_name", ""),
                "region_name_el": region.get("region_name_el", ""),
                "region_name_el_report": region.get("region_name_el_report", ""),
            }
        for bridge in region.get("bridges", []):
            cn = bridge.get("code_name") or bridge.get("bridge_info", {}).get("code_name", "")
            if cn:
                code_names.append(cn)

    return code_names, region_meta


# ---------------------------------------------------------------------------
# Collect pilot date range across all bridges
# ---------------------------------------------------------------------------
def _collect_date_range(code_names: list[str]) -> tuple[str, str]:
    """Return (min_start, max_finish) across all bridges."""
    starts, finishes = [], []
    for code_name in code_names:
        try:
            data = fetch_bridge(code_name)
            for region in data.get("regions", []):
                for bridge in region.get("bridges", []):
                    for site in bridge.get("sites", []):
                        for sensor in site.get("sensors", []):
                            s = sensor.get("Pilot_Date_Start", "") or ""
                            f = sensor.get("Pilot_Date_Finish", "") or ""
                            if s:
                                starts.append(s)
                            if f:
                                finishes.append(f)
        except Exception:
            pass
    min_start = min(starts) if starts else ""
    max_finish = max(finishes) if finishes else ""
    return min_start, max_finish


# ---------------------------------------------------------------------------
# Table of contents insertion
# ---------------------------------------------------------------------------
def _insert_toc(doc: Document) -> None:
    """Remove the {CONTENTS} placeholder paragraph (TOC is skipped to avoid docx corruption)."""
    for i, para in enumerate(doc.paragraphs):
        full_text = "".join(r.text for r in para.runs)
        if "{CONTENTS}" not in full_text and "{CONTENTS}" not in para.text:
            continue
        # Clear the placeholder paragraph instead of removing the element to avoid
        # corrupting python-docx's internal body state, which breaks later append order.
        para.clear()
        para.style = doc.styles["Normal"]
        print("  [OK] Removed {CONTENTS} placeholder (TOC skipped)")
        return
    print("  ! {CONTENTS} placeholder not found — skipping TOC")


# ---------------------------------------------------------------------------
# Logo replacement in region template
# ---------------------------------------------------------------------------
def _replace_logos_in_doc(doc: Document, logo_path: Path) -> None:
    """Replace {LOGO} text placeholders with the actual logo image."""
    if not logo_path.exists():
        print(f"  ! Logo file not found: {logo_path}")
        return

    replaced = 0
    for para in _iter_paragraphs(doc):
        full_text = "".join(r.text for r in para.runs)
        if "{LOGO}" not in full_text:
            continue
        # Clear the placeholder text and insert the image
        for r in para.runs:
            r.text = ""
        try:
            run = para.add_run()
            logo = run.add_picture(str(logo_path))
            # Constrain height to max 3.33 cm, keep width proportional
            max_height = Cm(3.33)
            if logo.height > max_height:
                ratio = max_height / logo.height
                logo.height = max_height
                logo.width = int(logo.width * ratio)
            replaced += 1
            print(f"  [OK] Replaced {{LOGO}} placeholder {replaced} with {logo_path.name}")
        except Exception as exc:
            print(f"  ! Failed to insert logo image: {exc}")

    if replaced == 0:
        print(f"  ! No {{LOGO}} placeholder found in region template")


def _replace_ellada2_logo_in_doc(doc: Document) -> None:
    """Replace {ELLADA2_LOGO} placeholder with the Ellada 2.0 / NextGenerationEU logo."""
    if not ELLADA2_LOGO_PATH.exists():
        print(f"  ! Ellada 2.0 logo file not found: {ELLADA2_LOGO_PATH}")
        return

    replaced = 0
    for para in _iter_paragraphs(doc):
        full_text = "".join(r.text for r in para.runs)
        if "{ELLADA2_LOGO}" not in full_text:
            continue
        for r in para.runs:
            r.text = ""
        try:
            run = para.add_run()
            run.add_picture(str(ELLADA2_LOGO_PATH), width=Cm(8.9))
            replaced += 1
            print(f"  [OK] Replaced {{ELLADA2_LOGO}} placeholder {replaced} with Ellada 2.0 logo")
        except Exception as exc:
            print(f"  ! Failed to insert Ellada 2.0 logo image: {exc}")

    if replaced == 0:
        print(f"  ! No {{ELLADA2_LOGO}} placeholder found in region template")


B_SECTION_HEADING = "Β. Αναλυτική παρουσίαση αποτελεσμάτων ανά γέφυρα"


def _add_b_heading(doc: Document) -> None:
    """Insert the single 'B' section heading into the document."""
    # Region template uses Heading 2 for A, so use Heading 2 for B as well
    try:
        para = doc.add_paragraph(B_SECTION_HEADING, style="Heading 2")
        # Ensure the run is bold
        for run in para.runs:
            run.bold = True
    except KeyError:
        para = doc.add_paragraph()
        run = para.add_run(B_SECTION_HEADING)
        run.bold = True
        run.font.size = Pt(14)
    para.alignment = WD_ALIGN_PARAGRAPH.LEFT


def _extract_bridge_display_name(source: Document) -> str | None:
    """Extract the bridge display name from the bridge template's first subheading."""
    for para in source.paragraphs:
        text = "".join(r.text for r in para.runs).strip()
        m = re.match(r"^1\.\s+(.*)$", text)
        if m:
            return m.group(1).strip()
    return None


def _replace_bridge_heading_number(source: Document, seq: int, display_name: str) -> None:
    """Update the bridge heading in source from '1. Name' to '{seq}. Name' (in-place)."""
    for para in source.paragraphs:
        text = "".join(r.text for r in para.runs).strip()
        if re.match(r"^1\.\s+", text):
            para.clear()
            para.style = "Heading 3"
            para.add_run(f"{seq}.\t{display_name}")
            return


def _strip_b_heading_only(source: Document) -> None:
    """Remove only the B section heading from source (in-place), keeping bridge subheading."""
    body = source.element.body
    for para in source.paragraphs:
        text = "".join(r.text for r in para.runs).strip()
        if B_SECTION_HEADING in text or text.replace("\t", " ") == B_SECTION_HEADING:
            body.remove(para._element)
            return


def _strip_b_heading_from_doc(source: Document) -> None:
    """Remove the B section heading and the bridge name subheading from source (in-place)."""
    body = source.element.body
    to_remove = []
    for para in source.paragraphs:
        text = "".join(r.text for r in para.runs).strip()
        # Remove B section heading (handles tab or space after Β.)
        if B_SECTION_HEADING in text or text.replace("\t", " ") == B_SECTION_HEADING:
            to_remove.append(para._element)
            continue
        # Remove the hardcoded "1.\tBridge Name" subheading from bridge template
        if re.match(r"^1\.\s+", text):
            to_remove.append(para._element)
    for el in to_remove:
        body.remove(el)


# ---------------------------------------------------------------------------
# Per-bridge report generation (delegates to main.generate_bridge_docx)
# ---------------------------------------------------------------------------
def _generate_bridge_docx(
    code_name: str,
    idx: int,
    bridge_template: Path,
    out_dir: Path,
    global_tags: dict,
    force: bool = False,
    session = None,
    csrftoken: str = "",
    region_code: str = "",
) -> Optional[Path]:
    """Render and enrich a single bridge .docx via main.generate_bridge_docx.

    If the output file already exists and force=False, skip re-generation.
    Returns path or None on failure.
    """
    bridge_dir = out_dir
    bridge_path = bridge_dir / f"{code_name}.docx"
    if bridge_path.exists() and not force:
        print(f"    [skip] {code_name}.docx already exists (use --force to regenerate)")
        return bridge_path

    extra_tags = dict(global_tags)
    extra_tags["CODE_NAME"] = code_name
    extra_tags["BRIDGE_NUMBER"] = str(idx)

    return generate_bridge_docx(
        code_name=code_name,
        template=bridge_template,
        out_dir=bridge_dir,
        extra_tags=extra_tags,
        bridge_number=idx,
        session=session,
        csrftoken=csrftoken,
        region_code=region_code,
    )


# ---------------------------------------------------------------------------
# Main region report assembly
# ---------------------------------------------------------------------------
def _enforce_report_formatting(doc: Document) -> None:
    for para in _iter_paragraphs(doc):
        text = "".join(run.text for run in para.runs).strip()
        if para.style and para.style.name == "Heading 4":
            for run in para.runs:
                run.bold = True
                run.font.bold = True
                run.font.color.rgb = RGBColor(0, 0, 0)
        if para.style and para.style.name == "Heading 5":
            para.paragraph_format.space_before = Pt(12)
            for run in para.runs:
                run.bold = True
                run.font.bold = True
                run.font.color.rgb = RGBColor(0, 0, 0)
        if text.startswith("Στη ") and " MB κυψελοειδών δεδομένων" in text:
            para.alignment = WD_ALIGN_PARAGRAPH.JUSTIFY
        if text.startswith("Παρακάτω παρουσιάζονται συνοπτικά οι μετρήσεις παραμορφώσεων"):
            para.alignment = WD_ALIGN_PARAGRAPH.JUSTIFY
        if text.startswith("Παρακάτω παρουσιάζονται οι καθημερινές μετρήσεις θερμοκρασίας"):
            para.alignment = WD_ALIGN_PARAGRAPH.JUSTIFY
        if text.startswith("Στα παρακάτω διαγράμματα παρουσιάζονται οι χρονοσειρές των μετρήσεων παραμόρφωσης"):
            para.alignment = WD_ALIGN_PARAGRAPH.JUSTIFY
        if text.startswith("Κατά την διάρκεια της περιόδου παρακολούθησης αναγνωρίζονται τα «Γεγονότα»"):
            para.alignment = WD_ALIGN_PARAGRAPH.JUSTIFY
        if para.style and para.style.name == "Caption":
            para.alignment = WD_ALIGN_PARAGRAPH.JUSTIFY
            for run in para.runs:
                run.font.size = Pt(10)
                run.font.name = "Arial"


def generate_region_report(
    region_api_name: Optional[str],
    bridges_txt: Optional[Path],
    bridge_template: Path,
    region_template: Path,
    out_path: Path,
    skip_bridge_gen: bool = False,
    force: bool = False,
) -> None:
    """
    1. Get list of code_names (API or txt).
    2. Fetch region metadata.
    3. Collect pilot date range.
    4. Render per-bridge .docx files.
    5. Open region_template, replace {REGION}, {DATE_RANGE}, {CONTENTS}, logos.
    6. Append each bridge .docx as a section.
    7. Save combined report.
    """
    # --- Step 1: code names ---
    code_names: list[str] = []
    region_meta: dict = {}

    if region_api_name:
        print(f"Fetching bridges for region '{region_api_name}' from API...")
        code_names, region_meta = fetch_region_bridges(region_api_name)
        print(f"Found {len(code_names)} bridges: {code_names}")
    elif bridges_txt:
        code_names = read_bridges_txt(bridges_txt)
        print(f"Read {len(code_names)} bridges from {bridges_txt}: {code_names}")
        # Extract region code from first bridge (e.g., "ΚΡ_001_ΠΕΡ" -> "ΚΡ")
        if code_names:
            region_code = code_names[0].split("_")[0]
            print(f"Extracted region code: {region_code}, fetching region meta from API...")
            try:
                _, region_meta = fetch_region_bridges(region_code)
                print(f"  Region meta: {region_meta}")
            except Exception as exc:
                print(f"  ! Could not fetch region meta for '{region_code}': {exc}")
                region_meta = {"region_code": region_code, "region_name_el_report": region_code}
    else:
        print("ERROR: Provide --region or --bridges")
        sys.exit(1)

    if not code_names:
        print("No bridges found. Aborting.")
        return

    # --- Step 2: date range ---
    print("Collecting pilot date range...")
    min_start, max_finish = _collect_date_range(code_names)
    # Convert dates from YYYY-MM-DD to DD/MM/YYYY format
    from datetime import datetime
    if min_start:
        try:
            min_start = datetime.strptime(min_start[:10], "%Y-%m-%d").strftime("%d/%m/%Y")
        except (ValueError, IndexError):
            pass
    if max_finish:
        try:
            max_finish = datetime.strptime(max_finish[:10], "%Y-%m-%d").strftime("%d/%m/%Y")
        except (ValueError, IndexError):
            pass
    date_range = f"{min_start} - {max_finish}" if min_start or max_finish else ""
    print(f"Date range: {date_range}")

    region_display = region_meta.get("region_name_el_report", region_meta.get("region_name_el", ""))
    print(f"Region: {region_display}")

    # --- Step 3: logo ---
    logo_path = _get_logo_path(region_display)
    # If logo not found, try nominative form (region_name_el) instead of genitive (region_name_el_report)
    if not logo_path:
        logo_path = _get_logo_path(region_meta.get("region_name_el", ""))
    print(f"Logo: {logo_path}")

    # --- Step 4: per-bridge .docx files ---
    out_dir = out_path.parent
    out_dir.mkdir(parents=True, exist_ok=True)

    bridge_paths: list[Path] = []
    if skip_bridge_gen:
        print("  --skip-bridge-gen: using existing per-bridge .docx files from output dir")
        for code_name in code_names:
            p = out_dir / f"{code_name}.docx"
            if p.exists():
                bridge_paths.append(p)
                print(f"    Found {p.name}")
            else:
                print(f"    ! Missing {p.name}, skipping")
    else:
        # Shared Resensys session for all bridges in this region
        session, csrftoken = None, ""
        try:
            from bridge_info import resensys_login
            session, csrftoken = resensys_login()
        except Exception as exc:
            print(f"  ! Resensys login failed (WLS charts will be skipped): {exc}")

        for idx, code_name in enumerate(code_names, start=1):
            print(f"\n  [{idx}/{len(code_names)}] Processing {code_name}...")
            region_code = code_name.split("_")[0]
            bp = _generate_bridge_docx(
                code_name=code_name,
                idx=idx,
                bridge_template=bridge_template,
                out_dir=out_dir,
                global_tags={},
                force=force,
                session=session,
                csrftoken=csrftoken,
                region_code=region_code,
            )
            if bp:
                bridge_paths.append(bp)

    print(f"\n  Per-bridge files: {bridge_paths}")

    # --- Step 5: open region template, replace placeholders ---
    print(f"\nOpening region template: {region_template}")
    region_doc = Document(str(region_template))
    _set_doc_font(region_doc)

    # Replace text placeholders
    from datetime import datetime as _dt
    region_tags = {
        "REGION": region_display,
        "DATE_RANGE": date_range,
        "DATE": _dt.now().strftime("%d/%m/%Y"),
    }
    for para in _iter_paragraphs(region_doc):
        _replace_in_paragraph(para, region_tags)

    # Insert TOC at {CONTENTS}
    _insert_toc(region_doc)

    # Replace logo images
    if logo_path:
        _replace_logos_in_doc(region_doc, logo_path)

    # Replace Ellada 2.0 / NextGenerationEU logo
    _replace_ellada2_logo_in_doc(region_doc)

    # --- Step 6: append bridge sections using docxcompose Composer ---
    # Add the 'B' heading once before the first bridge
    if bridge_paths:
        _add_b_heading(region_doc)
        # Copy heading styles from the first bridge doc so appended Heading 4/5
        # paragraphs are recognized in the merged document
        _copy_styles_from_source(region_doc, Document(str(bridge_paths[0])))
    
    composer = Composer(region_doc)
    
    for seq, bridge_path in enumerate(bridge_paths, start=1):
        print(f"  Appending {bridge_path.name}...")
        bridge_doc = Document(str(bridge_path))
        display_name = _extract_bridge_display_name(bridge_doc)
        code_name = bridge_path.stem
        # Update the bridge heading number in-place so it stays with the bridge content
        _replace_bridge_heading_number(bridge_doc, seq, display_name or code_name)
        # Remove the B heading from the bridge doc; the region doc already has its own B heading
        _strip_b_heading_only(bridge_doc)
        # Remove the per-bridge trailing Ellada 2.0 logo; the region report adds its own at the end
        _strip_trailing_logo(bridge_doc)
        
        # Start each bridge on a new page (except the first, which is right after B heading)
        if seq > 1:
            add_page_break(composer.doc)
        composer.append(bridge_doc)
    
    # Save the merged document
    _enforce_report_formatting(composer.doc)
    add_ellada2_logo(composer.doc)
    composer.save(str(out_path))
    print(f"\nRegion report saved: {out_path}")
    try:
        REGIONAL_REPORTS_DIR.mkdir(parents=True, exist_ok=True)
        shutil.copy2(str(out_path), str(REGIONAL_REPORTS_DIR / out_path.name))
        print(f"  Copied to: {REGIONAL_REPORTS_DIR / out_path.name}")
    except Exception as e:
        print(f"  ! Failed to copy to regional reports dir: {e}")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def main() -> None:
    ap = argparse.ArgumentParser(description="Generate region report combining all bridge reports.")
    source = ap.add_mutually_exclusive_group(required=True)
    source.add_argument(
        "--region",
        default=None,
        help="Region code passed to API (e.g. ΚΡ, ΔΕ). All bridges in that region are included.",
    )
    source.add_argument(
        "--bridges",
        type=Path,
        default=None,
        help="Path to bridges.txt (one code_name per line). Region meta fetched from first bridge.",
    )
    ap.add_argument(
        "--template",
        required=True,
        type=Path,
        help="Path to per-bridge .docx template.",
    )
    ap.add_argument(
        "--region-template",
        type=Path,
        default=Path(__file__).parent / "region_template.docx",
        help="Path to region .docx template. Default: region_template.docx next to this script.",
    )
    ap.add_argument(
        "--out",
        type=Path,
        default=None,
        help="Output .docx path. Default: output/region_<code>.docx",
    )
    ap.add_argument(
        "--skip-bridge-gen",
        action="store_true",
        help="Skip per-bridge .docx generation and use existing files from the output dir.",
    )
    ap.add_argument(
        "--force",
        action="store_true",
        help="Regenerate all per-bridge .docx files even if they already exist in the output dir.",
    )
    args = ap.parse_args()

    out_path = args.out
    if out_path is None:
        if args.region:
            region_slug = safe_filename(args.region)
        else:
            region_slug = safe_filename(args.bridges.stem) if args.bridges else "bridges"
        out_path = Path("output") / f"region_{region_slug}.docx"

    generate_region_report(
        region_api_name=args.region,
        bridges_txt=args.bridges,
        bridge_template=args.template,
        region_template=args.region_template,
        out_path=out_path,
        skip_bridge_gen=args.skip_bridge_gen,
        force=args.force,
    )


if __name__ == "__main__":
    main()
