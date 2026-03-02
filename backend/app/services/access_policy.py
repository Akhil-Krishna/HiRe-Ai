import logging

from fastapi import HTTPException

from app.models.interview import Interview
from app.models.user import User, UserRole

logger = logging.getLogger(__name__)


class AccessPolicy:
    @staticmethod
    def ensure_interview_viewer(iv: Interview, user: User) -> None:
        if user.role == UserRole.ADMIN:
            return
        if user.role == UserRole.HR:
            iv_org = iv.hr.organisation_id if iv.hr else None
            if iv_org and user.organisation_id == iv_org:
                return
        if user.role == UserRole.INTERVIEWER and any(ii.interviewer_id == user.id for ii in iv.interviewers):
            return
        if user.role == UserRole.CANDIDATE and iv.candidate_id == user.id:
            return
        logger.warning(
            "policy denied viewer",
            extra={
                "event": "policy_denied",
                "component": "authz",
                "error": f"user={user.id} interview={iv.id}",
            },
        )
        raise HTTPException(status_code=403, detail="Access denied")

    @staticmethod
    def ensure_candidate_owner(iv: Interview, user: User, message: str = "Access denied") -> None:
        if user.role == UserRole.ADMIN or iv.candidate_id == user.id:
            return
        logger.warning(
            "policy denied candidate ownership",
            extra={
                "event": "policy_denied",
                "component": "authz",
                "error": f"user={user.id} interview={iv.id}",
            },
        )
        raise HTTPException(status_code=403, detail=message)
