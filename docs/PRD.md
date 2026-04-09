# Validex v1 PRD and Build Plan

## Summary
Validex v1 is a Reflex + SQLite web app for validating demographic and biometric data with a minimal, polished multi-page flow based on the attached mockups.

The app will ship with three pages only:
- `Dashboard` (`/`) as the landing page
- `Demographics` (`/demographics`)
- `Biometrics` (`/biometrics`)

The UI should preserve the overall visual language of the screenshots:
- same clean hero landing page
- same validation-init modal concept
- same card-based demographics and biometrics layouts

The following mockup elements are explicitly removed from v1:
- left sidebar
- profile avatar
- notifications/help/settings chrome
- `Logs` page
- non-essential admin/product controls

## Implementation Changes

### 1. App structure and navigation
- Build Reflex routes for `/`, `/demographics`, and `/biometrics`.
- Use a top navigation only, with `Dashboard`, `Demographics`, and `Biometrics`.
- `Continue to Validate` on the landing page opens a modal with two cards:
  - `Demographic Validation`
  - `Biometric Validation`
- Selecting a card routes to the matching page.
- Keep the page count minimal; no extra flows, auth, or settings.

### 2. Dashboard page
- Keep the landing page visually close to the first screenshot:
  - prominent `Validex` brand
  - short subtitle about demographic and biometric validation
  - one primary CTA: `Continue to Validate`
- Keep copy short; no long marketing sections.
- Footer can remain minimal and static, but remove fake enterprise links that do not map to real features.

### 3. Demographics page
- Keep the core layout from the mockup, but without the sidebar.
- Main content should contain:
  - manual-entry validation card
  - batch upload card
  - validation score card/gauge
  - field inspection/results table

Manual-entry fields:
- first name
- last name
- date of birth
- age
- phone number
- email

Validation rules:
- `first_name`, `last_name`
  - required for manual mode
  - 2 to 50 characters
  - no digits
  - allow letters, spaces, hyphen, apostrophe
- `date_of_birth`
  - parse accepted formats: `YYYY-MM-DD`, `DD/MM/YYYY`, `MM/DD/YYYY`
  - must not be future-dated
  - derived age must be `0-150`
- `age`
  - integer only
  - range `0-150`
  - if DOB exists, mismatch beyond `±1 year` becomes warning
- `phone`
  - allow formatting characters, but stripped digit count must be `7-15`
  - no alphabetic characters
- `email`
  - standard email regex
  - domain must contain at least one dot

Scoring model:
- equal weights across six fields: `16.67%` each
- status bands:
  - `90-100`: Excellent
  - `70-89`: Good
  - `50-69`: Fair
  - `<50`: Poor
- hard field failures score `0` for that field
- warning-level issues apply partial deductions
- age/DOB mismatch is a warning, not a full record fail

Results presentation:
- show an overall validation score card
- show a per-field table with columns:
  - `Field`
  - `Entered Value`
  - `Status`
  - `Issues`
  - `Field Score`
- use `Pass`, `Warning`, `Fail`, `Missing`
- add a short summary sentence under the score

Duplicate detection:
- run after every successful manual or batch validation
- compare against stored demographic records in SQLite
- use Levenshtein-based composite similarity on:
  - first name `35%`
  - last name `35%`
  - DOB `30%`
- default duplicate threshold: `85%`
- duplicate is a flagged warning, not a hard rejection
- show the closest matched record and similarity score

Batch upload:
- support `CSV only`
- read headers dynamically from row 1
- map recognized aliases case-insensitively after trimming spaces/underscores
- accepted aliases:
  - first name: `first name`, `firstname`, `first_name`
  - last name: `last name`, `lastname`, `last_name`
  - date of birth: `date of birth`, `dob`, `date_of_birth`
  - age: `age`
  - phone: `phone`, `phone number`, `phone_number`
  - email: `email`, `email address`, `email_address`
- ignore unrecognized columns
- fail upload if none of the recognized headers are present
- validate row-by-row using the same rules as manual mode

Batch results:
- show summary stats:
  - total rows
  - passed rows
  - warning rows
  - failed rows
  - average validation score
  - duplicate count
- show paginated results table
- allow export of the processed results as CSV with appended validation columns

### 4. Biometrics page
- Keep the core composition from the biometric mockup, but remove sidebar/search/profile/floating extras.
- Main content should contain:
  - modality toggle for `Face` and `Fingerprint`
  - upload card
  - preview area
  - result/report card

OpenBQ integration path:
- use a Reflex backend wrapper around the documented OpenBQ CLI for v1
- do not plan around an undocumented remote API contract
- store CLI execution output in a normalized internal result model before rendering

OpenBQ-supported inputs, based on official docs:
- face:
  - `JPG`, `JPEG`, `JP2`, `BMP`, `PNG`
- fingerprint:
  - `WSQ`, `PNG`
  - `500 PPI` minimum recommended; lower-resolution files may run but should be warned as potentially inaccurate

Biometric flow:
- user selects modality
- user uploads one file
- app performs local pre-validation:
  - extension/type allowed for modality
  - file present and readable
  - fingerprint DPI check where metadata is available
- if pre-validation fails, do not call OpenBQ
- if pre-validation passes, run OpenBQ CLI and capture output
- normalize result into:
  - modality
  - source filename
  - overall quality score
  - status (`Accepted`, `Review`, `Rejected`)
  - issue list
  - metrics dictionary
  - preview path

Biometric result display:
- show prominent quality score ring/card
- show status badge
- show 3-6 key metrics only, not the full raw payload
- face metrics should prioritize items like brightness, sharpness, face ratio, pose/offset
- fingerprint metrics should prioritize NFIQ2 score, image dimensions, uniformity/contrast flags
- show concise issue messages for operator action

Threshold defaults:
- face and fingerprint results should map to three bands:
  - `Accepted`
  - `Review`
  - `Rejected`
- exact mapping should be implemented through an internal threshold config object, seeded with sensible defaults and kept easy to tune in code

### 5. Data model and interfaces
Public app interfaces and state models to standardize:
- `ManualDemographicInput`
- `FieldValidationResult`
- `DemographicValidationResult`
- `BatchValidationSummary`
- `BatchValidationRowResult`
- `BiometricValidationRequest`
- `BiometricValidationResult`
- `DuplicateMatch`

SQLite tables:
- `demographic_records`
  - raw inputs
  - normalized values
  - validation score
  - per-field result JSON
  - duplicate flag
  - duplicate match metadata
  - source type (`manual` or filename)
  - created timestamp
- `biometric_records`
  - modality
  - filename
  - normalized output JSON
  - score
  - status
  - issue summary
  - created timestamp

Persistence rules:
- save every completed manual validation
- save every processed batch row
- save every biometric run
- demographic and biometric records remain separate in v1; no person-linking model yet

## Test Plan
- Landing page opens modal and routes correctly to both validation paths.
- Demographic manual validation handles valid inputs end-to-end.
- Name validation catches digits and illegal symbols.
- DOB parsing accepts only the three specified formats and rejects future dates.
- Age/DOB mismatch produces warning behavior and partial deduction.
- Phone and email validators catch malformed values.
- Overall score bands render correctly for excellent/good/fair/poor cases.
- Duplicate detection flags near matches at or above the `85%` threshold.
- CSV upload accepts recognized header aliases in any column order.
- CSV upload ignores unrelated columns and errors when no supported headers exist.
- Batch processing produces aggregate summary counts and row-level results.
- Face upload accepts only documented face file types.
- Fingerprint upload accepts only `WSQ` and `PNG`, with DPI warning handling.
- Biometrics flow blocks invalid files before OpenBQ execution.
- OpenBQ CLI output is parsed into stable UI cards for both face and fingerprint runs.
- SQLite persistence works for manual, batch, and biometric runs.

## Assumptions and Defaults
- v1 uses Reflex `0.8.28.post1` and SQLite only.
- No authentication, user accounts, or team/admin features are included.
- `Logs` is removed entirely from v1.
- The mockups are treated as layout/style references, not literal enterprise-dashboard chrome.
- CSV is the only demographic batch format in v1.
- Demographic scoring uses equal weights because that best matches your stated scoring intent.
- Duplicate detection is advisory, not blocking.
- Biometrics integration targets the OpenBQ CLI because the public docs clearly document modality inputs and CLI output, while the public API contract is not equally explicit.
- OpenBQ source references for implementation:
  - https://openbq.io/
  - https://docs.openbq.io/modalities/face.html
  - https://docs.openbq.io/modalities/fingerprint.html
