import asyncio
import time
import uuid
from collections import defaultdict, deque
from dataclasses import dataclass, field
from typing import Any, Deque, Dict, List, Optional, Tuple


@dataclass
class Participant:
    participant_id: str
    user_id: str
    role: str
    display_name: str
    ws: Any
    mic_on: bool = True
    cam_on: bool = True
    speaking: bool = False
    joined_at: float = field(default_factory=time.time)

    def as_public(self) -> dict:
        return {
            "participant_id": self.participant_id,
            "user_id": self.user_id,
            "role": self.role,
            "display_name": self.display_name,
            "mic_on": self.mic_on,
            "cam_on": self.cam_on,
            "speaking": self.speaking,
        }


@dataclass
class Room:
    token: str
    participants: Dict[str, Participant] = field(default_factory=dict)


class RoomManager:
    def __init__(self, capacity: int, join_rate_limit: int, join_window_seconds: int):
        self.capacity = capacity
        self.join_rate_limit = join_rate_limit
        self.join_window_seconds = join_window_seconds
        self._lock = asyncio.Lock()
        self._rooms: Dict[str, Room] = {}
        self._join_attempts: Dict[str, Deque[float]] = defaultdict(deque)

    def _join_key(self, room_token: str, user_id: str) -> str:
        return f"{room_token}:{user_id}"

    def _prune_attempts(self, attempts: Deque[float], now: float) -> None:
        cutoff = now - self.join_window_seconds
        while attempts and attempts[0] < cutoff:
            attempts.popleft()

    async def allow_join(self, room_token: str, user_id: str) -> bool:
        now = time.time()
        async with self._lock:
            key = self._join_key(room_token, user_id)
            attempts = self._join_attempts[key]
            self._prune_attempts(attempts, now)
            if len(attempts) >= self.join_rate_limit:
                return False
            attempts.append(now)
            return True

    async def join(
        self,
        room_token: str,
        user_id: str,
        role: str,
        display_name: str,
        ws: Any,
    ) -> Tuple[Optional[Participant], Optional[str]]:
        async with self._lock:
            room = self._rooms.get(room_token)
            if room is None:
                room = Room(token=room_token)
                self._rooms[room_token] = room

            if any(p.user_id == user_id for p in room.participants.values()):
                return None, "duplicate_join"

            if len(room.participants) >= self.capacity:
                return None, "room_full"

            pid = f"{user_id}:{uuid.uuid4().hex[:8]}"
            while pid in room.participants:
                pid = f"{user_id}:{uuid.uuid4().hex[:8]}"

            participant = Participant(
                participant_id=pid,
                user_id=user_id,
                role=role,
                display_name=display_name,
                ws=ws,
            )
            room.participants[pid] = participant
            return participant, None

    async def leave(self, room_token: str, participant_id: str) -> Optional[Participant]:
        async with self._lock:
            room = self._rooms.get(room_token)
            if not room:
                return None
            leaving = room.participants.pop(participant_id, None)
            if not room.participants:
                self._rooms.pop(room_token, None)
            return leaving

    async def update_media(self, room_token: str, participant_id: str, mic_on: bool, cam_on: bool) -> bool:
        async with self._lock:
            room = self._rooms.get(room_token)
            if not room or participant_id not in room.participants:
                return False
            p = room.participants[participant_id]
            p.mic_on = bool(mic_on)
            p.cam_on = bool(cam_on)
            return True

    async def update_speaking(self, room_token: str, participant_id: str, speaking: bool) -> bool:
        async with self._lock:
            room = self._rooms.get(room_token)
            if not room or participant_id not in room.participants:
                return False
            room.participants[participant_id].speaking = bool(speaking)
            return True

    async def snapshot(self, room_token: str) -> List[dict]:
        async with self._lock:
            room = self._rooms.get(room_token)
            if not room:
                return []
            return [p.as_public() for p in room.participants.values()]

    async def get_ws(self, room_token: str, participant_id: str) -> Optional[Any]:
        async with self._lock:
            room = self._rooms.get(room_token)
            if not room:
                return None
            p = room.participants.get(participant_id)
            return p.ws if p else None

    async def others_ws(self, room_token: str, exclude_participant_id: Optional[str] = None) -> List[Any]:
        async with self._lock:
            room = self._rooms.get(room_token)
            if not room:
                return []
            sockets = []
            for pid, p in room.participants.items():
                if exclude_participant_id and pid == exclude_participant_id:
                    continue
                sockets.append(p.ws)
            return sockets

    async def count(self, room_token: str) -> int:
        async with self._lock:
            room = self._rooms.get(room_token)
            return len(room.participants) if room else 0
