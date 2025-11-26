"""
Firestore Repository
====================

Data access layer for Firestore operations.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Optional

from google.cloud import firestore
from google.cloud.firestore_v1.base_query import FieldFilter

from src.config import get_settings
from src.models import (
    DailyActivity,
    DraftDocument,
    DraftStats,
    DraftStatus,
    FilterTab,
    FollowupDocument,
)


class DraftRepository:
    """
    Repository for email draft operations.
    
    Provides clean data access methods with proper typing
    and query optimization.
    """
    
    def __init__(self, client: Optional[firestore.Client] = None) -> None:
        """Initialize repository with Firestore client."""
        self._client = client or firestore.Client()
        self._settings = get_settings()
        self._drafts_col = self._settings.firestore.drafts_collection
        self._followups_col = self._settings.firestore.followups_collection
        self._opens_col = self._settings.firestore.opens_collection
    
    @property
    def db(self) -> firestore.Client:
        """Get Firestore client."""
        return self._client
    
    # ========================================================================
    # Draft Queries
    # ========================================================================
    
    def get_draft(self, draft_id: str) -> Optional[DraftDocument]:
        """
        Get a single draft by ID.
        
        Args:
            draft_id: Document ID.
            
        Returns:
            DraftDocument or None if not found.
        """
        doc = self._client.collection(self._drafts_col).document(draft_id).get()
        if not doc.exists:
            return None
        return DraftDocument.from_firestore(doc.id, doc.to_dict() or {})
    
    def get_draft_raw(self, draft_id: str) -> Optional[dict[str, Any]]:
        """Get raw draft data."""
        doc = self._client.collection(self._drafts_col).document(draft_id).get()
        return doc.to_dict() if doc.exists else None
    
    def get_pending_drafts(
        self,
        limit: int = 100,
        offset: int = 0
    ) -> list[DraftDocument]:
        """
        Get all pending drafts ordered by creation date.
        
        Args:
            limit: Maximum results.
            offset: Number of documents to skip.
            
        Returns:
            List of pending drafts.
        """
        query = (
            self._client.collection(self._drafts_col)
            .where(filter=FieldFilter("status", "==", DraftStatus.PENDING.value))
            .order_by("created_at", direction=firestore.Query.DESCENDING)
            .limit(limit)
        )
        
        if offset > 0:
            query = query.offset(offset)
        
        return [
            DraftDocument.from_firestore(doc.id, doc.to_dict() or {})
            for doc in query.stream()
        ]
    
    def get_sent_drafts(
        self,
        filter_tab: FilterTab = FilterTab.ALL,
        limit: int = 100
    ) -> list[DraftDocument]:
        """
        Get sent drafts with optional filtering.
        
        Args:
            filter_tab: Filter to apply.
            limit: Maximum results.
            
        Returns:
            List of sent drafts.
        """
        query = self._client.collection(self._drafts_col)
        
        if filter_tab == FilterTab.BOUNCED:
            query = query.where(filter=FieldFilter("status", "==", DraftStatus.BOUNCED.value))
        elif filter_tab == FilterTab.REPLIED:
            query = query.where(filter=FieldFilter("has_reply", "==", True))
        elif filter_tab == FilterTab.READ:
            query = query.where(filter=FieldFilter("status", "==", DraftStatus.SENT.value))
            query = query.where(filter=FieldFilter("open_count", ">", 0))
        elif filter_tab == FilterTab.UNREAD:
            query = query.where(filter=FieldFilter("status", "==", DraftStatus.SENT.value))
            query = query.where(filter=FieldFilter("open_count", "==", 0))
        else:
            query = query.where(filter=FieldFilter("status", "==", DraftStatus.SENT.value))
        
        query = query.order_by("sent_at", direction=firestore.Query.DESCENDING)
        query = query.limit(limit)
        
        return [
            DraftDocument.from_firestore(doc.id, doc.to_dict() or {})
            for doc in query.stream()
        ]
    
    def count_pending(self) -> int:
        """Count pending drafts."""
        query = (
            self._client.collection(self._drafts_col)
            .where(filter=FieldFilter("status", "==", DraftStatus.PENDING.value))
        )
        return len(list(query.stream()))
    
    # ========================================================================
    # Draft Mutations
    # ========================================================================
    
    def update_draft(self, draft_id: str, data: dict[str, Any]) -> None:
        """Update a draft document."""
        self._client.collection(self._drafts_col).document(draft_id).update(data)
    
    def update_draft_status(
        self,
        draft_id: str,
        status: DraftStatus,
        extra_data: Optional[dict[str, Any]] = None
    ) -> None:
        """Update draft status with optional extra data."""
        data = {"status": status.value}
        if extra_data:
            data.update(extra_data)
        self.update_draft(draft_id, data)
    
    def update_notes(self, draft_id: str, notes: str) -> None:
        """Update draft notes."""
        self.update_draft(draft_id, {"notes": notes})
    
    def delete_draft(self, draft_id: str) -> None:
        """Delete a draft document."""
        self._client.collection(self._drafts_col).document(draft_id).delete()
    
    def delete_rejected_drafts(self) -> int:
        """
        Delete all rejected drafts.
        
        Returns:
            Number of deleted documents.
        """
        query = (
            self._client.collection(self._drafts_col)
            .where(filter=FieldFilter("status", "==", DraftStatus.REJECTED.value))
        )
        
        count = 0
        batch = self._client.batch()
        
        for doc in query.stream():
            batch.delete(doc.reference)
            count += 1
            
            # Commit in batches of 500
            if count % 500 == 0:
                batch.commit()
                batch = self._client.batch()
        
        if count % 500 != 0:
            batch.commit()
        
        return count
    
    # ========================================================================
    # Followup Operations
    # ========================================================================
    
    def get_followups_for_draft(self, draft_id: str) -> list[FollowupDocument]:
        """Get all followups for a draft."""
        query = (
            self._client.collection(self._followups_col)
            .where(filter=FieldFilter("original_draft_id", "==", draft_id))
            .order_by("followup_number")
        )
        
        return [
            FollowupDocument.from_firestore(doc.id, doc.to_dict() or {})
            for doc in query.stream()
        ]
    
    def get_followup(self, followup_id: str) -> Optional[FollowupDocument]:
        """Get a followup by ID."""
        doc = self._client.collection(self._followups_col).document(followup_id).get()
        if not doc.exists:
            return None
        return FollowupDocument.from_firestore(doc.id, doc.to_dict() or {})
    
    def create_followup(self, data: dict[str, Any]) -> str:
        """Create a new followup document."""
        doc_ref = self._client.collection(self._followups_col).document()
        data["created_at"] = datetime.utcnow()
        doc_ref.set(data)
        return doc_ref.id
    
    # ========================================================================
    # Open Tracking
    # ========================================================================
    
    def get_opens_for_draft(self, draft_id: str) -> list[dict[str, Any]]:
        """Get all open events for a draft."""
        query = (
            self._client.collection(self._opens_col)
            .document(draft_id)
            .collection("opens")
            .order_by("timestamp", direction=firestore.Query.DESCENDING)
            .limit(50)
        )
        
        return [doc.to_dict() or {} for doc in query.stream()]
    
    def get_thread_messages(self, draft_id: str) -> list[dict[str, Any]]:
        """Get thread messages for a draft."""
        query = (
            self._client.collection(self._opens_col)
            .document(draft_id)
            .collection("thread_messages")
            .order_by("timestamp", direction=firestore.Query.ASCENDING)
        )
        
        return [doc.to_dict() or {} for doc in query.stream()]
    
    # ========================================================================
    # Statistics
    # ========================================================================
    
    def get_stats(self) -> DraftStats:
        """Calculate overall statistics."""
        drafts = list(self._client.collection(self._drafts_col).stream())
        
        stats = DraftStats()
        stats.total_drafts = len(drafts)
        
        total_opens = 0
        drafts_with_opens = 0
        replied_count = 0
        
        for doc in drafts:
            data = doc.to_dict() or {}
            status = data.get("status", "pending")
            
            if status == "pending":
                stats.pending_count += 1
            elif status == "sent":
                stats.sent_count += 1
            elif status == "bounced":
                stats.bounced_count += 1
            elif status == "rejected":
                stats.rejected_count += 1
            
            # Open tracking
            open_count = data.get("open_count", 0)
            if open_count > 0:
                total_opens += open_count
                drafts_with_opens += 1
            
            # Reply tracking
            if data.get("has_reply"):
                replied_count += 1
        
        stats.replied_count = replied_count
        stats.total_opens = total_opens
        stats.unique_opens = drafts_with_opens
        
        # Calculate rates
        if stats.sent_count > 0:
            stats.open_rate = (drafts_with_opens / stats.sent_count) * 100
            stats.reply_rate = (replied_count / stats.sent_count) * 100
        
        return stats
    
    def get_daily_activity(self, days: int = 30) -> list[DailyActivity]:
        """
        Get daily activity for the last N days.
        
        Args:
            days: Number of days to look back.
            
        Returns:
            List of DailyActivity records.
        """
        cutoff = datetime.utcnow() - timedelta(days=days)
        
        # Query sent emails in date range
        query = (
            self._client.collection(self._drafts_col)
            .where(filter=FieldFilter("sent_at", ">=", cutoff))
        )
        
        # Aggregate by day
        daily_data: dict[str, DailyActivity] = {}
        
        for doc in query.stream():
            data = doc.to_dict() or {}
            sent_at = data.get("sent_at")
            if sent_at:
                date_str = sent_at.strftime("%Y-%m-%d")
                
                if date_str not in daily_data:
                    daily_data[date_str] = DailyActivity(date=date_str)
                
                daily_data[date_str].sent += 1
                
                if data.get("has_reply"):
                    daily_data[date_str].replies += 1
                if data.get("status") == "bounced":
                    daily_data[date_str].bounces += 1
                if data.get("open_count", 0) > 0:
                    daily_data[date_str].opens += 1
        
        # Sort by date
        return sorted(daily_data.values(), key=lambda x: x.date)


# Singleton instance
_repository: Optional[DraftRepository] = None


def get_repository() -> DraftRepository:
    """Get global repository instance."""
    global _repository
    if _repository is None:
        _repository = DraftRepository()
    return _repository
