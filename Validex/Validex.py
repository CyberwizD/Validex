from __future__ import annotations

import math
from typing import Any

import reflex as rx

from .db import fetch_demographic_records, init_database, insert_biometric_record, insert_demographic_record
from .models import DEFAULT_DEMOGRAPHIC_RULES, BiometricValidationRequest, ManualDemographicInput
from .services import (
    apply_duplicate_match,
    build_batch_row_result,
    build_batch_summary,
    build_export_csv,
    parse_csv_payload,
    payload_from_csv_row,
    run_openbq_analysis,
    save_uploaded_bytes,
    validate_demographic_input,
)


BATCH_UPLOAD_ID = "batch-upload"
BIOMETRIC_UPLOAD_ID = "biometric-upload"
SURFACE = "#F7F7FB"
CARD_BG = "rgba(255, 255, 255, 0.94)"
CARD_BORDER = "1px solid rgba(15, 23, 42, 0.08)"
PRIMARY = "#141C32"
MUTED = "#667085"
ACCENT = "#FDBA4D"
SUCCESS = "#157F3B"
WARNING = "#D97706"
FAILURE = "#B42318"
PILL = "rgba(255,255,255,0.85)"


def default_batch_summary() -> dict[str, Any]:
    return {
        "total_records": 0,
        "passed_records": 0,
        "warning_records": 0,
        "failed_records": 0,
        "average_validation_score": 0.0,
        "duplicate_count": 0,
    }


class AppState(rx.State):
    validation_modal_open: bool = False

    first_name: str = ""
    last_name: str = ""
    date_of_birth: str = ""
    age: str = ""
    phone: str = ""
    email: str = ""

    manual_score: float = 0.0
    manual_band: str = "Awaiting validation"
    manual_summary: str = "Run a manual validation to inspect field-level quality."
    manual_results_rows: list[dict[str, Any]] = []
    manual_duplicate_match: dict[str, Any] = {}
    has_manual_result: bool = False
    
    manual_filter: str = "All"
    
    def set_manual_filter(self, val: str):
        self.manual_filter = val

    @rx.var
    def filtered_manual_results_rows(self) -> list[dict[str, Any]]:
        if self.manual_filter == "All":
            return self.manual_results_rows
        return [row for row in self.manual_results_rows if row["status"] == self.manual_filter]

    @rx.var
    def authority_ring_bg(self) -> str:
        score_val = max(0, min(100, int(self.manual_score))) if self.manual_score else 0
        return f"conic-gradient(#FDBA4D {score_val}%, #141C32 {score_val}% 100%)"

    batch_source_name: str = ""
    batch_error: str = ""
    batch_results_rows: list[dict[str, Any]] = []
    batch_summary: dict[str, Any] = default_batch_summary()
    batch_page: int = 1
    batch_page_size: int = 6
    has_batch_results: bool = False

    biometric_modality: str = "face"
    biometric_error: str = ""
    biometric_score: float = 0.0
    biometric_status: str = "Awaiting upload"
    biometric_source_filename: str = ""
    biometric_preview_filename: str = ""
    biometric_preview_is_image: bool = False
    biometric_preview_history: list[str] = []
    biometric_metric_rows: list[dict[str, Any]] = []
    biometric_issues: list[str] = []
    has_biometric_result: bool = False
    has_manual_duplicate: bool = False
    biometric_face_detected: bool = False
    biometric_image_filename: str = ""

    @rx.var
    def biometric_ring_bg(self) -> str:
        score_val = max(0, min(100, int(self.biometric_score))) if self.biometric_score else 0
        return f"conic-gradient(#141C32 {score_val}%, #E5E7EB {score_val}% 100%)"

    @rx.var
    def biometric_badge_color(self) -> str:
        if not self.has_biometric_result: return "gray"
        if self.biometric_status == "Accepted": return "grass"
        if self.biometric_status in ("Rejected", "Failed"): return "tomato"
        return "orange"
        
    @rx.var
    def biometric_badge_text(self) -> str:
        return self.biometric_status.upper() if self.has_biometric_result else "PENDING"
        
    @rx.var
    def biometric_status_message(self) -> str:
        if not self.has_biometric_result: return "Awaiting sample upload"
        if self.biometric_modality == "face" and not self.biometric_face_detected:
            return "No face detected; image-quality metrics only"
        if self.biometric_status == "Accepted": return "Optimal Quality Threshold Met"
        if self.biometric_status == "Failed": return "Analysis Failed"
        return "Quality Threshold Not Met"

    @rx.var
    def biometric_face_badge_text(self) -> str:
        if self.biometric_modality != "face" or not self.has_biometric_result:
            return ""
        return "FACE DETECTED" if self.biometric_face_detected else "NO FACE DETECTED"

    @rx.var
    def biometric_face_badge_color(self) -> str:
        if self.biometric_modality != "face" or not self.has_biometric_result:
            return "gray"
        return "grass" if self.biometric_face_detected else "tomato"

    @rx.var
    def batch_total_pages(self) -> int:
        if not self.batch_results_rows:
            return 1
        return max(1, math.ceil(len(self.batch_results_rows) / self.batch_page_size))

    @rx.var
    def paginated_batch_rows(self) -> list[dict[str, Any]]:
        start = (self.batch_page - 1) * self.batch_page_size
        end = start + self.batch_page_size
        return self.batch_results_rows[start:end]

    @rx.var
    def biometric_accept_map(self) -> dict[str, list[str]]:
        if self.biometric_modality == "face":
            return {
                "image/jpeg": [".jpg", ".jpeg"],
                "image/png": [".png"],
                "image/bmp": [".bmp"],
                "image/jp2": [".jp2"],
                "image/jpeg2000": [".jp2"],
            }
        return {
            "image/png": [".png"],
            "application/octet-stream": [".wsq"],
            "application/wsq": [".wsq"],
        }

    def open_validation_modal(self) -> None:
        self.validation_modal_open = True

    def close_validation_modal(self) -> None:
        self.validation_modal_open = False

    def route_to_demographics(self):
        self.validation_modal_open = False
        return rx.redirect("/demographics")

    def route_to_biometrics(self):
        self.validation_modal_open = False
        return rx.redirect("/biometrics")
        
    rules_modal_open: bool = False
    active_rules: list[dict[str, Any]] = DEFAULT_DEMOGRAPHIC_RULES.copy()

    def open_rules_modal(self) -> None:
        self.rules_modal_open = True
        
    def close_rules_modal(self) -> None:
        self.rules_modal_open = False

    def toggle_rule(self, rule_id: str, new_value: bool) -> None:
        for idx, r in enumerate(self.active_rules):
            if r["id"] == rule_id:
                # Need to explicitly clone the inner dict so it mutates functionally in reflex state
                new_rule = self.active_rules[idx].copy()
                new_rule["enabled"] = new_value
                self.active_rules[idx] = new_rule
                break
                
    def reset_rules(self) -> None:
        self.active_rules = DEFAULT_DEMOGRAPHIC_RULES.copy()

    def reset_manual_form(self) -> None:
        self.first_name = ""
        self.last_name = ""
        self.date_of_birth = ""
        self.age = ""
        self.phone = ""
        self.email = ""

    def new_validation(self):
        self.reset_manual_form()
        self.manual_score = 0.0
        self.manual_band = "Awaiting validation"
        self.manual_summary = "Run a manual validation to inspect field-level quality."
        self.manual_results_rows = []
        self.manual_duplicate_match = {}
        self.has_manual_result = False
        self.has_manual_duplicate = False
        self.manual_filter = "All"
        self.batch_source_name = ""
        self.batch_results_rows = []
        self.batch_summary = default_batch_summary()
        self.has_batch_results = False
        self.batch_page = 1
        self.batch_error = ""
        self.biometric_error = ""
        self.biometric_score = 0.0
        self.biometric_status = "Awaiting upload"
        self.biometric_source_filename = ""
        self.biometric_preview_filename = ""
        self.biometric_preview_is_image = False
        self.biometric_preview_history = []
        self.biometric_metric_rows = []
        self.biometric_issues = []
        self.has_biometric_result = False
        self.biometric_face_detected = False
        self.biometric_image_filename = ""
        return rx.redirect("/")

    def set_biometric_mode(self, mode: str):
        self.biometric_modality = mode
        self.biometric_error = ""
        self.biometric_score = 0.0
        self.biometric_status = "Awaiting upload"
        self.biometric_source_filename = ""
        self.biometric_preview_filename = ""
        self.biometric_preview_is_image = False
        self.biometric_preview_history = []
        self.biometric_metric_rows = []
        self.biometric_issues = []
        self.has_biometric_result = False
        self.biometric_face_detected = False
        self.biometric_image_filename = ""
        return rx.clear_selected_files(BIOMETRIC_UPLOAD_ID)

    def previous_batch_page(self) -> None:
        if self.batch_page > 1:
            self.batch_page -= 1

    def next_batch_page(self) -> None:
        if self.batch_page < self.batch_total_pages:
            self.batch_page += 1

    def validate_manual_entry(self) -> None:
        init_database()
        payload = ManualDemographicInput(
            first_name=self.first_name,
            last_name=self.last_name,
            date_of_birth=self.date_of_birth,
            age=self.age,
            phone=self.phone,
            email=self.email,
        )
        enabled_rule_ids = {r["id"] for r in self.active_rules if r["enabled"]}
        result = validate_demographic_input(payload, enabled_rule_ids)
        result = apply_duplicate_match(payload, result, fetch_demographic_records())
        insert_demographic_record(payload, result, "manual")
        self.manual_score = result.validation_score
        self.manual_band = result.score_band
        self.manual_summary = result.summary
        self.manual_results_rows = [item.to_dict() for item in result.fields]
        self.manual_duplicate_match = (
            result.duplicate_match.to_dict() if result.duplicate_match else {}
        )
        self.has_manual_duplicate = result.duplicate_match is not None
        self.has_manual_result = True

    async def handle_batch_upload(self, files: list[rx.UploadFile]):
        init_database()
        self.batch_error = ""
        if not files:
            self.batch_error = "Select a CSV file before starting batch validation."
            return
        file = files[0]
        filename = file.filename or "dataset.csv"
        if not filename.lower().endswith(".csv"):
            self.batch_error = "Validex v1 supports CSV uploads only."
            return rx.clear_selected_files(BATCH_UPLOAD_ID)
        content = await file.read()
        try:
            rows, header_map = parse_csv_payload(content)
        except ValueError as error:
            self.batch_error = str(error)
            return rx.clear_selected_files(BATCH_UPLOAD_ID)

        saved_name = save_uploaded_bytes(content, filename, rx.get_upload_dir())
        existing_records = fetch_demographic_records()
        batch_rows: list[dict[str, Any]] = []
        batch_row_models = []
        
        enabled_rule_ids = {r["id"] for r in self.active_rules if r["enabled"]}
        present_fields = set(header_map.keys())
        
        for index, row in enumerate(rows, start=1):
            payload = payload_from_csv_row(row, header_map)
            result = apply_duplicate_match(payload, validate_demographic_input(payload, enabled_rule_ids, present_fields), existing_records)
            record_id = insert_demographic_record(payload, result, saved_name)
            batch_row = build_batch_row_result(payload, result, index)
            batch_row_models.append(batch_row)
            batch_rows.append(batch_row.to_dict())
            existing_records.insert(
                0,
                {
                    "id": record_id,
                    "first_name": payload.first_name,
                    "last_name": payload.last_name,
                    "date_of_birth": payload.date_of_birth,
                },
            )

        self.batch_source_name = saved_name
        self.batch_results_rows = batch_rows
        self.batch_summary = build_batch_summary(batch_row_models).to_dict()
        self.batch_page = 1
        self.has_batch_results = True
        return rx.clear_selected_files(BATCH_UPLOAD_ID)

    def export_batch_results(self):
        if not self.batch_results_rows:
            return rx.noop()
        return rx.download(
            data=build_export_csv(self.batch_results_rows),
            filename="validex-batch-results.csv",
            mime_type="text/csv",
        )

    async def handle_biometric_upload(self, files: list[rx.UploadFile]):
        init_database()
        self.biometric_error = ""
        if not files:
            self.biometric_error = "Select a biometric sample before analysis."
            return

        for file in files:
            filename = file.filename or "sample"
            content = await file.read()
            saved_name = save_uploaded_bytes(content, filename, rx.get_upload_dir())
            saved_path = rx.get_upload_dir() / saved_name
            request = BiometricValidationRequest(
                modality=self.biometric_modality,
                source_filename=filename,
            )
            try:
                import asyncio
                result = await asyncio.to_thread(run_openbq_analysis, request, saved_path, saved_name)
                insert_biometric_record(result)
                self.biometric_score = result.overall_score
                self.biometric_status = result.status
                self.biometric_source_filename = result.source_filename
                self.biometric_image_filename = result.source_filename
                self.biometric_preview_filename = result.preview_filename
                self.biometric_preview_is_image = result.preview_filename.lower().endswith(
                    (".jpg", ".jpeg", ".png", ".bmp")
                )
                self.biometric_face_detected = result.face_detected
                self.biometric_metric_rows = result.metrics
                self.biometric_issues = result.issue_list
                self.has_biometric_result = True
                
                if result.status == "Rejected" and result.overall_score == 0:
                    self.biometric_error = "; ".join(result.issue_list)
                    
                if self.biometric_preview_is_image:
                    self.biometric_preview_history.insert(0, self.biometric_preview_filename)
            except Exception as e:
                self.biometric_error = f"Analysis Backend Offline. Ensure Docker Desktop is running. ({str(e)})"
                self.has_biometric_result = True
                self.biometric_score = 0
                self.biometric_status = "Failed"
                self.biometric_image_filename = filename
                self.biometric_face_detected = False
                self.biometric_metric_rows = [
                    {"label": "Runtime Status", "value": "Service Offline", "category": "Diagnostics", "status": "fail", "row_type": "metric"}
                ]
                self.biometric_issues = ["Could not connect to OpenBQ validation cluster."]
                if saved_name.lower().endswith((".jpg", ".jpeg", ".png", ".bmp")):
                    self.biometric_preview_history.insert(0, saved_name)

        return rx.clear_selected_files(BIOMETRIC_UPLOAD_ID)


def nav_link(label: str, href: str, active: str) -> rx.Component:
    is_active = label == active
    return rx.link(
        label,
        href=href,
        underline="none",
        color=PRIMARY if is_active else MUTED,
        font_weight="600" if is_active else "500",
        font_size="0.95rem",
        border_bottom=f"2px solid {PRIMARY}" if is_active else "2px solid transparent",
        padding_bottom="0.4rem",
    )


def brand_header(active: str) -> rx.Component:
    return rx.hstack(
        rx.heading("Validex", size="8", color=PRIMARY, font_weight="800"),
        rx.spacer(),
        rx.hstack(
            nav_link("Dashboard", "/", active),
            # nav_link("Demographics", "/demographics", active),
            # nav_link("Biometrics", "/biometrics", active),
            spacing="8",
            align="center",
        ),
        align="center",
        width="100%",
        padding_top="1.75rem",
        padding_bottom="1.25rem",
    )


def shell(content: rx.Component, active: str) -> rx.Component:
    return rx.box(
        rx.box(
            brand_header(active),
            content,
            width="100%",
            padding_x=["1.5rem", "2.5rem", "6rem"],
            padding_bottom="3rem",
            min_height="100vh",
            display="flex",
            flex_direction="column",
        ),
        min_height="100vh",
        background="#FAFAFC",
        position="relative",
        z_index="1",
        _before={
            "content": '""',
            "position": "absolute",
            "top": "0",
            "left": "0",
            "width": "100%",
            "height": "100%",
            "background_image": (
                "radial-gradient(circle at 15% 50%, rgba(0,0,0,0.015) 0%, transparent 40%),"
                "radial-gradient(circle at 85% 30%, rgba(0,0,0,0.015) 0%, transparent 40%)"
            ),
            "z_index": "-1",
        }
    )


def surface_card(*children: rx.Component, **props) -> rx.Component:
    return rx.card(
        *children,
        background=props.pop("background", CARD_BG),
        border=props.pop("border", CARD_BORDER),
        border_radius=props.pop("border_radius", "24px"),
        box_shadow=props.pop("box_shadow", "0 14px 48px rgba(15, 23, 42, 0.08)"),
        padding=props.pop("padding", "1.5rem"),
        **props,
    )


def section_eyebrow(text: str) -> rx.Component:
    return rx.hstack(
        rx.box(width="8px", height="8px", background="#FDBA4D", border_radius="50%"),
        rx.text(text, font_size="0.75rem", font_weight="700", letter_spacing="0.1em", text_transform="uppercase", color="#141C32"),
        align="center",
        spacing="2",
        padding_x="1rem",
        padding_y="0.5rem",
        background="white",
        border_radius="full",
        box_shadow="0 2px 12px rgba(0,0,0,0.04)"
    )


def status_badge(status: str | rx.Var) -> rx.Component:
    color_scheme = rx.cond(
        status == "Awaiting validation",
        "gray",
        rx.cond(
            status == "Awaiting upload",
            "gray",
            rx.cond(
                status == "Pass",
                "grass",
                rx.cond(
                    status == "Accepted",
                    "grass",
                    rx.cond(
                        status == "Excellent",
                        "grass",
                        rx.cond(
                            status == "Good",
                            "amber",
                            rx.cond(
                                status == "Fair",
                                "amber",
                                rx.cond(
                                    status == "Warning",
                                    "amber",
                                    rx.cond(status == "Review", "amber", "tomato"),
                                ),
                            ),
                        ),
                    ),
                ),
            ),
        ),
    )
    return rx.badge(
        status,
        color_scheme=color_scheme,
        variant="soft",
        radius="full",
        padding_x="0.8rem",
        padding_y="0.3rem",
        font_weight="600",
    )


def percent_value(value: float | rx.Var) -> rx.Component:
    return rx.hstack(
        rx.text(value, color=PRIMARY, font_size="2.8rem", font_weight="700"),
        rx.text("%", color=PRIMARY, font_size="1.25rem", font_weight="700"),
        align="end",
        spacing="1",
    )


def progress_value(value: float | rx.Var) -> int | rx.Var:
    if isinstance(value, rx.Var):
        return value.to(int)
    return int(value)


def score_panel(title: str, score: float | rx.Var, band: str | rx.Var, summary: str | rx.Var) -> rx.Component:
    return surface_card(
        rx.vstack(
            rx.text("VALIDATION SCORE", font_size="0.85rem", font_weight="800", letter_spacing="0.15em", color=PRIMARY),
            rx.center(
                rx.center(
                    rx.vstack(
                        rx.hstack(
                            rx.text(score, font_size="3.5rem", font_weight="800", color=PRIMARY, line_height="1"),
                            rx.text("%", font_size="1.5rem", font_weight="800", color=PRIMARY, margin_top="1.5rem"),
                            align="end",
                            spacing="1",
                            margin_top="0.5rem",
                        ),
                        rx.badge(band, background="#141C32", color="white", padding_x="0.8rem", padding_y="0.3rem", font_weight="800", font_size="0.75rem"),
                        spacing="2",
                        align="center",
                    ),
                    width="188px",
                    height="188px",
                    background="white",
                    border_radius="50%",
                ),
                width="220px",
                height="220px",
                background=AppState.authority_ring_bg,
                border_radius="50%",
                margin_y="1.5rem",
            ),
            rx.text(
                summary,
                color=MUTED,
                font_size="0.95rem",
                text_align="center",
                max_width="250px",
                line_height="1.5",
            ),
            width="100%",
            spacing="4",
            align="center",
        ),
        height="100%",
        padding="2.5rem",
    )


def table_header(columns: list[str]) -> rx.Component:
    return rx.table.header(
        rx.table.row(*[
            rx.table.column_header_cell(
                column, 
                font_size="0.75rem", 
                font_weight="800", 
                letter_spacing="0.05em", 
                color=PRIMARY
            ) for column in columns
        ]),
    )


def manual_row(row: dict[str, Any]) -> rx.Component:
    status_indicator = rx.hstack(
        rx.box(width="8px", height="8px", border_radius="2px", 
               background=rx.cond(row["status"] == "Pass", "#10B981", rx.cond(row["status"] == "Warning", "#F59E0B", "#EF4444"))),
        rx.text(rx.cond(row["status"] == "Pass", "PASS", rx.cond(row["status"] == "Warning", "WARNING", "FAIL")), font_size="0.75rem", font_weight="800", letter_spacing="0.05em", color=PRIMARY),
        align="center",
        spacing="2"
    )
    return rx.table.row(
        rx.table.cell(row["field"], font_weight="600", color=PRIMARY),
        rx.table.cell(rx.cond(row["entered_value"] != "", row["entered_value"], "-"), color=PRIMARY, font_weight="500"),
        rx.table.cell(status_indicator),
        rx.table.cell(row["issues_text"], color=MUTED, font_style="italic", font_size="0.9rem"),
        border_bottom="1px solid rgba(15,23,42,0.05)",
        _hover={"background": "#F9FAFB"}
    )


def batch_row(row: dict[str, Any]) -> rx.Component:
    duplicate_cell = rx.cond(
        row["duplicate_flag"],
        percent_value(row["duplicate_score"]),
        rx.text("-", color=MUTED),
    )
    return rx.table.row(
        rx.table.cell(rx.hstack(rx.text("#", color=MUTED, font_weight="600"), rx.text(row["row_number"], color=PRIMARY, font_weight="600"), spacing="1", align="center")),
        rx.table.cell(rx.text(row["record_name"], color=PRIMARY, font_weight="600")),
        rx.table.cell(status_badge(row["status"])),
        rx.table.cell(percent_value(row["score"])),
        rx.table.cell(rx.text(row["issues_text"], color=MUTED, font_size="0.9rem"), max_width="320px"),
        rx.table.cell(duplicate_cell),
        _hover={"background": "#F9FAFB"},
    )


def biometric_metric_status_chip(status: str | rx.Var) -> rx.Component:
    icon_name = rx.cond(
        status == "pass",
        "circle-check-big",
        rx.cond(
            status == "warn",
            "triangle-alert",
            rx.cond(status == "fail", "circle-x", "info"),
        ),
    )
    icon_color = rx.cond(
        status == "pass",
        SUCCESS,
        rx.cond(
            status == "warn",
            WARNING,
            rx.cond(status == "fail", FAILURE, MUTED),
        ),
    )
    return rx.hstack(
        rx.icon(icon_name, size=14, color=icon_color),
        rx.text(
            rx.cond(
                status == "pass",
                "PASS",
                rx.cond(
                    status == "warn",
                    "WARN",
                    rx.cond(status == "fail", "FAIL", "INFO"),
                ),
            ),
            color=icon_color,
            font_size="0.72rem",
            font_weight="800",
            letter_spacing="0.08em",
        ),
        spacing="1",
        align="center",
    )


def metric_row(row: dict[str, Any]) -> rx.Component:
    return rx.cond(
        row["row_type"] == "section",
        rx.box(
            rx.text(
                row["label"],
                font_size="0.78rem",
                font_weight="800",
                color=MUTED,
                letter_spacing="0.12em",
                text_transform="uppercase",
            ),
            width="100%",
            padding_top="1.1rem",
            padding_bottom="0.45rem",
            border_bottom="1px solid rgba(15, 23, 42, 0.08)",
        ),
        rx.hstack(
            rx.vstack(
                rx.text(row["label"], font_size="0.88rem", font_weight="600", color=PRIMARY),
                rx.text(row["category"], font_size="0.72rem", color=MUTED),
                spacing="0",
                align="start",
            ),
            rx.spacer(),
            biometric_metric_status_chip(row["status"]),
            rx.text(row["value"], font_size="0.88rem", font_weight="800", color=PRIMARY, text_align="right"),
            width="100%",
            align="center",
            padding_y="0.8rem",
            border_bottom="1px solid rgba(15, 23, 42, 0.05)",
            spacing="4",
        ),
    )


def validation_modal() -> rx.Component:
    option_style = {
        "width": "100%",
        "height": "100%",
        "background": "white",
        "border": "1px solid rgba(15, 23, 42, 0.06)",
        "border_radius": "16px",
        "padding": "1.75rem",
        "align_items": "start",
        "justify_content": "start",
        "box_shadow": "0 4px 12px rgba(0,0,0,0.02)",
        "cursor": "pointer",
        "transition": "all 0.2s ease",
        "_hover": {
            "border_color": "rgba(15, 23, 42, 0.15)",
            "box_shadow": "0 10px 25px rgba(0,0,0,0.06)",
            "transform": "translateY(-2px)"
        }
    }
    
    def icon_container(icon_name: str) -> rx.Component:
        return rx.center(
            rx.icon(icon_name, size=20, color=PRIMARY),
            width="44px",
            height="44px",
            background="#F4F5F7",
            border_radius="50%",
            margin_bottom="1rem"
        )

    return rx.dialog.root(
        rx.dialog.content(
            rx.vstack(
                rx.vstack(
                    rx.center(
                        rx.icon("shield-check", size=24, color="white"),
                        background=PRIMARY,
                        width="52px",
                        height="52px",
                        border_radius="14px",
                    ),
                    rx.heading("Initialize Validation", size="7", color=PRIMARY, font_weight="700"),
                    rx.text(
                        "Select the protocol required for identity verification.",
                        color=MUTED,
                        text_align="center",
                        font_size="1.05rem",
                    ),
                    spacing="3",
                    align="center",
                    width="100%",
                    padding_bottom="1.5rem",
                ),
                rx.grid(
                    rx.box(
                        rx.vstack(
                            icon_container("user-search"),
                            rx.text("Demographic Validation", font_weight="700", color=PRIMARY, font_size="1.15rem"),
                            rx.text(
                                "Name, DOB, Phone, Email & Rule-based checks",
                                color=MUTED,
                                font_size="0.95rem",
                                text_align="left",
                                line_height="1.5",
                            ),
                            align="start",
                            spacing="1",
                        ),
                        on_click=AppState.route_to_demographics,
                        **option_style,
                    ),
                    rx.box(
                        rx.vstack(
                            icon_container("fingerprint"),
                            rx.text("Biometric Validation", font_weight="700", color=PRIMARY, font_size="1.15rem"),
                            rx.text(
                                "Face and Fingerprint analysis via OpenBQ",
                                color=MUTED,
                                font_size="0.95rem",
                                text_align="left",
                                line_height="1.5",
                            ),
                            align="start",
                            spacing="1",
                        ),
                        on_click=AppState.route_to_biometrics,
                        **option_style,
                    ),
                    columns="2",
                    spacing="5",
                    width="100%",
                ),
                rx.hstack(
                    rx.hstack(
                        rx.box(width="8px", height="8px", background="#FDBA4D", border_radius="50%"),
                        rx.text("SYSTEM READY", font_size="0.85rem", font_weight="700", letter_spacing="0.12em", color=PRIMARY),
                        align="center",
                        spacing="2",
                    ),
                    rx.button(
                        "Cancel Request",
                        variant="ghost",
                        color=PRIMARY,
                        font_weight="600",
                        on_click=AppState.close_validation_modal,
                        padding="0.5rem",
                    ),
                    justify="between",
                    align="center",
                    width="100%",
                    padding_top="2rem",
                ),
                spacing="0",
                align="center",
                width="100%",
            ),
            background="#F3F4F6",
            border_radius="28px",
            padding="2.5rem",
            max_width="720px",
            width="90vw",
            box_shadow="0 25px 50px rgba(0,0,0,0.15)",
        ),
        open=AppState.validation_modal_open,
        on_open_change=AppState.set_validation_modal_open,
    )


def dashboard_page() -> rx.Component:
    hero = rx.vstack(
        rx.heading("Validex", size="9", color=PRIMARY, font_weight="800", font_size=["4rem", "5rem", "6rem"], letter_spacing="-0.02em"),
        rx.text(
            "Accurate Demographic & Biometric Validation",
            font_size=["1.2rem", "1.5rem", "1.8rem"],
            color="#4B5563",
            text_align="center",
        ),
        rx.button(
            rx.hstack(
                rx.text("Continue to Validate", font_weight="600"),
                rx.icon("arrow-right", size=20),
                spacing="2",
                align="center",
            ),
            on_click=AppState.open_validation_modal,
            size="4",
            background=PRIMARY,
            color="white",
            border_radius="8px",
            padding_x="2rem",
            padding_y="1.8rem",
            margin_top="1.5rem",
            box_shadow="0 10px 25px rgba(20, 28, 50, 0.2)",
        ),
        spacing="5",
        align="center",
        justify="center",
        width="100%",
        min_height="70vh",
        position="relative",
    )
    footer = rx.hstack(
        rx.hstack(
            rx.text("Validex", color=PRIMARY, font_weight="800"),
            rx.text("/", color="#D1D5DB", margin_x="0.2rem"),
            rx.text("Validation Engine v4.0.2", color=MUTED, font_weight="500"),
            align="center",
        ),
        rx.spacer(),
        rx.hstack(
            rx.icon("globe", color=MUTED, size=20),
            rx.icon("circle-help", color=MUTED, size=20),
            spacing="4",
            align="center",
        ),
        align="center",
        width="100%",
        padding_top="2rem",
        border_top="1px solid rgba(15, 23, 42, 0.06)",
        margin_top="auto",
    )
    content = rx.vstack(hero, rx.spacer(), footer, validation_modal(), spacing="0", width="100%", flex="1")
    return shell(content, "Dashboard")


def manual_entry_card() -> rx.Component:
    input_style = {
        "variant": "surface",
        "background": "transparent",
        "border_bottom": "2px solid #E5E7EB",
        "border_radius": "2",
        "padding_x": "0",
        "color": PRIMARY,
        "font_weight": "600",
        "font_size": "1.05rem",
        "_focus": {
            "border_bottom": f"2px solid {PRIMARY}",
            "outline": "none"
        }
    }
    return surface_card(
        rx.vstack(
            rx.hstack(
                rx.vstack(
                    rx.heading("Manual Entry", size="7", color=PRIMARY, font_weight="800"),
                    rx.text("Input individual identity parameters for atomic validation.", color=MUTED, font_size="0.95rem"),
                    align="start",
                    spacing="1",
                ),
                rx.spacer(),
                rx.hstack(
                    rx.box(width="6px", height="6px", background="#FDBA4D", border_radius="50%"),
                    rx.text("LIVE SYNC", font_size="0.65rem", font_weight="700", letter_spacing="0.1em", color=PRIMARY),
                    background="#F4F5F7",
                    padding_x="0.8rem",
                    padding_y="0.3rem",
                    border_radius="full",
                    align="center",
                    spacing="2",
                ),
                align="center",
                width="100%",
                padding_bottom="1rem",
            ),
            rx.grid(
                rx.vstack(
                    rx.text("FIRST NAME", font_size="0.75rem", font_weight="700", letter_spacing="0.05em", color=PRIMARY),
                    rx.input(placeholder="John", value=AppState.first_name, on_change=AppState.set_first_name, size="3", **input_style),
                    align="start", spacing="2", width="100%"),
                rx.vstack(
                    rx.text("LAST NAME", font_size="0.75rem", font_weight="700", letter_spacing="0.05em", color=PRIMARY),
                    rx.input(placeholder="Doe", value=AppState.last_name, on_change=AppState.set_last_name, size="3", **input_style),
                    align="start", spacing="2", width="100%"),
                rx.vstack(
                    rx.text("DATE OF BIRTH", font_size="0.75rem", font_weight="700", letter_spacing="0.05em", color=PRIMARY),
                    rx.input(type="date", max="9999-12-31", value=AppState.date_of_birth, on_change=AppState.set_date_of_birth, size="3", **input_style),
                    align="start", spacing="2", width="100%"),
                rx.vstack(
                    rx.text("AGE", font_size="0.75rem", font_weight="700", letter_spacing="0.05em", color=PRIMARY),
                    rx.input(placeholder="32", value=AppState.age, on_change=AppState.set_age, size="3", **input_style),
                    align="start", spacing="2", width="100%"),
                rx.vstack(
                    rx.text("PHONE", font_size="0.75rem", font_weight="700", letter_spacing="0.05em", color=PRIMARY),
                    rx.input(placeholder="+1 (555) 000-0000", value=AppState.phone, on_change=AppState.set_phone, size="3", **input_style),
                    align="start", spacing="2", width="100%"),
                rx.vstack(
                    rx.text("EMAIL ADDRESS", font_size="0.75rem", font_weight="700", letter_spacing="0.05em", color=PRIMARY),
                    rx.input(placeholder="john.doe@gmail.com", value=AppState.email, on_change=AppState.set_email, size="3", **input_style),
                    align="start", spacing="2", width="100%"),
                columns="3",
                spacing="5",
                width="100%",
                padding_bottom="1.5rem",
            ),
            rx.hstack(
                rx.spacer(),
                rx.button(
                    "Clear",
                    on_click=AppState.reset_manual_form,
                    # variant="soft",
                    # color_scheme="red",
                    color=PRIMARY,
                    background="#E5E7EB",
                    border_radius="8px",
                    padding_x="1.5rem",
                    padding_y="1.5rem",
                    font_weight="600"
                ),
                rx.button(
                    "Validate Identity",
                    on_click=AppState.validate_manual_entry,
                    background=PRIMARY,
                    color="white",
                    border_radius="8px",
                    padding_x="2rem",
                    padding_y="1.5rem",
                    font_weight="600"
                ),
                spacing="3",
                width="100%"
            ),
            width="100%",
            spacing="4"
        ),
        width="100%",
        padding="2.5rem"
    )


def batch_upload_card() -> rx.Component:
    return rx.box(
        rx.vstack(
            rx.center(
                rx.icon("file-up", size=20, color="white"),
                background=PRIMARY,
                width="48px",
                height="48px",
                border_radius="12px",
                margin_bottom="0.5rem"
            ),
            rx.heading("Batch Processing", size="6", color=PRIMARY, font_weight="800"),
            rx.text(
                "Drag and drop CSV or XLS files to validate bulk datasets.",
                color=MUTED,
                text_align="center",
                font_size="0.95rem",
                max_width="220px",
            ),
            rx.upload(
                rx.button(
                    "Browse Files",
                    background="#E5E7EB",
                    color=PRIMARY,
                    font_weight="600",
                    border_radius="8px",
                    padding_x="2rem",
                    padding_y="1.25rem",
                    margin_top="1rem",
                ),
                id=BATCH_UPLOAD_ID,
                accept={"text/csv": [".csv"]},
                max_files=1,
                border="none",
                padding="0",
                background="transparent",
            ),
            rx.text("MAX SIZE 25MB", font_size="0.65rem", font_weight="700", letter_spacing="0.1em", color=MUTED, margin_top="0.5rem"),
            rx.cond(AppState.batch_error != "", rx.text(AppState.batch_error, color="red", font_size="0.8rem", text_align="center"), rx.fragment()),
            rx.cond(rx.selected_files(BATCH_UPLOAD_ID).length() > 0, rx.button("Process", on_click=AppState.handle_batch_upload(rx.upload_files(upload_id=BATCH_UPLOAD_ID)), size="2"), rx.fragment()),
            spacing="3",
            align="center",
            justify="center",
            width="100%",
            height="100%",
        ),
        width="100%",
        height="100%",
        border="2px dashed rgba(15, 23, 42, 0.15)",
        border_radius="20px",
        background="#F4F5F7",
        padding="2.5rem",
    )


def rules_configuration_modal() -> rx.Component:
    def rule_item(rule: dict) -> rx.Component:
        return rx.hstack(
            rx.vstack(
                rx.text(rule["name"], font_weight="700", color=PRIMARY, font_size="0.95rem"),
                rx.text(rule["description"], color=MUTED, font_size="0.85rem"),
                spacing="1",
                align="start",
            ),
            rx.spacer(),
            rx.switch(
                checked=rule["enabled"], 
                on_change=lambda val: AppState.toggle_rule(rule["id"], val),
                color_scheme="amber",
                cursor="pointer",
            ),
            width="100%",
            align="center",
            padding_y="1rem",
            border_bottom="1px solid #E5E7EB",
        )

    return rx.dialog.root(
        rx.dialog.content(
            rx.vstack(
                rx.hstack(
                    rx.heading("Configure Validation Rules", size="6", color=PRIMARY, font_weight="800"),
                    rx.spacer(),
                    rx.dialog.close(
                        rx.icon("x", color=MUTED, cursor="pointer"),
                    ),
                    width="100%",
                    align="center",
                ),
                rx.text("Toggle demographic validation logic constraints.", color=MUTED, font_size="0.9rem", margin_bottom="1rem"),
                
                rx.vstack(
                    rx.foreach(AppState.active_rules, rule_item),
                    width="100%",
                    spacing="0",
                    border_top="1px solid #E5E7EB",
                ),
                
                rx.hstack(
                    rx.button(
                        "Reset to Defaults",
                        on_click=AppState.reset_rules,
                        background="transparent",
                        color=MUTED,
                        cursor="pointer",
                        variant="ghost",
                        _hover={"color": PRIMARY},
                    ),
                    rx.spacer(),
                    rx.dialog.close(
                        rx.button("Save & Apply", background=PRIMARY, color="white", cursor="pointer"),
                    ),
                    width="100%",
                    margin_top="1.5rem",
                    align="center",
                ),
                width="100%",
                align="start",
            ),
            max_width="600px",
            border_radius="24px",
            padding="2rem",
            background="#F3F4F6",
        ),
        open=AppState.rules_modal_open,
        on_open_change=AppState.set_rules_modal_open,
    )


def demographics_page() -> rx.Component:
    summary_grid = rx.grid(
        score_panel(
            "Validation Score",
            AppState.manual_score,
            AppState.manual_band,
            AppState.manual_summary,
        ),
        surface_card(
            rx.vstack(
                rx.hstack(
                    rx.heading("Field-Level Inspection", size="6", color=PRIMARY, font_weight="800"),
                    rx.spacer(),
                    rx.icon("list-filter", size=20, color=PRIMARY),
                    rx.cond(
                        AppState.has_manual_result,
                        rx.select(["All", "Pass", "Warning", "Fail", "Review"], value=AppState.manual_filter, on_change=AppState.set_manual_filter, variant="surface", size="2", width="120px", color_scheme="gray"),
                        rx.fragment()
                    ),
                    rx.cond(
                        AppState.has_manual_duplicate,
                        status_badge("Warning"),
                        rx.fragment(),
                    ),
                    width="100%",
                ),
                rx.cond(
                    AppState.has_manual_result,
                    rx.box(
                        rx.table.root(
                            table_header(["PARAMETER", "ENTERED VALUE", "STATUS", "DIAGNOSTIC LOG"]),
                            rx.table.body(
                                rx.foreach(AppState.filtered_manual_results_rows, manual_row),
                            ),
                            variant="ghost",
                            size="3",
                            width="100%",
                        ),
                        width="100%",
                        max_height="350px",
                        overflow_y="auto",
                        padding_right="0.5rem",
                    ),
                    rx.center(
                        rx.vstack(
                            rx.box(
                                rx.icon("file-search", size=32, color=PRIMARY),
                                background="#F3F5FA",
                                padding="1rem",
                                border_radius="16px",
                                margin_bottom="0.5rem"
                            ),
                            rx.text("No data to inspect", font_weight="700", color=PRIMARY, font_size="1.1rem"),
                            rx.text(
                                "Submit a manual entry to view a detailed breakdown of identity parameters, field verification statuses, and rule-based diagnostic logs.",
                                color=MUTED,
                                text_align="center",
                                font_size="0.95rem",
                                max_width="400px"
                            ),
                            align="center",
                            spacing="2",
                        ),
                        width="100%",
                        min_height="350px",
                        border="2px dashed rgba(20, 28, 50, 0.08)",
                        border_radius="16px",
                        background="#FAFAFC"
                    ),
                ),
                rx.cond(
                    AppState.has_manual_duplicate,
                    rx.callout(
                        rx.vstack(
                            rx.text("Potential duplicate detected.", font_weight="700"),
                            rx.text(
                                AppState.manual_duplicate_match["matched_name"],
                                color=MUTED,
                            ),
                            rx.text(
                                AppState.manual_duplicate_match["matched_date_of_birth"],
                                color=MUTED,
                            ),
                            spacing="1",
                            align="start",
                        ),
                        color_scheme="amber",
                        width="100%",
                    ),
                    rx.fragment(),
                ),
                spacing="4",
                width="100%",
                align="start",
            ),
            padding="2.5rem"
        ),
        grid_template_columns=["1fr", "1fr", "1fr 2fr"],
        spacing="5",
        width="100%",
    )

    batch_results = rx.cond(
        AppState.has_batch_results,
        surface_card(
            rx.vstack(
                rx.hstack(
                    rx.heading("Batch Results", size="7", color=PRIMARY),
                    rx.spacer(),
                    rx.button(
                        "Export CSV",
                        variant="soft",
                        color_scheme="gray",
                        on_click=AppState.export_batch_results,
                    ),
                    width="100%",
                ),
                rx.grid(
                    rx.box(
                        rx.text("Total Records", color=MUTED, font_size="0.85rem", font_weight="600"),
                        rx.heading(AppState.batch_summary["total_records"], size="7", color=PRIMARY),
                        padding="1.25rem",
                        background="white",
                        border="1.5px solid rgba(15, 23, 42, 0.12)",
                        border_radius="16px",
                    ),
                    rx.box(
                        rx.text("Passed", color=MUTED, font_size="0.85rem", font_weight="600"),
                        rx.heading(AppState.batch_summary["passed_records"], size="7", color=PRIMARY),
                        padding="1.25rem",
                        background="white",
                        border="1.5px solid rgba(15, 23, 42, 0.12)",
                        border_radius="16px",
                    ),
                    rx.box(
                        rx.text("Warnings", color=MUTED, font_size="0.85rem", font_weight="600"),
                        rx.heading(AppState.batch_summary["warning_records"], size="7", color=PRIMARY),
                        padding="1.25rem",
                        background="white",
                        border="1.5px solid rgba(15, 23, 42, 0.12)",
                        border_radius="16px",
                    ),
                    rx.box(
                        rx.text("Average Score", color=MUTED, font_size="0.85rem", font_weight="600"),
                        rx.heading(AppState.batch_summary["average_validation_score"], size="7", color=PRIMARY),
                        padding="1.25rem",
                        background="white",
                        border="1.5px solid rgba(15, 23, 42, 0.12)",
                        border_radius="16px",
                    ),
                    columns="4",
                    spacing="3",
                    width="100%",
                ),
                rx.table.root(
                    table_header(["Row", "Record", "Status", "Score", "Issues", "Duplicate"]),
                    rx.table.body(
                        rx.foreach(AppState.paginated_batch_rows, batch_row),
                    ),
                    variant="surface",
                    size="3",
                    width="100%",
                ),
                rx.hstack(
                    rx.button("Previous", on_click=AppState.previous_batch_page, variant="soft"),
                    rx.text(
                        AppState.batch_page,
                        color=PRIMARY,
                        font_weight="600",
                    ),
                    rx.text("/", color=MUTED),
                    rx.text(AppState.batch_total_pages, color=MUTED),
                    rx.button("Next", on_click=AppState.next_batch_page, variant="soft"),
                    spacing="3",
                    justify="end",
                    width="100%",
                ),
                spacing="5",
                width="100%",
                align="start",
            ),
            width="100%",
        ),
        rx.fragment(),
    )

    content = rx.vstack(
        rx.hstack(
            rx.vstack(
                rx.heading("Demographic Validation", size="9", color=PRIMARY),
                rx.text(
                    "Validate individual records or uploaded CSV files against rule-based demographic checks.",
                    color=MUTED,
                ),
                spacing="1",
                align="start",
            ),
            rx.spacer(),
            rx.button(
                "Configure Rules",
                on_click=AppState.open_rules_modal,
                background=PRIMARY,
                color="white",
            ),
            width="100%",
            align="center",
        ),
        rx.grid(
            manual_entry_card(),
            batch_upload_card(),
            grid_template_columns=["1fr", "1fr", "2fr 1fr"],
            spacing="5",
            width="100%",
            align_items="stretch",
        ),
        summary_grid,
        batch_results,
        rules_configuration_modal(),
        spacing="5",
        width="100%",
    )
    return shell(content, "Demographics")


def biometric_upload_card() -> rx.Component:
    supported_text = rx.cond(
        AppState.biometric_modality == "face",
        "Supported formats: RAW, TIFF, PNG, JPG (High Res Recommended)",
        "Supported formats: PNG, WSQ (500 PPI recommended)",
    )
    return surface_card(
        rx.vstack(
            rx.hstack(
                rx.icon("cloud-upload", size=24, color=PRIMARY),
                rx.heading("Source Asset Ingestion", size="6", color=PRIMARY, font_weight="800"),
                align="center",
                spacing="3",
            ),
            rx.upload(
                rx.vstack(
                    rx.center(
                        rx.icon("upload", size=24, color=PRIMARY),
                        width="64px",
                        height="64px",
                        background="#EDF0F7",
                        border_radius="16px",
                        margin_bottom="1rem",
                    ),
                    rx.text("Drag and drop biometric samples", color=PRIMARY, font_weight="600"),
                    rx.text(supported_text, color=MUTED, text_align="center", font_size="0.85rem"),
                    rx.button(
                        "Select Local Files",
                        background="#141C32",
                        color="white",
                        border_radius="8px",
                        padding_x="2rem",
                        margin_top="1.5rem",
                        font_weight="600"
                    ),
                    spacing="2",
                    align="center",
                    width="100%",
                ),
                id=BIOMETRIC_UPLOAD_ID,
                accept=AppState.biometric_accept_map,
                width="100%",
                border="2px dashed rgba(20, 28, 50, 0.14)",
                border_radius="24px",
                padding="3rem",
                margin_top="1.5rem",
            ),
            rx.cond(
                rx.selected_files(BIOMETRIC_UPLOAD_ID).length() > 0,
                rx.button(
                    "Analyze Sample",
                    on_click=AppState.handle_biometric_upload(rx.upload_files(upload_id=BIOMETRIC_UPLOAD_ID)),
                    background=PRIMARY,
                    color="white",
                    width="100%",
                    padding_y="1.5rem",
                    border_radius="12px",
                    font_weight="800",
                    margin_top="1rem",
                ),
                rx.fragment(),
            ),
            rx.cond(
                AppState.biometric_error != "",
                rx.callout(AppState.biometric_error, color_scheme="tomato", width="100%"),
                rx.fragment(),
            ),
            # rx.hstack(
            #     rx.icon("info", size=20, color=PRIMARY),
            #     rx.text("Ensure lighting is uniform and the subject occupies at least 70% of the frame for optimal ", rx.text("OpenBQ", font_weight="800"), " scoring.", font_size="0.8rem", color=PRIMARY),
            #     background="#F4F5F7",
            #     padding="1rem",
            #     border_radius="12px",
            #     width="100%",
            #     margin_top="1rem",
            #     align="center",
            #     spacing="3",
            # ),
            align="start",
            width="100%",
            spacing="1",
        ),
        width="100%",
    )


def biometric_preview_panel() -> rx.Component:
    def image_box(img_name: str) -> rx.Component:
        return rx.image(
            src=rx.get_upload_url(img_name),
            width="100%",
            height="180px",
            object_fit="cover",
            border_radius="16px",
        )
        
    def local_file_placeholder(name: str) -> rx.Component:
        return rx.center(
            rx.vstack(
                rx.icon("image", size=32, color=PRIMARY),
                rx.text(name, font_size="0.75rem", font_weight="600", color=PRIMARY, text_align="center", max_width="90%", overflow="hidden", text_overflow="ellipsis", white_space="nowrap"),
                align="center", justify="center", spacing="2",
                width="100%",
            ),
            width="100%", height="180px", background="#F4F5F7", border_radius="16px",
        )

    empty_box = rx.center(
        rx.icon("plus", size=32, color=MUTED),
        width="100%",
        height="180px",
        border="2px dashed rgba(20, 28, 50, 0.14)",
        border_radius="16px",
    )

    selected = rx.selected_files(BIOMETRIC_UPLOAD_ID)
    history = AppState.biometric_preview_history

    staging_view = rx.grid(
        rx.foreach(selected, local_file_placeholder),
        rx.cond(selected.length() < 3, empty_box, rx.fragment()),
        rx.cond(selected.length() < 2, empty_box, rx.fragment()),
        rx.cond(selected.length() < 1, empty_box, rx.fragment()),
        columns="3",
        spacing="4",
        width="100%",
        padding_top="1rem",
    )

    history_view = rx.grid(
        rx.foreach(history, image_box),
        rx.cond(history.length() < 3, empty_box, rx.fragment()),
        rx.cond(history.length() < 2, empty_box, rx.fragment()),
        rx.cond(history.length() < 1, empty_box, rx.fragment()),
        columns="3",
        spacing="4",
        width="100%",
        padding_top="1rem",
    )

    return rx.box(
        rx.cond(
            selected.length() > 0,
            staging_view,
            history_view
        ),
        width="100%"
    )


def biometrics_page() -> rx.Component:
    result_panel = surface_card(
        rx.vstack(
            rx.hstack(
                rx.text("VALIDATION OUTPUT", color=MUTED, font_size="0.75rem", font_weight="800", letter_spacing="0.1em"),
                rx.spacer(),
                rx.badge(AppState.biometric_badge_text, color_scheme=AppState.biometric_badge_color, variant="surface", padding_x="0.8rem", padding_y="0.3rem", font_weight="800", border_radius="full"),
                width="100%",
                align="center",
            ),
            rx.heading("Analysis Report", size="7", color=PRIMARY, font_weight="800"),
            rx.cond(
                AppState.biometric_image_filename != "",
                rx.text(
                    AppState.biometric_image_filename,
                    color=MUTED,
                    font_size="0.85rem",
                    font_weight="600",
                ),
                rx.fragment(),
            ),
            rx.cond(
                (AppState.biometric_modality == "face") & AppState.has_biometric_result,
                rx.badge(
                    AppState.biometric_face_badge_text,
                    color_scheme=AppState.biometric_face_badge_color,
                    variant="surface",
                    padding_x="0.95rem",
                    padding_y="0.4rem",
                    font_weight="800",
                    border_radius="full",
                ),
                rx.fragment(),
            ),
            rx.center(
                rx.center(
                    rx.center(
                        rx.vstack(
                            rx.text(AppState.biometric_score, font_size="2.5rem", font_weight="800", color=PRIMARY, line_height="1"),
                            rx.text("OpenBQ", font_size="0.75rem", font_weight="700", color=PRIMARY, letter_spacing="0.05em"),
                            spacing="1",
                            align="center",
                            margin_top="0.5rem"
                        ),
                        width="120px",
                        height="120px",
                        background="white",
                        border_radius="50%",
                    ),
                    width="144px",
                    height="144px",
                    background=AppState.biometric_ring_bg,
                    border_radius="50%",
                    margin_y="1.5rem",
                ),
                width="100%",
            ),
            rx.center(
                rx.text(AppState.biometric_status_message, font_weight="700", color=PRIMARY),
                width="100%",
            ),
            rx.cond(
                AppState.has_biometric_result,
                rx.vstack(
                    rx.divider(margin_y="1.5rem"),
                    rx.vstack(
                        rx.foreach(AppState.biometric_metric_rows, metric_row),
                        width="100%",
                        spacing="0",
                    ),
                    rx.cond(
                        AppState.biometric_status == "Review",
                        rx.callout(
                            rx.vstack(
                                rx.hstack(
                                    rx.icon("triangle-alert", size=16, color="#B45309"),
                                    rx.text("Manual Review Advised", font_weight="700", color="#B45309", font_size="0.85rem"),
                                ),
                                rx.text("One or more properties fall just below the optimal thresholds. While viable, secondary validation may be required.", color="#B45309", font_size="0.75rem", line_height="1.5"),
                                align="start",
                                spacing="1"
                            ),
                            background="#FEEBC8",
                            width="100%",
                            padding="1rem",
                            border_radius="8px",
                            margin_y="1rem",
                        ),
                        rx.fragment()
                    ),
                    rx.vstack(
                        rx.foreach(
                            AppState.biometric_issues,
                            lambda issue: rx.callout(
                                issue,
                                color_scheme=rx.cond(
                                    AppState.biometric_face_detected,
                                    "amber",
                                    "tomato",
                                ),
                                width="100%",
                            ),
                        ),
                        width="100%",
                        spacing="2",
                    ),
                    width="100%",
                ),
                rx.fragment()
            ),
            # Buttons removed per request
            align="start",
        ),
        width="100%",
        min_height=rx.cond(AppState.has_biometric_result, "auto", "410px"),
        border_left="6px solid #10B981",
        border_top_left_radius="4px",
        border_bottom_left_radius="4px",
        align_self="start",
    )

    # Live Network Pulse pill removed per request

    content = rx.vstack(
        rx.hstack(
            rx.vstack(
                rx.heading("Biometric Validation", size="9", color=PRIMARY),
                rx.text(
                    "Analyze and verify individual identity markers with precision curation.",
                    color=MUTED,
                ),
                spacing="1",
                align="start",
            ),
            rx.spacer(),
            rx.hstack(
                rx.button(
                    rx.hstack(rx.icon("scan-face", size=16), rx.text("FACE"), align="center", spacing="2"),
                    on_click=AppState.set_biometric_mode("face"),
                    background=rx.cond(AppState.biometric_modality == "face", "#F3F5FA", "transparent"),
                    color=rx.cond(AppState.biometric_modality == "face", PRIMARY, MUTED),
                    border=rx.cond(AppState.biometric_modality == "face", f"1px solid {PRIMARY}", "1px solid transparent"),
                    border_radius="12px",
                    padding_x="1.2rem",
                ),
                rx.button(
                    rx.hstack(rx.icon("fingerprint", size=16), rx.text("FINGERPRINT"), align="center", spacing="2"),
                    on_click=AppState.set_biometric_mode("fingerprint"),
                    background=rx.cond(AppState.biometric_modality == "fingerprint", "#F3F5FA", "transparent"),
                    color=rx.cond(AppState.biometric_modality == "fingerprint", PRIMARY, MUTED),
                    border=rx.cond(AppState.biometric_modality == "fingerprint", f"1px solid {PRIMARY}", "1px solid transparent"),
                    border_radius="12px",
                    padding_x="1.2rem",
                ),
                border="1px solid #E5E7EB",
                border_radius="16px",
                padding="0.25rem",
                spacing="2",
            ),
            width="100%",
            align="center",
        ),
        rx.grid(
            rx.vstack(
                biometric_upload_card(),
                biometric_preview_panel(),
                spacing="4",
                width="100%",
            ),
            result_panel,
            grid_template_columns=["1fr", "1fr", "3fr 2fr"],
            spacing="5",
            width="100%",
        ),
        spacing="5",
        width="100%",
    )
    return shell(content, "Biometrics")


global_style = {
    "::-webkit-scrollbar": {
        "width": "6px",
        "height": "6px",
    },
    "::-webkit-scrollbar-track": {
        "background": "transparent",
    },
    "::-webkit-scrollbar-thumb": {
        "background": "#CBD5E1",
        "border_radius": "10px",
    },
    "::-webkit-scrollbar-thumb:hover": {
        "background": "#94A3B8",
    },
}

app = rx.App(
    style=global_style,
    theme=rx.theme(
        accent_color="amber",
        gray_color="slate",
        radius="large",
        scaling="100%",
    ),
)
app.add_page(dashboard_page, route="/", title="Validex")
app.add_page(demographics_page, route="/demographics", title="Validex | Demographics")
app.add_page(biometrics_page, route="/biometrics", title="Validex | Biometrics")
