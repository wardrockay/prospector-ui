"""
Draft Service
=============

Business logic for draft operations.
"""

from __future__ import annotations

from typing import Any, Optional

import httpx

from src.config import get_settings
from src.models import (
    DailyActivity,
    DashboardData,
    DraftDocument,
    DraftStats,
    DraftStatus,
    FilterTab,
    FollowupDocument,
)
from src.repositories import get_repository


class DraftService:
    """
    Service layer for draft operations.
    
    Orchestrates repository operations and external service calls.
    """
    
    def __init__(self) -> None:
        """Initialize service with dependencies."""
        self._repo = get_repository()
        self._settings = get_settings()
        self._http_client = httpx.Client(timeout=30.0)
    
    def __del__(self) -> None:
        """Clean up HTTP client."""
        if hasattr(self, "_http_client"):
            self._http_client.close()
    
    # ========================================================================
    # Draft Retrieval
    # ========================================================================
    
    def get_draft(self, draft_id: str) -> Optional[DraftDocument]:
        """Get a draft by ID."""
        return self._repo.get_draft(draft_id)
    
    def get_pending_drafts(self, limit: int = 100) -> list[DraftDocument]:
        """Get all pending drafts."""
        return self._repo.get_pending_drafts(limit=limit)
    
    def get_sent_drafts(
        self,
        filter_tab: FilterTab = FilterTab.ALL,
        limit: int = 100
    ) -> list[DraftDocument]:
        """Get sent drafts with filtering."""
        return self._repo.get_sent_drafts(filter_tab=filter_tab, limit=limit)
    
    def count_pending(self) -> int:
        """Count pending drafts."""
        return self._repo.count_pending()
    
    # ========================================================================
    # Draft Actions
    # ========================================================================
    
    def approve_draft(self, draft_id: str) -> dict[str, Any]:
        """
        Approve and send a draft.
        
        Args:
            draft_id: Draft document ID.
            
        Returns:
            Result from draft-creator service.
        """
        response = self._http_client.post(
            f"{self._settings.services.draft_creator_url}/send-draft",
            json={"draft_id": draft_id, "test_mode": False}
        )
        response.raise_for_status()
        return response.json()
    
    def reject_draft(self, draft_id: str) -> None:
        """Reject a draft."""
        self._repo.update_draft_status(draft_id, DraftStatus.REJECTED)
    
    def send_test_email(
        self,
        draft_id: str,
        test_email: str
    ) -> dict[str, Any]:
        """
        Send a test email.
        
        Args:
            draft_id: Draft document ID.
            test_email: Email to send test to.
            
        Returns:
            Result from draft-creator service.
        """
        response = self._http_client.post(
            f"{self._settings.services.draft_creator_url}/send-draft",
            json={
                "draft_id": draft_id,
                "test_mode": True,
                "test_email": test_email
            }
        )
        response.raise_for_status()
        return response.json()
    
    def resend_to_another(
        self,
        draft_id: str,
        new_recipient_email: str,
        new_recipient_name: str = ""
    ) -> dict[str, Any]:
        """
        Resend a draft to a different address.
        
        Args:
            draft_id: Original draft ID.
            new_recipient_email: New recipient.
            new_recipient_name: New recipient name.
            
        Returns:
            Result from draft-creator service.
        """
        response = self._http_client.post(
            f"{self._settings.services.draft_creator_url}/resend-to-another",
            json={
                "draft_id": draft_id,
                "new_recipient_email": new_recipient_email,
                "new_recipient_name": new_recipient_name
            }
        )
        response.raise_for_status()
        return response.json()
    
    def update_notes(self, draft_id: str, notes: str) -> None:
        """Update draft notes."""
        self._repo.update_notes(draft_id, notes)
    
    def update_draft(self, draft_id: str, data: dict[str, Any]) -> None:
        """Update draft fields."""
        self._repo.update_draft(draft_id, data)
    
    def delete_rejected_drafts(self) -> int:
        """Delete all rejected drafts."""
        return self._repo.delete_rejected_drafts()
    
    # ========================================================================
    # Followup Operations
    # ========================================================================
    
    def get_followups(self, draft_id: str) -> list[FollowupDocument]:
        """Get followups for a draft."""
        return self._repo.get_followups_for_draft(draft_id)
    
    def get_followup(self, followup_id: str) -> Optional[FollowupDocument]:
        """Get a followup by ID."""
        return self._repo.get_followup(followup_id)
    
    def generate_followup(
        self,
        draft_id: str,
        followup_number: int
    ) -> dict[str, Any]:
        """
        Generate a followup email.
        
        Args:
            draft_id: Original draft ID.
            followup_number: Followup sequence number.
            
        Returns:
            Generated followup data.
        """
        response = self._http_client.post(
            f"{self._settings.services.auto_followup_url}/generate",
            json={
                "draft_id": draft_id,
                "followup_number": followup_number
            }
        )
        response.raise_for_status()
        return response.json()
    
    def send_followup(
        self,
        followup_id: str,
        test_mode: bool = False,
        test_email: Optional[str] = None
    ) -> dict[str, Any]:
        """
        Send a followup email.
        
        Args:
            followup_id: Followup document ID.
            test_mode: Whether to send as test.
            test_email: Test email address.
            
        Returns:
            Result from draft-creator service.
        """
        payload = {
            "followup_id": followup_id,
            "test_mode": test_mode
        }
        if test_email:
            payload["test_email"] = test_email
        
        response = self._http_client.post(
            f"{self._settings.services.draft_creator_url}/send-followup",
            json=payload
        )
        response.raise_for_status()
        return response.json()
    
    # ========================================================================
    # Tracking & Analytics
    # ========================================================================
    
    def get_opens(self, draft_id: str) -> list[dict[str, Any]]:
        """Get open events for a draft."""
        return self._repo.get_opens_for_draft(draft_id)
    
    def get_thread_messages(self, draft_id: str) -> list[dict[str, Any]]:
        """Get thread messages for a draft."""
        return self._repo.get_thread_messages(draft_id)
    
    def get_stats(self) -> DraftStats:
        """Get overall statistics."""
        return self._repo.get_stats()
    
    def get_daily_activity(self, days: int = 30) -> list[DailyActivity]:
        """Get daily activity data."""
        return self._repo.get_daily_activity(days=days)
    
    def get_dashboard_data(self) -> DashboardData:
        """
        Get complete dashboard data.
        
        Returns:
            DashboardData with stats, activity, and pending actions.
        """
        stats = self.get_stats()
        activity = self.get_daily_activity(days=30)
        
        # Get recent replies
        replied_drafts = self._repo.get_sent_drafts(
            filter_tab=FilterTab.REPLIED,
            limit=5
        )
        
        return DashboardData(
            stats=stats,
            activity=activity,
            recent_replies=replied_drafts,
            pending_actions=stats.pending_count
        )


# Singleton instance
_service: Optional[DraftService] = None


def get_draft_service() -> DraftService:
    """Get global draft service instance."""
    global _service
    if _service is None:
        _service = DraftService()
    return _service
