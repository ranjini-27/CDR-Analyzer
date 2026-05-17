"""
advanced_profiler.py
Provides extra behavioural metrics from a CDR file.
"""

import pandas as pd
from cdr_parser import parse_cdr_file

def compute_advanced_metrics(filepath: str, mapping: dict) -> dict:
    """
    Return a dict of advanced analytics:
      - duration_distribution
      - daily_trend
      - unique_contacts_trend
      - night_activity_count / ratio
      - very_short_calls count
      - contact_scores (weighted interactions)
    """
    df, _ = parse_cdr_file(filepath, manual_mapping=mapping)
    if df is None or df.empty:
        return {}

    # 1. Duration distribution (voice calls only)
    bins = [0, 5, 30, 60, 300, 900, float('inf')]
    labels = ['<5s', '5-30s', '30-60s', '1-5min', '5-15min', '15min+']
    calls = df[df['type'].str.lower().str.contains('call|incoming|outgoing|missed')] if 'type' in df.columns else df
    dur_dist = pd.cut(calls['duration'], bins=bins, labels=labels, right=False).value_counts().reindex(labels, fill_value=0)
    dur_dist_data = [{"range": k, "count": int(v)} for k, v in dur_dist.items()]

    # 2. Daily trend
    df['date_only'] = df['datetime'].dt.date
    daily_events = df.groupby('date_only').size().reset_index(name='count')
    daily_events['date'] = daily_events['date_only'].astype(str)
    daily_trend = daily_events[['date', 'count']].to_dict(orient='records')

    # 3. Unique contacts per day
    unique_per_day = df.groupby('date_only')['number'].nunique().reset_index(name='unique_contacts')
    unique_per_day['date'] = unique_per_day['date_only'].astype(str)
    unique_contacts_trend = unique_per_day[['date', 'unique_contacts']].to_dict(orient='records')

    # 4. Night activity (22:00-05:59)
    night_mask = df['datetime'].dt.hour.isin([22, 23, 0, 1, 2, 3, 4, 5])
    night_count = int(night_mask.sum())
    total = len(df)
    night_ratio = round((night_count / total) * 100, 1) if total else 0

    # 5. Very short calls (<5s)
    very_short_calls = calls[calls['duration'] < 5]
    short_call_count = len(very_short_calls)

    # 6. Contact scores (weighted: calls=1, SMS=0.2)
    df['is_sms'] = df['type'].str.lower().str.contains('sms') if 'type' in df.columns else False
    df['weight'] = df['is_sms'].apply(lambda x: 0.2 if x else 1.0)
    scores = df.groupby('number').agg(
        total_weight=('weight', 'sum'),
        contact_name=('contact_name', 'first')
    ).reset_index()
    scores = scores.nlargest(20, 'total_weight').to_dict(orient='records')

    return {
        "duration_distribution": dur_dist_data,
        "daily_trend": daily_trend,
        "unique_contacts_trend": unique_contacts_trend,
        "night_activity_count": night_count,
        "night_activity_ratio": night_ratio,
        "very_short_calls": short_call_count,
        "contact_scores": scores
    }
