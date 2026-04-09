# Validex

Validex is a Reflex + SQLite validation app for demographic and biometric records.

## Features

- Landing page with validation-init modal
- Demographic manual-entry validation with field-level scoring
- CSV batch validation with dynamic header detection
- Levenshtein-based duplicate matching against stored demographic records
- Biometric upload flow for face and fingerprint samples
- OpenBQ CLI wrapper with local pre-validation before analysis

## Run

```powershell
.\.venv\Scripts\python -m pip install -r requirements.txt
reflex run
```

## Notes

- Demographic results are persisted to `validex.db`.
- Biometric uploads are stored in Reflex's upload directory and analyzed through the `openbq` CLI.
- Full biometric scoring requires the `openbq` package and Docker to be available on the machine.
