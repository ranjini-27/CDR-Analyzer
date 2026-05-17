"""
profiler.py
Takes a cleaned CDR DataFrame and computes behavioural metrics.
"""

import pandas as pd
from typing import Dict, List, Optional

class Profiler:
    def __init__(self, df: pd.DataFrame):
        required_cols = {'datetime', 'number', 'duration', 'type'}
        if not required_cols.issubset(df.columns):
            missing = required_cols - set(df.columns)
            raise ValueError(f"DataFrame missing required columns: {missing}")

        self.df = df.copy()
        self.is_sms = self.df['type'].str.lower().str.contains('sms')
        self.is_call = ~self.is_sms

        self._total_calls = None
        self._total_sms = None
        self._avg_call_duration = None
        self._top_contacts = None
        self._top_contacts_detailed = None
        self._hourly_activity = None
        self._daily_activity = None
        self._heatmap_data = None

        self._compute()

    def _compute(self):
        # totals
        self._total_calls = int(self.is_call.sum())
        self._total_sms = int(self.is_sms.sum())

        # avg call duration
        call_durations = self.df.loc[self.is_call, 'duration']
        self._avg_call_duration = call_durations.mean() if len(call_durations) > 0 else 0.0

        # top contacts (simple)
        contact_group = self.df.groupby('number').agg(
            count=('duration', 'size'),
            total_duration=('duration', 'sum'),
            contact_name=('contact_name', 'first')
        )
        self._top_contacts = contact_group.nlargest(10, 'count').reset_index()
        self._top_contacts = self._top_contacts[['number', 'contact_name', 'count', 'total_duration']]

        # top contacts detailed (new)
        self._top_contacts_detailed = self._compute_detailed_contacts()

        # hourly activity
        self._hourly_activity = self.df['datetime'].dt.hour.value_counts().reindex(
            range(24), fill_value=0
        ).sort_index()

        # daily activity
        self._daily_activity = self.df['datetime'].dt.dayofweek.value_counts().reindex(
            range(7), fill_value=0
        ).sort_index()
        day_names = ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday']
        self._daily_activity.index = [day_names[d] for d in self._daily_activity.index]

        # heatmap
        self._heatmap_data = self._compute_heatmap()

    def _compute_detailed_contacts(self) -> pd.DataFrame:
        """Return top 10 contacts with breakdown by call type."""
        df = self.df.copy()

        # Classify each row
        def classify(row):
            t = row['type'].lower()
            if 'sms' in t:
                return 'sms'
            if 'missed' in t:
                return 'missed'
            if 'incoming' in t:
                return 'incoming'
            return 'outgoing'

        df['call_type'] = df.apply(classify, axis=1)

        grouped = df.groupby('number').agg(
            contact_name=('contact_name', 'first'),
            total_events=('duration', 'size'),
            total_duration=('duration', 'sum'),
            calls_in=('call_type', lambda x: (x == 'incoming').sum()),
            calls_out=('call_type', lambda x: (x == 'outgoing').sum()),
            missed=('call_type', lambda x: (x == 'missed').sum()),
            sms=('call_type', lambda x: (x == 'sms').sum()),
        ).reset_index()

        return grouped.nlargest(10, 'total_events')

    def _compute_heatmap(self) -> pd.DataFrame:
        dow = self.df['datetime'].dt.dayofweek  # Monday=0
        hour = self.df['datetime'].dt.hour
        ct = pd.crosstab(dow, hour, dropna=False)
        ct = ct.reindex(range(7), fill_value=0)
        ct = ct.reindex(columns=range(24), fill_value=0)
        day_names = ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday']
        ct.index = [day_names[d] for d in ct.index]
        return ct

    @property
    def total_calls(self) -> int:
        return self._total_calls

    @property
    def total_sms(self) -> int:
        return self._total_sms

    @property
    def avg_call_duration(self) -> float:
        return self._avg_call_duration

    @property
    def top_contacts(self) -> pd.DataFrame:
        return self._top_contacts

    @property
    def top_contacts_detailed(self) -> pd.DataFrame:
        return self._top_contacts_detailed

    @property
    def hourly_activity(self) -> pd.Series:
        return self._hourly_activity

    @property
    def daily_activity(self) -> pd.Series:
        return self._daily_activity

    @property
    def heatmap_data(self) -> pd.DataFrame:
        return self._heatmap_data

    def get_summary(self) -> Dict:
        return {
            "total_calls": self.total_calls,
            "total_sms": self.total_sms,
            "avg_call_duration_sec": round(self.avg_call_duration, 1),
            "top_contacts": self.top_contacts.to_dict(orient='records'),
            "top_contacts_detailed": self.top_contacts_detailed.to_dict(orient='records'),
            "hourly_activity": self.hourly_activity.to_dict(),
            "daily_activity": self.daily_activity.to_dict(),
            "heatmap": {
                "days": self.heatmap_data.index.tolist(),
                "hours": [str(h) for h in self.heatmap_data.columns.tolist()],
                "data": self.heatmap_data.values.tolist()
            }
        }
