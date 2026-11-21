"""Streamlit application combining the supplied 1.py, 2.py and 3.py.

Only a ZIP of SEQ files is accepted. No Google Drive or Colab required.
Original parsing, cancellation and interval-calculation logic is retained.
"""
from __future__ import annotations

import csv
import io
import math
import re
import tempfile
import zipfile
from contextlib import redirect_stdout
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import pandas as pd
import streamlit as st

EXTEND_COLS = ["Extend6", "Extend5", "Extend4", "Extend3", "Extend2", "Extend1"]
SUMMARY_COLS = ["warn_num", "warn_type", "start_date", "start_time", "end_date", "end_time"]
DAILY_COLS = ["Date", "7-7H", "24H"]
MAX_INPUT_BYTES = 200 * 1024 * 1024
MAX_UNCOMPRESSED_BYTES = 350 * 1024 * 1024
MAX_SEQ_FILES = 5000

# ---- Original 1.py Option B: parsing ----


def parse_line_to_header_and_values(line: str) -> Tuple[Optional[str], List[str]]:
    """
    Parse a single .seq line.

    Example:
        'Warn Type=Advisory|Advisory|Advisory'

    Returns:
        header = 'Warn Type'
        values = ['Advisory', 'Advisory', 'Advisory']

    Any token containing 'Warn Num=' is ignored completely.
    """
    # Split by '|' and remove empty pieces
    pieces = [p.strip() for p in line.split("|") if p.strip()]

    # Drop any 'Warn Num=' tokens entirely
    pieces = [p for p in pieces if "Warn Num=" not in p]

    if not pieces:
        return None, []

    # First piece should contain "Header=FirstValue"
    first = pieces[0]
    if "=" not in first:
        return None, []

    header, first_val = first.split("=", 1)
    header = header.strip()
    values: List[str] = [first_val.strip()]

    # Remaining pieces are additional values for that header
    for p in pieces[1:]:
        values.append(p.strip())

    return header, values

def format_time_value(header: str, value: str) -> str:
    """
    If the *header* suggests this column is a time (contains 'time' or 'extend'),
    and the *value* is numeric-ish (804, 804.0, '804Z', '08:04'), then:

        -> return a zero-padded 4-digit string, e.g. '0804'.

    Otherwise, return the original value unchanged.
    """
    h = header.lower()
    if "time" not in h and "extend" not in h:
        return value

    # Normalise the raw value to digits only
    s = str(value).strip()

    # Strip trailing '.0' if present (e.g. '804.0' -> '804')
    if s.endswith(".0"):
        s = s[:-2]

    # Keep only digits
    digits = re.sub(r"\D", "", s)

    if 1 <= len(digits) <= 4:
        return "'" + digits.zfill(4)

    return value

def read_and_clean_seq(path: Path) -> List[List[str]]:
    """
    Read one .seq file and return a 2D table for CSV.

    Steps:
      - For each line: parse -> (header, [values...])
      - Collect:
            headers     = [h1, h2, ...]
            values_rows = [
                [h1_val1, h1_val2, ...],
                [h2_val1, h2_val2, ...],
                ...
            ]
      - Pad value rows so they are equal length
      - Transpose so we get rows = records (time steps)
      - Format time columns (Issue Time, Onset Time, etc.) to HHMM (4 digits)
      - Return:
            [
              [header1, header2, ...],
              [row1_col1, row1_col2, ...],
              [row2_col1, row2_col2, ...],
              ...
            ]
    """
    headers: List[str] = []
    values_rows: List[List[str]] = []
    max_len = 0

    with open(path, "r", encoding="utf-8", errors="ignore") as f:
        for raw_line in f:
            line = raw_line.strip()
            if not line:
                continue

            header, values = parse_line_to_header_and_values(line)
            if header is None:
                continue

            headers.append(header)
            values_rows.append(values)
            max_len = max(max_len, len(values))

    # Pad all value rows to the same length
    for row in values_rows:
        if len(row) < max_len:
            row += [""] * (max_len - len(row))

    # Transpose: values_rows is per-header; we want per-record rows.
    transposed_values: List[List[str]] = [list(r) for r in zip(*values_rows)]

    # Apply time formatting column by column
    for col_index, header in enumerate(headers):
        for row in transposed_values:
            if row[col_index] != "":
                row[col_index] = format_time_value(header, row[col_index])

    # First row = headers; remaining rows = data
    table: List[List[str]] = [headers] + transposed_values
    return table

def parse_line_to_header_and_values(line: str) -> Tuple[Optional[str], List[str]]:
    """
    Parse a single .seq line into a header + list of values.

    Example input:
        'Warn Type=Advisory|Advisory|Advisory'

    Returns:
        header = 'Warn Type'
        values = ['Advisory', 'Advisory', 'Advisory']

    Behaviour:
      - Split the line by '|' to get pieces.
      - Strip whitespace from each piece.
      - Ignore empty pieces.
      - Ignore any piece containing 'Warn Num=' completely.
      - The FIRST remaining piece must contain "Header=FirstValue".
      - All remaining pieces are additional values for that header.
      - If no usable header/value can be found, returns (None, []).
    """
    # Split by '|' and trim whitespace; discard empty pieces
    pieces = [p.strip() for p in line.split("|") if p.strip()]

    # Drop any 'Warn Num=' tokens entirely
    pieces = [p for p in pieces if "Warn Num=" not in p]

    if not pieces:
        return None, []

    # First piece should be of the form "Header=FirstValue"
    first = pieces[0]
    if "=" not in first:
        # Cannot determine a header from this line
        return None, []

    header, first_val = first.split("=", 1)
    header = header.strip()
    values: List[str] = [first_val.strip()]

    # Remaining pieces are additional values for this same header
    for p in pieces[1:]:
        values.append(p.strip())

    return header, values

def format_time_value(header: str, value: str) -> str:
    """
    Normalise time-like values.

    If the *header* suggests this column is a time field
    (its lowercase name contains 'time' or 'extend') and
    the *value* looks numeric-ish (804, 804.0, '804Z', '08:04'):

        - Keep only digits:  '804.0' -> '804', '08:04' -> '0804'
        - If 1–4 digits remain, pad to 4 digits: '804' -> '0804', '59' -> '0059'
        - Prefix with "'" so that spreadsheet programs (Sheets, Excel, Numbers)
          treat it as TEXT and preserve the leading zero.

    Otherwise, return the original value unchanged.
    """
    h = header.lower()

    # Only process headers that clearly refer to time-like fields
    # e.g. "Issue Time", "Onset Time", "ExtendTime1", etc.
    if "time" not in h and "extend" not in h:
        return value

    # Convert value to string and normalise
    s = str(value).strip()

    # If it looks like a float ending with .0 (e.g. '804.0'), strip the .0
    if s.endswith(".0"):
        s = s[:-2]

    # Keep only digits (strip out ':', 'Z', spaces, etc.)
    digits = re.sub(r"\D", "", s)

    # If we have 1–4 digits, treat it as a HHMM-style time code
    if 1 <= len(digits) <= 4:
        padded = digits.zfill(4)   # 804 -> 0804, 59 -> 0059
        # Leading apostrophe ensures spreadsheets keep leading zeroes
        return "'" + padded

    # If it doesn't look like a simple numeric time, leave it as is
    return value

def read_and_clean_seq(path: Path) -> List[List[str]]:
    """
    Read one .seq file and return a 2D table of strings ready for CSV.

    Steps:
      1. For each non-empty line:
           - parse_line_to_header_and_values(line)
           - collect 'header' into `headers`
           - collect 'values' (list) into `values_rows`
      2. After all lines:
           - all header names are in `headers` (one per line)
           - `values_rows` is a list of lists:
                [
                  [h1_val1, h1_val2, h1_val3, ...],
                  [h2_val1, h2_val2, h2_val3, ...],
                  ...
                ]
      3. Pad each list in `values_rows` to the length of the longest one.
      4. Transpose the table so that:
           - each row = one time step / record
           - each column = a header from `headers`
      5. For each time-like column (header contains 'time' or 'extend'),
         normalise values using format_time_value().
      6. Prepend the headers as the first row.

    Returns:
        table = [
          [header1, header2, header3, ...],
          [row1_col1, row1_col2, row1_col3, ...],
          [row2_col1, row2_col2, row2_col3, ...],
          ...
        ]
    """
    headers: List[str] = []
    values_rows: List[List[str]] = []
    max_len = 0  # track the maximum length of any values list

    # --- pass 1: parse each line into header + values list ---
    with open(path, "r", encoding="utf-8", errors="ignore") as f:
        for raw_line in f:
            line = raw_line.strip()
            if not line:
                # Skip blank lines
                continue

            header, values = parse_line_to_header_and_values(line)
            if header is None:
                # Line was not in "Header=..." format, skip it
                continue

            headers.append(header)
            values_rows.append(values)
            max_len = max(max_len, len(values))

    # --- pass 2: pad value lists so they all have the same length ---
    for row in values_rows:
        if len(row) < max_len:
            row += [""] * (max_len - len(row))

    # --- pass 3: transpose so rows = records, columns = headers ---
    # values_rows is currently organised by header:
    #   [ [h1_vals...],
    #     [h2_vals...],
    #     ... ]
    # zip(*values_rows) flips it to:
    #   [ [row0_h1, row0_h2, ...],
    #     [row1_h1, row1_h2, ...],
    #     ... ]
    transposed_values: List[List[str]] = [list(r) for r in zip(*values_rows)]

    # --- pass 4: apply time formatting column by column ---
    for col_index, header in enumerate(headers):
        for row in transposed_values:
            if row[col_index] != "":
                row[col_index] = format_time_value(header, row[col_index])

    # Prepend header row
    table: List[List[str]] = [headers] + transposed_values
    return table


# ---- Original 2.py: warning consolidation ----


def _norm_col(c: str) -> str:
    return re.sub(r"\s+", "", c.strip().lower())

def _find_col(df: pd.DataFrame, target: str) -> Optional[str]:
    t = _norm_col(target)
    for c in df.columns:
        if _norm_col(c) == t:
            return c
    return None

def _clean_val(x) -> str:
    if pd.isna(x):
        return "-"
    s = str(x).strip()
    return s if s else "-"

def _parse_date(d: str) -> Optional[datetime]:
    d = _clean_val(d)
    if d == "-" or d.lower() == "nan":
        return None
    try:
        return datetime.strptime(d, "%d-%m-%Y")
    except ValueError:
        for fmt in ("%d/%m/%Y", "%Y-%m-%d"):
            try:
                return datetime.strptime(d, fmt)
            except ValueError:
                pass
    return None

def _parse_hhmm(t: str) -> Optional[Tuple[int, int]]:
    t = _clean_val(t)
    if t == "-" or t.lower() == "nan":
        return None
    t = re.sub(r"\D", "", t)
    if len(t) == 0 or len(t) > 4:
        return None
    if t == "2400":
        t = "0000"
    t = t.zfill(4)
    hh = int(t[:2])
    mm = int(t[2:])
    if not (0 <= hh <= 23 and 0 <= mm <= 59):
        return None
    return hh, mm

def _parse_issue_dt(issue_date: str, issue_time: str) -> Optional[datetime]:
    d = _parse_date(issue_date)
    hm = _parse_hhmm(issue_time)
    if d is None or hm is None:
        return None
    return d.replace(hour=hm[0], minute=hm[1], second=0, microsecond=0)

def _format_date(dt: datetime) -> str:
    return dt.strftime("%d/%m/%Y")

def _format_time_hhmm(t: str) -> str:
    hm = _parse_hhmm(t)
    if hm is None:
        return _clean_val(t)
    return f"{hm[0]:02d}{hm[1]:02d}"

def _infer_warn_num(df: pd.DataFrame, filename: str) -> str:
    return filename.split('-')[-1].split('.')[0]

def _pick_warn_type(df: pd.DataFrame) -> str:
    col = _find_col(df, "Warn Type")
    if not col:
        return "-"
    for v in df[col].tolist():
        v = _clean_val(v)
        if v != "-":
            return v
    return "-"

def _pick_final_end_time(last_row: pd.Series, df_cols: List[str]) -> str:
    for ext in EXTEND_COLS:
        c = next((col for col in df_cols if _norm_col(col) == _norm_col(ext)), None)
        if c:
            v = _clean_val(last_row[c])
            if v != "-" and v.lower() != "nan":
                return _format_time_hhmm(v)

    end_col = _find_col(pd.DataFrame(columns=df_cols), "End Time")
    if end_col:
        return _format_time_hhmm(_clean_val(last_row[end_col]))

    return "-"

def _combine_date_time(date_dt: datetime, time_hhmm: str) -> Optional[datetime]:
    hm = _parse_hhmm(time_hhmm)
    if date_dt is None or hm is None:
        return None
    return date_dt.replace(hour=hm[0], minute=hm[1], second=0, microsecond=0)

def fix_end_before_start(
    warn_num: str,
    source_name: str,
    start_date_dt: datetime,
    start_time_str: str,
    end_date_dt: datetime,
    end_time_str: str,
) -> Tuple[datetime, str]:
    start_dt = _combine_date_time(start_date_dt, start_time_str)
    end_dt = _combine_date_time(end_date_dt, end_time_str)

    if start_dt is None or end_dt is None:
        return end_date_dt, end_time_str

    if end_dt < start_dt:
        old_end_date = _format_date(end_date_dt)
        new_end_date_dt = end_date_dt + timedelta(days=1)
        new_end_date = _format_date(new_end_date_dt)

        print(
            f"[AUTO-FIX] warn_num={warn_num} file={source_name} "
            f"end<start: start={_format_date(start_date_dt)} {start_time_str} "
            f"end={old_end_date} {end_time_str} -> end_date shifted to {new_end_date} (time kept {end_time_str})"
        )
        return new_end_date_dt, end_time_str

    return end_date_dt, end_time_str

def _detect_cancellation(
    df_sorted: pd.DataFrame,
    warn_num: str,
    source_name: str,
    issue_date_col: str,
    issue_time_col: Optional[str],
) -> Optional[Tuple[datetime, str]]:
    """
    If there is an Issue Type == 'Can' row:
      end_date = that row's Issue Date
      end_time = that row's Cancel column (HHMM)
    Returns (end_date_dt, end_time_str) if cancellation exists, else None.
    """
    issue_type_col = _find_col(df_sorted, "Issue Type")
    cancel_col = _find_col(df_sorted, "Cancel")

    if issue_type_col is None or cancel_col is None:
        return None

    # Find cancellation rows
    it = df_sorted[issue_type_col].astype(str).str.strip().str.lower()
    can_mask = it == "can"
    if not can_mask.any():
        return None

    # If multiple, take the last one in sorted order
    can_row = df_sorted[can_mask].iloc[-1]

    can_issue_date_dt = _parse_date(_clean_val(can_row[issue_date_col]))
    can_time_str = _format_time_hhmm(_clean_val(can_row[cancel_col]))

    if can_issue_date_dt is None or can_time_str == "-" or can_time_str.lower() == "nan":
        # If malformed cancellation row, ignore (or you can raise)
        return None

    # Print cancellation notice
    # Include issue time too if available, since it's sometimes useful debugging
    issue_time_info = ""
    if issue_time_col is not None and issue_time_col in df_sorted.columns:
        issue_time_info = f", issued_at={_format_time_hhmm(_clean_val(can_row[issue_time_col]))}"

    print(
        f"[CANCEL] warn_num={warn_num} file={source_name} "
        f"cancel_at={_format_date(can_issue_date_dt)} {can_time_str}{issue_time_info}"
    )

    return can_issue_date_dt, can_time_str

def process_warn_csv(df: pd.DataFrame, source_name: str) -> dict:
    issue_date_col = _find_col(df, "Issue Date")
    issue_time_col = _find_col(df, "Issue Time")
    onset_col = _find_col(df, "Onset Time")

    if issue_date_col is None or onset_col is None:
        raise ValueError(
            f"{source_name}: Missing required columns. Need at least 'Issue Date' and 'Onset Time'. "
            f"Found columns: {list(df.columns)}"
        )

    warn_num = _infer_warn_num(df, source_name)
    warn_type = _pick_warn_type(df)

    # Sort revisions chronologically if possible
    if issue_time_col is not None:
        issue_dt = df.apply(
            lambda r: _parse_issue_dt(_clean_val(r[issue_date_col]), _clean_val(r[issue_time_col])),
            axis=1,
        )
        df2 = df.copy()
        df2["_issue_dt"] = issue_dt
        if df2["_issue_dt"].notna().any():
            df2 = df2.sort_values(by="_issue_dt", kind="stable")
        df_sorted = df2.drop(columns=["_issue_dt"])
    else:
        df_sorted = df

    first = df_sorted.iloc[0]
    last = df_sorted.iloc[-1]

    # Start = first issue date + onset time
    start_date_dt = _parse_date(_clean_val(first[issue_date_col]))
    start_time_str = _format_time_hhmm(_clean_val(first[onset_col]))
    if start_date_dt is None:
        raise ValueError(f"{source_name}: Could not parse start Issue Date: {first[issue_date_col]}")

    # ---- NEW: Cancellation overrides normal end time ----
    cancel_end = _detect_cancellation(
        df_sorted=df_sorted,
        warn_num=warn_num,
        source_name=source_name,
        issue_date_col=issue_date_col,
        issue_time_col=issue_time_col,
    )

    if cancel_end is not None:
        end_date_dt, end_time_str = cancel_end
    else:
        # Normal logic: end date = last issue date, end time = extend/end time
        end_date_dt = _parse_date(_clean_val(last[issue_date_col]))
        if end_date_dt is None:
            raise ValueError(f"{source_name}: Could not parse end Issue Date: {last[issue_date_col]}")
        end_time_str = _pick_final_end_time(last, list(df_sorted.columns))

    # Midnight-crossing fix (applies to both cancelled and non-cancelled)
    end_date_dt, end_time_str = fix_end_before_start(
        warn_num=warn_num,
        source_name=source_name,
        start_date_dt=start_date_dt,
        start_time_str=start_time_str,
        end_date_dt=end_date_dt,
        end_time_str=end_time_str,
    )

    return {
        "warn_num": str(warn_num),
        "warn_type": str(warn_type),
        "start_date": _format_date(start_date_dt),
        "start_time": start_time_str,
        "end_date": _format_date(end_date_dt),
        "end_time": end_time_str,
    }

def trim_to_warn_type(df: pd.DataFrame, source_name: str) -> pd.DataFrame:
    warn_type_col = _find_col(df, "Warn Type")
    if warn_type_col is None:
        return df

    s = df[warn_type_col].astype(str).str.strip()
    valid = (~df[warn_type_col].isna()) & (s != "") & (s != "-") & (s.str.lower() != "nan")

    if not valid.any():
        return df.iloc[0:0]

    last_valid_idx = df.index[valid].max()
    return df.loc[:last_valid_idx].copy()

def should_include_by_shtswarn(df: pd.DataFrame, source_name: str) -> bool:
    col = _find_col(df, "shtswarn")
    if col is None:
        return False

    v = df[col].iloc[0]
    if pd.isna(v):
        return False

    try:
        return float(str(v).strip()) == 1.0
    except ValueError:
        return str(v).strip() == "1"


# ---- Original 3.py: daily durations ----


def parse_date(date_str: str) -> date:
    s = str(date_str).strip()
    for fmt in ("%d/%m/%Y", "%d-%m-%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            pass
    raise ValueError(f"Unrecognized date format: {date_str!r}")

def parse_hhmm(value) -> Tuple[int, int]:
    """
    Accepts HHMM with possible leading-zero truncation:
      "0820", "820", "0015", "15", "8:20", "00:15"
    """
    if value is None or (isinstance(value, float) and pd.isna(value)):
        raise ValueError("Time is missing/NaN")

    s = str(value).strip()
    if s == "" or s == "-" or s.lower() == "nan":
        raise ValueError(f"Time is missing: {value!r}")

    digits = "".join(ch for ch in s if ch.isdigit())
    if not digits:
        raise ValueError(f"Unrecognized time format: {value!r}")

    if len(digits) > 4:
        digits = digits[-4:]  # defensive

    digits = digits.zfill(4)  # key fix: 15 -> 0015, 820 -> 0820

    hh = int(digits[:2])
    mm = int(digits[2:])
    if not (0 <= hh <= 23 and 0 <= mm <= 59):
        raise ValueError(f"Invalid HHMM after normalization: {value!r} -> {digits}")
    return hh, mm

def combine_dt(d, t) -> datetime:
    dd = parse_date(d)
    hh, mm = parse_hhmm(t)
    return datetime(dd.year, dd.month, dd.day, hh, mm)

def clip_interval(a_start: datetime, a_end: datetime, b_start: datetime, b_end: datetime) -> Optional[Tuple[datetime, datetime]]:
    start = max(a_start, b_start)
    end = min(a_end, b_end)
    if start >= end:
        return None
    return start, end

def merge_intervals(intervals: List[Tuple[datetime, datetime]]) -> List[Tuple[datetime, datetime]]:
    """Merge overlapping/adjacent intervals so overlaps aren't double-counted."""
    if not intervals:
        return []
    intervals = sorted(intervals, key=lambda x: x[0])
    merged = [intervals[0]]
    for s, e in intervals[1:]:
        last_s, last_e = merged[-1]
        if s <= last_e:  # overlap or touch
            merged[-1] = (last_s, max(last_e, e))
        else:
            merged.append((s, e))
    return merged

def total_minutes(intervals: List[Tuple[datetime, datetime]]) -> int:
    return int(sum((e - s).total_seconds() // 60 for s, e in intervals))

def round_up_to_0_1h_from_minutes(mins: int) -> float:
    """Round UP to nearest 0.1 hour where 0.1h = 6 minutes."""
    if mins <= 0:
        return 0.0
    return math.ceil(mins / 6.0) / 10.0

def daterange(start_d: date, end_d: date):
    d = start_d
    while d <= end_d:
        yield d
        d += timedelta(days=1)

# ---- Streamlit orchestration: ZIP -> per-file CSVs -> summary -> daily CSV ----

def to_csv_bytes(df: pd.DataFrame) -> bytes:
    return df.to_csv(index=False).encode("utf-8-sig")


def _numeric_or_inf(x: str) -> float:
    try:
        return float(x)
    except (ValueError, TypeError):
        return float("inf")


def build_daily_hours(summary: pd.DataFrame, log: list[str]) -> pd.DataFrame:
    """Same business logic as original 3.py, adapted to an in-memory DataFrame."""
    df = summary.copy()
    required = ["warn_type", "start_date", "start_time", "end_date", "end_time"]
    missing = [c for c in required if c not in df.columns]
    if missing:
        raise ValueError(f"Missing required columns: {missing}")
    df = df[df["warn_type"].astype(str).str.strip().str.lower() == "warning"].copy()
    if df.empty:
        return pd.DataFrame(columns=DAILY_COLS)

    # Match original 3.py: date range comes from WARNING rows only.
    min_day = df["start_date"].map(parse_date).min()
    max_day = df["end_date"].map(parse_date).max()
    day_24: Dict[date, List[Tuple[datetime, datetime]]] = {}
    day_77: Dict[date, List[Tuple[datetime, datetime]]] = {}
    for _, r in df.iterrows():
        try:
            start_dt = combine_dt(r["start_date"], r["start_time"])
            end_dt = combine_dt(r["end_date"], r["end_time"])
        except ValueError as exc:
            log.append(f"[SKIP ROW] {exc}; row={r.to_dict()}")
            continue
        if end_dt <= start_dt:
            continue
        day = start_dt.date()
        while day <= end_dt.date():
            day_start = datetime(day.year, day.month, day.day)
            day_end = day_start + timedelta(days=1)
            c24 = clip_interval(start_dt, end_dt, day_start, day_end)
            if c24:
                day_24.setdefault(day, []).append(c24)
            window_start = day_start + timedelta(hours=7)
            window_end = day_start + timedelta(hours=19)
            c77 = clip_interval(start_dt, end_dt, window_start, window_end)
            if c77:
                day_77.setdefault(day, []).append(c77)
            day += timedelta(days=1)

    rows = []
    for day in daterange(min_day, max_day):
        mins_24 = total_minutes(merge_intervals(day_24.get(day, [])))
        mins_77 = total_minutes(merge_intervals(day_77.get(day, [])))
        rows.append({
            "Date": day.strftime("%d/%m/%Y"),
            "7-7H": round_up_to_0_1h_from_minutes(mins_77),
            "24H": round_up_to_0_1h_from_minutes(mins_24),
        })
    return pd.DataFrame(rows, columns=DAILY_COLS)


def process_zip(uploaded_bytes: bytes) -> dict:
    """Process every .seq inside ZIP in memory, without extracting ZIP paths."""
    if len(uploaded_bytes) > MAX_INPUT_BYTES:
        raise ValueError("ZIP is larger than 200 MB; please split it into smaller uploads.")

    rows = []
    logs = []
    skipped = []
    processed = 0
    intermediate = io.BytesIO()
    try:
        source_zip = zipfile.ZipFile(io.BytesIO(uploaded_bytes))
    except zipfile.BadZipFile as exc:
        raise ValueError("The uploaded file is not a valid ZIP archive.") from exc

    with source_zip as archive:
        entries = [info for info in archive.infolist() if not info.is_dir() and info.filename.lower().endswith(".seq")]
        if not entries:
            raise ValueError("No .seq files found inside the ZIP (including subfolders).")
        if len(entries) > MAX_SEQ_FILES:
            raise ValueError(f"ZIP contains more than {MAX_SEQ_FILES:,} SEQ files; please split it.")
        if sum(info.file_size for info in entries) > MAX_UNCOMPRESSED_BYTES:
            raise ValueError("Uncompressed SEQ data exceeds 350 MB; please split the ZIP.")

        with zipfile.ZipFile(intermediate, mode="w", compression=zipfile.ZIP_DEFLATED) as csv_zip:
            for idx, info in enumerate(sorted(entries, key=lambda i: i.filename.casefold()), start=1):
                try:
                    raw = archive.read(info)
                    # Original 1.py reads UTF-8 with errors='ignore'. Temporary files
                    # allow us to run its unchanged read_and_clean_seq(Path) function.
                    with tempfile.TemporaryDirectory() as tmp:
                        seq_path = Path(tmp) / "input.seq"
                        seq_path.write_bytes(raw)
                        table = read_and_clean_seq(seq_path)
                    if len(table) < 2 or not table[0]:
                        raise ValueError("No usable records found")

                    # Preserve original 1.py CSV serialization and original 2.py
                    # pandas CSV-reading behavior (including inferred dtypes).
                    csv_text = io.StringIO(newline="")
                    csv.writer(csv_text).writerows(table)
                    csv_raw = csv_text.getvalue().encode("utf-8")
                    df = pd.read_csv(io.BytesIO(csv_raw))
                    # Retain folder hierarchy; prefix the numeric index to avoid
                    # collisions where a ZIP contains repeated names.
                    output_name = f"{idx:04d}_{Path(info.filename).stem}.csv"
                    csv_zip.writestr(output_name, csv_raw)
                    processed += 1
                    df = trim_to_warn_type(df, info.filename)
                    if df.empty or not should_include_by_shtswarn(df, info.filename):
                        skipped.append(f"{info.filename}: no eligible shtswarn=1 records")
                        continue
                    # Use basename for filename-derived warning number, not ZIP path.
                    with io.StringIO() as console_log, redirect_stdout(console_log):
                        row = process_warn_csv(df, Path(info.filename).name)
                        detail = console_log.getvalue().strip()
                    if detail:
                        logs.extend(detail.splitlines())
                    rows.append(row)
                except Exception as exc:
                    skipped.append(f"{info.filename}: {type(exc).__name__}: {exc}")

    if processed == 0:
        raise ValueError("None of the SEQ files could be parsed. Check the input format.")

    summary = pd.DataFrame(rows, columns=SUMMARY_COLS)
    if not summary.empty:
        summary = summary.sort_values(
            by="warn_num", key=lambda s: s.map(_numeric_or_inf), kind="stable"
        ).reset_index(drop=True)
    daily = build_daily_hours(summary, logs)
    return {
        "summary": summary,
        "daily": daily,
        "converted_zip": intermediate.getvalue(),
        "logs": logs,
        "skipped": skipped,
        "files_found": len(entries),
        "files_parsed": processed,
        "warnings_included": len(summary),
    }


def main() -> None:
    st.set_page_config(page_title="SEQ Weather Warning Processor", page_icon="🌦️", layout="wide")
    st.title("🌦️ SEQ Weather Warning Processor")
    st.write(
        "Upload a **ZIP containing multiple `.seq` files**. The app automatically "
        "runs all three processing stages and prepares your final daily warning-hours CSV."
    )
    uploaded = st.file_uploader("Upload ZIP of SEQ files", type=["zip"], accept_multiple_files=False)
    if uploaded is None:
        st.info("Only ZIP uploads are supported; individual SEQ uploads are not needed.")
        st.session_state.pop("result", None)
        st.session_state.pop("upload_key", None)
        return

    upload_key = (uploaded.name, uploaded.size, uploaded.file_id if hasattr(uploaded, "file_id") else None)
    if st.session_state.get("upload_key") != upload_key:
        st.session_state.pop("result", None)

    if st.button("Process ZIP", type="primary"):
        with st.spinner("Parsing SEQ files and calculating daily warning hours..."):
            try:
                result = process_zip(uploaded.getvalue())
            except Exception as exc:
                st.error(f"Processing failed: {exc}")
                return
        st.session_state["result"] = result
        st.session_state["upload_key"] = upload_key

    result = st.session_state.get("result")
    if result is None:
        return

    st.success("Processing complete")
    a, b, c = st.columns(3)
    a.metric("SEQ files parsed", f"{result['files_parsed']} / {result['files_found']}")
    b.metric("Eligible warnings", result["warnings_included"])
    c.metric("Daily rows", len(result["daily"]))

    st.subheader("Final CSV — daily warning hours")
    st.dataframe(result["daily"], use_container_width=True, hide_index=True)
    st.download_button(
        "⬇️ Download final CSV",
        data=to_csv_bytes(result["daily"]),
        file_name="daily_warning_hours.csv",
        mime="text/csv",
        type="primary",
    )

    with st.expander("Optional: inspect or download intermediate results"):
        st.write("Step 2 — warning summary")
        st.dataframe(result["summary"], use_container_width=True, hide_index=True)
        st.download_button(
            "Download warnings_summary.csv",
            data=to_csv_bytes(result["summary"]),
            file_name="warnings_summary.csv",
            mime="text/csv",
        )
        st.download_button(
            "Download ZIP of step 1 converted CSVs",
            data=result["converted_zip"],
            file_name="converted_seq_csvs.zip",
            mime="application/zip",
        )

    if result["logs"]:
        with st.expander(f"Cancellation / auto-fix log ({len(result['logs'])})"):
            st.code("\n".join(result["logs"]), language="text")
    if result["skipped"]:
        with st.expander(f"Excluded files / parsing errors ({len(result['skipped'])})"):
            st.code("\n".join(result["skipped"]), language="text")
    if result["warnings_included"] == 0:
        st.warning("No qualifying warnings were found. The final CSV contains headers only, matching the original scripts.")


if __name__ == "__main__":
    main()
