import asyncio
import json
import time
import uuid
from collections import defaultdict, deque
from dataclasses import dataclass, field
from typing import Any, AsyncIterator, Deque, Dict, List, Optional, Tuple

from app.core.config import settings

try:
    from redis.asyncio import Redis
except Exception:  # pragma: no cover
    Redis = None


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
    last_seen: float = field(default_factory=time.time)

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

    @classmethod
    def from_public(cls, data: dict) -> "Participant":
        return cls(
            participant_id=data["participant_id"],
            user_id=data["user_id"],
            role=data["role"],
            display_name=data.get("display_name", ""),
            ws=None,
            mic_on=bool(data.get("mic_on", True)),
            cam_on=bool(data.get("cam_on", True)),
            speaking=bool(data.get("speaking", False)),
        )


class RoomManager:
    def __init__(self, capacity: int, join_rate_limit: int, join_window_seconds: int):
        self.capacity = capacity
        self.join_rate_limit = join_rate_limit
        self.join_window_seconds = join_window_seconds
        self._lock = asyncio.Lock()
        self._join_attempts: Dict[str, Deque[float]] = defaultdict(deque)
        self._rooms: Dict[str, Dict[str, Participant]] = defaultdict(dict)
        self._room_to_user_pid: Dict[str, Dict[str, str]] = defaultdict(dict)
        self._backend = self._resolve_backend()
        self._redis: Optional[Redis] = self._build_redis()
        self._pubsub_tasks: Dict[str, asyncio.Task] = {}
        self._touch_interval_seconds = 2.0

    def _resolve_backend(self) -> str:
        mode = settings.ROOM_BACKEND
        if mode == "memory":
            return "memory"
        if mode == "redis":
            return "redis"
        if settings.REDIS_URL and Redis is not None:
            return "redis"
        return "memory"

    def backend_name(self) -> str:
        return self._backend

    def _build_redis(self) -> Optional[Redis]:
        if self._backend != "redis" or Redis is None or not settings.REDIS_URL:
            return None
        return Redis.from_url(
            settings.REDIS_URL,
            socket_connect_timeout=settings.REDIS_SOCKET_TIMEOUT_SECONDS,
            socket_timeout=settings.REDIS_SOCKET_TIMEOUT_SECONDS,
            decode_responses=True,
        )

    def _join_key(self, room_token: str, user_id: str) -> str:
        return f"{room_token}:{user_id}"

    def _room_key(self, room_token: str) -> str:
        return f"rtc:room:{room_token}:participants"

    def _room_user_idx_key(self, room_token: str) -> str:
        return f"rtc:room:{room_token}:user_index"

    def _room_channel(self, room_token: str) -> str:
        return f"rtc:room:{room_token}:events"

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

    async def _cleanup_stale_locked(self, room_token: str) -> None:
        ttl = float(settings.RTC_STALE_PARTICIPANT_TTL_SECONDS)
        now = time.time()
        stale_pids = [
            pid for pid, p in self._rooms[room_token].items()
            if (now - float(p.last_seen)) > ttl
        ]
        for pid in stale_pids:
            p = self._rooms[room_token].pop(pid, None)
            if p and self._room_to_user_pid[room_token].get(p.user_id) == pid:
                self._room_to_user_pid[room_token].pop(p.user_id, None)
        if self._backend == "redis" and self._redis and stale_pids:
            for pid in stale_pids:
                await self._redis.hdel(self._room_key(room_token), pid)
            for pid in stale_pids:
                # reverse lookup cleanup best-effort
                pass

    async def join(
        self,
        room_token: str,
        user_id: str,
        role: str,
        display_name: str,
        ws: Any,
    ) -> Tuple[Optional[Participant], Optional[str]]:
        async with self._lock:
            await self._cleanup_stale_locked(room_token)
            existing_pid = self._room_to_user_pid[room_token].get(user_id)
            if existing_pid:
                # reconnection: replace stale/already-open participant for same user
                self._rooms[room_token].pop(existing_pid, None)
                self._room_to_user_pid[room_token].pop(user_id, None)
                if self._backend == "redis" and self._redis:
                    await self._redis.hdel(self._room_key(room_token), existing_pid)

            if len(self._rooms[room_token]) >= self.capacity:
                return None, "room_full"

            pid = f"{user_id}:{uuid.uuid4().hex[:8]}"
            participant = Participant(
                participant_id=pid,
                user_id=user_id,
                role=role,
                display_name=display_name,
                ws=ws,
            )
            self._rooms[room_token][pid] = participant
            self._room_to_user_pid[room_token][user_id] = pid
            if self._backend == "redis" and self._redis:
                await self._redis.hset(self._room_key(room_token), pid, json.dumps(participant.as_public(), ensure_ascii=True))
                await self._redis.hset(self._room_user_idx_key(room_token), user_id, pid)
                await self._redis.expire(self._room_key(room_token), int(settings.RTC_STALE_PARTICIPANT_TTL_SECONDS * 3))
                await self._redis.expire(self._room_user_idx_key(room_token), int(settings.RTC_STALE_PARTICIPANT_TTL_SECONDS * 3))
            return participant, None

    async def leave(self, room_token: str, participant_id: str) -> Optional[Participant]:
        async with self._lock:
            leaving = self._rooms[room_token].pop(participant_id, None)
            if leaving and self._room_to_user_pid[room_token].get(leaving.user_id) == participant_id:
                self._room_to_user_pid[room_token].pop(leaving.user_id, None)
            if self._backend == "redis" and self._redis:
                await self._redis.hdel(self._room_key(room_token), participant_id)
                if leaving:
                    await self._redis.hdel(self._room_user_idx_key(room_token), leaving.user_id)
            if not self._rooms[room_token]:
                self._rooms.pop(room_token, None)
                self._room_to_user_pid.pop(room_token, None)
            return leaving

    async def touch(self, room_token: str, participant_id: str) -> None:
        async with self._lock:
            p = self._rooms.get(room_token, {}).get(participant_id)
            if p:
                now = time.time()
                if now - float(p.last_seen) >= self._touch_interval_seconds:
                    p.last_seen = now

    async def update_media(self, room_token: str, participant_id: str, mic_on: bool, cam_on: bool) -> bool:
        async with self._lock:
            room = self._rooms.get(room_token)
            if not room or participant_id not in room:
                return False
            p = room[participant_id]
            p.mic_on = bool(mic_on)
            p.cam_on = bool(cam_on)
            p.last_seen = time.time()
            if self._backend == "redis" and self._redis:
                await self._redis.hset(self._room_key(room_token), participant_id, json.dumps(p.as_public(), ensure_ascii=True))
            return True

    async def update_speaking(self, room_token: str, participant_id: str, speaking: bool) -> bool:
        async with self._lock:
            room = self._rooms.get(room_token)
            if not room or participant_id not in room:
                return False
            p = room[participant_id]
            p.speaking = bool(speaking)
            p.last_seen = time.time()
            if self._backend == "redis" and self._redis:
                await self._redis.hset(self._room_key(room_token), participant_id, json.dumps(p.as_public(), ensure_ascii=True))
            return True

    async def snapshot(self, room_token: str) -> List[dict]:
        if self._backend == "redis" and self._redis:
            try:
                rows = await self._redis.hvals(self._room_key(room_token))
                return [json.loads(row) for row in rows]
            except Exception:
                pass
        async with self._lock:
            room = self._rooms.get(room_token)
            if not room:
                return []
            return [p.as_public() for p in room.values()]

    async def get_ws(self, room_token: str, participant_id: str) -> Optional[Any]:
        async with self._lock:
            p = self._rooms.get(room_token, {}).get(participant_id)
            return p.ws if p else None

    async def others_ws(self, room_token: str, exclude_participant_id: Optional[str] = None) -> List[Any]:
        async with self._lock:
            room = self._rooms.get(room_token)
            if not room:
                return []
            return [
                p.ws
                for pid, p in room.items()
                if not exclude_participant_id or pid != exclude_participant_id
            ]

    async def count(self, room_token: str) -> int:
        if self._backend == "redis" and self._redis:
            try:
                return int(await self._redis.hlen(self._room_key(room_token)))
            except Exception:
                pass
        async with self._lock:
            return len(self._rooms.get(room_token, {}))

    async def publish_event(self, room_token: str, payload: dict) -> None:
        if self._backend != "redis" or not self._redis:
            return
        try:
            await self._redis.publish(self._room_channel(room_token), json.dumps(payload, ensure_ascii=True))
        except Exception:
            return

    async def subscribe_events(self, room_token: str) -> AsyncIterator[dict]:
        if self._backend != "redis" or not self._redis:
            return
        pubsub = self._redis.pubsub()
        await pubsub.subscribe(self._room_channel(room_token))
        try:
            while True:
                msg = await pubsub.get_message(ignore_subscribe_messages=True, timeout=1.0)
                if msg and isinstance(msg.get("data"), str):
                    try:
                        yield json.loads(msg["data"])
                    except Exception:
                        continue
                await asyncio.sleep(0.01)
        finally:
            await pubsub.unsubscribe(self._room_channel(room_token))
            await pubsub.close()
