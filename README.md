# SEQ Weather Warning Processor

A Streamlit Community Cloud app that combines the supplied `1.py`, `2.py`, and `3.py` processing steps.

## Usage
1. Prepare a ZIP with multiple `.seq` files (subfolders are supported).
2. Upload the ZIP and click **Process ZIP**.
3. Download **daily_warning_hours.csv** (columns: `Date`, `7-7H`, `24H`).
4. Optionally download `warnings_summary.csv` and ZIP of per-SEQ converted CSV files.

No Google Drive mounting or single-SEQ upload is required. Input files are processed temporarily and not committed to GitHub.

## Deploy
1. Create a GitHub repo and upload `app.py`, `requirements.txt`, `README.md`, `.gitignore` (all in the repository root).
2. Visit https://share.streamlit.io and create a new app from your GitHub repository.
3. Set the entrypoint/main file path to `app.py`, and deploy.

## Run locally
```bash
python -m pip install -r requirements.txt
streamlit run app.py
```

## Preserved processing rules
- SEQ: parse `Field=val1|val2|...`, ignore `Warn Num=` tokens, pad missing values, normalize time columns.
- Summary: keep records only if the first `shtswarn` entry is `1`; trim trailing invalid `Warn Type` rows; sort revisions by issue date/time; cancellations override end/extension values; shift end date +1 day if end precedes start.
- Daily: keep `warn_type == Warning`, union overlaps, calculate 07:00–19:00 and 24-hour windows, round *up* to six-minute increments; include zero-hour dates between earliest and latest WARNING dates.

## Notes
- ZIP may contain up to 5,000 SEQ files; max compressed size 200 MB and total SEQ uncompressed size 350 MB. Streamlit Community Cloud resource limits may require smaller batches.
- The app does not automatically connect to Google Drive or persist files across sessions.
- Malformed files are reported under **Excluded files / parsing errors**, while other valid files are still processed.
- Source filenames should end in `-<warning number>.seq` for correct warning-number extraction.
- Daily CSV is header-only if no eligible `Warning` rows exist, matching the original `3.py` behavior.
