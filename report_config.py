"""One explicit API destination for the repaired pilot report client."""
import os
import ssl
from pathlib import Path
from dotenv import load_dotenv

ROOT=Path(__file__).resolve().parent
load_dotenv(ROOT/'secrets'/'.env')
load_dotenv(ROOT/'.env')
API_KEY=os.getenv('API_KEY') or os.getenv('RESENSYS_API_KEY','')
API_ROOT=os.getenv('RESENSYS_API_V3_URL','https://greece-api.resensys.cloud/api/v3').rstrip('/')
PORTAL_ROOT=os.getenv('RESENSYS_PORTAL_URL','https://greece.resensys.cloud').rstrip('/')
BRIDGES_URL=os.getenv('BRIDGES_API_URL','https://lms4.globiled.com/Bridges_api/bridges_api_new.php')
REQUESTS_VERIFY=os.getenv('REQUESTS_CA_BUNDLE') or os.getenv('SSL_CERT_FILE') or ssl.get_default_verify_paths().cafile or True
REPORT_OUTPUT_DIR=Path(os.getenv('PILOT_REPORTS_DIR') or ROOT/'generated_reports')
