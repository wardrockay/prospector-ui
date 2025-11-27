"""
Flask Blueprints
================

Modular Flask blueprints for route organization.
"""

from __future__ import annotations

import os
from datetime import datetime

from flask import Blueprint, flash, jsonify, redirect, render_template, request, url_for
from google.cloud import firestore
import google.auth
from google.auth.transport.requests import Request as GoogleRequest
import markdown
import requests as http_requests

from src.models import DraftStatus, FilterTab

# Markdown extensions
MARKDOWN_EXTENSIONS = ["nl2br", "tables", "fenced_code", "sane_lists"]

# Configuration
DRAFT_COLLECTION = os.environ.get("DRAFT_COLLECTION", "email_drafts")
FOLLOWUP_COLLECTION = os.environ.get("FOLLOWUP_COLLECTION", "email_followups")
PIXEL_COLLECTION = os.environ.get("PIXEL_COLLECTION", "email_opens")
SEND_MAIL_SERVICE_URL = os.environ.get("SEND_MAIL_SERVICE_URL", "").rstrip("/")
AUTO_FOLLOWUP_URL = os.environ.get("AUTO_FOLLOWUP_URL", "").rstrip("/")
ODOO_DB_URL = os.environ.get("ODOO_DB_URL", "").rstrip("/")
ODOO_SECRET = os.environ.get("ODOO_SECRET", "")
MAIL_WRITER_URL = os.environ.get("MAIL_WRITER_URL", "").rstrip("/")
GMAIL_NOTIFIER_URL = os.environ.get("GMAIL_NOTIFIER_URL", "").rstrip("/")

# Firestore client
db = firestore.Client()


def get_id_token(target_audience: str) -> str:
    """Generate an ID token for authenticating calls to other Cloud Run services."""
    try:
        credentials, project_id = google.auth.default()
        credentials.refresh(GoogleRequest())
        
        if hasattr(credentials, 'id_token'):
            return credentials.id_token
        
        sa_email = credentials.service_account_email if hasattr(credentials, 'service_account_email') else None
        
        if not sa_email:
            sa_email = "prospector-ui-sa@handy-resolver-477513-a1.iam.gserviceaccount.com"
        
        url = f"https://iamcredentials.googleapis.com/v1/projects/-/serviceAccounts/{sa_email}:generateIdToken"
        
        headers = {
            "Authorization": f"Bearer {credentials.token}",
            "Content-Type": "application/json",
        }
        
        payload = {
            "audience": target_audience,
            "includeEmail": True
        }
        
        response = http_requests.post(url, json=payload, headers=headers, timeout=10)
        response.raise_for_status()
        
        return response.json()["token"]
        
    except Exception as e:
        print(f"[ERROR] Error generating ID token: {e}")
        raise


def render_markdown(text: str) -> str:
    """Convert markdown to HTML."""
    return markdown.markdown(text, extensions=MARKDOWN_EXTENSIONS)


# ============================================================================
# Main Blueprint (Drafts)
# ============================================================================

main_bp = Blueprint("main", __name__)


@main_bp.route("/")
def index():
    """Show pending drafts - main page."""
    try:
        drafts_ref = db.collection(DRAFT_COLLECTION).where("status", "==", "pending").order_by("created_at", direction=firestore.Query.DESCENDING)
        
        grouped_drafts = {}
        
        for doc in drafts_ref.stream():
            draft_data = doc.to_dict()
            draft_data["id"] = doc.id
            
            group_key = draft_data.get("version_group_id", doc.id)
            
            if group_key not in grouped_drafts:
                grouped_drafts[group_key] = {"versions": [], "latest": None}
            
            grouped_drafts[group_key]["versions"].append(draft_data)
        
        drafts = []
        for group_key, group_data in grouped_drafts.items():
            versions = group_data["versions"]
            versions.sort(key=lambda x: x.get("created_at", datetime.min), reverse=True)
            
            latest = versions[0]
            latest["version_count"] = len(versions)
            latest["all_version_ids"] = [v["id"] for v in versions]
            
            drafts.append(latest)
        
        drafts.sort(key=lambda x: x.get("created_at", datetime.min), reverse=True)
        
        return render_template("index.html", drafts=drafts)
    
    except Exception as e:
        flash(f"Erreur lors de la récupération des drafts: {str(e)}", "error")
        return render_template("index.html", drafts=[])


@main_bp.route("/draft/<draft_id>")
def draft_detail(draft_id: str):
    """Show draft details."""
    try:
        doc_ref = db.collection(DRAFT_COLLECTION).document(draft_id)
        doc = doc_ref.get()
        
        if not doc.exists:
            flash("Draft non trouvé", "error")
            return redirect(url_for("main.index"))
        
        draft_data = doc.to_dict()
        draft_data["id"] = doc.id
        
        versions = []
        version_group_id = draft_data.get("version_group_id")
        
        if version_group_id:
            versions_ref = db.collection(DRAFT_COLLECTION).where("version_group_id", "==", version_group_id).where("status", "==", "pending").order_by("created_at")
            
            for idx, version_doc in enumerate(versions_ref.stream()):
                version_data = version_doc.to_dict()
                version_data["id"] = version_doc.id
                version_data["version_number"] = idx + 1
                version_data["is_current"] = version_doc.id == draft_id
                versions.append(version_data)
        
        if not versions:
            draft_data["version_number"] = 1
            versions = [draft_data]
        
        return render_template("draft_detail.html", draft=draft_data, versions=versions)
    
    except Exception as e:
        flash(f"Erreur: {str(e)}", "error")
        return redirect(url_for("main.index"))


@main_bp.route("/send/<draft_id>", methods=["POST"])
def send_draft(draft_id: str):
    """Send a draft via the send_mail service."""
    try:
        if not SEND_MAIL_SERVICE_URL:
            flash("Service d'envoi non configuré (SEND_MAIL_SERVICE_URL manquant)", "error")
            return redirect(url_for("main.draft_detail", draft_id=draft_id))
        
        id_token = get_id_token(SEND_MAIL_SERVICE_URL)
        
        response = http_requests.post(
            f"{SEND_MAIL_SERVICE_URL}/send-draft",
            json={"draft_id": draft_id},
            headers={"Authorization": f"Bearer {id_token}"},
            timeout=30
        )
        
        if response.status_code == 200:
            result = response.json()
            flash(f"Email envoyé avec succès! Message ID: {result.get('message_id')}", "success")
            
            doc_ref = db.collection(DRAFT_COLLECTION).document(draft_id)
            doc = doc_ref.get()
            
            if doc.exists:
                draft_data = doc.to_dict()
                version_group_id = draft_data.get("version_group_id")
                
                if version_group_id:
                    other_versions_ref = db.collection(DRAFT_COLLECTION).where("version_group_id", "==", version_group_id).where("status", "==", "pending")
                    rejected_count = 0
                    
                    for other_doc in other_versions_ref.stream():
                        if other_doc.id != draft_id:
                            other_doc.reference.update({
                                "status": "rejected",
                                "rejected_at": datetime.utcnow(),
                                "auto_rejected": True,
                                "rejected_reason": f"Autre version envoyée (draft {draft_id})"
                            })
                            rejected_count += 1
                    
                    if rejected_count > 0:
                        flash(f"{rejected_count} autre(s) version(s) automatiquement rejetée(s)", "info")
            
            if AUTO_FOLLOWUP_URL:
                try:
                    followup_response = http_requests.post(
                        f"{AUTO_FOLLOWUP_URL}/schedule-followups",
                        json={"draft_id": draft_id},
                        timeout=10
                    )
                    if followup_response.status_code == 200:
                        followup_result = followup_response.json()
                        flash(f"Relances planifiées: {followup_result.get('followups_created', 0)}", "info")
                except Exception as e:
                    print(f"Erreur lors de la planification des relances: {str(e)}")
            
            return redirect(url_for("main.index"))
        else:
            error_msg = response.json().get("error", "Erreur inconnue")
            flash(f"Erreur lors de l'envoi: {error_msg}", "error")
            return redirect(url_for("main.draft_detail", draft_id=draft_id))
    
    except Exception as e:
        flash(f"Erreur lors de l'envoi: {str(e)}", "error")
        return redirect(url_for("main.draft_detail", draft_id=draft_id))


@main_bp.route("/send-test/<draft_id>", methods=["POST"])
def send_test_draft(draft_id: str):
    """Send a test email without tracking or status change."""
    try:
        test_email = request.form.get("test_email", "").strip()
        
        if not test_email:
            flash("Adresse email de test manquante", "error")
            return redirect(url_for("main.draft_detail", draft_id=draft_id))
        
        if not SEND_MAIL_SERVICE_URL:
            flash("Service d'envoi non configuré (SEND_MAIL_SERVICE_URL manquant)", "error")
            return redirect(url_for("main.draft_detail", draft_id=draft_id))
        
        doc_ref = db.collection(DRAFT_COLLECTION).document(draft_id)
        doc = doc_ref.get()
        
        if not doc.exists:
            flash("Draft non trouvé", "error")
            return redirect(url_for("main.index"))
        
        id_token = get_id_token(SEND_MAIL_SERVICE_URL)
        
        response = http_requests.post(
            f"{SEND_MAIL_SERVICE_URL}/send-draft",
            json={
                "draft_id": draft_id,
                "test_mode": True,
                "test_email": test_email
            },
            headers={"Authorization": f"Bearer {id_token}"},
            timeout=30
        )
        
        if response.status_code == 200:
            flash(f"Mail de test envoyé avec succès à {test_email}!", "success")
            return redirect(url_for("main.draft_detail", draft_id=draft_id))
        else:
            error_msg = response.json().get("error", "Erreur inconnue")
            flash(f"Erreur lors de l'envoi du test: {error_msg}", "error")
            return redirect(url_for("main.draft_detail", draft_id=draft_id))
    
    except Exception as e:
        flash(f"Erreur lors de l'envoi du test: {str(e)}", "error")
        return redirect(url_for("main.draft_detail", draft_id=draft_id))


@main_bp.route("/change-email-and-send/<draft_id>", methods=["POST"])
def change_email_and_send(draft_id: str):
    """Change the email address and send the draft."""
    try:
        new_email = request.form.get("new_email", "").strip()
        
        if not new_email:
            flash("Nouvelle adresse email manquante", "error")
            return redirect(url_for("main.draft_detail", draft_id=draft_id))
        
        doc_ref = db.collection(DRAFT_COLLECTION).document(draft_id)
        doc = doc_ref.get()
        
        if not doc.exists:
            flash("Draft non trouvé", "error")
            return redirect(url_for("main.index"))
        
        doc_ref.update({
            "to": new_email,
            "email_changed": True,
            "original_email": doc.to_dict().get("to"),
            "email_changed_at": datetime.utcnow()
        })
        
        flash(f"Adresse email mise à jour vers {new_email}", "info")
        
        if not SEND_MAIL_SERVICE_URL:
            flash("Service d'envoi non configuré (SEND_MAIL_SERVICE_URL manquant)", "error")
            return redirect(url_for("main.draft_detail", draft_id=draft_id))
        
        id_token = get_id_token(SEND_MAIL_SERVICE_URL)
        
        response = http_requests.post(
            f"{SEND_MAIL_SERVICE_URL}/send-draft",
            json={"draft_id": draft_id},
            headers={"Authorization": f"Bearer {id_token}"},
            timeout=30
        )
        
        if response.status_code == 200:
            result = response.json()
            flash(f"Email envoyé avec succès à {new_email}! Message ID: {result.get('message_id')}", "success")
            
            doc = doc_ref.get()
            if doc.exists:
                draft_data = doc.to_dict()
                version_group_id = draft_data.get("version_group_id")
                
                if version_group_id:
                    other_versions_ref = db.collection(DRAFT_COLLECTION).where("version_group_id", "==", version_group_id).where("status", "==", "pending")
                    rejected_count = 0
                    
                    for other_doc in other_versions_ref.stream():
                        if other_doc.id != draft_id:
                            other_doc.reference.update({
                                "status": "rejected",
                                "rejected_at": datetime.utcnow(),
                                "auto_rejected": True,
                                "rejected_reason": f"Autre version envoyée (draft {draft_id})"
                            })
                            rejected_count += 1
                    
                    if rejected_count > 0:
                        flash(f"{rejected_count} autre(s) version(s) automatiquement rejetée(s)", "info")
            
            if AUTO_FOLLOWUP_URL:
                try:
                    followup_response = http_requests.post(
                        f"{AUTO_FOLLOWUP_URL}/schedule-followups",
                        json={"draft_id": draft_id},
                        timeout=10
                    )
                    if followup_response.status_code == 200:
                        followup_result = followup_response.json()
                        flash(f"Relances planifiées: {followup_result.get('followups_created', 0)}", "info")
                except Exception as e:
                    print(f"Erreur lors de la planification des relances: {str(e)}")
            
            return redirect(url_for("main.index"))
        else:
            error_msg = response.json().get("error", "Erreur inconnue")
            flash(f"Erreur lors de l'envoi: {error_msg}", "error")
            return redirect(url_for("main.draft_detail", draft_id=draft_id))
    
    except Exception as e:
        flash(f"Erreur: {str(e)}", "error")
        return redirect(url_for("main.draft_detail", draft_id=draft_id))


@main_bp.route("/reject/<draft_id>", methods=["POST"])
def reject_draft(draft_id: str):
    """Reject a draft."""
    try:
        doc_ref = db.collection(DRAFT_COLLECTION).document(draft_id)
        doc = doc_ref.get()
        
        if not doc.exists:
            flash("Draft non trouvé", "error")
            return redirect(url_for("main.index"))
        
        doc_ref.update({
            "status": "rejected",
            "rejected_at": datetime.utcnow()
        })
        
        flash("Draft rejeté", "success")
        return redirect(url_for("main.index"))
    
    except Exception as e:
        flash(f"Erreur: {str(e)}", "error")
        return redirect(url_for("main.index"))


@main_bp.route("/edit/<draft_id>", methods=["POST"])
def edit_draft(draft_id: str):
    """Create a new version of the draft with manual edits."""
    try:
        new_subject = request.form.get("subject", "").strip()
        new_body = request.form.get("body", "").strip()
        
        if not new_subject or not new_body:
            flash("Le sujet et le corps du message ne peuvent pas être vides", "error")
            return redirect(url_for("main.draft_detail", draft_id=draft_id))
        
        doc_ref = db.collection(DRAFT_COLLECTION).document(draft_id)
        doc = doc_ref.get()
        
        if not doc.exists:
            flash("Draft non trouvé", "error")
            return redirect(url_for("main.index"))
        
        original_data = doc.to_dict()
        
        new_draft_data = {
            "to": original_data.get("to"),
            "subject": new_subject,
            "body": new_body,
            "status": "pending",
            "created_at": datetime.utcnow(),
            "x_external_id": original_data.get("x_external_id"),
            "version_group_id": original_data.get("version_group_id"),
            "odoo_id": original_data.get("odoo_id"),
            "manually_edited": True,
            "edited_from_draft_id": draft_id
        }
        
        if "contact_info" in original_data:
            new_draft_data["contact_info"] = original_data["contact_info"]
        
        new_draft_ref = db.collection(DRAFT_COLLECTION).add(new_draft_data)
        new_draft_id = new_draft_ref[1].id
        
        flash("Nouvelle version du draft créée avec vos modifications", "success")
        return redirect(url_for("main.draft_detail", draft_id=new_draft_id))
    
    except Exception as e:
        flash(f"Erreur lors de la modification: {str(e)}", "error")
        return redirect(url_for("main.draft_detail", draft_id=draft_id))


@main_bp.route("/regenerate/<draft_id>", methods=["POST"])
def regenerate_draft(draft_id: str):
    """Regenerate a draft by fetching data from Odoo."""
    try:
        doc_ref = db.collection(DRAFT_COLLECTION).document(draft_id)
        doc = doc_ref.get()
        
        if not doc.exists:
            flash("Draft non trouvé", "error")
            return redirect(url_for("main.index"))
        
        draft_data = doc.to_dict()
        x_external_id = draft_data.get("x_external_id")
        version_group_id = draft_data.get("version_group_id")
        
        if not x_external_id:
            flash("Impossible de régénérer: x_external_id manquant", "error")
            return redirect(url_for("main.draft_detail", draft_id=draft_id))
        
        if not ODOO_DB_URL or not ODOO_SECRET:
            flash("Configuration Odoo manquante (ODOO_DB_URL ou ODOO_SECRET)", "error")
            return redirect(url_for("main.draft_detail", draft_id=draft_id))
        
        if not MAIL_WRITER_URL:
            flash("Configuration mail_writer manquante (MAIL_WRITER_URL)", "error")
            return redirect(url_for("main.draft_detail", draft_id=draft_id))
        
        odoo_url = f"{ODOO_DB_URL}/json/2/crm.lead/search_read"
        odoo_payload = {
            "domain": [["x_external_id", "ilike", x_external_id]],
            "fields": [
                "id", "email_normalized", "website", "contact_name",
                "partner_name", "function", "description"
            ]
        }
        odoo_headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {ODOO_SECRET}"
        }
        
        odoo_response = http_requests.post(odoo_url, json=odoo_payload, headers=odoo_headers, timeout=15)
        odoo_response.raise_for_status()
        odoo_data = odoo_response.json()
        
        if not odoo_data or len(odoo_data) == 0:
            flash(f"Aucun lead trouvé dans Odoo avec x_external_id: {x_external_id}", "error")
            return redirect(url_for("main.draft_detail", draft_id=draft_id))
        
        lead = odoo_data[0]
        odoo_id = lead.get("id")
        contact_name = lead.get("contact_name", "")
        name_parts = contact_name.split(" ", 1) if contact_name else ["", ""]
        first_name = name_parts[0] if len(name_parts) > 0 else ""
        last_name = name_parts[1] if len(name_parts) > 1 else ""
        
        mail_writer_payload = {
            "first_name": first_name,
            "last_name": last_name,
            "email": lead.get("email_normalized", ""),
            "website": lead.get("website", ""),
            "partner_name": lead.get("partner_name", ""),
            "function": lead.get("function", ""),
            "description": lead.get("description", ""),
            "x_external_id": x_external_id,
            "version_group_id": version_group_id,
            "odoo_id": odoo_id
        }
        
        mail_writer_response = http_requests.post(MAIL_WRITER_URL, json=mail_writer_payload, timeout=60)
        mail_writer_response.raise_for_status()
        mail_writer_data = mail_writer_response.json()
        
        new_draft_id = mail_writer_data.get("draft", {}).get("draft_id")
        
        if new_draft_id:
            flash(f"Nouvelle version du mail générée avec succès!", "success")
            return redirect(url_for("main.draft_detail", draft_id=new_draft_id))
        else:
            flash("Mail régénéré mais impossible de récupérer le nouveau draft", "warning")
            return redirect(url_for("main.index"))
    
    except http_requests.exceptions.RequestException as e:
        flash(f"Erreur lors de la communication avec les services: {str(e)}", "error")
        return redirect(url_for("main.draft_detail", draft_id=draft_id))
    except Exception as e:
        flash(f"Erreur lors de la régénération: {str(e)}", "error")
        return redirect(url_for("main.draft_detail", draft_id=draft_id))


# ============================================================================
# API Blueprint
# ============================================================================

api_bp = Blueprint("api", __name__, url_prefix="/api")


@api_bp.route("/draft/<draft_id>/notes", methods=["GET", "POST"])
def draft_notes(draft_id: str):
    """Get or update draft notes."""
    try:
        if request.method == "GET":
            doc_ref = db.collection(DRAFT_COLLECTION).document(draft_id)
            doc = doc_ref.get()
            return jsonify({"notes": doc.to_dict().get("notes", "") if doc.exists else ""})
        
        data = request.get_json()
        notes = data.get("notes", "")
        
        doc_ref = db.collection(DRAFT_COLLECTION).document(draft_id)
        doc_ref.update({
            "notes": notes,
            "notes_updated_at": datetime.utcnow()
        })
        
        return jsonify({"status": "ok"})
    
    except Exception as e:
        return jsonify({"status": "error", "error": str(e)}), 500


@api_bp.route("/stats")
def get_stats():
    """Get dashboard statistics."""
    try:
        pending_count = len(list(db.collection(DRAFT_COLLECTION).where("status", "==", "pending").stream()))
        sent_count = len(list(db.collection(DRAFT_COLLECTION).where("status", "==", "sent").stream()))
        rejected_count = len(list(db.collection(DRAFT_COLLECTION).where("status", "==", "rejected").stream()))
        
        return jsonify({
            "pending": pending_count,
            "sent": sent_count,
            "rejected": rejected_count,
            "total": pending_count + sent_count + rejected_count
        })
    
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@api_bp.route("/delete-rejected", methods=["POST"])
def delete_rejected():
    """Delete all rejected drafts."""
    try:
        rejected_drafts_ref = db.collection(DRAFT_COLLECTION).where("status", "==", "rejected")
        
        deleted_count = 0
        for doc in rejected_drafts_ref.stream():
            doc.reference.delete()
            deleted_count += 1
        
        flash(f"✓ {deleted_count} draft(s) rejeté(s) supprimé(s) avec succès", "success")
        return redirect(url_for("history.history_list"))
    
    except Exception as e:
        flash(f"Erreur lors de la suppression: {str(e)}", "error")
        return redirect(url_for("history.history_list"))


@api_bp.route("/drafts/delete-multiple", methods=["POST"])
def delete_multiple_drafts():
    """Delete multiple drafts by IDs."""
    try:
        data = request.get_json()
        draft_ids = data.get("draft_ids", [])
        
        if not draft_ids:
            return jsonify({"success": False, "error": "Aucun draft spécifié"}), 400
        
        deleted_count = 0
        errors = []
        
        for draft_id in draft_ids:
            try:
                doc_ref = db.collection(DRAFT_COLLECTION).document(draft_id)
                doc = doc_ref.get()
                
                if doc.exists:
                    doc_ref.delete()
                    deleted_count += 1
                else:
                    errors.append(f"Draft {draft_id} non trouvé")
            except Exception as e:
                errors.append(f"Erreur pour {draft_id}: {str(e)}")
        
        return jsonify({
            "success": True,
            "deleted_count": deleted_count,
            "errors": errors if errors else None
        })
    
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


# ============================================================================
# History Blueprint
# ============================================================================

history_bp = Blueprint("history", __name__, url_prefix="/history")


def fetch_missing_reply(draft_id: str) -> dict:
    """Call gmail-notifier to fetch missing reply content."""
    if not GMAIL_NOTIFIER_URL:
        raise Exception("GMAIL_NOTIFIER_URL non configuré")
    
    try:
        id_token = get_id_token(GMAIL_NOTIFIER_URL)
        headers = {
            "Authorization": f"Bearer {id_token}",
            "Content-Type": "application/json"
        }
    except Exception:
        headers = {"Content-Type": "application/json"}
    
    response = http_requests.post(
        f"{GMAIL_NOTIFIER_URL}/fetch-reply",
        json={"draft_id": draft_id},
        headers=headers,
        timeout=30
    )
    response.raise_for_status()
    return response.json()


def fetch_thread_messages_from_gmail(draft_id: str) -> dict:
    """Call gmail-notifier to fetch entire thread."""
    if not GMAIL_NOTIFIER_URL:
        raise Exception("GMAIL_NOTIFIER_URL non configuré")
    
    try:
        id_token = get_id_token(GMAIL_NOTIFIER_URL)
        headers = {
            "Authorization": f"Bearer {id_token}",
            "Content-Type": "application/json"
        }
    except Exception:
        headers = {"Content-Type": "application/json"}
    
    response = http_requests.post(
        f"{GMAIL_NOTIFIER_URL}/fetch-thread",
        json={"draft_id": draft_id},
        headers=headers,
        timeout=30
    )
    response.raise_for_status()
    return response.json()


@history_bp.route("/")
def history_list():
    """Show sent email history."""
    try:
        sent_drafts_ref = db.collection(DRAFT_COLLECTION).where("status", "==", "sent").order_by("sent_at", direction=firestore.Query.DESCENDING).limit(50)
        sent_drafts = []
        
        total_sent = 0
        total_opened = 0
        total_bounced = 0
        total_replied = 0
        
        for doc in sent_drafts_ref.stream():
            draft_data = doc.to_dict()
            draft_data["id"] = doc.id
            
            total_sent += 1
            
            if draft_data.get("has_bounce"):
                total_bounced += 1
            
            if draft_data.get("has_reply"):
                total_replied += 1
            
            pixel_id = draft_data.get("pixel_id")
            if pixel_id:
                pixel_doc = db.collection(PIXEL_COLLECTION).document(pixel_id).get()
                if pixel_doc.exists:
                    pixel_data = pixel_doc.to_dict()
                    draft_data["open_count"] = pixel_data.get("open_count", 0)
                    draft_data["first_opened_at"] = pixel_data.get("first_opened_at")
                    draft_data["last_open_at"] = pixel_data.get("last_open_at")
                    
                    if draft_data["open_count"] > 0:
                        total_opened += 1
            
            followups_ref = db.collection(FOLLOWUP_COLLECTION).where("draft_id", "==", doc.id)
            followups = list(followups_ref.stream())
            draft_data["total_followups"] = len(followups)
            draft_data["scheduled_followups"] = len([f for f in followups if f.to_dict().get("status") == "scheduled"])
            draft_data["sent_followups"] = len([f for f in followups if f.to_dict().get("status") == "sent"])
            draft_data["cancelled_followups"] = len([f for f in followups if f.to_dict().get("status") == "cancelled"])
            
            sent_drafts.append(draft_data)
        
        open_rate = (total_opened / total_sent * 100) if total_sent > 0 else 0
        bounce_rate = (total_bounced / total_sent * 100) if total_sent > 0 else 0
        reply_rate = (total_replied / total_sent * 100) if total_sent > 0 else 0
        
        stats = {
            "total_sent": total_sent,
            "total_opened": total_opened,
            "total_bounced": total_bounced,
            "total_replied": total_replied,
            "open_rate": round(open_rate, 1),
            "bounce_rate": round(bounce_rate, 1),
            "reply_rate": round(reply_rate, 1)
        }
        
        rejected_drafts_ref = db.collection(DRAFT_COLLECTION).where("status", "==", "rejected").order_by("rejected_at", direction=firestore.Query.DESCENDING).limit(50)
        rejected_drafts = []
        
        for doc in rejected_drafts_ref.stream():
            draft_data = doc.to_dict()
            draft_data["id"] = doc.id
            rejected_drafts.append(draft_data)
        
        return render_template("history.html", sent_drafts=sent_drafts, rejected_drafts=rejected_drafts, stats=stats)
    
    except Exception as e:
        flash(f"Erreur lors de la récupération de l'historique: {str(e)}", "error")
        return render_template("history.html", sent_drafts=[], rejected_drafts=[], stats={})


@history_bp.route("/draft/<draft_id>")
def sent_draft_detail(draft_id: str):
    """Show sent draft details."""
    try:
        doc_ref = db.collection(DRAFT_COLLECTION).document(draft_id)
        doc = doc_ref.get()
        
        if not doc.exists:
            flash("Mail non trouvé", "error")
            return redirect(url_for("history.history_list"))
        
        draft_data = doc.to_dict()
        draft_data["id"] = doc.id
        
        if draft_data.get("status") != "sent":
            flash("Ce mail n'a pas encore été envoyé", "warning")
            return redirect(url_for("main.draft_detail", draft_id=draft_id))
        
        pixel_id = draft_data.get("pixel_id")
        open_history = []
        
        if pixel_id:
            pixel_doc = db.collection(PIXEL_COLLECTION).document(pixel_id).get()
            if pixel_doc.exists:
                pixel_data = pixel_doc.to_dict()
                draft_data["open_count"] = pixel_data.get("open_count", 0)
                draft_data["first_opened_at"] = pixel_data.get("first_opened_at")
                draft_data["last_open_at"] = pixel_data.get("last_open_at")
                
                opens_ref = db.collection(PIXEL_COLLECTION).document(pixel_id).collection("opens").order_by("opened_at", direction=firestore.Query.DESCENDING)
                for open_doc in opens_ref.stream():
                    open_data = open_doc.to_dict()
                    open_data["id"] = open_doc.id
                    open_history.append(open_data)
        
        followups_ref = db.collection(FOLLOWUP_COLLECTION).where("draft_id", "==", doc.id).order_by("days_after_initial")
        followups = []
        sent_followup_messages = []
        total_followups = 0
        scheduled_followups = 0
        sent_followups = 0
        cancelled_followups = 0
        
        for followup_doc in followups_ref.stream():
            followup_data = followup_doc.to_dict()
            followup_data["id"] = followup_doc.id
            followups.append(followup_data)
            
            total_followups += 1
            status = followup_data.get("status")
            if status == "scheduled":
                scheduled_followups += 1
            elif status == "sent":
                sent_followups += 1
                sent_followup_messages.append(followup_data)
            elif status == "cancelled":
                cancelled_followups += 1
        
        draft_data["total_followups"] = total_followups
        draft_data["scheduled_followups"] = scheduled_followups
        draft_data["sent_followups"] = sent_followups
        draft_data["cancelled_followups"] = cancelled_followups
        
        thread_messages = []
        if draft_data.get("gmail_thread_id"):
            thread_ref = doc_ref.collection('thread_messages').order_by('timestamp')
            thread_count = 0
            for msg_doc in thread_ref.stream():
                msg_data = msg_doc.to_dict()
                msg_data["id"] = msg_doc.id
                thread_messages.append(msg_data)
                thread_count += 1
            
            if thread_count == 0:
                try:
                    fetch_thread_messages_from_gmail(draft_id)
                    thread_messages = []
                    for msg_doc in thread_ref.stream():
                        msg_data = msg_doc.to_dict()
                        msg_data["id"] = msg_doc.id
                        thread_messages.append(msg_data)
                except Exception as fetch_error:
                    print(f"[WARNING] Impossible de récupérer le thread: {fetch_error}")
        
        if draft_data.get("has_reply") and not draft_data.get("reply_message"):
            try:
                fetch_missing_reply(draft_id)
                updated_doc = doc_ref.get()
                if updated_doc.exists:
                    draft_data = updated_doc.to_dict()
                    draft_data["id"] = doc.id
                    draft_data["total_followups"] = total_followups
                    draft_data["scheduled_followups"] = scheduled_followups
                    draft_data["sent_followups"] = sent_followups
                    draft_data["cancelled_followups"] = cancelled_followups
            except Exception as fetch_error:
                print(f"[WARNING] Impossible de récupérer la réponse: {fetch_error}")
        
        return render_template("sent_draft_detail.html", draft=draft_data, followups=followups, sent_followup_messages=sent_followup_messages, thread_messages=thread_messages, open_history=open_history)
    
    except Exception as e:
        flash(f"Erreur: {str(e)}", "error")
        return redirect(url_for("history.history_list"))


@history_bp.route("/fetch-reply/<draft_id>", methods=["POST"])
def fetch_reply(draft_id: str):
    """Endpoint to manually fetch a missing reply."""
    try:
        result = fetch_missing_reply(draft_id)
        flash(f"Réponse récupérée avec succès: {result.get('message', '')}", "success")
    except Exception as e:
        flash(f"Erreur lors de la récupération de la réponse: {str(e)}", "error")
    
    return redirect(url_for("history.sent_draft_detail", draft_id=draft_id))


@history_bp.route("/fetch-thread/<draft_id>", methods=["POST"])
def fetch_thread(draft_id: str):
    """Endpoint to manually fetch entire thread."""
    try:
        result = fetch_thread_messages_from_gmail(draft_id)
        flash(f"Thread récupéré avec succès: {result.get('message_count', 0)} messages", "success")
    except Exception as e:
        flash(f"Erreur lors de la récupération du thread: {str(e)}", "error")
    
    return redirect(url_for("history.sent_draft_detail", draft_id=draft_id))


@history_bp.route("/resend-bounced/<draft_id>", methods=["POST"])
def resend_bounced_email(draft_id: str):
    """Create a new draft with a new address for a bounced email."""
    try:
        new_email = request.form.get("new_email", "").strip()
        
        if not new_email:
            flash("Nouvelle adresse email manquante", "error")
            return redirect(url_for("history.sent_draft_detail", draft_id=draft_id))
        
        doc_ref = db.collection(DRAFT_COLLECTION).document(draft_id)
        doc = doc_ref.get()
        
        if not doc.exists:
            flash("Draft non trouvé", "error")
            return redirect(url_for("history.history_list"))
        
        draft_data = doc.to_dict()
        
        if not draft_data.get("has_bounce"):
            flash("Ce draft n'a pas bounced", "warning")
            return redirect(url_for("history.sent_draft_detail", draft_id=draft_id))
        
        new_draft_data = {
            "to": new_email,
            "subject": draft_data.get("subject"),
            "body": draft_data.get("body"),
            "status": "pending",
            "created_at": datetime.utcnow(),
            "x_external_id": draft_data.get("x_external_id"),
            "version_group_id": draft_data.get("version_group_id"),
            "odoo_id": draft_data.get("odoo_id"),
            "resent_from_bounced": True,
            "original_bounced_draft_id": draft_id,
            "original_bounced_email": draft_data.get("to")
        }
        
        if "contact_info" in draft_data:
            new_draft_data["contact_info"] = draft_data["contact_info"]
        
        new_draft_ref = db.collection(DRAFT_COLLECTION).add(new_draft_data)
        new_draft_id = new_draft_ref[1].id
        
        doc_ref.update({
            "resent_draft_id": new_draft_id,
            "resent_at": datetime.utcnow()
        })
        
        flash(f"Nouveau draft créé avec l'adresse {new_email}. Vous pouvez le vérifier et l'envoyer.", "success")
        return redirect(url_for("main.draft_detail", draft_id=new_draft_id))
    
    except Exception as e:
        flash(f"Erreur: {str(e)}", "error")
        return redirect(url_for("history.sent_draft_detail", draft_id=draft_id))


@history_bp.route("/resend-to-another/<draft_id>", methods=["POST"])
def resend_to_another(draft_id: str):
    """Resend an already sent email to another address."""
    try:
        new_email = request.form.get("new_email", "").strip()
        update_original = request.form.get("update_original") == "1"
        
        if not new_email:
            flash("Adresse email manquante", "error")
            return redirect(url_for("history.sent_draft_detail", draft_id=draft_id))
        
        if not SEND_MAIL_SERVICE_URL:
            flash("Service d'envoi non configuré (SEND_MAIL_SERVICE_URL manquant)", "error")
            return redirect(url_for("history.sent_draft_detail", draft_id=draft_id))
        
        doc_ref = db.collection(DRAFT_COLLECTION).document(draft_id)
        doc = doc_ref.get()
        
        if not doc.exists:
            flash("Draft non trouvé", "error")
            return redirect(url_for("history.history_list"))
        
        draft_data = doc.to_dict()
        original_to = draft_data.get("to", "")
        
        id_token = get_id_token(SEND_MAIL_SERVICE_URL)
        
        response = http_requests.post(
            f"{SEND_MAIL_SERVICE_URL}/resend-to-another",
            json={
                "draft_id": draft_id,
                "new_email": new_email
            },
            headers={"Authorization": f"Bearer {id_token}"},
            timeout=30
        )
        
        if response.status_code == 200:
            flash(f"Mail renvoyé avec succès à {new_email}!", "success")
            
            if update_original:
                doc_ref.update({
                    "to": new_email,
                    "original_to": original_to,
                    "email_forwarded_at": datetime.utcnow()
                })
                
                followups_ref = db.collection(FOLLOWUP_COLLECTION).where("draft_id", "==", draft_id).where("status", "==", "scheduled")
                for followup_doc in followups_ref.stream():
                    followup_doc.reference.update({"to": new_email})
                
                flash(f"L'adresse du prospect a été mise à jour. Les futures relances seront envoyées à {new_email}.", "info")
            
            return redirect(url_for("history.sent_draft_detail", draft_id=draft_id))
        else:
            error_msg = response.json().get("error", "Erreur inconnue")
            flash(f"Erreur lors du renvoi: {error_msg}", "error")
            return redirect(url_for("history.sent_draft_detail", draft_id=draft_id))
    
    except Exception as e:
        flash(f"Erreur lors du renvoi: {str(e)}", "error")
        return redirect(url_for("history.sent_draft_detail", draft_id=draft_id))


# ============================================================================
# Dashboard Blueprint
# ============================================================================

dashboard_bp = Blueprint("dashboard", __name__, url_prefix="/dashboard")


@dashboard_bp.route("/")
def dashboard():
    """Show analytics dashboard."""
    try:
        from datetime import timedelta
        from collections import defaultdict
        
        sent_drafts = list(db.collection(DRAFT_COLLECTION).where("status", "==", "sent").stream())
        
        total_sent = len(sent_drafts)
        total_opened = 0
        total_replied = 0
        total_bounced = 0
        
        sends_by_date = defaultdict(int)
        opens_by_date = defaultdict(int)
        replies_by_date = defaultdict(int)
        
        response_times = []
        
        for doc in sent_drafts:
            data = doc.to_dict()
            
            # Vérifier les ouvertures depuis la collection des pixels
            pixel_id = data.get("pixel_id")
            open_count = 0
            first_opened_at = None
            
            if pixel_id:
                pixel_doc = db.collection(PIXEL_COLLECTION).document(pixel_id).get()
                if pixel_doc.exists:
                    pixel_data = pixel_doc.to_dict()
                    open_count = pixel_data.get("open_count", 0)
                    first_opened_at = pixel_data.get("first_opened_at")
            
            if open_count > 0:
                total_opened += 1
                
                # Pour le graphique : utiliser first_opened_at, sinon sent_at comme fallback
                open_date = first_opened_at if first_opened_at else data.get("sent_at")
                if open_date:
                    if hasattr(open_date, 'strftime'):
                        date_key = open_date.strftime("%Y-%m-%d")
                    else:
                        date_key = str(open_date)[:10]
                    opens_by_date[date_key] += 1
                    
            if data.get("has_reply"):
                total_replied += 1
            if data.get("has_bounce"):
                total_bounced += 1
            
            sent_at = data.get("sent_at")
            if sent_at:
                if hasattr(sent_at, 'strftime'):
                    date_key = sent_at.strftime("%Y-%m-%d")
                else:
                    date_key = str(sent_at)[:10]
                sends_by_date[date_key] += 1
            
            reply_at = data.get("reply_received_at")
            if reply_at and sent_at:
                if hasattr(reply_at, 'strftime'):
                    date_key = reply_at.strftime("%Y-%m-%d")
                else:
                    date_key = str(reply_at)[:10]
                replies_by_date[date_key] += 1
                
                if hasattr(reply_at, 'timestamp') and hasattr(sent_at, 'timestamp'):
                    diff = reply_at.timestamp() - sent_at.timestamp()
                    response_times.append(diff / 3600)
        
        open_rate = (total_opened / total_sent * 100) if total_sent > 0 else 0
        reply_rate = (total_replied / total_sent * 100) if total_sent > 0 else 0
        bounce_rate = (total_bounced / total_sent * 100) if total_sent > 0 else 0
        
        avg_response_time = sum(response_times) / len(response_times) if response_times else 0
        
        if avg_response_time < 24:
            avg_response_formatted = f"{avg_response_time:.1f} heures"
        else:
            avg_response_formatted = f"{avg_response_time / 24:.1f} jours"
        
        today = datetime.utcnow().date()
        dates = [(today - timedelta(days=i)).strftime("%Y-%m-%d") for i in range(13, -1, -1)]
        
        chart_data = {
            "labels": [d[5:] for d in dates],
            "sends": [sends_by_date.get(d, 0) for d in dates],
            "opens": [opens_by_date.get(d, 0) for d in dates],
            "replies": [replies_by_date.get(d, 0) for d in dates]
        }
        
        pending_count = len(list(db.collection(DRAFT_COLLECTION).where("status", "==", "pending").stream()))
        
        return render_template("dashboard.html",
            total_sent=total_sent,
            total_opened=total_opened,
            total_replied=total_replied,
            total_bounced=total_bounced,
            open_rate=open_rate,
            reply_rate=reply_rate,
            bounce_rate=bounce_rate,
            avg_response_time=avg_response_formatted,
            pending_count=pending_count,
            chart_data=chart_data
        )
    
    except Exception as e:
        flash(f"Erreur lors du chargement du dashboard: {str(e)}", "error")
        return redirect(url_for("main.index"))


# ============================================================================
# Kanban Blueprint
# ============================================================================

kanban_bp = Blueprint("kanban", __name__, url_prefix="/kanban")


@kanban_bp.route("/")
def kanban_board():
    """Show kanban board view."""
    try:
        all_drafts = list(db.collection(DRAFT_COLLECTION).order_by("created_at", direction=firestore.Query.DESCENDING).limit(100).stream())
        
        columns = {
            "pending": [],
            "sent": [],
            "replied": [],
            "bounced": []
        }
        
        for doc in all_drafts:
            data = doc.to_dict()
            data["id"] = doc.id
            
            status = data.get("status", "pending")
            
            if status == "pending":
                columns["pending"].append(data)
            elif status == "sent":
                if data.get("has_bounce"):
                    columns["bounced"].append(data)
                elif data.get("has_reply"):
                    columns["replied"].append(data)
                else:
                    columns["sent"].append(data)
        
        return render_template("kanban.html", columns=columns)
    
    except Exception as e:
        flash(f"Erreur lors du chargement du kanban: {str(e)}", "error")
        return redirect(url_for("main.index"))


# ============================================================================
# Followups Timeline Blueprint
# ============================================================================

followups_bp = Blueprint("followups", __name__, url_prefix="/followups")


@followups_bp.route("/")
def timeline():
    """Show followups timeline view."""
    try:
        # Récupérer tous les followups triés par date planifiée (plus proche en premier)
        followups_ref = db.collection(FOLLOWUP_COLLECTION).order_by("scheduled_for", direction=firestore.Query.DESCENDING).limit(200)
        
        followups = []
        draft_cache = {}
        
        for doc in followups_ref.stream():
            followup_data = doc.to_dict()
            followup_data["id"] = doc.id
            
            # Récupérer les infos du draft parent (avec cache)
            draft_id = followup_data.get("draft_id")
            if draft_id:
                if draft_id not in draft_cache:
                    draft_doc = db.collection(DRAFT_COLLECTION).document(draft_id).get()
                    if draft_doc.exists:
                        draft_cache[draft_id] = draft_doc.to_dict()
                        draft_cache[draft_id]["id"] = draft_id
                    else:
                        draft_cache[draft_id] = None
                
                followup_data["draft"] = draft_cache.get(draft_id)
            
            followups.append(followup_data)
        
        # Statistiques
        stats = {
            "total": len(followups),
            "scheduled": len([f for f in followups if f.get("status") == "scheduled"]),
            "sent": len([f for f in followups if f.get("status") == "sent"]),
            "cancelled": len([f for f in followups if f.get("status") == "cancelled"])
        }
        
        # Filtrer par statut si demandé
        filter_status = request.args.get("status", "all")
        if filter_status != "all":
            followups = [f for f in followups if f.get("status") == filter_status]
        
        return render_template("followups_timeline.html", followups=followups, stats=stats, current_filter=filter_status)
    
    except Exception as e:
        flash(f"Erreur lors du chargement des relances: {str(e)}", "error")
        return redirect(url_for("main.index"))


@followups_bp.route("/cancel/<followup_id>", methods=["POST"])
def cancel_followup(followup_id: str):
    """Cancel a scheduled followup."""
    try:
        doc_ref = db.collection(FOLLOWUP_COLLECTION).document(followup_id)
        doc = doc_ref.get()
        
        if not doc.exists:
            flash("Relance non trouvée", "error")
            return redirect(url_for("followups.timeline"))
        
        followup_data = doc.to_dict()
        
        if followup_data.get("status") != "scheduled":
            flash("Cette relance ne peut pas être annulée", "warning")
            return redirect(url_for("followups.timeline"))
        
        doc_ref.update({
            "status": "cancelled",
            "cancelled_at": datetime.utcnow(),
            "cancelled_reason": "Annulée manuellement"
        })
        
        flash("Relance annulée avec succès", "success")
        return redirect(url_for("followups.timeline"))
    
    except Exception as e:
        flash(f"Erreur: {str(e)}", "error")
        return redirect(url_for("followups.timeline"))
