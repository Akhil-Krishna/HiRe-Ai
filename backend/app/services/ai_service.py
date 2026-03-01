
import httpx
import json
import logging
from typing import Optional, List, Tuple

from app.core.config import settings
from app.models.interview import Interview, InterviewMessage

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """You are a professional AI technical interviewer. Conduct a structured, fair interview.

RULES:
1. Ask ONE question at a time. Wait for the answer before proceeding.
2. Keep responses concise (2-3 sentences unless evaluating code).
3. If asking a CODING question, start your ENTIRE message with: CODING_QUESTION:
4. When you receive [CODE SUBMITTED], evaluate it carefully: correctness, efficiency, edge cases. Then continue.
5. Flow: introduction → technical concepts (3-4 Qs) → coding problem (1-2 Qs) → system design → wrap up.
6. After 8-10 exchanges which must include 1-2 additional questions from resume ,also keep track of time duration {duration_minutes} min, end with a summary line starting with: INTERVIEW_COMPLETE

Job Role: {job_role}
Question number: {q_num}
Question bank : {question_bank_context}
Resume : {resume_context}"""

EVAL_PROMPT = """You are an expert technical interview evaluator. Score this interview transcript.

Position: {job_role}
{resume_context}
Transcript:
{transcript}

Respond with ONLY a valid JSON object — no markdown, no backticks, no explanation:
{{
  "answer_score": <integer 0-100>,
  "code_score": <integer 0-100, or null if no coding was done>,
  "overall_score": <integer 0-100>,
  "passed": <true if overall_score >= 60 else false>,
  "strengths": ["strength 1", "strength 2", "strength 3"],
  "weaknesses": ["weakness 1", "weakness 2"],
  "ai_feedback": "2-3 sentences summarizing overall performance."
}}"""


def _build_history(messages: List[InterviewMessage]) -> List[dict]:
    result = []
    for m in messages:
        content = m.content
        if m.code_snippet:
            content += f"\n\n[CODE SUBMITTED]\n```\n{m.code_snippet}\n```"
        if m.role == "interviewer":
            result.append({"role": "user", "content": content})
        else:
            result.append({
                "role": "user" if m.role == "candidate" else "assistant",
                "content": content,
            })
    return result


def _parse_json_response(text: str) -> Optional[dict]:
    try:
        clean = text.strip()
        if clean.startswith("```"):
            lines = clean.split("\n")
            inner = "\n".join(lines[1:])
            inner = inner.rstrip("`").strip()
            clean = inner
        return json.loads(clean)
    except Exception as e:
        logger.error("JSON parse failed: %s\nRaw: %.300s", e, text)
        return None


async def _chat_groq(system: str, messages: List[dict]) -> Optional[str]:
    if not settings.GROQ_API_KEY:
        return None
    payload = {
        "model": settings.GROQ_MODEL,
        "messages": [{"role": "system", "content": system}] + messages,
        "temperature": 0.7,
        "max_tokens": 1024,
        "stream": False,
    }
    try:
        async with httpx.AsyncClient(timeout=settings.GROQ_TIMEOUT) as client:
            resp = await client.post(
                "https://api.groq.com/openai/v1/chat/completions",
                json=payload,
                headers={"Authorization": f"Bearer {settings.GROQ_API_KEY}"},
            )
            resp.raise_for_status()
            return resp.json()["choices"][0]["message"]["content"].strip()
    except Exception as e:
        logger.error("Groq error: %s", e)
        return None


async def _chat_ollama(system: str, messages: List[dict]) -> Optional[str]:
    try:
        async with httpx.AsyncClient(timeout=settings.OLLAMA_TIMEOUT) as client:
            resp = await client.post(
                f"{settings.OLLAMA_BASE_URL}/api/chat",
                json={
                    "model": settings.OLLAMA_MODEL,
                    "stream": False,
                    "messages": [{"role": "system", "content": system}] + messages,
                    "options": {"temperature": 0.7, "num_predict": 1024},
                },
            )
            resp.raise_for_status()
            return resp.json()["message"]["content"].strip()
    except Exception as e:
        logger.warning("Ollama error: %s", e)
        return None


async def _chat_openai(system: str, messages: List[dict]) -> Optional[str]:
    if not settings.OPENAI_API_KEY:
        return None
    try:
        async with httpx.AsyncClient(timeout=settings.OPENAI_TIMEOUT) as client:
            resp = await client.post(
                "https://api.openai.com/v1/chat/completions",
                json={
                    "model": settings.OPENAI_MODEL,
                    "messages": [{"role": "system", "content": system}] + messages,
                    "temperature": 0.7, "max_tokens": 1024,
                },
                headers={"Authorization": f"Bearer {settings.OPENAI_API_KEY}"},
            )
            resp.raise_for_status()
            return resp.json()["choices"][0]["message"]["content"].strip()
    except Exception as e:
        logger.error("OpenAI error: %s", e)
        return None


async def _dispatch_llm(system: str, messages: List[dict]) -> Optional[str]:
    p = settings.LLM_PROVIDER.lower().strip()
    if p == "groq":     return await _chat_groq(system, messages)
    if p == "ollama":   return await _chat_ollama(system, messages)
    if p == "openai":   return await _chat_openai(system, messages)
    return None


def _mock_interview_response(q_num: int, job_role: str) -> str:
    questions = [
        f"[MOCK INTERVIEW] Welcome! I'll be interviewing you for the {job_role} role today. Could you start by briefly introducing yourself and your most relevant experience?",
        "Great! Walk me through a technically challenging problem you solved recently — what was the challenge and how did you approach it?",
        "CODING_QUESTION: Let's do a coding exercise. Write a function that finds the two numbers in an array that add up to a target sum. Aim for O(n) complexity. Use the code editor.",
        "Good effort! Can you explain the difference between synchronous and asynchronous execution, with a real-world example of each?",
        "How do you typically debug a hard-to-reproduce production issue?",
        "CODING_QUESTION: Design and implement a simple LRU Cache with get() and put() methods. Use the code editor on the right.",
        "Describe the components you'd include when designing a URL shortener at scale.",
        "What practices do you follow to ensure your code remains maintainable as the team grows?",
        f"INTERVIEW_COMPLETE\n\nThank you for your time today — that concludes our interview for the {job_role} position!\n\nYou communicated your background clearly and demonstrated solid technical knowledge. We'll review your performance and be in touch shortly.",
    ]
    idx = min(max(q_num - 1, 0), len(questions) - 1)
    return questions[idx]


def _mock_evaluation() -> dict:
    return {
        "answer_score": 5, "code_score": 0, "overall_score": 10, "passed": False,
        "strengths": ["---"],
        "weaknesses": ["---"],
        "ai_feedback": "Unable to generate evaluation at this time.",
    }


async def get_ai_response(
    interview: Interview,
    messages: List[InterviewMessage],
    candidate_message: str,
    code_snippet: Optional[str] = None,
) -> Tuple[str, bool]:
    q_num = len([m for m in messages if m.role == "ai"]) + 1

    # Build question bank context
    qb_context = ""
    if interview.question_bank:
        qs = interview.question_bank
        if isinstance(qs, list) and qs:
            qb_lines = []
            for i, q in enumerate(qs[:15], 1):
                if isinstance(q, dict):
                    qb_lines.append(f"  {i}. [{q.get('difficulty','med').upper()}] {q.get('question','')}")
                else:
                    qb_lines.append(f"  {i}. {q}")
            qb_context = "QUESTION BANK (use these questions in order, adapt as needed):\n" + "\n".join(qb_lines)

    # Build resume context
    resume_context = ""
    if interview.resume_text:
        resume_context = f"CANDIDATE RESUME (use for context, ask relevant questions):\n{interview.resume_text[:3000]}"

    system = SYSTEM_PROMPT.format(
        job_role=interview.job_role, q_num=q_num,
        question_bank_context=qb_context, resume_context=resume_context,duration_minutes=interview.duration_minutes
    )

    history = _build_history(messages)
    user_content = candidate_message
    if code_snippet:
        user_content += f"\n\n[CODE SUBMITTED]\n```\n{code_snippet}\n```"
    history.append({"role": "user", "content": user_content})

    text = await _dispatch_llm(system, history)

    if text is None:
        if settings.LLM_PROVIDER != "mock":
            logger.warning("LLM provider '%s' unavailable — using mock", settings.LLM_PROVIDER)
        text = _mock_interview_response(q_num, interview.job_role)

    return text, "INTERVIEW_COMPLETE" in text


async def generate_final_evaluation(
    interview: Interview,
    messages: List[InterviewMessage],
    emotion_data: Optional[dict] = None,
    cheating_score: Optional[float] = None,
) -> dict:
    transcript = "\n".join([
        f"{'Interviewer' if m.role == 'ai' else ('Human Interviewer' if m.role == 'interviewer' else 'Candidate')}: {m.content}"
        + (f"\n[Code: {m.code_snippet[:300]}...]" if m.code_snippet else "")
        for m in messages
    ])

    resume_context = ""
    if interview.resume_text:
        resume_context = f"Candidate Resume Summary:\n{interview.resume_text[:1500]}\n"

    system = "You are an expert technical interview evaluator. Output only valid JSON, no markdown."
    prompt = EVAL_PROMPT.format(
        job_role=interview.job_role, transcript=transcript, resume_context=resume_context
    )

    text = await _dispatch_llm(system, [{"role": "user", "content": prompt}])
    result = None
    if text:
        result = _parse_json_response(text)
    if result is None:
        result = _mock_evaluation()

    weights = {"answer": 0.50, "code": 0.25, "emotion": 0.15, "integrity": 0.10}

    emotion_score = None
    if emotion_data:
        conf = emotion_data.get("avg_confidence", 50.0)
        eng  = emotion_data.get("avg_engagement", 50.0)
        emotion_score = round((float(conf) + float(eng)) / 2.0, 1)
    result["emotion_score"] = emotion_score

    integrity_score = None
    if cheating_score is not None:
        integrity_score = round(max(0.0, 100.0 - float(cheating_score)), 1)
    result["integrity_score"] = integrity_score
    result["cheating_score"]  = float(cheating_score) if cheating_score is not None else None

    # modified 70->0
    answer = float(result.get("answer_score", 0))
    code   = result.get("code_score")

    if code is not None and emotion_score is not None and integrity_score is not None:
        overall = answer * 0.50 + float(code) * 0.25 + emotion_score * 0.15 + integrity_score * 0.10
        weights_used = weights
    elif code is not None and emotion_score is not None:
        overall = answer * 0.55 + float(code) * 0.30 + emotion_score * 0.15
        weights_used = {"answer": 0.55, "code": 0.30, "emotion": 0.15}
    elif code is not None:
        overall = answer * 0.60 + float(code) * 0.40
        weights_used = {"answer": 0.60, "code": 0.40}
    elif emotion_score is not None:
        overall = answer * 0.75 + emotion_score * 0.25
        weights_used = {"answer": 0.75, "emotion": 0.25}
    else:
        overall = answer
        weights_used = {"answer": 1.0}

    result["overall_score"] = round(float(overall), 1)
    result["passed"]        = result["overall_score"] >= 60.0
    result["weights_used"]  = weights_used

    for key in ("answer_score", "code_score", "overall_score", "emotion_score", "integrity_score", "cheating_score"):
        if result.get(key) is not None:
            result[key] = float(result[key])

    return result
