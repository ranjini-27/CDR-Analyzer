"""
app.py – Full backend for CDR Forensic Analyzer
Includes case management, forensic analysis, location, tokens, and all CDR endpoints.
Enhanced with daily timeline and radar data endpoints.
"""

import os
import json
import uuid
import time
import shutil
import numpy as np
import pandas as pd
from dotenv import load_dotenv
load_dotenv()
from flask import Flask, render_template, request, jsonify, send_from_directory
from werkzeug.utils import secure_filename
from cdr_parser import parse_cdr_file, auto_detect_columns, load_cdr_file
from profiler import Profiler
from location_engine import estimate_location
from advanced_profiler import compute_advanced_metrics

app = Flask(__name__)
CONFIG_FILE = "config.json"

# Temporary session-only storage
cases_storage = {}

# Temporary uploads folder
TEMP_UPLOAD_DIR = "temp_uploads"
os.makedirs(TEMP_UPLOAD_DIR, exist_ok=True)
# Clear temporary uploads on every restart
for file in os.listdir(TEMP_UPLOAD_DIR):
    file_path = os.path.join(TEMP_UPLOAD_DIR, file)

    try:
        if os.path.isfile(file_path):
            os.remove(file_path)
    except:
        pass

# ---------- config helpers ----------
def load_settings():
    return {
        "unwired_api_token": os.environ.get("UNWIRED_TOKEN", ""),
        "opencellid_api_token": os.environ.get("OPENCELLID_TOKEN", ""),
        "google_api_key": os.environ.get("GOOGLE_API_KEY", "")
    }

def save_settings(settings):
    pass

# ---------- simple in‑memory CDR cache ----------
cdr_cache: dict = {}

def get_cdr_df(filepath: str, mapping: dict):
    cache_key = filepath + json.dumps(mapping)
    if cache_key in cdr_cache:
        return cdr_cache[cache_key]
    df, _ = parse_cdr_file(filepath, manual_mapping=mapping)
    cdr_cache[cache_key] = df
    return df

# ======================================================================
#   CASE MANAGEMENT
# ======================================================================



def get_case_data(case_id):
    """Return (filepath, mapping) for a case, or (None, None) if not found."""
    case = cases_storage.get(case_id)
    if not case or not case.get('filepath'):
        return None, None
    return case['filepath'], case['mapping']

@app.route('/api/cases', methods=['GET'])
def list_cases():
    cases = cases_storage
    case_list = []
    for case_id, data in cases.items():
        case_list.append({
            "id": case_id,
            "name": data.get("name"),
            "created_at": data.get("created_at"),
            "has_cdr": bool(data.get("filepath"))
        })
    return jsonify(case_list)

@app.route('/api/cases', methods=['POST'])
def create_case():
    case_name = request.form.get('case_name')
    if not case_name:
        return jsonify({"error": "case_name is required"}), 400
    file = request.files.get('file')
    if not file:
        return jsonify({"error": "CDR file is required"}), 400

    case_id = str(uuid.uuid4())[:8]
    filename = secure_filename(file.filename)
    filepath = os.path.join(TEMP_UPLOAD_DIR, f"{case_id}_{filename}")
    file.save(filepath)

    try:
        df = load_cdr_file(filepath)
        mapping = auto_detect_columns(df)
    except Exception as e:
        if os.path.exists(filepath):
           os.remove(filepath)

        return jsonify({"error": f"Invalid CDR file: {str(e)}"}), 400
    cases_storage[case_id] = {
      "id": case_id,
      "name": case_name,
      "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
      "filepath": filepath,
      "mapping": mapping,
      "columns": list(df.columns)
}
    return jsonify({"id": case_id, "name": case_name, "filepath": filepath, "mapping": mapping})

@app.route('/api/cases/<case_id>/upload_cdr', methods=['POST'])
def upload_cdr_to_case(case_id):
    cases = cases_storage
    if case_id not in cases:
        return jsonify({"error": "Case not found"}), 404
    file = request.files.get('file')
    if not file:
        return jsonify({"error": "No file provided"}), 400

    filename = secure_filename(file.filename)
    filepath = os.path.join(TEMP_UPLOAD_DIR, f"{case_id}_{filename}")
    file.save(filepath)

    try:
        df = load_cdr_file(filepath)
        mapping = auto_detect_columns(df)
    except Exception as e:
        return jsonify({"error": f"Invalid CDR file: {str(e)}"}), 400

    # Update case
    cases[case_id]['filepath'] = filepath
    cases[case_id]['mapping'] = mapping
    cases[case_id]['columns'] = list(df.columns)
    

    # Invalidate cache
    cache_key = filepath + json.dumps(mapping)
    if cache_key in cdr_cache:
        del cdr_cache[cache_key]

    return jsonify({"status": "ok", "filepath": filepath, "mapping": mapping})

@app.route('/api/cases/<case_id>', methods=['GET'])
def get_case(case_id):
    cases = cases_storage
    case = cases.get(case_id)
    if not case:
        return jsonify({"error": "Case not found"}), 404
    return jsonify({
        "id": case_id,
        "name": case["name"],
        "created_at": case["created_at"],
        "filepath": case["filepath"],
        "mapping": case["mapping"],
        "columns": case.get("columns", [])
    })

@app.route('/api/cases/<case_id>', methods=['DELETE'])
def delete_case(case_id):
    cases = cases_storage

    if case_id not in cases:
        return jsonify({"error": "Case not found"}), 404

    case = cases[case_id]

    if os.path.exists(case["filepath"]):
        os.remove(case["filepath"])

    del cases[case_id]

    return jsonify({"status": "deleted"})
# ======================================================================
#   FRONTEND – NEW DASHBOARD
# ======================================================================
@app.route('/')
def index():
    return render_template('forensic_dashboard.html')

# ======================================================================
#   CDR UPLOAD (legacy, but keep for compatibility)
# ======================================================================
@app.route('/api/upload_cdr', methods=['POST'])
def upload_cdr():
    if 'file' not in request.files:
        return jsonify({"error": "No file part"}), 400
    file = request.files['file']
    if file.filename == '':
        return jsonify({"error": "No selected file"}), 400
    filepath = os.path.join(TEMP_UPLOAD_DIR, file.filename)
    file.save(filepath)
    try:
        df = load_cdr_file(filepath)
        mapping = auto_detect_columns(df)
        return jsonify({"columns": list(df.columns), "mapping": mapping, "filepath": filepath})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

# ======================================================================
#   BASIC CDR ANALYSIS (accepts case_id or legacy filepath+mapping)
# ======================================================================
@app.route('/api/analyse_cdr', methods=['POST'])
def analyse_cdr():
    data = request.json
    case_id = data.get('case_id')
    if case_id:
        filepath, mapping = get_case_data(case_id)
        if not filepath:
            return jsonify({"error": "No CDR file for this case"}), 400
    else:
        # legacy mode
        filepath = data.get('filepath')
        mapping = data.get('mapping')
        if not filepath or not mapping:
            return jsonify({"error": "Missing filepath or mapping"}), 400

    try:
        df, _ = parse_cdr_file(filepath, manual_mapping=mapping)
        profiler = Profiler(df)
        summary = {
            "total_calls": profiler.total_calls,
            "total_sms": profiler.total_sms,
            "avg_duration": round(profiler.avg_call_duration, 1),
        }
        top_contacts = profiler.top_contacts.to_dict(orient='records')
        top_contacts_detailed = profiler.top_contacts_detailed.to_dict(orient='records')
        hourly = profiler.hourly_activity.reset_index()
        hourly.columns = ['hour', 'count']
        hourly_data = hourly.to_dict(orient='records')
        daily = profiler.daily_activity.reset_index()
        daily.columns = ['day', 'count']
        daily_data = daily.to_dict(orient='records')
        heatmap_data = profiler.heatmap_data
        heatmap_labels = {
            "days": heatmap_data.index.tolist(),
            "hours": [str(h) for h in heatmap_data.columns.tolist()],
            "data": heatmap_data.values.tolist()
        }
        return jsonify({
            "summary": summary,
            "top_contacts": top_contacts,
            "top_contacts_detailed": top_contacts_detailed,
            "hourly_activity": hourly_data,
            "daily_activity": daily_data,
            "heatmap": heatmap_labels
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500

# ======================================================================
#   ADVANCED ANALYSIS (case_id)
# ======================================================================
@app.route('/api/advanced_analysis', methods=['POST'])
def advanced_analysis():
    data = request.json
    case_id = data.get('case_id')
    if not case_id:
        return jsonify({"error": "case_id required"}), 400
    filepath, mapping = get_case_data(case_id)
    if not filepath:
        return jsonify({"error": "No CDR file for this case"}), 400
    try:
        metrics = compute_advanced_metrics(filepath, mapping)
        return jsonify(metrics)
    except Exception as e:
        return jsonify({"error": str(e)}), 500

# ======================================================================
#   CONTACT TIMELINE (case_id)
# ======================================================================
@app.route('/api/contact_timeline', methods=['POST'])
def contact_timeline():
    data = request.json
    case_id = data.get('case_id')
    phone = data.get('phone')
    if not case_id or not phone:
        return jsonify({"error": "Missing case_id or phone"}), 400
    filepath, mapping = get_case_data(case_id)
    if not filepath:
        return jsonify({"error": "No CDR file for this case"}), 400
    try:
        df = get_cdr_df(filepath, mapping)
        contact_df = df[df['number'] == phone].sort_values('datetime')
        timeline = contact_df[['datetime', 'type', 'duration', 'contact_name']].copy()
        timeline['datetime'] = timeline['datetime'].dt.strftime('%Y-%m-%d %H:%M:%S')
        timeline['duration'] = timeline['duration'].round(1)
        return jsonify(timeline.to_dict(orient='records'))
    except Exception as e:
        return jsonify({"error": str(e)}), 500

# ======================================================================
#   TOWER UPLOAD & LOCATION (unchanged)
# ======================================================================
@app.route('/api/upload_towers', methods=['POST'])
def upload_towers():
    if 'file' not in request.files:
        return jsonify({"error": "No file part"}), 400
    file = request.files['file']
    if file.filename == '':
        return jsonify({"error": "No selected file"}), 400
    try:
        if file.filename.endswith('.csv'):
            df = pd.read_csv(file)
        else:
            df = pd.read_excel(file, engine='openpyxl')
        for col in ['MCC', 'MNC', 'LAC', 'CellID']:
            if col not in df.columns:
                match = [c for c in df.columns if c.lower() == col.lower()]
                if match:
                    df.rename(columns={match[0]: col}, inplace=True)
                else:
                    return jsonify({"error": f"Missing column: {col}"}), 400
        if 'Signal_dBm' not in df.columns:
            df['Signal_dBm'] = None
        towers = []
        for _, row in df.iterrows():
            towers.append({
                "mcc": int(row['MCC']),
                "mnc": int(row['MNC']),
                "lac": int(row['LAC']),
                "cellid": int(row['CellID']),
                "signal_dbm": float(row['Signal_dBm']) if not pd.isna(row.get('Signal_dBm')) else None
            })
        return jsonify({"towers": towers, "count": len(towers)})
    except Exception as e:
        return jsonify({"error": str(e)}), 400

@app.route('/api/estimate_location', methods=['POST'])
def estimate_loc():
    data = request.json
    towers = data.get('towers')
    settings = load_settings()

    unwired_token = settings.get('unwired_api_token')
    opencellid_token = settings.get('opencellid_api_token')
    if not towers:
        return jsonify({"error": "No towers provided"}), 400
    try:
        result = estimate_location(towers, unwired_token=unwired_token, opencellid_token=opencellid_token)
        return jsonify(result)
    except Exception as e:
        return jsonify({"error": str(e)}), 500

# ======================================================================
#   ENDPOINTS FOR THE DASHBOARD (NEW)
# ======================================================================
@app.route('/api/daily_timeline', methods=['POST'])
def daily_timeline():
    """Returns daily call and SMS counts for the line chart."""
    data = request.json
    case_id = data.get('case_id')
    if not case_id:
        return jsonify({"error": "case_id required"}), 400
    filepath, mapping = get_case_data(case_id)
    if not filepath:
        return jsonify({"error": "No CDR file for this case"}), 400
    df = get_cdr_df(filepath, mapping)
    df['date'] = df['datetime'].dt.date
    df['is_call'] = ~df['type'].str.lower().str.contains('sms')
    df['is_sms'] = df['type'].str.lower().str.contains('sms')
    daily_calls = df[df['is_call']].groupby('date').size().reset_index(name='calls')
    daily_sms = df[df['is_sms']].groupby('date').size().reset_index(name='sms')
    merged = pd.merge(daily_calls, daily_sms, on='date', how='outer').fillna(0)
    merged['date_str'] = merged['date'].astype(str)
    return jsonify({
        "dates": merged['date_str'].tolist(),
        "calls": merged['calls'].astype(int).tolist(),
        "sms": merged['sms'].astype(int).tolist()
    })

@app.route('/api/radar_data', methods=['POST'])
def radar_data():
    """Returns anomaly radar scores for the radar chart."""
    data = request.json
    case_id = data.get('case_id')
    if not case_id:
        return jsonify({"error": "case_id required"}), 400
    filepath, mapping = get_case_data(case_id)
    if not filepath:
        return jsonify({"error": "No CDR file for this case"}), 400
    df = get_cdr_df(filepath, mapping)
    # Compute radar metrics
    night_mask = (df['datetime'].dt.hour >= 22) | (df['datetime'].dt.hour <= 5)
    night_calls = df[night_mask & (~df['type'].str.lower().str.contains('sms'))]
    night_score = min(10, len(night_calls) / max(1, len(df)) * 20)

    # Burst events: rapid calls to same number within 10 min
    df_sorted = df.sort_values(['number', 'datetime'])
    burst_count = 0
    for number, group in df_sorted.groupby('number'):
        if len(group) < 2:
            continue
        prev = None
        for _, row in group.iterrows():
            if prev and (row['datetime'] - prev).total_seconds() <= 600:
                burst_count += 1
            prev = row['datetime']
    burst_score = min(10, burst_count / max(1, len(df)) * 30)

    # New contacts per day (surge)
    first_seen = {}
    new_contacts_count = 0
    for _, row in df.sort_values('datetime').iterrows():
        if row['number'] not in first_seen:
            first_seen[row['number']] = row['datetime'].date()
            new_contacts_count += 1
    new_score = min(10, new_contacts_count / max(1, df['number'].nunique()) * 15)

    # Roaming / off-network (placeholders: assume 5% of calls)
    roaming_score = min(10, len(df) * 0.05 / max(1, len(df)) * 100)

    # Silent gaps (days with zero activity)
    days_active = df['datetime'].dt.date.nunique()
    total_days = (df['datetime'].max() - df['datetime'].min()).days + 1 if len(df) > 1 else 1
    silent_gaps = max(0, total_days - days_active)
    silent_score = min(10, silent_gaps / max(1, total_days) * 30)

    # Rapid SMS sequences (more than 3 SMS within 5 min)
    sms_df = df[df['type'].str.lower().str.contains('sms')].sort_values('datetime')
    rapid_sms = 0
    if len(sms_df) > 1:
        prev = sms_df.iloc[0]['datetime']
        count = 1
        for _, row in sms_df.iloc[1:].iterrows():
            if (row['datetime'] - prev).total_seconds() <= 300:
                count += 1
                if count >= 3:
                    rapid_sms += 1
            else:
                count = 1
            prev = row['datetime']
    rapid_score = min(10, rapid_sms / max(1, len(sms_df)) * 50)

    return jsonify({
        "labels": ["Night Calls", "Burst Events", "New Contacts", "Roaming", "Silent Gaps", "Rapid SMS"],
        "actual": [night_score, burst_score, new_score, roaming_score, silent_score, rapid_score],
        "baseline": [5, 4, 6, 3, 4, 5]   # typical baseline
    })

# ======================================================================
#   ORIGINAL ANALYSIS ENDPOINTS (required by dashboard)
# ======================================================================

@app.route('/api/frequency')
def get_frequency():
    case_id = request.args.get('case_id')
    filepath = request.args.get('filepath')
    mapping = request.args.get('mapping')
    if case_id:
        filepath, mapping = get_case_data(case_id)
        if not filepath:
            return jsonify({"error": "No CDR file for this case"}), 400
    elif not filepath or not mapping:
        return jsonify({"error": "Missing case_id or filepath/mapping"}), 400
    else:
        mapping = json.loads(mapping)

    try:
        df = get_cdr_df(filepath, mapping)
        freq = df['number'].value_counts().reset_index()
        freq.columns = ['called_number', 'call_count']
        top = freq.head(30).to_dict(orient='records')
        return jsonify(top)
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/api/hourly')
def get_hourly():
    case_id = request.args.get('case_id')
    filepath = request.args.get('filepath')
    mapping = request.args.get('mapping')
    if case_id:
        filepath, mapping = get_case_data(case_id)
        if not filepath:
            return jsonify({"error": "No CDR file for this case"}), 400
    elif not filepath or not mapping:
        return jsonify({"error": "Missing case_id or filepath/mapping"}), 400
    else:
        mapping = json.loads(mapping)

    try:
        df = get_cdr_df(filepath, mapping)
        hours = [0] * 24
        for dt in df['datetime']:
            hours[dt.hour] += 1
        return jsonify([{"hour": i, "count": hours[i]} for i in range(24)])
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/api/network')
def get_network():
    case_id = request.args.get('case_id')
    filepath = request.args.get('filepath')
    mapping = request.args.get('mapping')
    if case_id:
        filepath, mapping = get_case_data(case_id)
        if not filepath:
            return jsonify({"error": "No CDR file for this case"}), 400
    elif not filepath or not mapping:
        return jsonify({"error": "Missing case_id or filepath/mapping"}), 400
    else:
        mapping = json.loads(mapping)

    try:
        df = get_cdr_df(filepath, mapping)
        subscriber = df['number'].value_counts().index[0]
        freq = df['number'].value_counts().reset_index()
        freq.columns = ['id', 'call_count']
        nodes = [{"id": subscriber, "call_count": 0}]
        for _, row in freq.iterrows():
            if row['id'] != subscriber:
                nodes.append({"id": row['id'], "call_count": int(row['call_count'])})
        edges = []
        for _, row in freq.iterrows():
            if row['id'] != subscriber:
                edges.append({"source": subscriber, "target": row['id'], "weight": int(row['call_count'])})
        return jsonify({"nodes": nodes, "edges": edges, "subscriber": subscriber})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/api/behavior')
def get_behavior():
    case_id = request.args.get('case_id')
    filepath = request.args.get('filepath')
    mapping = request.args.get('mapping')
    if case_id:
        filepath, mapping = get_case_data(case_id)
        if not filepath:
            return jsonify({"error": "No CDR file for this case"}), 400
    elif not filepath or not mapping:
        return jsonify({"error": "Missing case_id or filepath/mapping"}), 400
    else:
        mapping = json.loads(mapping)

    try:
        df = get_cdr_df(filepath, mapping)
        night_mask = (df['datetime'].dt.hour >= 22) | (df['datetime'].dt.hour <= 5)
        night_calls = df[night_mask]
        night_activity_count = len(night_calls)
        long_calls = df[df['duration'] > 300]
        long_call_count = len(long_calls)
        df['date_only'] = df['datetime'].dt.date
        daily_counts = df.groupby('date_only').size().reset_index(name='count')
        if len(daily_counts) >= 2:
            mean_count = daily_counts['count'].mean()
            spike_data = []
            for _, row in daily_counts.iterrows():
                is_spike = row['count'] > (mean_count * 1.5)
                spike_data.append({
                    "date": row['date_only'].isoformat(),
                    "count": int(row['count']),
                    "baseline": round(mean_count, 1),
                    "is_spike": is_spike
                })
            spike_events = sum(1 for d in spike_data if d['is_spike'])
        else:
            spike_data = [{
                "date": df['date_only'].iloc[0].isoformat(),
                "count": len(df),
                "baseline": len(df),
                "is_spike": False
            }]
            spike_events = 0

        total_calls = len(df)
        contact_stats = []
        for number in df['number'].unique():
            contact_df = df[df['number'] == number]
            cnt = len(contact_df)
            night_ratio = len(contact_df[night_mask]) / max(1, cnt)
            freq_avg = total_calls / max(1, df['number'].nunique())
            std = df.groupby('number').size().std() or 1
            freq_z = (cnt - freq_avg) / max(1, std)
            score = (night_ratio * 5) + (max(0, freq_z) * 0.5)
            flags = []
            if night_ratio > 0.3:
                flags.append("night_activity")
            if freq_z > 1.5:
                flags.append("frequency_spike")
            risk = "HIGH" if score > 6 else "MEDIUM" if score > 3 else "LOW"
            contact_stats.append({
                "number": number,
                "score": round(score, 1),
                "risk": risk,
                "flags": flags
            })
        top_suspicious = sorted(contact_stats, key=lambda x: x['score'], reverse=True)[:10]

        event_log = []
        for _, row in night_calls.head(50).iterrows():
            event_log.append({
                "timestamp": row['datetime'].isoformat(),
                "number": row['number'],
                "type": "night_activity",
                "description": f"Call at {row['datetime'].strftime('%H:%M')} (unusual hour)"
            })
        for _, row in long_calls.head(20).iterrows():
            event_log.append({
                "timestamp": row['datetime'].isoformat(),
                "number": row['number'],
                "type": "long_call",
                "description": f"Long call: {int(row['duration'])}s (>5 min)"
            })
        for spike in spike_data:
            if spike.get("is_spike", False):
                event_log.append({
                    "timestamp": spike["date"],
                    "number": "—",
                    "type": "spike",
                    "description": f"Volume spike: {spike['count']} vs baseline {spike['baseline']}"
                })
        return jsonify({
            "summary": {
                "total_anomalies": len(event_log),
                "night_activity_count": night_activity_count,
                "spike_events": spike_events,
                "new_contacts": 0
            },
            "top_suspicious": top_suspicious,
            "spike_data": spike_data,
            "night_heatmap": [],
            "event_log": event_log[:50]
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/api/forensic_anomalies', methods=['POST'])
def forensic_anomalies():
    data = request.json
    case_id = data.get('case_id')
    analysis_type = data.get('analysis_type')
    if not case_id:
        return jsonify({"error": "case_id required"}), 400
    filepath, mapping = get_case_data(case_id)
    if not filepath:
        return jsonify({"error": "No CDR file for this case"}), 400
    df = get_cdr_df(filepath, mapping)

    # Helper functions for each analysis
    def top_contacts():
        freq = df['number'].value_counts().reset_index()
        freq.columns = ['number', 'call_count']
        mean_freq = freq['call_count'].mean()
        std_freq = freq['call_count'].std()
        threshold = mean_freq + 1.5 * std_freq
        suspicious = freq[freq['call_count'] > threshold].to_dict(orient='records')
        for s in suspicious:
            s['call_count'] = int(s['call_count'])
        return {
            "analysis": "Top Contacts",
            "data": freq.head(20).to_dict(orient='records'),
            "anomalies": suspicious,
            "insight": "Contacts with unusually high frequency may indicate strong dependency or coordination."
        }

    def daily_activity():
        df['date'] = df['datetime'].dt.date
        daily = df.groupby('date').size().reset_index(name='count')
        mean_daily = daily['count'].mean()
        std_daily = daily['count'].std()
        threshold = mean_daily + 1.5 * std_daily
        spikes = daily[daily['count'] > threshold].copy()
        spikes['date'] = spikes['date'].astype(str)
        return {
            "analysis": "Daily Activity",
            "data": daily.to_dict(orient='records'),
            "anomalies": spikes.to_dict(orient='records'),
            "insight": "Sudden spikes in call volume may indicate coordinated events or unusual activity."
        }

    def hourly_pattern():
        hourly = df['datetime'].dt.hour.value_counts().sort_index()
        night_hours = [22,23,0,1,2,3,4,5]
        night_activity = hourly[hourly.index.isin(night_hours)].sum()
        total = hourly.sum()
        night_ratio = night_activity / total if total else 0
        anomalies = []
        if night_ratio > 0.2:
            anomalies.append({
                "hours": night_hours,
                "call_count": int(night_activity),
                "percentage": round(night_ratio*100,1),
                "reason": "High late-night activity (unusual hours)"
            })
        mean_hour = hourly.mean()
        std_hour = hourly.std()
        high_hours = hourly[hourly > mean_hour + 1.5*std_hour].index.tolist()
        if high_hours:
            anomalies.append({
                "hours": high_hours,
                "call_count": int(hourly[high_hours].sum()),
                "reason": "Peak hours significantly above normal"
            })
        return {
            "analysis": "Hourly Pattern",
            "data": [{"hour": int(h), "count": int(c)} for h,c in hourly.items()],
            "anomalies": anomalies,
            "insight": "Non-routine communication behavior, especially late night."
        }

    def call_length():
        calls = df[~df['type'].str.lower().str.contains('sms')].copy()
        if len(calls) == 0:
            return {"analysis": "Call Length Pattern", "data": [], "anomalies": [], "insight": "No voice calls found."}
        call_durations = calls['duration']
        mean_dur = call_durations.mean()
        std_dur = call_durations.std()
        short_calls = calls[calls['duration'] < 5]
        long_threshold = mean_dur + 2*std_dur
        long_calls = calls[calls['duration'] > long_threshold]
        anomalies = []
        if len(short_calls) > 0:
            anomalies.append({
                "type": "Very short calls (<5s)",
                "count": len(short_calls),
                "example_numbers": short_calls['number'].head(5).tolist(),
                "insight": "May indicate signaling, missed calls, or automated pings."
            })
        if len(long_calls) > 0:
            anomalies.append({
                "type": "Unusually long calls",
                "count": len(long_calls),
                "threshold_seconds": round(long_threshold,1),
                "example_durations": long_calls['duration'].head(5).tolist(),
                "insight": "Intensive or covert communication."
            })
        return {
            "analysis": "Call Length Pattern",
            "data": [{"duration_sec": d} for d in call_durations.head(100).tolist()],
            "anomalies": anomalies,
            "insight": "Short bursts may signal; long calls indicate deep engagement."
        }

    def time_spent():
        calls = df[~df['type'].str.lower().str.contains('sms')]
        if len(calls) == 0:
            return {"analysis": "Time Spent per Contact", "data": [], "anomalies": [], "insight": "No call data."}
        time_spent = calls.groupby('number')['duration'].sum().reset_index()
        time_spent.columns = ['number', 'total_duration']
        time_spent = time_spent.sort_values('total_duration', ascending=False)
        mean_time = time_spent['total_duration'].mean()
        std_time = time_spent['total_duration'].std()
        threshold = mean_time + 1.5 * std_time
        heavy_contacts = time_spent[time_spent['total_duration'] > threshold]
        return {
            "analysis": "Time Spent per Contact",
            "data": time_spent.head(20).to_dict(orient='records'),
            "anomalies": heavy_contacts.to_dict(orient='records'),
            "insight": "Contacts consuming disproportionate total time may represent strong relationships or suspiciously long conversations."
        }

    def rapid_calls():
        df_sorted = df.sort_values(['number', 'datetime'])
        rapid_events = []
        for number, group in df_sorted.groupby('number'):
            if len(group) < 2:
                continue
            prev_time = None
            burst = []
            for _, row in group.iterrows():
                current = row['datetime']
                if prev_time and (current - prev_time).total_seconds() <= 600:
                    if not burst:
                        burst = [prev_time.strftime('%Y-%m-%d %H:%M:%S'), current.strftime('%Y-%m-%d %H:%M:%S')]
                    else:
                        burst.append(current.strftime('%Y-%m-%d %H:%M:%S'))
                else:
                    if len(burst) >= 2:
                        rapid_events.append({"number": number, "timestamps": burst})
                    burst = []
                prev_time = current
            if len(burst) >= 2:
                rapid_events.append({"number": number, "timestamps": burst})
        return {
            "analysis": "Rapid Calls",
            "data": rapid_events[:20],
            "anomalies": rapid_events,
            "insight": "Multiple calls to the same number within short time windows may indicate urgency or coordination."
        }

    def new_contacts():
        df_sorted = df.sort_values('datetime')
        first_seen = {}
        new_contacts_by_date = []
        for _, row in df_sorted.iterrows():
            num = row['number']
            date = row['datetime'].date()
            if num not in first_seen:
                first_seen[num] = date
                new_contacts_by_date.append({"date": date.isoformat(), "number": num})
        from collections import Counter
        date_counts = Counter([d['date'] for d in new_contacts_by_date])
        counts_list = list(date_counts.values())
        if counts_list:
            mean_new = np.mean(counts_list)
            std_new = np.std(counts_list)
            threshold = mean_new + 1.5 * std_new
            surge_dates = [{"date": date, "new_contacts": count} for date, count in date_counts.items() if count > threshold]
        else:
            surge_dates = []
        return {
            "analysis": "New Contacts",
            "data": [{"date": d, "new_contacts": c} for d, c in date_counts.most_common(20)],
            "anomalies": surge_dates,
            "insight": "Sudden increase in new numbers may indicate network expansion or a coordinated outreach campaign."
        }

    handlers = {
        "top_contacts": top_contacts,
        "daily_activity": daily_activity,
        "hourly_pattern": hourly_pattern,
        "call_length": call_length,
        "time_spent": time_spent,
        "rapid_calls": rapid_calls,
        "new_contacts": new_contacts,
    }
    if analysis_type not in handlers:
        return jsonify({"error": f"Unknown analysis_type. Choose from {list(handlers.keys())}"}), 400
    result = handlers[analysis_type]()
    return jsonify(result)
@app.route('/api/records')
def get_records():
    case_id = request.args.get('case_id')
    page = int(request.args.get('page', 1))
    page_size = int(request.args.get('page_size', 50))
    if not case_id:
        return jsonify({"error": "case_id required"}), 400
    filepath, mapping = get_case_data(case_id)
    if not filepath:
        return jsonify({"error": "No CDR file for this case"}), 400
    try:
        df = get_cdr_df(filepath, mapping)
        total = len(df)
        df_sorted = df.sort_values('datetime', ascending=False)
        start = (page - 1) * page_size
        end = start + page_size
        records_page = df_sorted.iloc[start:end]
        records_list = []
        for _, row in records_page.iterrows():
            records_list.append({
                "id": row.name,
                "called_number": row['number'],
                "call_datetime": row['datetime'].strftime('%Y-%m-%d %H:%M:%S'),
                "duration_seconds": int(row['duration']),
                "call_type": row['type']
            })
        return jsonify({
            "total": total,
            "page": page,
            "pages": (total + page_size - 1) // page_size,
            "records": records_list
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500
# ================= SAMPLE DATA DOWNLOAD =================

@app.route('/download/sample-cdr')
def download_sample_cdr():
    return send_from_directory(
        'sample_data',
        'sample_cdr.csv',
        as_attachment=True
    )

@app.route('/download/sample-location')
def download_sample_location():
    return send_from_directory(
        'sample_data',
        'sample_locations.xlsx',
        as_attachment=True
    )
# ======================================================================
if __name__ == '__main__':
    port=int(os.environ.get("PORT",10000))
    app.run(host="0.0.0.0",port=port)
