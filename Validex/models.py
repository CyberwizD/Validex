from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


FIELD_WEIGHT = round(100 / 6, 2)


DEFAULT_DEMOGRAPHIC_RULES = [
    {"id": "name_length", "name": "Name Length Check", "description": "Ensures first and last names are between 2 and 50 characters in length.", "enabled": True},
    {"id": "name_format", "name": "Name Format Verification", "description": "Checks for illegal characters or sequential digits inside names.", "enabled": True},
    {"id": "dob_future", "name": "Future Date Restriction", "description": "Rejects any birth dates that are algorithmically derived to be in the future.", "enabled": True},
    {"id": "age_dob_align", "name": "Age/DOB Coherence", "description": "Flags mismatch logic between explicitly stated age and implicit Date of Birth.", "enabled": True},
    {"id": "phone_format", "name": "Phone Standards", "description": "Validates phone number lengths and strict numeric pattern constraints.", "enabled": True},
    {"id": "email_domain", "name": "Email Domain Check", "description": "Ensures the email address structure contains a valid, strict top-level root domain.", "enabled": True},
]


@dataclass(slots=True)
class ManualDemographicInput:
    first_name: str = ""
    last_name: str = ""
    date_of_birth: str = ""
    age: str = ""
    phone: str = ""
    email: str = ""

    def to_dict(self) -> dict[str, str]:
        return asdict(self)


@dataclass(slots=True)
class FieldValidationResult:
    field: str
    entered_value: str
    status: str
    issues: list[str] = field(default_factory=list)
    field_score: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "field": self.field,
            "entered_value": self.entered_value,
            "status": self.status,
            "issues": self.issues,
            "issues_text": "; ".join(self.issues) if self.issues else "No issues found",
            "field_score": round(self.field_score, 2),
        }


@dataclass(slots=True)
class DuplicateMatch:
    matched_record_id: int
    matched_name: str
    matched_date_of_birth: str
    similarity_score: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "matched_record_id": self.matched_record_id,
            "matched_name": self.matched_name,
            "matched_date_of_birth": self.matched_date_of_birth,
            "similarity_score": round(self.similarity_score, 2),
        }


@dataclass(slots=True)
class DemographicValidationResult:
    fields: list[FieldValidationResult]
    validation_score: float
    score_band: str
    summary: str
    duplicate_match: DuplicateMatch | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "fields": [item.to_dict() for item in self.fields],
            "validation_score": round(self.validation_score, 2),
            "score_band": self.score_band,
            "summary": self.summary,
            "duplicate_match": self.duplicate_match.to_dict()
            if self.duplicate_match
            else None,
        }


@dataclass(slots=True)
class BatchValidationRowResult:
    row_number: int
    record_name: str
    score: float
    score_band: str
    status: str
    issue_count: int
    duplicate_flag: bool
    duplicate_score: float | None
    first_name: str = ""
    last_name: str = ""
    date_of_birth: str = ""
    age: str = ""
    phone: str = ""
    email: str = ""
    issues: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "row_number": self.row_number,
            "record_name": self.record_name,
            "score": round(self.score, 2),
            "score_band": self.score_band,
            "status": self.status,
            "issue_count": self.issue_count,
            "duplicate_flag": self.duplicate_flag,
            "duplicate_score": round(self.duplicate_score, 2)
            if self.duplicate_score is not None
            else None,
            "first_name": self.first_name,
            "last_name": self.last_name,
            "date_of_birth": self.date_of_birth,
            "age": self.age,
            "phone": self.phone,
            "email": self.email,
            "issues": self.issues,
            "issues_text": "; ".join(self.issues) if self.issues else "No issues found",
        }


@dataclass(slots=True)
class BatchValidationSummary:
    total_records: int
    passed_records: int
    warning_records: int
    failed_records: int
    average_validation_score: float
    duplicate_count: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "total_records": self.total_records,
            "passed_records": self.passed_records,
            "warning_records": self.warning_records,
            "failed_records": self.failed_records,
            "average_validation_score": round(self.average_validation_score, 2),
            "duplicate_count": self.duplicate_count,
        }


@dataclass(slots=True)
class BiometricValidationRequest:
    modality: str
    source_filename: str


@dataclass(slots=True)
class BiometricValidationResult:
    modality: str
    source_filename: str
    overall_score: float
    status: str
    issue_list: list[str]
    metrics: dict[str, str]
    preview_filename: str
    raw_output: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "modality": self.modality,
            "source_filename": self.source_filename,
            "overall_score": round(self.overall_score, 2),
            "status": self.status,
            "issue_list": self.issue_list,
            "metrics": self.metrics,
            "preview_filename": self.preview_filename,
            "raw_output": self.raw_output,
        }
