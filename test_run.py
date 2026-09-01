import sys, os, time
sys.stdout.reconfigure(encoding='utf-8')
sys.path.insert(0, '.')

from pathlib import Path
from bridge_info import fetch_bridge, build_tag_map, resensys_login
from main import render_docx

code_name = "ΑΤΤ_004_ΠΙΚ"
t0 = time.time()

def log(msg):
    print(f"[{time.time()-t0:6.0f}s] {msg}")
    sys.stdout.flush()

log("1. fetch_bridge...")
data = fetch_bridge(code_name)
log(f"   OK - {len(data.get('regions',[]))} regions")

log("2. render_docx...")
tags = build_tag_map(data)
render_docx(Path("template.docx"), tags, Path("output/step2_template.docx"))
log("   OK")

log("3. resensys_login...")
session, csrftoken = resensys_login(timeout=15)
log(f"   OK")

# Gather sensors
accel_sensors = []
for region in data.get("regions", []):
    for bridge in region.get("bridges", []):
        for site in bridge.get("sites", []):
            for sensor in site.get("sensors", []):
                sensor["site_id"] = site.get("site_id")
                accel_sensors.append(sensor)
log(f"   Sensors: {len(accel_sensors)} total")

graphs_dir = Path("graphs") / code_name
graphs_dir.mkdir(parents=True, exist_ok=True)

log("4. process_bridge_acceleration... (3 AC sensors x 3 axes = 9 calls, ~3min)")
try:
    # Extract pilot dates from sensors (required, no hardcoded fallback)
    pilot_start, pilot_finish = "", ""
    for s in accel_sensors:
        ps = s.get("Pilot_Date_Start", "") or ""
        pf = s.get("Pilot_Date_Finish", "") or ""
        if ps and not pilot_start:
            pilot_start = ps
        if pf and not pilot_finish:
            pilot_finish = pf
    if not pilot_start or not pilot_finish:
        raise RuntimeError("Missing Pilot_Date_Start/Fiinish in sensor data")
    from accel_fetcher import process_bridge_acceleration
    process_bridge_acceleration(code_name, accel_sensors, session, csrftoken, graphs_dir,
                                start_date=pilot_start, end_date=pilot_finish)
    log("   OK")
except Exception as e:
    log(f"   FAILED: {type(e).__name__}: {e}")

log("5. process_bridge_deformation... (12 strain sensors x 2 calls = 24 calls, ~8min)")
try:
    from main import process_bridge_deformation
    pilot_start, pilot_finish, deformation_sensors = process_bridge_deformation(code_name, session, csrftoken, graphs_dir)
    log(f"   OK: {pilot_start} -> {pilot_finish}")
except Exception as e:
    log(f"   FAILED: {type(e).__name__}: {e}")
    import traceback; traceback.print_exc()

log(f"\n=== Total: {time.time()-t0:.0f}s ===")
