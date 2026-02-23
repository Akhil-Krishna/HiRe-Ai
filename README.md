# HirE.AI — Intelligent Interview Platform v2.0

AI-powered interview platform with real-time monitoring, speech recognition optimized for Indian English, live interviewer controls, recording, resume integration, and organisational RBAC.

---

## 🚀 Quick Start (5 minutes)

### Prerequisites
- Python 3.11.9 or +
- A free Groq API key: https://console.groq.com

### Setup

```bash
# 1. Clone / unzip the project
cd hireai

# 2. Create virtual environment
python -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate

# 3. Install dependencies
pip install -r requirements.txt

# 4. Configure
cp .env .env.local    # Edit .env and set your GROQ_API_KEY
# Minimum required: GROQ_API_KEY=gsk_...

# 5. Run
uvicorn main:app --reload --port 8000
```

Open http://localhost:8000

---

## 🔑 Demo Accounts

| Email | Password | Role |
|-------|----------|------|
| admin@demo.com | admin123 | Admin |
| hr@demo.com | hr123456 | HR Manager |
| interviewer@demo.com | int12345 | Interviewer |
| candidate@demo.com | can12345 | Candidate |

All accounts belong to **Demo Corp** organisation.

---

## 🏢 Organisational Structure

```
Organisation (e.g. "Acme Corp")
├── Admin        — full access, user management
├── HR Manager   — schedule interviews, see results, upload resumes
│   └── can only assign Interviewers from SAME org
├── Interviewer  — join live, pause AI, ask manual questions, see results
└── Candidate    — join interview via unique link, no metrics shown
```

---

## ✨ Features

### Interview Room (Candidate)
- **Voice-to-text** with Indian English support (`lang='en-IN'`) using Web Speech API
- **Code editor** with syntax highlighting — auto-opens for coding questions
- **Screen + webcam recording** — composite (screen + cam inset)
- **Tab switch detection** — flags raised instantly on tab change
- **Face monitoring** — flags for looking away, multiple faces
- **Alerts panel** — candidate sees integrity flags only (not confidence scores)

### Live Monitor (Interviewer / HR)
- **Real-time message polling** — new messages appear every 2 seconds without refresh
- **Pause AI** — take over and ask manual questions directly
- **Resume AI** — hand back control to the AI
- **Live metrics** — tab switches, message counts, session duration
- **Recording playback** — after interview ends

### Dashboard
- **Admin**: User management, all interviews, org management
- **HR**: Schedule, assign interviewers (same org only), upload resumes, see reports
- **Interviewer**: Assigned interviews, live watch, results
- **Candidate**: Upcoming interviews, join button, results

### AI Interviewer
- Uses Groq (free) / Ollama / OpenAI — configurable via `.env`
- **Question Bank**: HR uploads questions; AI uses them in order
- **Resume context**: AI tailors questions based on candidate's resume
- Fallback to scripted responses if API unavailable

---

## Configuration

```env
# .env

# LLM Provider (pick one)
LLM_PROVIDER=groq          # groq | ollama | openai | mock
GROQ_API_KEY=gsk_...       # Free: https://console.groq.com
GROQ_MODEL=llama-3.3-70b-versatile

# For local Ollama:
# LLM_PROVIDER=ollama
# OLLAMA_BASE_URL=http://localhost:11434
# OLLAMA_MODEL=llama3.2

# Vision (face analysis)
VISION_PROVIDER=deepface       # mock (no deps) | deepface (needs TF)

# Email
EMAIL_PROVIDER=log         # log (console) | smtp (real Gmail)
SMTP_USER=you@gmail.com
SMTP_PASSWORD=xxxx-xxxx-xxxx-xxxx   # Gmail App Password
```

---



## How to run an Interview

1. **HR logs in** → Schedules an interview
   - Enters candidate email (auto-creates candidate account + sends invite)
   - Optionally: uploads question bank (one per line or JSON)
   - Optionally: uploads candidate resume (PDF/TXT)
   - Assigns interviewers from org's interviewer pool

2. **Candidate** receives email with unique interview link --- issue fix needed

3. **Candidate joins** → Camera/mic check → Interview starts
   - AI conducts interview using question bank + resume context
   - Code editor for coding questions
   - Voice input with Indian English accent support

4. **Interviewer** can open `/watch/<token>` (or click Watch in dashboard)
   - Sees live messages auto-updating
   - Can pause AI and take over with manual questions
   - Resume AI when done

5. **Interview completes** → Scores calculated → Report visible in dashboard


---

## Indian English Speech Recognition

The platform uses the Web Speech API with `lang='en-IN'` which is supported by:
- Chrome (best support)
- Edge
- Safari (limited)

This handles Indian English accent patterns natively. No server-side STT needed — it runs entirely in the browser, zero latency.

For even better accuracy with Indian accents, you can integrate:
- **Whisper.cpp** (local, free) — run as a sidecar service
- **Sarvam AI** (Indian language specialist, free tier)

- **whisper with fine tuned on indian eng**

---

## Scaling

- Switch to PostgreSQL for production
- Add Redis for session caching
- Deploy behind Nginx for HTTPS + load balancing
- Add Celery for async email/report generation

## Note

Vision Service — Real-time Emotion Analysis + Cheating Detection
================================================================
Provider: DeepFace + OpenCV Haar cascades.

VISION_PROVIDER=deepface  → real ML analysis (TensorFlow, ~1.5GB auto-download)
VISION_PROVIDER=mock      → scripted scores, no ML deps needed

THREE CRITICAL PRODUCTION FIXES APPLIED IN THIS VERSION:

1. THREAD POOL OFFLOAD
   DeepFace.analyze() and all OpenCV work run in asyncio's default thread pool
   via loop.run_in_executor(). Without this, a 300ms DeepFace call blocks the
   entire event loop, freezing all other requests (chat, vision, API) for that
   duration. Fixed: _run_vision_sync() contains all blocking work; async wrapper
   calls it via run_in_executor.

2. CACHED HAAR CASCADE
   The original code called CascadeClassifier() on every frame — this reloads
   the XML file from disk each time (~5-10ms I/O per frame). Fixed: the cascade
   is loaded once at module level and reused for all frames.

3. SUCCESS FLAG ACCURACY
   The original set result["success"] = True unconditionally at the end of
   _analyze_deepface(), even if DeepFace threw an exception and result["error"]
   was set. Fixed: success is True only if the emotion block completed without
   error, so the frontend can correctly detect degraded analysis.