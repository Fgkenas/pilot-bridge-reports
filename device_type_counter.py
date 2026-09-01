"""
Device type counter for sensor inventory tables.

Uses Resensys API to get accurate device type information and count
sensors by type for report generation.
"""

import os
import requests
import urllib3
from typing import Dict, List
from dotenv import load_dotenv

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

load_dotenv()
API_KEY = os.getenv("API_KEY")

resensys_header = {
    "X-API-Key": API_KEY
}

_device_type_cache: Dict[str, Dict] = {}


def get_device_type(did: str) -> Dict:
    """Get device type information from Resensys API.

    Results are cached in memory to avoid duplicate API calls
    for the same device across multiple bridges.

    Args:
        did: Device ID

    Returns:
        Dictionary containing device type information including
        code_name, product_name, product_description, etc.
    """
    if did in _device_type_cache:
        return _device_type_cache[did]

    payload = {"did": did}
    try:
        r = requests.post(
            "https://greece-api.resensys.cloud/api/v3/device_type",
            headers=resensys_header,
            json=payload,
            timeout=15,
            verify=False,
        )
        r_json = r.json()
        _device_type_cache[did] = r_json
        return r_json
    except Exception as e:
        print(f"Error getting device type for {did}: {e}")

    return dict()


def count_device_types(device_ids: List[str], use_code_name: bool = True) -> Dict[str, int]:
    """Count devices by type from a list of device IDs.
    
    Args:
        device_ids: List of device IDs to count
        use_code_name: If True, use code_name for counting; 
                      if False, use product_name
        
    Returns:
        Dictionary mapping device type to count
        Example: {"HPA": 5, "STRAIN": 3}
    """
    type_counts = {}

    for did in dict.fromkeys(device_ids):  # deduplicate, preserve order
        device_type_dict = get_device_type(did)
        
        if use_code_name and 'code_name' in device_type_dict:
            device_type = device_type_dict['code_name']
        elif not use_code_name and 'product_name' in device_type_dict:
            device_type = device_type_dict['product_name']
        else:
            continue
        
        type_counts[device_type] = type_counts.get(device_type, 0) + 1
    
    return type_counts


def get_device_type_details(device_ids: List[str]) -> Dict[str, Dict]:
    """Get detailed device type information for a list of device IDs.
    
    Args:
        device_ids: List of device IDs
        
    Returns:
        Dictionary mapping device ID to its type information
    """
    device_details = {}
    
    for did in device_ids:
        device_type_dict = get_device_type(did)
        if device_type_dict:
            device_details[did] = device_type_dict
    
    return device_details
