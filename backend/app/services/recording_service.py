from pathlib import Path
from typing import Dict


def process_recording_metadata(recording_path: str, size_bytes: int) -> Dict[str, object]:
    path = Path(recording_path)
    return {
        "exists": path.exists(),
        "suffix": path.suffix.lower(),
        "size_bytes": int(size_bytes or 0),
    }

