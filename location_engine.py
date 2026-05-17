"""
location_engine.py
Multi-source cell tower location estimation with waterfall fallback:
  1. Unwired Labs (primary, requires token)
  2. BeaconDB (fallback 1, free)
  3. OpenCellID (fallback 2, requires token)
  4. IP geolocation (final fallback)
"""

import requests
import os
import json

DEFAULT_CONFIG = "config.json"

def load_config():
    if os.path.exists(DEFAULT_CONFIG):
        with open(DEFAULT_CONFIG, 'r') as f:
            return json.load(f)
    return {}

# ----------------------------------------------------------------------
# Source 1: Unwired Labs (primary)
# ----------------------------------------------------------------------
def fetch_tower_location_unwired(mcc, mnc, lac, cellid, token, timeout=10):
    if not token:
        return None
    url = "https://eu1.unwiredlabs.com/v2/process.php"
    payload = {
        "token": token,
        "radio": "lte",
        "mcc": mcc,
        "mnc": mnc,
        "cells": [{"lac": lac, "cid": cellid}],
        "address": 0
    }
    try:
        resp = requests.post(url, json=payload, timeout=timeout)
        resp.raise_for_status()
        data = resp.json()
        if data.get("status") == "ok":
            lat = data.get("lat")
            lon = data.get("lon")
            if lat is not None and lon is not None:
                return {"lat": float(lat), "lon": float(lon), "range": float(data.get("accuracy", 500))}
    except Exception:
        pass
    return None

# ----------------------------------------------------------------------
# Source 2: BeaconDB (free)
# ----------------------------------------------------------------------
def fetch_tower_location_beacondb(mcc, mnc, lac, cellid, timeout=10):
    url = "https://api.beacondb.net/v1/geolocate"
    payload = {
        "cellTowers": [{
            "mobileCountryCode": mcc,
            "mobileNetworkCode": mnc,
            "locationAreaCode": lac,
            "cellId": cellid
        }]
    }
    try:
        resp = requests.post(url, json=payload, timeout=timeout)
        resp.raise_for_status()
        data = resp.json()
        location = data.get("location", {})
        lat = location.get("lat")
        lon = location.get("lng")
        if lat is not None and lon is not None:
            return {"lat": float(lat), "lon": float(lon), "range": float(data.get("accuracy", 1000))}
    except Exception:
        pass
    return None

# ----------------------------------------------------------------------
# Source 3: OpenCellID (requires token)
# ----------------------------------------------------------------------
def fetch_tower_location_opencellid(mcc, mnc, lac, cellid, token, timeout=15):
    if not token:
        return None
    url = "https://opencellid.org/cell/get"
    params = {
        "key": token,
        "mcc": mcc,
        "mnc": mnc,
        "lac": lac,
        "cellid": cellid,
        "format": "json"
    }
    try:
        resp = requests.get(url, params=params, timeout=timeout)
        resp.raise_for_status()
        data = resp.json()
        if data.get("error"):
            return None
        lat = data.get("lat")
        lon = data.get("lon")
        if lat is None or lon is None:
            return None
        return {"lat": float(lat), "lon": float(lon), "range": float(data.get("range", 1000))}
    except Exception:
        pass
    return None

# ----------------------------------------------------------------------
# Final Fallback: IP Geolocation
# ----------------------------------------------------------------------
def fetch_ip_location():
    try:
        resp = requests.get("http://ip-api.com/json/", timeout=5)
        resp.raise_for_status()
        data = resp.json()
        if data.get("status") == "success":
            return {"lat": data["lat"], "lon": data["lon"], "range": 20000}
    except Exception:
        pass
    return None

# ----------------------------------------------------------------------
# Main waterfall (original order)
# ----------------------------------------------------------------------
def fetch_tower_location(mcc, mnc, lac, cellid, unwired_token=None, opencellid_token=None, **kwargs):
    # 1. Unwired Labs
    if unwired_token:
        result = fetch_tower_location_unwired(mcc, mnc, lac, cellid, unwired_token)
        if result:
            return result

    # 2. BeaconDB
    result = fetch_tower_location_beacondb(mcc, mnc, lac, cellid)
    if result:
        return result

    # 3. OpenCellID
    if opencellid_token:
        result = fetch_tower_location_opencellid(mcc, mnc, lac, cellid, opencellid_token)
        if result:
            return result

    # 4. IP fallback
    result = fetch_ip_location()
    if result:
        return result

    raise RuntimeError(f"Could not determine location for cell {cellid} from any source.")

# ----------------------------------------------------------------------
# Weighted centroid (unchanged)
# ----------------------------------------------------------------------
def dbm_to_linear(signal_dbm):
    return 10 ** (signal_dbm / 10)

def weighted_centroid(towers):
    if not towers:
        raise ValueError("No towers provided")
    if len(towers) == 1:
        return towers[0]["lat"], towers[0]["lon"]
    total_weight = 0.0
    sum_lat = 0.0
    sum_lon = 0.0
    for t in towers:
        lat = float(t["lat"])
        lon = float(t["lon"])
        signal = t.get("signal_dbm")
        w = dbm_to_linear(float(signal)) if signal is not None else 1.0
        total_weight += w
        sum_lat += lat * w
        sum_lon += lon * w
    return sum_lat / total_weight, sum_lon / total_weight

def estimate_location(tower_list, unwired_token=None, opencellid_token=None, **kwargs):
    if not tower_list:
        raise ValueError("tower_list is empty")
    full_tower_data = []
    for tower in tower_list:
        loc = fetch_tower_location(
            mcc=tower["mcc"],
            mnc=tower["mnc"],
            lac=tower["lac"],
            cellid=tower["cellid"],
            unwired_token=unwired_token,
            opencellid_token=opencellid_token
        )
        loc_copy = loc.copy()
        if "signal_dbm" in tower:
            loc_copy["signal_dbm"] = tower["signal_dbm"]
        loc_copy["cellid"] = tower["cellid"]
        full_tower_data.append(loc_copy)
    if len(full_tower_data) == 1:
        est_lat = full_tower_data[0]["lat"]
        est_lon = full_tower_data[0]["lon"]
        accuracy = full_tower_data[0]["range"]
    else:
        est_lat, est_lon = weighted_centroid(full_tower_data)
        accuracy = max(t["range"] for t in full_tower_data)
    return {
        "lat": est_lat,
        "lon": est_lon,
        "accuracy": accuracy,
        "towers_used": full_tower_data
    }
