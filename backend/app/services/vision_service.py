
import asyncio
import base64
import logging
import time
from concurrent.futures import ThreadPoolExecutor

from app.core.config import settings

logger = logging.getLogger(__name__)

# ── Dedicated vision thread pool ──────────────────────────────────────────────
# Isolated from the STT executor. max_workers=1 because DeepFace is single-
# threaded per inference and multiple concurrent vision calls won't help.
_vision_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="vision-worker")

# ── Lazy globals loaded once ───────────────────────────────────────────────────
_deepface     = None
_cv2          = None
_np           = None
_haar_cascade = None


def _load_deps() -> bool:
    global _deepface, _cv2, _np, _haar_cascade

    if _np is None:
        try:
            import numpy as np
            _np = np
        except ImportError:
            logger.warning("numpy not installed → pip install numpy")

    if _cv2 is None:
        try:
            import cv2
            _cv2 = cv2
            logger.info("✅ OpenCV loaded (version %s)", cv2.__version__)
        except ImportError:
            logger.warning("OpenCV not installed → pip install opencv-python-headless")

    if _deepface is None:
        try:
            from deepface import DeepFace
            _deepface = DeepFace
            logger.info("✅ DeepFace loaded")
        except ImportError:
            logger.warning("DeepFace not installed → pip install deepface tf-keras")

    if _haar_cascade is None and _cv2 is not None:
        try:
            xml_path = _cv2.data.haarcascades + "haarcascade_frontalface_default.xml"
            cascade  = _cv2.CascadeClassifier(xml_path)
            if not cascade.empty():
                _haar_cascade = cascade
                logger.info("✅ Haar cascade cached")
            else:
                logger.warning("Haar cascade XML not found at %s", xml_path)
        except Exception as e:
            logger.warning("Haar cascade load failed: %s", e)

    return (_deepface is not None and _cv2 is not None and _np is not None)


# ── Emotion → interview score mapping ─────────────────────────────────────────
EMOTION_MAP = {
    "happy":    {"confidence": 88.0, "engagement": 92.0, "stress": 8.0},
    "neutral":  {"confidence": 72.0, "engagement": 67.0, "stress": 18.0},
    "surprise": {"confidence": 58.0, "engagement": 82.0, "stress": 32.0},
    "sad":      {"confidence": 38.0, "engagement": 42.0, "stress": 58.0},
    "angry":    {"confidence": 42.0, "engagement": 52.0, "stress": 68.0},
    "fear":     {"confidence": 28.0, "engagement": 48.0, "stress": 78.0},
    "disgust":  {"confidence": 35.0, "engagement": 40.0, "stress": 72.0},
}


def _decode_frame_sync(b64: str):
    try:
        if "," in b64:
            b64 = b64.split(",", 1)[1]
        raw = base64.b64decode(b64)
        arr = _np.frombuffer(raw, _np.uint8)
        img = _cv2.imdecode(arr, _cv2.IMREAD_COLOR)
        if img is None:
            raise ValueError("cv2.imdecode returned None")
        return img
    except Exception as e:
        logger.error("Frame decode failed: %s", e)
        return None


def _count_faces_sync(img) -> int:
    if _haar_cascade is None or _haar_cascade.empty():
        return 1
    try:
        gray  = _cv2.cvtColor(img, _cv2.COLOR_BGR2GRAY)
        faces = _haar_cascade.detectMultiScale(
            gray, scaleFactor=1.1, minNeighbors=5,
            minSize=(50, 50), flags=_cv2.CASCADE_SCALE_IMAGE,
        )
        return int(len(faces))
    except Exception as e:
        logger.debug("Face count error: %s", e)
        return 1


def _gaze_score_sync(img) -> float:
    try:
        h, w  = img.shape[:2]
        gray  = _cv2.cvtColor(img, _cv2.COLOR_BGR2GRAY)
        eye   = gray[int(h*0.20):int(h*0.48), int(w*0.05):int(w*0.95)]
        mid   = eye.shape[1] // 2
        l_m   = float(_np.mean(eye[:, :mid]))
        r_m   = float(_np.mean(eye[:, mid:]))
        peak  = max(l_m, r_m, 1.0)
        return min(100.0, abs(l_m - r_m) / peak * 450.0)
    except Exception:
        return 0.0


def _run_vision_sync(b64_image: str) -> dict:
    """Full synchronous DeepFace + OpenCV pipeline. Runs in _vision_executor."""
    result = {
        "success": False,
        "emotions": {},
        "dominant_emotion": "neutral",
        "confidence_score": 65.0,
        "engagement_score": 65.0,
        "stress_score": 20.0,
        "face_count": 1,
        "gaze_deviation": 0.0,
        "cheating_flags": [],
        "cheating_score": 0.0,
        "error": None,
    }

    deps_ok = _load_deps()
    if not deps_ok:
        result["error"] = (
            "Vision libs not installed. "
            "Run: pip install deepface opencv-python-headless tf-keras numpy"
        )
        return result

    img = _decode_frame_sync(b64_image)
    if img is None:
        result["error"] = "Could not decode image frame"
        return result

    cheating_flags: list  = []
    cheating_score: float = 0.0

    # Step 1: Face count (cached Haar, ~3ms)
    face_count = _count_faces_sync(img)
    result["face_count"] = face_count

    if face_count == 0:
        cheating_flags.append("no_face_detected")
        cheating_score += 35.0
    elif face_count > 1:
        cheating_flags.append(f"multiple_faces_{face_count}")
        cheating_score += 45.0

    # Step 2: DeepFace emotion (~200-400ms CPU)
    emotion_ok = False
    try:
        t0 = time.perf_counter()
        analysis = _deepface.analyze(
            img_path=img,
            actions=["emotion"],
            enforce_detection=False,
            silent=True,
            detector_backend="opencv",
        )
        logger.debug("DeepFace.analyze took %.3fs", time.perf_counter() - t0)

        face_data    = analysis[0] if isinstance(analysis, list) else analysis
        raw_emotions = face_data.get("emotion", {})
        dominant     = str(face_data.get("dominant_emotion", "neutral")).lower()

        total        = max(float(sum(raw_emotions.values())), 1.0)
        emotions_pct = {k: round(float(v) / total * 100.0, 2) for k, v in raw_emotions.items()}

        result["emotions"]         = emotions_pct
        result["dominant_emotion"] = dominant

        mapping  = EMOTION_MAP.get(dominant, EMOTION_MAP["neutral"])
        h_pct    = emotions_pct.get("happy", 0.0) / 100.0
        f_pct    = emotions_pct.get("fear",  0.0) / 100.0
        conf     = mapping["confidence"] * (1.0 + h_pct * 0.10 - f_pct * 0.15)
        eng      = mapping["engagement"] * (1.0 + h_pct * 0.08)

        result["confidence_score"] = round(min(100.0, max(0.0, float(conf))), 1)
        result["engagement_score"] = round(min(100.0, max(0.0, float(eng))),  1)
        result["stress_score"]     = round(float(mapping["stress"]), 1)

        if dominant in ("fear", "angry", "disgust") and emotions_pct.get(dominant, 0.0) > 45.0:
            cheating_score += 12.0

        emotion_ok = True
    except Exception as e:
        logger.warning("DeepFace emotion error: %s", e)
        result["error"] = f"Emotion analysis failed: {e}"

    # Step 3: Gaze deviation (OpenCV, ~2ms)
    try:
        gaze_dev = _gaze_score_sync(img)
        result["gaze_deviation"] = round(float(gaze_dev), 1)
        if gaze_dev > 45.0:
            cheating_flags.append("gaze_away")
            cheating_score += min(20.0, float(gaze_dev) * 0.38)
    except Exception as e:
        logger.debug("Gaze estimation error: %s", e)

    result["cheating_flags"] = cheating_flags
    result["cheating_score"] = round(min(100.0, float(cheating_score)), 1)
    result["success"]        = emotion_ok
    return result


# ── Mock provider ──────────────────────────────────────────────────────────────

async def _analyze_mock(_b64_image: str) -> dict:
    import random
    dominant = random.choice(["happy", "neutral", "neutral", "neutral", "surprise"])
    mapping  = EMOTION_MAP.get(dominant, EMOTION_MAP["neutral"])
    return {
        "success": True,
        "emotions": {
            "happy":    round(random.uniform(20, 60), 2),
            "neutral":  round(random.uniform(20, 50), 2),
            "surprise": round(random.uniform(0,  15), 2),
            "sad":      round(random.uniform(0,  10), 2),
            "angry":    round(random.uniform(0,   8), 2),
            "fear":     round(random.uniform(0,   8), 2),
            "disgust":  round(random.uniform(0,   5), 2),
        },
        "dominant_emotion": dominant,
        "confidence_score": round(mapping["confidence"] + random.uniform(-5, 5), 1),
        "engagement_score": round(mapping["engagement"] + random.uniform(-5, 5), 1),
        "stress_score":     round(mapping["stress"]     + random.uniform(-3, 3), 1),
        "face_count": 1,
        "gaze_deviation": round(random.uniform(0, 20), 1),
        "cheating_flags": [],
        "cheating_score": 0.0,
        "error": None,
    }


# ── DeepFace provider ──────────────────────────────────────────────────────────

async def _analyze_deepface(b64_image: str) -> dict:
    """Async wrapper — CPU work runs in dedicated _vision_executor."""
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(_vision_executor, _run_vision_sync, b64_image)


# ── Public entry point ─────────────────────────────────────────────────────────

async def analyze_frame(b64_image: str) -> dict:
    provider = settings.VISION_PROVIDER.lower().strip()
    if provider == "mock":
        return await _analyze_mock(b64_image)
    result = await _analyze_deepface(b64_image)
    if not result["success"] and result.get("error", "").startswith("Vision libs"):
        logger.info("DeepFace unavailable — falling back to mock")
        return await _analyze_mock(b64_image)
    return result


# ── Post-interview aggregation ─────────────────────────────────────────────────

def aggregate_vision_logs(logs: list) -> dict:
    if not logs:
        return {}

    confidences  = [float(l.confidence_score) for l in logs if l.confidence_score is not None]
    engagements  = [float(l.engagement_score)  for l in logs if l.engagement_score  is not None]
    stresses     = [float(l.stress_score)      for l in logs if l.stress_score      is not None]
    cheat_scores = [float(l.cheating_score)    for l in logs]

    all_flags: list = []
    for l in logs:
        if l.cheating_flags:
            flags = (
                l.cheating_flags
                if isinstance(l.cheating_flags, list)
                else l.cheating_flags.get("flags", [])
            )
            all_flags.extend(flags)

    dominant_counts: dict = {}
    for l in logs:
        if l.dominant_emotion:
            dominant_counts[l.dominant_emotion] = dominant_counts.get(l.dominant_emotion, 0) + 1
    dominant_sorted = sorted(dominant_counts, key=dominant_counts.get, reverse=True)

    def _avg(lst, default=65.0):
        return round(sum(lst) / len(lst), 1) if lst else default

    return {
        "avg_confidence":     _avg(confidences),
        "avg_engagement":     _avg(engagements),
        "avg_stress":         _avg(stresses, default=20.0),
        "dominant_emotions":  dominant_sorted,
        "cheating_flags":     list(set(all_flags)),
        "frames_analyzed":    len(logs),
        "max_cheating_score": round(max(cheat_scores), 1) if cheat_scores else 0.0,
        "avg_cheating_score": _avg(cheat_scores, default=0.0),
    }
