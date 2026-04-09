from __future__ import annotations

import unittest
from datetime import date
from pathlib import Path
from tempfile import TemporaryDirectory

from PIL import Image

from Validex.models import ManualDemographicInput
from Validex.services import (
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


if __name__ == "__main__":
    unittest.main()
