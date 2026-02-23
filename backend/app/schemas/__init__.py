from datetime import datetime
from typing import Optional, List, Any
from pydantic import BaseModel, EmailStr
from app.models.user import UserRole
from app.models.interview import InterviewStatus


# ─── Auth ─────────────────────────────────────────────────────────────────────
class Token(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: "UserOut"

class LoginRequest(BaseModel):
    email: EmailStr
    password: str


# ─── Organisation ─────────────────────────────────────────────────────────────
class OrgCreate(BaseModel):
    name: str
    domain: Optional[str] = None

class OrgOut(BaseModel):
    id: str
    name: str
    domain: Optional[str] = None
    created_at: datetime
    model_config = {"from_attributes": True}


# ─── User ─────────────────────────────────────────────────────────────────────
class UserCreate(BaseModel):
    email: EmailStr
    full_name: str
    password: str
    role: UserRole = UserRole.CANDIDATE
    organisation_id: Optional[str] = None

class UserOut(BaseModel):
    id: str
    email: str
    full_name: str
    role: UserRole
    is_active: bool
    created_at: datetime
    organisation_id: Optional[str] = None
    organisation: Optional["OrgOut"] = None
    model_config = {"from_attributes": True}

class UserUpdate(BaseModel):
    full_name: Optional[str] = None
    is_active: Optional[bool] = None


# ─── Interview ────────────────────────────────────────────────────────────────
class InterviewCreate(BaseModel):
    title: str
    job_role: str
    description: Optional[str] = None
    candidate_email: EmailStr
    interviewer_ids: List[str] = []
    scheduled_at: datetime
    duration_minutes: int = 60
    enable_emotion_analysis: bool = True
    enable_cheating_detection: bool = True
    question_bank: Optional[List[dict]] = None

class InterviewOut(BaseModel):
    id: str
    title: str
    job_role: str
    description: Optional[str] = None
    hr_id: str
    candidate_id: str
    organisation_id: Optional[str] = None
    scheduled_at: datetime
    duration_minutes: int
    status: InterviewStatus
    access_token: str
    enable_emotion_analysis: bool
    enable_cheating_detection: bool
    question_bank: Optional[Any] = None
    resume_path: Optional[str] = None
    ai_paused: bool = False
    answer_score: Optional[float] = None
    code_score: Optional[float] = None
    emotion_score: Optional[float] = None
    cheating_score: Optional[float] = None
    integrity_score: Optional[float] = None
    overall_score: Optional[float] = None
    passed: Optional[bool] = None
    ai_feedback: Optional[str] = None
    recording_url: Optional[str] = None
    recording_size_bytes: Optional[int] = None
    has_recording: bool = False
    created_at: datetime
    started_at: Optional[datetime] = None
    ended_at: Optional[datetime] = None
    hr: Optional["UserOut"] = None
    candidate: Optional["UserOut"] = None
    model_config = {"from_attributes": True}

class InterviewerOut(BaseModel):
    id: str
    interviewer: UserOut
    model_config = {"from_attributes": True}

class InterviewWithInterviewers(InterviewOut):
    interviewers: List[InterviewerOut] = []
    has_recording: bool = False
    temp_password: Optional[str] = None  # Only set when a new candidate account is auto-created
    model_config = {"from_attributes": True}


# ─── Vision ───────────────────────────────────────────────────────────────────
class FrameEmotionData(BaseModel):
    avg_confidence: float = 65.0
    avg_engagement: float = 65.0
    avg_stress: float = 20.0
    dominant_emotions: List[str] = []
    cheating_flags: List[str] = []
    frames_analyzed: int = 0
    max_cheating_score: float = 0.0
    avg_cheating_score: float = 0.0
    tab_switches: int = 0


# ─── Chat ─────────────────────────────────────────────────────────────────────
class ChatMessage(BaseModel):
    content: str
    code_snippet: Optional[str] = None

class ChatResponse(BaseModel):
    message: str
    is_complete: bool = False
    ai_paused: bool = False

class CompleteInterviewRequest(BaseModel):
    cheating_score: Optional[float] = None
    emotion_data: Optional[FrameEmotionData] = None
    tab_switches: int = 0


# ─── Evaluation ───────────────────────────────────────────────────────────────
class ScoreBreakdown(BaseModel):
    answer_score: float
    code_score: Optional[float] = None
    emotion_score: Optional[float] = None
    integrity_score: Optional[float] = None
    overall_score: float
    passed: bool
    weights_used: dict = {}

class EvaluationResult(BaseModel):
    overall_score: float
    answer_score: float
    code_score: Optional[float] = None
    emotion_score: Optional[float] = None
    integrity_score: Optional[float] = None
    passed: bool
    strengths: List[str] = []
    weaknesses: List[str] = []
    ai_feedback: str
    cheating_score: Optional[float] = None
    score_breakdown: Optional[ScoreBreakdown] = None
    cheating_flags: List[str] = []


# ─── Recording ────────────────────────────────────────────────────────────────
class RecordingUploadResponse(BaseModel):
    success: bool
    recording_url: str
    size_bytes: int
    message: str


# ─── Message ──────────────────────────────────────────────────────────────────
class MessageOut(BaseModel):
    id: str
    role: str
    content: str
    code_snippet: Optional[str] = None
    timestamp: datetime
    model_config = {"from_attributes": True}


# ─── Interviewer Control ──────────────────────────────────────────────────────
class InterviewerQuestion(BaseModel):
    question: str


Token.model_rebuild()
OrgOut.model_rebuild()
UserOut.model_rebuild()
InterviewOut.model_rebuild()
InterviewWithInterviewers.model_rebuild()
