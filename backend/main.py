from contextlib import asynccontextmanager
from pathlib import Path
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse

from app.core.config import settings
from app.core.database import init_db
from app.api.v1 import api_router


@asynccontextmanager
async def lifespan(app: FastAPI):
    Path("recordings").mkdir(parents=True, exist_ok=True)
    Path("uploads/resumes").mkdir(parents=True, exist_ok=True)
    await init_db()
    await create_default_data()

    # Pre-load Whisper STT model so the first candidate request is instant.
    # Model loads in a background thread; server is ready immediately.
    try:
        from app.services.whisper_service import warmup_model
        import asyncio
        asyncio.create_task(warmup_model())
    except Exception as _e:
        print(f"Whisper warmup skipped: {_e}")

    print("=" * 60)
    print(f"  {settings.APP_NAME}  —  {settings.APP_ENV}")
    print(f"  LLM_PROVIDER    : {settings.LLM_PROVIDER}")
    print(f"  VISION_PROVIDER : {settings.VISION_PROVIDER}")
    print(f"  EMAIL_PROVIDER  : {settings.EMAIL_PROVIDER}")
    print("=" * 60)
    yield


async def create_default_data():
    from app.core.database import AsyncSessionLocal
    from app.models.user import User, UserRole, Organisation
    from app.core.security import get_password_hash
    from sqlalchemy import select

    async with AsyncSessionLocal() as session:
        # Check if already seeded
        result = await session.execute(select(User).where(User.email == "admin@demo.com"))
        if result.scalar_one_or_none():
            return

        # Create demo org
        org = Organisation(name="Demo Corp", domain="demo.com")
        session.add(org)
        await session.flush()

        demo_users = [
            User(email="admin@demo.com",       full_name="Admin User",       role=UserRole.ADMIN,
                 hashed_password=get_password_hash("admin123"), organisation_id=org.id),
            User(email="hr@demo.com",           full_name="HR Manager",       role=UserRole.HR,
                 hashed_password=get_password_hash("hr123456"), organisation_id=org.id),
            User(email="interviewer@demo.com",  full_name="Tech Interviewer", role=UserRole.INTERVIEWER,
                 hashed_password=get_password_hash("int12345"), organisation_id=org.id),
            User(email="candidate@demo.com",    full_name="John Candidate",   role=UserRole.CANDIDATE,
                 hashed_password=get_password_hash("can12345")),
        ]
        for u in demo_users:
            session.add(u)
        await session.commit()
        print("✅ Demo data seeded (org: Demo Corp, 4 users)")


app = FastAPI(
    title=settings.APP_NAME,
    version="2.0.0",
    description="AI-Powered Interview Platform",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"] if settings.DEBUG else [settings.FRONTEND_URL],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(api_router, prefix="/api/v1")

static_path = Path(__file__).parent.parent / "frontend" / "static"
static_path.mkdir(parents=True, exist_ok=True)
app.mount("/static", StaticFiles(directory=str(static_path)), name="static")

templates_path = Path(__file__).parent.parent / "frontend" / "templates"


@app.get("/", response_class=HTMLResponse, include_in_schema=False)
async def serve_index():
    return (templates_path / "index.html").read_text(encoding="utf-8")


@app.get("/interview/{access_token}", response_class=HTMLResponse, include_in_schema=False)
async def serve_interview(access_token: str):
    return (templates_path / "interview.html").read_text(encoding="utf-8")


@app.get("/watch/{access_token}", response_class=HTMLResponse, include_in_schema=False)
async def serve_watch(access_token: str):
    """Interviewer live view page."""
    return (templates_path / "watch.html").read_text(encoding="utf-8")


@app.get("/health")
async def health():
    return {"status": "ok", "version": "2.0.0"}
