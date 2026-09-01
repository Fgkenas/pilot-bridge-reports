# BridgeReport

Generate `.docx` reports per bridge, using data from the `bridge_api` endpoint.

## Install

```cmd
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
```

### Secrets

Copy the example environment file and fill in real values:

```cmd
copy secrets\.env.example secrets\.env
```

The `secrets/.env` file contains credentials and is **git-ignored** — never commit it.

## Input

1. **Excel** (`.xlsx`) with one column of `code_name` values, e.g.:

   | code_name   |
   | ----------- |
   | ΚΜΑ*006*Α07 |
   | ΚΜΑ*006*Α08 |

2. **Bridges text file** (`.txt`) with one `code_name` per line (comments with `#`, blank lines ignored).

3. **DOCX template** with placeholders in `{TAG}` form (uppercase).

## Available tags

Tags come from the API JSON (`http://lms4.globiled.com/Bridges_api/bridge_api.php?code_name=...`).

Shortcut tags (from `bridge_info`):

- `{CODE_NAME}`
- `{NAME}`
- `{DHMOS}`
- `{XWROTHETHSH}`
- `{DIASTASEIS_ODOSTRWMATOS}`
- `{MESH_HMERHSIA_KYKLOFORIA}`
- `{EIDOS_GEFURAS}`
- `{GEOMETRIA_A3ONA}`
- `{LO3OTHTA}`
- `{MORFOLOGIA}`
- `{DESCRIPTION}`
- `{LNG}`, `{LAT}`

Also available as `{BRIDGE_INFO_<FIELD>}`.

Run with `--dump-tags` to get a full `.tags.json` per bridge listing every
available tag for that record.

## Run

### From Excel

```cmd
python main.py --excel bridges.xlsx --template template.docx --out output
```

Options:

- `--column` : column holding `code_name` (1-based index or letter, default `1`).
- `--sheet` : worksheet name (default: active).
- `--no-header` : use if first row is data.
- `--dump-tags` : also dump a JSON file with resolved tags per bridge.

Output: one `<code_name>.docx` per row in `--out`.

### From bridges.txt

```cmd
python main.py --bridges bridges.txt --template template.docx
```

With a main report wrapper and global placeholders:

```cmd
python main.py --bridges bridges.txt --template template.docx --report main_report.docx --out output/report_final.docx --region "Κεντρική Μακεδονία" --start-date 01/01/2025 --end-date 31/12/2025
```

Options:

- `--bridges` : path to `.txt` file with one `code_name` per line.
- `--template` : per-bridge `.docx` template with `{TAG}` placeholders.
- `--report` : (optional) main `.docx` report; bridge sections are appended into it.
- `--out` : output `.docx` path (default `output/report_final.docx`).
- `--region` : value for `{REGION_NAME}`.
- `--start-date` : value for `{START_DATE}`.
- `--end-date` : value for `{END_DATE}`.

If `--report` is omitted, only individual per-bridge `.docx` files are generated.


---

## Region report (`region_report.py`)

Generates a **combined region report** by fetching all bridges in a region (via API or a `bridges.txt` file), producing per-bridge `.docx` sections, and assembling them into `region_template.docx`.

### Placeholders replaced in `region_template.docx`

| Placeholder | Value |
|---|---|
| `{REGION}` | `region_name_el_report` from the API (e.g. `ΔΥΤΙΚΗΣ ΕΛΛΑΔΑΣ`) |
| `{DATE_RANGE}` | `min(Pilot_Date_Start)` – `max(Pilot_Date_Finish)` across all bridges |
| `{CONTENTS}` | Word TOC field (update on first open in Word with F9) |

The first two inline images in pages 1–2 of the region template are replaced with the correct consortium logo based on the region:

| Region | Logo |
|---|---|
| ΑΤΤΙΚΗ, ΘΕΣΣΑΛΙΑΣ, ΔΥΤΙΚΗ ΜΑΚΕΔΟΝΙΑ, ΝΟΤΙΟ ΑΙΓΑΙΟ | `logos/nuro.png` |
| ΚΡΗΤΗ, ΑΜΘ, ΗΠΕΙΡΟΣ, ΚΕΝΤΡΙΚΗ ΜΑΚΕΔΟΝΙΑ | `logos/terna.png` |
| ΔΥΤΙΚΗ ΕΛΛΑΔΑ, ΙΟΝΙΩΝ ΝΗΣΩΝ, ΣΤΕΡΕΑ ΕΛΛΑΔΑ, ΠΕΛΟΠΟΝΝΗΣΟΣ, ΒΟΡΕΙΟΥ ΑΙΓΑΙΟΥ | `logos/cosmote.png` |

### Run — all bridges in a region via API

```cmd
python region_report.py --region ΚΡ --template template.docx
```

### Run — selected bridges from a .txt file

```cmd
python region_report.py --bridges bridges.txt --template template.docx
```

### Options

- `--region` : region code/name passed to the API (e.g. `ΚΡ`, `ΔΕ`). Mutually exclusive with `--bridges`.
- `--bridges` : path to `.txt` file (one `code_name` per line). Region meta is fetched from the first bridge.
- `--template` : per-bridge `.docx` template (**required**).
- `--region-template` : region `.docx` template (default: `region_template.docx` next to the script).
- `--out` : output `.docx` path (default: `output/region_<code>.docx`).

### Output

- One `<code_name>.docx` per bridge in the output directory (same full pipeline as `main.py`).
- One combined `region_<code>.docx` with the region template cover + TOC + all bridge sections.

## Project Structure

| Path | Purpose |
|------|---------|
| `main.py` | Per-bridge report generator (entry point) |
| `region_report.py` | Combined per-region report (`region_template.docx`) |
| `bridge_info.py` | Bridge API fetching + tag-map building |
| `sensor_data.py` | Sensor data processing |
| `deformation_report_section.py` | Deformation report section |
| `strain_comp_charts.py`, `accel_highrate_charts.py`, `wls_raw_charts.py` | Chart generators |
| `*_checker.py` | Battery / charge / HPA / signal / strain / WLS checkers |
| `data_consumption.py`, `device_type_counter.py` | MySQL consumption / device counting |
| `run_3batches.py`, `run_all_batches.py`, `run_batch.py`, `run_all_regions.py`, `split_bridges.py`, `split_by_region.py` | Batch / region orchestration (inputs: `bridges.txt`) |
| `bridges_api_new.php` | Server-side bridge API (credentials via environment) |
| `template.docx`, `region_template.docx` | Report templates (tracked) |
| `logos/`, `ellada2/` | Logos and Greece 2.0 branding used in reports |
| `secrets/.env.example` | Environment variable template (copy to `secrets/.env`) |

## Notes

- Generated reports, charts, logs, batch splits and secrets are not tracked by git (see `.gitignore`)
- `secrets/.env` is required locally for API access (see Secrets above)
