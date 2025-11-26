"""
Data Models
===========

Pydantic models for data validation and type safety.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, EmailStr, Field


class DraftStatus(str, Enum):
    """Status of an email draft."""
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    SENT = "sent"
    BOUNCED = "bounced"
    REPLIED = "replied"


class FilterTab(str, Enum):
    """Filter tabs for history view."""
    ALL = "all"
    REPLIED = "replied"
    UNREAD = "unread"
    READ = "read"
    BOUNCED = "bounced"


# ============================================================================
# Draft Models
# ============================================================================

class DraftBase(BaseModel):
    """Base draft model with common fields."""
    
    model_config = ConfigDict(from_attributes=True, populate_by_name=True)
    
    subject: str = ""
    body: str = ""
    recipient_email: str = Field(default="", alias="to_address")
    recipient_name: str = Field(default="", alias="to_name")
    sender_email: str = Field(default="", alias="from_address")
    sender_name: str = Field(default="", alias="from_name")
    company_name: str = ""


class DraftDocument(DraftBase):
    """Complete draft document from Firestore."""
    
    id: str
    status: DraftStatus = DraftStatus.PENDING
    created_at: Optional[datetime] = None
    sent_at: Optional[datetime] = None
    message_id: Optional[str] = None
    thread_id: Optional[str] = None
    gmail_draft_id: Optional[str] = None
    followup_number: int = 0
    notes: str = ""
    
    # Open tracking
    open_count: int = 0
    last_opened_at: Optional[datetime] = None
    
    # Reply tracking
    has_reply: bool = False
    reply_count: int = 0
    
    @classmethod
    def from_firestore(cls, doc_id: str, data: dict[str, Any]) -> DraftDocument:
        """Create from Firestore document."""
        return cls(
            id=doc_id,
            subject=data.get("subject", ""),
            body=data.get("body") or data.get("content", ""),
            recipient_email=data.get("recipient_email") or data.get("to_address", ""),
            recipient_name=data.get("recipient_name") or data.get("to_name", ""),
            sender_email=data.get("sender_email") or data.get("from_address", ""),
            sender_name=data.get("sender_name") or data.get("from_name", ""),
            company_name=data.get("company_name", ""),
            status=DraftStatus(data.get("status", "pending")),
            created_at=data.get("created_at"),
            sent_at=data.get("sent_at"),
            message_id=data.get("message_id"),
            thread_id=data.get("thread_id"),
            gmail_draft_id=data.get("gmail_draft_id"),
            followup_number=data.get("followup_number", 0),
            notes=data.get("notes", ""),
            open_count=data.get("open_count", 0),
            last_opened_at=data.get("last_opened_at"),
            has_reply=data.get("has_reply", False),
            reply_count=data.get("reply_count", 0)
        )


class FollowupDocument(DraftBase):
    """Followup email document from Firestore."""
    
    id: str
    original_draft_id: str
    followup_number: int
    status: DraftStatus = DraftStatus.PENDING
    created_at: Optional[datetime] = None
    sent_at: Optional[datetime] = None
    message_id: Optional[str] = None
    thread_id: Optional[str] = None
    
    @classmethod
    def from_firestore(cls, doc_id: str, data: dict[str, Any]) -> FollowupDocument:
        """Create from Firestore document."""
        return cls(
            id=doc_id,
            original_draft_id=data.get("original_draft_id", ""),
            followup_number=data.get("followup_number", 1),
            subject=data.get("subject", ""),
            body=data.get("body") or data.get("content", ""),
            recipient_email=data.get("recipient_email") or data.get("to_address", ""),
            recipient_name=data.get("recipient_name") or data.get("to_name", ""),
            sender_email=data.get("sender_email") or data.get("from_address", ""),
            sender_name=data.get("sender_name") or data.get("from_name", ""),
            company_name=data.get("company_name", ""),
            status=DraftStatus(data.get("status", "pending")),
            created_at=data.get("created_at"),
            sent_at=data.get("sent_at"),
            message_id=data.get("message_id"),
            thread_id=data.get("thread_id")
        )


# ============================================================================
# Statistics Models
# ============================================================================

class DraftStats(BaseModel):
    """Aggregated draft statistics."""
    
    total_drafts: int = 0
    pending_count: int = 0
    sent_count: int = 0
    replied_count: int = 0
    bounced_count: int = 0
    rejected_count: int = 0
    
    # Open rates
    total_opens: int = 0
    unique_opens: int = 0
    open_rate: float = 0.0
    
    # Reply rates
    reply_rate: float = 0.0
    
    # Time metrics
    avg_response_time_hours: Optional[float] = None


class DailyActivity(BaseModel):
    """Daily activity metrics."""
    
    date: str
    sent: int = 0
    opens: int = 0
    replies: int = 0
    bounces: int = 0


class DashboardData(BaseModel):
    """Complete dashboard data."""
    
    stats: DraftStats
    activity: list[DailyActivity] = []
    recent_replies: list[DraftDocument] = []
    pending_actions: int = 0


# ============================================================================
# Request Models
# ============================================================================

class UpdateNotesRequest(BaseModel):
    """Request to update draft notes."""
    
    notes: str = Field(default="", max_length=5000)


class SendTestEmailRequest(BaseModel):
    """Request to send test email."""
    
    draft_id: str
    test_email: EmailStr


class ResendRequest(BaseModel):
    """Request to resend to another address."""
    
    draft_id: str
    new_recipient_email: EmailStr
    new_recipient_name: str = ""


class UpdateDraftRequest(BaseModel):
    """Request to update draft content."""
    
    subject: Optional[str] = None
    body: Optional[str] = None
    notes: Optional[str] = None
    status: Optional[DraftStatus] = None


# ============================================================================
# Response Models
# ============================================================================

class APIResponse(BaseModel):
    """Standard API response."""
    
    success: bool = True
    message: str = ""
    data: Optional[dict[str, Any]] = None


class ErrorResponse(BaseModel):
    """Error response."""
    
    error: bool = True
    message: str
    code: Optional[str] = None


class PaginatedResponse(BaseModel):
    """Paginated response."""
    
    items: list[Any]
    total: int
    page: int
    page_size: int
    has_more: bool
