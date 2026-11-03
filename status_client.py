"""Keep transport success separate from the good/bad sensor verdict."""
import time
import requests
from api_rate_limiter import acquire_slot,release_slot,wait_for_gap
from report_config import API_ROOT,API_KEY,REQUESTS_VERIFY


def fetch_status(endpoint,did,metric):
    if not did:return {'ok':False,'fetch_ok':False,'error':'Δεν υπάρχει device_id'}
    response=None
    for attempt in range(3):
        acquire_slot()
        try:
            wait_for_gap()
            response=requests.post(API_ROOT+'/'+endpoint,json={'did':did},
                                   headers={'X-API-Key':API_KEY},timeout=(5,60),verify=REQUESTS_VERIFY)
        except requests.RequestException as error:
            if attempt==2:
                return {'ok':False,'fetch_ok':False,'error':'Αποτυχία επικοινωνίας: '+type(error).__name__}
            response=None
        finally:release_slot()
        if response is not None and response.status_code not in (429,500,502,503,504):break
        if attempt<2:time.sleep(2**attempt)
    if response is None:return {'ok':False,'fetch_ok':False,'error':'Αποτυχία επικοινωνίας'}
    try:raw=response.json()
    except ValueError:raw={}
    if response.status_code!=200:
        detail=raw.get('error','') if isinstance(raw,dict) else ''
        return {'ok':False,'fetch_ok':False,'status_code':response.status_code,
                'error':f'HTTP {response.status_code}'+(': '+str(detail)[:180] if detail else '')}
    if not isinstance(raw,dict) or raw.get('error'):
        return {'ok':False,'fetch_ok':False,'error':'Μη έγκυρη απάντηση ελέγχου'}
    status=str(raw.get('status','')).lower()
    verdict=True if status in {'good','καλό','καλή'} else False if status in {'bad','κακό','κακή'} else None
    reason={'No battery reading in the last 24 hours':'Δεν υπάρχουν μετρήσεις μπαταρίας τις τελευταίες 24 ώρες',
            'No recent acceleration reading':'Δεν υπάρχουν πρόσφατες μετρήσεις επιτάχυνσης',
            'Acceleration configuration threshold is unavailable':'Δεν είναι διαθέσιμη η ρύθμιση ορίου επιτάχυνσης',
            'Historical HPA classifier is not configured':'Δεν είναι διαθέσιμο το μοντέλο αξιολόγησης επιτάχυνσης'}.get(raw.get('reason'),raw.get('reason'))
    return {'ok':verdict is True,'fetch_ok':True,'verdict':verdict,'raw':raw,
            'latest_time':raw.get('latest_time',''),'time_zone':raw.get('time_zone',''),
            'metrics':{metric:raw.get('latest','')},'reason':reason or 'Η αξιολόγηση δεν είναι διαθέσιμη'}


def status_cells(result,metric,bad_label='Κακή'):
    """Return measured value, verdict, explanation and the actual reading time."""
    if not result.get('fetch_ok'):
        return '—','N/A',result.get('error','Αποτυχία ανάκτησης ελέγχου'),''
    value=result.get('metrics',{}).get(metric)
    value='—' if value is None or value=='' else str(value)
    verdict=result.get('verdict')
    status='Καλή' if verdict is True else bad_label if verdict is False else 'N/A'
    comment='Εντός ορίων' if verdict is True else 'Εκτός ορίων' if verdict is False else result.get('reason','Δεν αξιολογήθηκε')
    return value,status,comment,result.get('latest_time','')
