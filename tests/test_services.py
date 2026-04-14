from __future__ import annotations

import unittest
from datetime import date
from pathlib import Path
from tempfile import TemporaryDirectory

from PIL import Image

from Validex.models import ManualDemographicInput
from Validex.services import (
    _parse_openbq_csv,
    _parse_openbq_log,
    _selected_face_metrics,
    apply_duplicate_match,
    map_csv_headers,
    normalized_similarity,
    parse_csv_payload,
    prevalidate_biometric_file,
    validate_demographic_input,
)
from Validex.models import BiometricValidationRequest


class ValidationServiceTests(unittest.TestCase):
    def test_valid_demographic_input_scores_high(self) -> None:
        payload = ManualDemographicInput(
            first_name="John",
            last_name="Doe",
            date_of_birth="1992-05-10",
            age=str(max(0, date.today().year - 1992)),
            phone="+234 801 234 5678",
            email="john.doe@example.com",
        )

        result = validate_demographic_input(payload)

        self.assertGreaterEqual(result.validation_score, 80)
        self.assertEqual(result.score_band, "Excellent")

    def test_age_dob_mismatch_is_warning(self) -> None:
        payload = ManualDemographicInput(
            first_name="Amina",
            last_name="Ola",
            date_of_birth="1990-01-01",
            age="18",
            phone="+2348012345678",
            email="amina@example.com",
        )

        result = validate_demographic_input(payload)
        age_result = next(field for field in result.fields if field.field == "Age")

        self.assertEqual(age_result.status, "Warning")
        self.assertLess(age_result.field_score, 16.67)

    def test_csv_header_mapping_is_case_insensitive(self) -> None:
        headers = ["Age", "EMAIL_ADDRESS", "First Name", "Random"]

        mapped = map_csv_headers(headers)

        self.assertEqual(mapped["age"], "Age")
        self.assertEqual(mapped["email"], "EMAIL_ADDRESS")
        self.assertEqual(mapped["first_name"], "First Name")

    def test_parse_csv_payload_rejects_unknown_headers(self) -> None:
        with self.assertRaises(ValueError):
            parse_csv_payload(b"username,identifier\njohn,123\n")

    def test_duplicate_detection_flags_near_match(self) -> None:
        payload = ManualDemographicInput(
            first_name="Jon",
            last_name="Smith",
            date_of_birth="1992-05-10",
            age="32",
            phone="+2348012345678",
            email="jon@example.com",
        )
        result = validate_demographic_input(payload)
        result = apply_duplicate_match(
            payload,
            result,
            [
                {
                    "id": 4,
                    "first_name": "John",
                    "last_name": "Smith",
                    "date_of_birth": "1992-05-10",
                }
            ],
        )

        self.assertIsNotNone(result.duplicate_match)
        self.assertGreaterEqual(result.duplicate_match.similarity_score, 85)

    def test_similarity_is_normalized(self) -> None:
        self.assertEqual(normalized_similarity("same", "same"), 1.0)
        self.assertLess(normalized_similarity("john", "joan"), 1.0)

    def test_biometric_prevalidation_reads_png_metadata(self) -> None:
        with TemporaryDirectory() as temp_dir:
            image_path = Path(temp_dir) / "finger.png"
            image = Image.new("L", (512, 512), color=255)
            image.save(image_path, dpi=(500, 500))

            issues, metadata = prevalidate_biometric_file(
                BiometricValidationRequest(modality="fingerprint", source_filename="finger.png"),
                image_path,
            )

        self.assertIn(metadata["dpi"], {"499", "500"})
        self.assertNotIn(
            "Fingerprint DPI metadata is unavailable; OpenBQ results may require review.",
            issues,
        )

    def test_openbq_csv_parser_selects_matching_file_row(self) -> None:
        with TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            csv_path = temp_path / "output_face.csv"
            csv_path.write_text(
                "file,image_height,image_width,face_detection,brightness,sharpness,contrast,dynamic_range,file_size_bytes,image_format,image_mode,face_ratio\n"
                ".\\uploaded_files\\other.png,512,512,0.12,140,50,60,70,1000,PNG,RGBA,\n"
                ".\\uploaded_files\\target.png,512,512,0.82,174.36,236.05,92.05,115.17,139152,PNG,RGBA,0.02854\n",
                encoding="utf-8",
            )

            selected = _parse_openbq_csv(temp_path, Path("target.png"))

        self.assertEqual(selected["file"], ".\\uploaded_files\\target.png")
        self.assertEqual(selected["face_detection"], "0.82")

    def test_openbq_log_parser_filters_to_matching_file(self) -> None:
        with TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            log_path = temp_path / "log_face.json"
            log_path.write_text(
                '{"log": ['
                '{"face detection": "no face found", "file": "uploaded_files/other.png"},'
                '{"face mesh": "failed to extract face mesh", "file": "uploaded_files/target.png"},'
                '{"Head pose estimation": "missing landmarks", "file": "uploaded_files/target.png"}'
                ']}',
                encoding="utf-8",
            )

            diagnostics = _parse_openbq_log(temp_path, Path("target.png"))

        self.assertEqual(
            diagnostics,
            [
                "face mesh: failed to extract face mesh",
                "Head pose estimation: missing landmarks",
            ],
        )

    def test_selected_face_metrics_include_categories_and_status(self) -> None:
        metrics = _selected_face_metrics(
            {
                "image_height": "512",
                "image_width": "512",
                "face_detection": "0.82",
                "smile": "0",
                "brightness": "174.36",
                "dynamic_range": "115.17",
                "sharpness": "236.05",
                "contrast": "92.05",
                "file_size_bytes": "139152",
                "image_format": "PNG",
                "image_mode": "RGBA",
                "face_ratio": "0.02854",
                "glasses": "0",
            }
        )

        labels = {row["label"] for row in metrics if row["row_type"] == "metric"}
        categories = {row["category"] for row in metrics}

        self.assertIn("Brightness", labels)
        self.assertIn("Detection Confidence", labels)
        self.assertIn("Resolution", labels)
        self.assertIn("Image Quality", categories)
        self.assertIn("Face Detection", categories)
        self.assertTrue(all("status" in row for row in metrics))


if __name__ == "__main__":
    unittest.main()
