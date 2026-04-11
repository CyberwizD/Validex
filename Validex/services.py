from __future__ import annotations

import csv
import io
import re
import shutil
import subprocess
import tempfile
from datetime import date, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from PIL import Image

from .models import (
    BatchValidationRowResult,
    BatchValidationSummary,
    BiometricValidationRequest,
    BiometricValidationResult,
    DemographicValidationResult,
    DuplicateMatch,
    FIELD_WEIGHT,
    FieldValidationResult,
    ManualDemographicInput,
)


NAME_PATTERN = re.compile(r"^[A-Za-z][A-Za-z\s'\-]*[A-Za-z]$|^[A-Za-z]$")
EMAIL_PATTERN = re.compile(r"^[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}$")
PHONE_STRIP_PATTERN = re.compile(r"[()\-\s+]")
CSV_HEADER_ALIASES = {
    "first_name": {"firstname", "first name", "first_name"},
    "last_name": {"lastname", "last name", "last_name"},
    "date_of_birth": {"dob", "date of birth", "date_of_birth"},
    "age": {"age"},
    "phone": {"phone", "phone number", "phone_number"},
    "email": {"email", "email address", "email_address"},
}
FACE_EXTENSIONS = {".jpg", ".jpeg", ".jp2", ".bmp", ".png"}
FINGERPRINT_EXTENSIONS = {".wsq", ".png"}
BIOMETRIC_THRESHOLDS = {"accepted": 80, "review": 55}


def normalize_header(header: str) -> str:
    return header.strip().lower().replace("_", " ")


def map_csv_headers(headers: list[str]) -> dict[str, str]:
    mapped: dict[str, str] = {}
    for header in headers:
        cleaned = normalize_header(header)
        for canonical, aliases in CSV_HEADER_ALIASES.items():
            if cleaned in aliases:
                mapped[canonical] = header
    return mapped


def parse_supported_date(value: str) -> date | None:
    clean = value.strip()
    if not clean:
        return None
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%m/%d/%Y"):
        try:
            return datetime.strptime(clean, fmt).date()
        except ValueError:
            continue
    return None


def score_band(score: float) -> str:
    if score >= 90:
        return "Excellent"
    if score >= 70:
        return "Good"
    if score >= 50:
        return "Fair"
    return "Poor"


def build_summary(
    score: float,
    statuses: set[str],
    duplicate_match: DuplicateMatch | None,
) -> str:
    band = score_band(score)
    if duplicate_match:
        return (
            f"{band} validation quality with a potential duplicate match at "
            f"{duplicate_match.similarity_score:.1f}% similarity."
        )
    if "Fail" in statuses:
        return f"{band} validation quality. One or more required fields failed validation."
    if "Warning" in statuses:
        return f"{band} validation quality with warning-level consistency issues."
    if "Missing" in statuses:
        return f"{band} validation quality. Some fields are incomplete."
    return f"{band} validation quality. All inspected fields passed."


def validate_name(value: str, label: str, rules: set[str]) -> FieldValidationResult:
    raw = value.strip()
    if not raw:
        return FieldValidationResult(label, "", "Missing", [f"{label} is required."], 0.0)
    issues: list[str] = []
    if "name_length" in rules and (len(raw) < 2 or len(raw) > 50):
        issues.append(f"{label} must be between 2 and 50 characters.")
    if "name_format" in rules:
        if any(char.isdigit() for char in raw):
            issues.append(f"{label} cannot contain digits.")
        if not NAME_PATTERN.match(raw):
            issues.append(f"{label} allows letters, spaces, hyphens, and apostrophes only.")
    status = "Fail" if issues else "Pass"
    score = 0.0 if issues else FIELD_WEIGHT
    return FieldValidationResult(label, raw, status, issues, score)


def validate_dob(value: str, rules: set[str]) -> tuple[FieldValidationResult, date | None]:
    raw = value.strip()
    if not raw:
        return (
            FieldValidationResult(
                "Date of Birth",
                "",
                "Missing",
                ["Date of Birth is required."],
                0.0,
            ),
            None,
        )
    parsed = parse_supported_date(raw)
    issues: list[str] = []
    if parsed is None:
        issues.append("Date of Birth must use YYYY-MM-DD, DD/MM/YYYY, or MM/DD/YYYY.")
    elif "dob_future" in rules and parsed > date.today():
        issues.append("Date of Birth cannot be in the future.")
    else:
        derived_age = date.today().year - parsed.year - (
            (date.today().month, date.today().day) < (parsed.month, parsed.day)
        )
        if "dob_future" in rules and (derived_age < 0 or derived_age > 150):
            issues.append("Derived age must be between 0 and 150 years.")
    status = "Fail" if issues else "Pass"
    score = 0.0 if issues else FIELD_WEIGHT
    return FieldValidationResult("Date of Birth", raw, status, issues, score), parsed


def validate_age(value: str, parsed_dob: date | None, rules: set[str]) -> FieldValidationResult:
    raw = value.strip()
    if not raw:
        return FieldValidationResult("Age", "", "Missing", ["Age is required."], 0.0)
    if not raw.isdigit():
        return FieldValidationResult("Age", raw, "Fail", ["Age must be a whole number."], 0.0)
    numeric_age = int(raw)
    if numeric_age < 0 or numeric_age > 150:
        return FieldValidationResult("Age", raw, "Fail", ["Age must be between 0 and 150."], 0.0)
    issues: list[str] = []
    status = "Pass"
    score = FIELD_WEIGHT
    if "age_dob_align" in rules and parsed_dob:
        derived_age = date.today().year - parsed_dob.year - (
            (date.today().month, date.today().day) < (parsed_dob.month, parsed_dob.day)
        )
        if abs(derived_age - numeric_age) > 1:
            issues.append("Age does not align with the supplied Date of Birth.")
            status = "Warning"
            score = round(FIELD_WEIGHT * 0.5, 2)
    return FieldValidationResult("Age", raw, status, issues, score)


def validate_phone(value: str, rules: set[str]) -> FieldValidationResult:
    raw = value.strip()
    if not raw:
        return FieldValidationResult(
            "Phone Number",
            "",
            "Missing",
            ["Phone Number is required."],
            0.0,
        )
    issues: list[str] = []
    if "phone_format" in rules:
        if any(char.isalpha() for char in raw):
            issues.append("Phone Number cannot contain alphabetic characters.")
        digits = PHONE_STRIP_PATTERN.sub("", raw)
        if not digits.isdigit() or len(digits) < 7 or len(digits) > 15:
            issues.append("Phone Number must resolve to 7 to 15 digits.")
    status = "Fail" if issues else "Pass"
    score = 0.0 if issues else FIELD_WEIGHT
    return FieldValidationResult("Phone Number", raw, status, issues, score)


def validate_email(value: str, rules: set[str]) -> FieldValidationResult:
    raw = value.strip()
    if not raw:
        return FieldValidationResult("Email", "", "Missing", ["Email is required."], 0.0)
    
    if "email_domain" in rules:
        if not EMAIL_PATTERN.match(raw):
            return FieldValidationResult(
                "Email",
                raw,
                "Fail",
                ["Email must match a standard address pattern."],
                0.0,
            )
        if "." not in raw.rsplit("@", 1)[-1]:
            return FieldValidationResult(
                "Email",
                raw,
                "Warning",
                ["Email domain must contain a dot."],
                round(FIELD_WEIGHT * 0.5, 2),
            )
    return FieldValidationResult("Email", raw, "Pass", [], FIELD_WEIGHT)


def validate_demographic_input(
    payload: ManualDemographicInput, 
    rules: set[str] | None = None
) -> DemographicValidationResult:
    if rules is None:
        rules = {"name_length", "name_format", "dob_future", "age_dob_align", "phone_format", "email_domain"}
    dob_result, parsed_dob = validate_dob(payload.date_of_birth, rules)
    fields = [
        validate_name(payload.first_name, "First Name", rules),
        validate_name(payload.last_name, "Last Name", rules),
        dob_result,
        validate_age(payload.age, parsed_dob, rules),
        validate_phone(payload.phone, rules),
        validate_email(payload.email, rules),
    ]
    total_score = round(sum(field.field_score for field in fields), 2)
    return DemographicValidationResult(
        fields=fields,
        validation_score=total_score,
        score_band=score_band(total_score),
        summary=build_summary(total_score, {field.status for field in fields}, None),
    )


def levenshtein_distance(left: str, right: str) -> int:
    if left == right:
        return 0
    if not left:
        return len(right)
    if not right:
        return len(left)
    previous = list(range(len(right) + 1))
    for index, left_char in enumerate(left, start=1):
        current = [index]
        for position, right_char in enumerate(right, start=1):
            insert_cost = current[position - 1] + 1
            delete_cost = previous[position] + 1
            replace_cost = previous[position - 1] + (left_char != right_char)
            current.append(min(insert_cost, delete_cost, replace_cost))
        previous = current
    return previous[-1]


def normalized_similarity(left: str, right: str) -> float:
    left_clean = left.strip().lower()
    right_clean = right.strip().lower()
    if not left_clean and not right_clean:
        return 1.0
    maximum = max(len(left_clean), len(right_clean))
    if maximum == 0:
        return 1.0
    return max(0.0, 1 - (levenshtein_distance(left_clean, right_clean) / maximum))


def find_duplicate_match(
    payload: ManualDemographicInput,
    existing_records: list[dict[str, Any]],
    threshold: float = 85.0,
) -> DuplicateMatch | None:
    best_match: DuplicateMatch | None = None
    for record in existing_records:
        first_score = normalized_similarity(payload.first_name, str(record.get("first_name", "")))
        last_score = normalized_similarity(payload.last_name, str(record.get("last_name", "")))
        dob_score = normalized_similarity(
            payload.date_of_birth,
            str(record.get("date_of_birth", "") or ""),
        )
        composite = ((first_score * 0.35) + (last_score * 0.35) + (dob_score * 0.30)) * 100
        if composite >= threshold and (
            best_match is None or composite > best_match.similarity_score
        ):
            best_match = DuplicateMatch(
                matched_record_id=int(record["id"]),
                matched_name=(
                    f"{record.get('first_name', '').strip()} "
                    f"{record.get('last_name', '').strip()}"
                ).strip(),
                matched_date_of_birth=str(record.get("date_of_birth", "") or ""),
                similarity_score=round(composite, 2),
            )
    return best_match


def apply_duplicate_match(
    payload: ManualDemographicInput,
    result: DemographicValidationResult,
    existing_records: list[dict[str, Any]],
) -> DemographicValidationResult:
    duplicate = find_duplicate_match(payload, existing_records)
    result.duplicate_match = duplicate
    result.summary = build_summary(
        result.validation_score,
        {field.status for field in result.fields},
        duplicate,
    )
    return result


def batch_row_status(result: DemographicValidationResult) -> str:
    statuses = {field.status for field in result.fields}
    if "Fail" in statuses or result.validation_score < 50:
        return "Fail"
    if "Warning" in statuses or result.duplicate_match:
        return "Warning"
    return "Pass"


def build_batch_row_result(
    payload: ManualDemographicInput,
    result: DemographicValidationResult,
    row_number: int,
) -> BatchValidationRowResult:
    issues = [issue for field in result.fields for issue in field.issues]
    if result.duplicate_match:
        issues.append(
            f"Potential duplicate: {result.duplicate_match.matched_name} "
            f"({result.duplicate_match.similarity_score:.1f}% similarity)."
        )
    record_name = f"{payload.first_name} {payload.last_name}".strip() or f"Row {row_number}"
    return BatchValidationRowResult(
        row_number=row_number,
        record_name=record_name,
        score=result.validation_score,
        score_band=result.score_band,
        status=batch_row_status(result),
        issue_count=len(issues),
        duplicate_flag=result.duplicate_match is not None,
        duplicate_score=result.duplicate_match.similarity_score
        if result.duplicate_match
        else None,
        first_name=payload.first_name,
        last_name=payload.last_name,
        date_of_birth=payload.date_of_birth,
        age=payload.age,
        phone=payload.phone,
        email=payload.email,
        issues=issues,
    )


def build_batch_summary(rows: list[BatchValidationRowResult]) -> BatchValidationSummary:
    total = len(rows)
    if total == 0:
        return BatchValidationSummary(0, 0, 0, 0, 0.0, 0)
    return BatchValidationSummary(
        total_records=total,
        passed_records=sum(1 for row in rows if row.status == "Pass"),
        warning_records=sum(1 for row in rows if row.status == "Warning"),
        failed_records=sum(1 for row in rows if row.status == "Fail"),
        average_validation_score=round(sum(row.score for row in rows) / total, 2),
        duplicate_count=sum(1 for row in rows if row.duplicate_flag),
    )


def payload_from_csv_row(
    row: dict[str, str],
    header_map: dict[str, str],
) -> ManualDemographicInput:
    return ManualDemographicInput(
        first_name=str(row.get(header_map.get("first_name", ""), "") or "").strip(),
        last_name=str(row.get(header_map.get("last_name", ""), "") or "").strip(),
        date_of_birth=str(row.get(header_map.get("date_of_birth", ""), "") or "").strip(),
        age=str(row.get(header_map.get("age", ""), "") or "").strip(),
        phone=str(row.get(header_map.get("phone", ""), "") or "").strip(),
        email=str(row.get(header_map.get("email", ""), "") or "").strip(),
    )


def parse_csv_payload(content: bytes) -> tuple[list[dict[str, str]], dict[str, str]]:
    decoded = content.decode("utf-8-sig")
    reader = csv.DictReader(io.StringIO(decoded))
    if reader.fieldnames is None:
        raise ValueError("CSV file is missing a header row.")
    mapped_headers = map_csv_headers(reader.fieldnames)
    if not mapped_headers:
        raise ValueError("No recognised demographic columns found.")
    return [dict(row) for row in reader], mapped_headers


def build_export_csv(rows: list[dict[str, Any]]) -> str:
    headers = [
        "row_number",
        "record_name",
        "first_name",
        "last_name",
        "date_of_birth",
        "age",
        "phone",
        "email",
        "score",
        "score_band",
        "status",
        "issue_count",
        "duplicate_flag",
        "duplicate_score",
        "issues_text",
    ]
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=headers)
    writer.writeheader()
    for row in rows:
        writer.writerow({header: row.get(header, "") for header in headers})
    return buffer.getvalue()


def safe_filename(filename: str) -> str:
    suffix = Path(filename).suffix.lower()
    stem = Path(filename).stem
    clean_stem = re.sub(r"[^A-Za-z0-9._-]", "-", stem).strip("-") or "upload"
    return f"{clean_stem}-{uuid4().hex[:8]}{suffix}"


def save_uploaded_bytes(content: bytes, filename: str, destination_dir: Path) -> str:
    destination_dir.mkdir(parents=True, exist_ok=True)
    output_name = safe_filename(filename)
    (destination_dir / output_name).write_bytes(content)
    return output_name


def prevalidate_biometric_file(
    request: BiometricValidationRequest,
    file_path: Path,
) -> tuple[list[str], dict[str, str]]:
    issues: list[str] = []
    metadata: dict[str, str] = {}
    extension = file_path.suffix.lower()
    supported = FACE_EXTENSIONS if request.modality == "face" else FINGERPRINT_EXTENSIONS
    if extension not in supported:
        issues.append(
            f"{request.modality.title()} validation accepts: "
            f"{', '.join(sorted(supported))}."
        )
        return issues, metadata
    if not file_path.exists() or file_path.stat().st_size == 0:
        issues.append("Uploaded file is empty or unreadable.")
        return issues, metadata
    if extension in {".png", ".jpg", ".jpeg", ".bmp", ".jp2"}:
        try:
            with Image.open(file_path) as image:
                metadata["image_width"] = str(image.width)
                metadata["image_height"] = str(image.height)
                dpi = image.info.get("dpi")
                if dpi:
                    metadata["dpi"] = str(int(dpi[0]))
        except OSError:
            issues.append("Image could not be opened for local pre-validation.")
    if request.modality == "fingerprint":
        dpi_value = metadata.get("dpi")
        if dpi_value and int(dpi_value) < 500:
            issues.append(
                "Fingerprint DPI is below the recommended 500 PPI threshold."
            )
        elif not dpi_value:
            issues.append(
                "Fingerprint DPI metadata is unavailable; OpenBQ results may require review."
            )
    return issues, metadata


def _coerce_float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _extract_face_score(metrics: dict[str, Any]) -> float:
    if (quality := _coerce_float(metrics.get("quality"))) is not None:
        return max(0.0, min(100.0, quality))
    factors: list[float] = []
    for key in ("confidence", "sharpness", "contrast", "dynamic_range"):
        value = _coerce_float(metrics.get(key))
        if value is not None:
            if key == "confidence" and value <= 1:
                value *= 100
            factors.append(max(0.0, min(100.0, value)))
    face_ratio = _coerce_float(metrics.get("face_ratio"))
    if face_ratio is not None:
        factors.append(max(0.0, min(100.0, face_ratio * 100)))
    return round(sum(factors) / len(factors), 2) if factors else 0.0


def _extract_fingerprint_score(metrics: dict[str, Any]) -> float:
    if (score := _coerce_float(metrics.get("NFIQ2"))) is not None:
        return max(0.0, min(100.0, score))
    if (score := _coerce_float(metrics.get("quality"))) is not None:
        return max(0.0, min(100.0, score))
    return 0.0


def _status_from_score(score: float) -> str:
    if score >= BIOMETRIC_THRESHOLDS["accepted"]:
        return "Accepted"
    if score >= BIOMETRIC_THRESHOLDS["review"]:
        return "Review"
    return "Rejected"


def _selected_face_metrics(metrics: dict[str, Any]) -> dict[str, str]:
    keys = ["brightness", "sharpness", "face_ratio", "yaw_degree", "face_offset_x"]
    return {
        key.replace("_", " ").title(): str(metrics[key])
        for key in keys
        if metrics.get(key) not in (None, "")
    }


def _selected_fingerprint_metrics(metrics: dict[str, Any]) -> dict[str, str]:
    keys = [
        "NFIQ2",
        "image_width",
        "image_height",
        "uniform_image",
        "empty_image_or_contrast_too_low",
    ]
    return {
        key.replace("_", " ").title(): str(metrics[key])
        for key in keys
        if metrics.get(key) not in (None, "")
    }


def _issues_from_metrics(modality: str, metrics: dict[str, Any]) -> list[str]:
    issues: list[str] = []
    if modality == "face":
        brightness = _coerce_float(metrics.get("brightness"))
        sharpness = _coerce_float(metrics.get("sharpness"))
        face_ratio = _coerce_float(metrics.get("face_ratio"))
        if brightness is not None and brightness < 35:
            issues.append("Lighting appears low for a strong face capture.")
        if sharpness is not None and sharpness < 35:
            issues.append("Face image sharpness is below the preferred threshold.")
        if face_ratio is not None and face_ratio < 0.5:
            issues.append("Face coverage is low; recapture with the subject filling more of the frame.")
    else:
        if str(metrics.get("empty_image_or_contrast_too_low", "")).lower() in {"1", "true", "yes"}:
            issues.append("Fingerprint image is blank or lacks sufficient contrast.")
        foreground = _coerce_float(metrics.get("sufficient_fingerprint_foreground"))
        if foreground is not None and foreground < 20:
            issues.append("Fingerprint foreground coverage is weak.")
    return issues


def _parse_openbq_csv(temp_dir: Path) -> dict[str, Any]:
    csv_files = sorted(temp_dir.rglob("*.csv"))
    if not csv_files:
        raise FileNotFoundError("OpenBQ did not generate a CSV result.")
    with csv_files[0].open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        first_row = next(reader, None)
    if first_row is None:
        raise ValueError("OpenBQ output CSV was empty.")
    return dict(first_row)


def run_openbq_analysis(
    request: BiometricValidationRequest,
    source_path: Path,
    saved_preview_filename: str,
) -> BiometricValidationResult:
    issues, metadata = prevalidate_biometric_file(request, source_path)
    if issues and (
        "accepts:" in issues[0]
        or "empty or unreadable" in issues[0]
        or "could not be opened" in issues[0]
    ):
        return BiometricValidationResult(
            modality=request.modality,
            source_filename=request.source_filename,
            overall_score=0.0,
            status="Rejected",
            issue_list=issues,
            metrics=metadata,
            preview_filename=saved_preview_filename,
            raw_output={},
        )

    cli_path = shutil.which("openbq")
    if cli_path is None:
        issues.append(
            "OpenBQ CLI is not installed. Install `openbq` and Docker to enable biometric analysis."
        )
        return BiometricValidationResult(
            modality=request.modality,
            source_filename=request.source_filename,
            overall_score=0.0,
            status="Rejected",
            issue_list=issues,
            metrics=metadata,
            preview_filename=saved_preview_filename,
            raw_output={},
        )

    with tempfile.TemporaryDirectory() as temp_dir_name:
        temp_dir = Path(temp_dir_name)
        input_dir = temp_dir / "input"
        input_dir.mkdir(parents=True, exist_ok=True)
        temp_input = input_dir / source_path.name
        shutil.copy2(source_path, temp_input)
        command = [cli_path, "--mode", request.modality, "--input", str(input_dir)]
        try:
            completed = subprocess.run(
                command,
                cwd=temp_dir,
                check=False,
                capture_output=True,
                text=True,
                timeout=120,
            )
        except subprocess.TimeoutExpired:
            issues.append("OpenBQ analysis timed out.")
            return BiometricValidationResult(
                modality=request.modality,
                source_filename=request.source_filename,
                overall_score=0.0,
                status="Rejected",
                issue_list=issues,
                metrics=metadata,
                preview_filename=saved_preview_filename,
                raw_output={},
            )

        raw_output = {
            "stdout": completed.stdout,
            "stderr": completed.stderr,
            "returncode": completed.returncode,
        }
        if completed.returncode != 0:
            issues.append(completed.stderr.strip() or "OpenBQ returned a non-zero exit code.")
            return BiometricValidationResult(
                modality=request.modality,
                source_filename=request.source_filename,
                overall_score=0.0,
                status="Rejected",
                issue_list=issues,
                metrics=metadata,
                preview_filename=saved_preview_filename,
                raw_output=raw_output,
            )

        try:
            metrics = {**metadata, **_parse_openbq_csv(temp_dir)}
        except (FileNotFoundError, ValueError) as error:
            issues.append(str(error))
            return BiometricValidationResult(
                modality=request.modality,
                source_filename=request.source_filename,
                overall_score=0.0,
                status="Rejected",
                issue_list=issues,
                metrics=metadata,
                preview_filename=saved_preview_filename,
                raw_output=raw_output,
            )

    score = (
        _extract_face_score(metrics)
        if request.modality == "face"
        else _extract_fingerprint_score(metrics)
    )
    issues.extend(_issues_from_metrics(request.modality, metrics))
    selected_metrics = (
        _selected_face_metrics(metrics)
        if request.modality == "face"
        else _selected_fingerprint_metrics(metrics)
    )
    return BiometricValidationResult(
        modality=request.modality,
        source_filename=request.source_filename,
        overall_score=round(score, 2),
        status=_status_from_score(score),
        issue_list=issues or ["No operator issues detected."],
        metrics=selected_metrics,
        preview_filename=saved_preview_filename,
        raw_output=metrics,
    )
