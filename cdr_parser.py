"""
cdr_parser.py
Handles loading, column auto-detection, validation, and cleaning of CDR files.
Supports CSV and Excel. The 'type' column is OPTIONAL (defaults to 'call').
"""

import pandas as pd
import os
import re
from typing import Dict, Tuple, Optional

def load_cdr_file(filepath: str) -> pd.DataFrame:
    """Load CSV or Excel file into DataFrame."""
    if not os.path.exists(filepath):
        raise FileNotFoundError(f"File not found: {filepath}")
    ext = os.path.splitext(filepath)[1].lower()
    if ext == '.csv':
        # Try to detect delimiter
        try:
            df = pd.read_csv(filepath, encoding='utf-8')
        except UnicodeDecodeError:
            df = pd.read_csv(filepath, encoding='latin1')
        except Exception:
            df = pd.read_csv(filepath, sep=None, engine='python')
    elif ext in ('.xlsx', '.xls'):
        df = pd.read_excel(filepath, engine='openpyxl')
    else:
        raise ValueError(f"Unsupported file format: {ext}. Use CSV or XLSX.")
    return df

def auto_detect_columns(df: pd.DataFrame) -> Dict[str, str]:
    """Map standard fields to actual column names."""
    cols = {col.strip().lower(): col for col in df.columns}
    keywords = {
        'date': ['date', 'day', 'fecha', 'call_date', 'calldate'],
        'time': ['time', 'hora', 'hour', 'call_time', 'calltime'],
        'duration': ['duration', 'dur', 'length', 'seconds', 'sec', 'call_duration', 'talktime'],
        'number': ['number', 'phone', 'mobile', 'contact', 'msisdn', 'caller', 'recipient', 'called_number', 'b_number'],
        'type': ['type', 'call type', 'event', 'status', 'direction', 'kind', 'category'],
        'contact_name': ['name', 'contact name', 'person', 'contact']
    }
    mapping: Dict[str, str] = {}
    for std_field, word_list in keywords.items():
        for word in word_list:
            for norm_col, raw_col in cols.items():
                if word in norm_col:
                    mapping[std_field] = raw_col
                    break
            if std_field in mapping:
                break
    # Special handling for number if still missing
    if 'number' not in mapping:
        for kw in ['from', 'to', 'caller', 'recipient', 'msisdn']:
            for norm_col, raw_col in cols.items():
                if kw in norm_col:
                    mapping['number'] = raw_col
                    break
            if 'number' in mapping:
                break
    return mapping

def validate_required_columns(df: pd.DataFrame, mapping: Dict[str, str]) -> None:
    """Check that required columns exist. 'type' is optional."""
    required = ['date', 'time', 'number', 'duration']
    for field in required:
        if field not in mapping or mapping[field] not in df.columns:
            raise ValueError(
                f"Required column for '{field}' not found in file. "
                f"Please map it manually. Columns found: {list(df.columns)}"
            )

def clean_and_parse(df: pd.DataFrame, mapping: Dict[str, str]) -> pd.DataFrame:
    """Convert raw columns to datetime, numeric, and standardised strings."""
    clean_df = df.copy()
    
    # Combine date & time
    date_col = mapping['date']
    time_col = mapping['time']
    clean_df[date_col] = clean_df[date_col].astype(str).str.strip()
    clean_df[time_col] = clean_df[time_col].astype(str).str.strip()
    datetime_str = clean_df[date_col] + ' ' + clean_df[time_col]
    # Try multiple datetime formats
    for fmt in ["%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%d-%m-%Y %H:%M:%S", "%d-%m-%Y %H:%M", "%d/%m/%Y %H:%M:%S", "%m/%d/%Y %H:%M:%S"]:
        try:
            clean_df['datetime'] = pd.to_datetime(datetime_str, format=fmt, errors='coerce')
            if clean_df['datetime'].notna().any():
                break
        except:
            continue
    # Fallback to flexible parsing
    if 'datetime' not in clean_df or clean_df['datetime'].isna().all():
        clean_df['datetime'] = pd.to_datetime(datetime_str, errors='coerce', dayfirst=False)
    
    # Duration (numeric)
    duration_col = mapping['duration']
    clean_df['duration'] = pd.to_numeric(clean_df[duration_col], errors='coerce').fillna(0)
    
    # Number (clean to string)
    number_col = mapping['number']
    clean_df['number'] = clean_df[number_col].astype(str).str.strip()
    # Remove any non-numeric except '+'
    clean_df['number'] = clean_df['number'].apply(lambda x: re.sub(r'[^0-9+]', '', x))
    
    # Type (optional – default to 'call')
    if 'type' in mapping and mapping['type'] in df.columns:
        type_col = mapping['type']
        clean_df['type'] = clean_df[type_col].astype(str).str.strip().str.lower()
        # Map variations
        clean_df['type'] = clean_df['type'].apply(lambda x: 'sms' if 'sms' in x else ('call' if 'call' in x or 'voice' in x or 'outgoing' in x else x))
    else:
        clean_df['type'] = 'call'
    
    # Contact name (optional)
    if 'contact_name' in mapping and mapping['contact_name'] in df.columns:
        contact_col = mapping['contact_name']
        clean_df['contact_name'] = clean_df[contact_col].astype(str).str.strip()
    else:
        clean_df['contact_name'] = 'Unknown'
    
    # Drop rows with invalid datetime
    clean_df = clean_df.dropna(subset=['datetime'])
    return clean_df[['datetime', 'number', 'duration', 'type', 'contact_name']].copy()

def parse_cdr_file(filepath: str, manual_mapping: Optional[Dict[str, str]] = None) -> Tuple[pd.DataFrame, Dict[str, str]]:
    """
    Parse CDR file and return cleaned DataFrame with mapping.
    If manual_mapping provided, use it; otherwise auto-detect.
    """
    df = load_cdr_file(filepath)
    if manual_mapping:
        mapping = manual_mapping
    else:
        mapping = auto_detect_columns(df)
    validate_required_columns(df, mapping)
    cleaned_df = clean_and_parse(df, mapping)
    return cleaned_df, mapping
