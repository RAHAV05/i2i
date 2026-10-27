# Doc Scanner — Mobile-First Hybrid Clinic Platform

A document-scanning mobile-first hybrid application designed for clinic health-camp workflows. It pairs two-page paper health intake forms (Page 1: Patient Details & Vitals; Page 2: Clinical Examination & Referral), extracts structured medical data across 8 clinical sections via Groq Vision AI, highlights missing or uncertain fields, and enables physician verification before persisting to a local SQLite database, viewing in the in-app spreadsheet viewer, and exporting to Microsoft Excel (.xlsx) or CSV.

---

## Key Features

1. **Mobile-First Hybrid Experience & Desktop Expand Frame**
   - Engineered specifically for handheld phone use during community camps, wrapped in a high-fidelity iPhone mockup frame when viewed on desktop monitors.
   - Includes an **Expand View** toggle allowing physicians to expand the UI across their entire screen.
   - Laser scan splash animation, physician sign-in, and circular conic camp progress meter.

2. **Real Two-Page Document Capture & Calibration**
   - Live camera viewfinder using HTML5 `getUserMedia` with yellow corner alignment brackets and laser scanning overlay.
   - Real frame freezing onto canvas and conversion to high-resolution JPEG blobs.
   - Upload photo option (`input[type=file]`) and camera flip support.
   - Built-in "Load 2-Page Sample Form" helper for instant demonstration without physical paper forms or webcam permissions.
   - "Check the Pair" screen to verify Page 1 and Page 2 belong to the same patient before AI extraction.

3. **8-Group Clinical Extraction with Vision AI**
   - Transcribes all 8 comprehensive sections: Personal Info, Medical History, Allergies, Family History, General Health Assessment, Systemic Examination, Vision & Dental, and Referral & Sign.
   - Server-side schema validation and sanitization using Groq Vision (`llama-3.2-11b-vision-preview` or `qwen/qwen3.8-27b`).
   - Flags ambiguous handwriting in `uncertain_fields` and blank inputs in `missing_fields`.
   - Graceful manual fallback: if unconfigured, enables full manual entry without blocking clinic staff.

4. **Human-in-the-Loop Physician Verification**
   - 7-tab categorization for streamlined physician review of all 95+ clinical fields.
   - Scanned document drawer showing original captured paper pages for side-by-side comparison.
   - Explicit "Approve & Save to Database" action required before a record receives the `reviewed` status.
   - Idempotent duplicate prevention and real-time aggregate stats.

5. **Comprehensive Microsoft Excel Export (.xlsx) & In-App Viewer**
   - Generates native multi-sheet Microsoft Excel workbooks (`clinic_records_all.xlsx` and `camp_<id>_records.xlsx`) styled with deep teal headers, auto column widths, and frozen panes.
   - **Sheet 1 (Patient Records)**: All 95+ clinical fields across all 8 form sections with calculated BMI, vitals, medical history, systemic exams, vision, dental, and doctor notes.
   - **Sheet 2 (Camp Summary)**: Camp targets, scanned counts, to check, reviewed counts, and completion percentages.
   - **Interactive In-App Spreadsheet Viewer**: View, search, and inspect the entire Excel spreadsheet directly inside the application, complete with sheet switching tabs, search filtering, and fullscreen overlay.
   - RFC 4180 compliant CSV export with formula injection sanitization.

---

## Project Structure

```
INK2Insights/
├── api/                     # Vercel serverless entry point (index.py)
├── backend/                 # Python Flask backend
│   ├── app.py               # REST endpoints & static file serving
│   ├── database.py          # SQLite persistence & query aggregations
│   ├── requirements.txt     # Python backend dependencies
│   ├── .env.example         # Template configuration
│   ├── .env                 # Local environment secrets
│   └── services/            # AI extraction, Groq, Gemini & Record services
├── docs/                    # Reference medical sheets & abbreviation dictionaries
├── fixtures/                # Sample clinic intake forms for testing & calibration
├── frontend/                # Web application UI
│   ├── index.html           # Accessible, semantic UI with responsive theme
│   ├── css/styles.css       # Design tokens & responsive styles
│   ├── js/                  # App orchestration, scanner, and API client
│   └── assets/              # Web assets (logos, heroes, loading animation)
├── mobile/                  # React Native / Expo mobile application
├── sample/                  # UI design benchmarks & reference media
├── tests/                   # Pytest test suite (62 comprehensive tests)
├── conftest.py              # Pytest environment setup
├── requirements.txt         # Project-level Python dependencies
├── vercel.json              # Vercel deployment configuration
├── supabase_schema.sql      # Supabase cloud database schema
├── .gitignore               # Ignored files, databases, and artifacts
└── README.md                # Project documentation
```

---

## Getting Started (macOS)

### 1. Prerequisites
- Python 3.10+
- Modern Web Browser (Safari, Chrome, Firefox, Edge)

### 2. Set Up Virtual Environment & Dependencies

From the project root:

```bash
# Create and activate virtual environment
python3 -m venv .venv
source .venv/bin/activate

# Install required packages
pip install -r backend/requirements.txt
```

### 3. Configure Environment Variables

Copy the example configuration file:

```bash
cp backend/.env.example backend/.env
```

Edit `backend/.env`:
```ini
PORT=5001
AI_PROVIDER=groq

# Optional: Add your Groq API key for automated vision extraction
# If left blank, the app will run in manual entry demo mode
GROQ_API_KEY=your_groq_api_key_here
GROQ_MODEL=llama-3.2-11b-vision-preview
```

### 4. Run the Automated Test Suite

```bash
.venv/bin/pytest tests -v
```

All 23 unit and integration tests will execute and verify database operations, CSV formula sanitization, schema validation, and API routes.

### 5. Launch the Application

```bash
.venv/bin/python backend/app.py
```

Open your browser and navigate to:
```
http://localhost:5001
```

The Flask backend serves both the REST endpoints and the responsive frontend application directly.

---

## AI Vision Provider Architecture

The AI layer is structured around an abstract service interface in [`backend/services/ai_service.py`](backend/services/ai_service.py):

```python
class AIService(ABC):
    @abstractmethod
    def extract_from_images(self, page1_bytes: bytes, page1_mime: str, page2_bytes: bytes, page2_mime: str) -> Dict[str, Any]:
        pass

    @abstractmethod
    def is_configured(self) -> bool:
        pass
```

### Integrating NVIDIA NIM in the Future
To add NVIDIA NIM:
1. Create `backend/services/nvidia_nim_service.py` implementing `AIService`.
2. Target the NVIDIA NIM endpoint (e.g., `https://integrate.api.nvidia.com/v1/chat/completions`) using OpenAI-compatible payload with base64 data URLs.
3. Validate the response with `validate_and_sanitize_extraction(raw_content)`.
4. Update `get_ai_service()` in `backend/app.py` to instantiate `NvidiaNimService` when `AI_PROVIDER=nvidia`.

---

## Security & Privacy Notice

- **Demo Purpose Only**: This software is an MVP for health-camp workflow demonstrations. It is not HIPAA/GDPR certified and should not be used with real patient data without clinical institutional review.
- **No Real Patient Seed Data**: All sample records and camps are explicitly labeled as synthetic demo records.
- **Zero Key Leakage**: API credentials remain strictly in the backend `.env` file and are never sent to the client.
- **Spreadsheet Formula Injection Protection**: All exported CSV cells are sanitized against spreadsheet formula execution (`=`, `+`, `-`, `@`, `\t`, `\r`).
- **No Long-term Image Retention**: Scanned images in this demo are processed in-memory or stored as temporary object URLs.
# i2i
