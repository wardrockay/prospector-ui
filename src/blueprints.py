"""
Flask Blueprints
================

Modular Flask blueprints for route organization.
"""

from __future__ import annotations

import os
import uuid
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
GENERATION_COLLECTION = os.environ.get("GENERATION_COLLECTION", "mail_writer_operations")
AGENT_INSTRUCTIONS_COLLECTION = os.environ.get("AGENT_INSTRUCTIONS_COLLECTION", "agent_instructions")
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
        
        # Récupérer aussi les drafts en erreur
        error_drafts_ref = db.collection(DRAFT_COLLECTION).where("status", "==", "error").order_by("created_at", direction=firestore.Query.DESCENDING)
        error_drafts = []
        for doc in error_drafts_ref.stream():
            draft_data = doc.to_dict()
            draft_data["id"] = doc.id
            error_drafts.append(draft_data)
        
        # Récupérer les générations en cours (statut pending dans mail_writer_operations)
        pending_generations = []
        try:
            # Essayer d'abord avec order_by, sinon sans (index peut manquer)
            try:
                generations_ref = db.collection(GENERATION_COLLECTION).where("status", "==", "pending").order_by("started_at", direction=firestore.Query.DESCENDING)
                for doc in generations_ref.stream():
                    gen_data = doc.to_dict()
                    gen_data["id"] = doc.id
                    # S'assurer que metadata existe
                    if "metadata" not in gen_data:
                        gen_data["metadata"] = {}
                    pending_generations.append(gen_data)
            except Exception:
                # Fallback sans order_by
                generations_ref = db.collection(GENERATION_COLLECTION).where("status", "==", "pending")
                for doc in generations_ref.stream():
                    gen_data = doc.to_dict()
                    gen_data["id"] = doc.id
                    if "metadata" not in gen_data:
                        gen_data["metadata"] = {}
                    pending_generations.append(gen_data)
                # Trier manuellement
                pending_generations.sort(key=lambda x: x.get("started_at", datetime.min), reverse=True)
        except Exception as gen_error:
            print(f"[WARNING] Impossible de récupérer les générations en cours: {gen_error}")
        
        return render_template("index.html", drafts=drafts, error_drafts=error_drafts, pending_generations=pending_generations)
    
    except Exception as e:
        flash(f"Erreur lors de la récupération des drafts: {str(e)}", "error")
        return render_template("index.html", drafts=[], error_drafts=[], pending_generations=[])


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
        
        # Générer un UUID pour le nouveau draft
        new_draft_id = str(uuid.uuid4())
        db.collection(DRAFT_COLLECTION).document(new_draft_id).set(new_draft_data)
        
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
            "odoo_id": odoo_id,
            "regenerate_id": str(uuid.uuid4())  # Force new generation
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


@api_bp.route("/retry-failed-generations", methods=["POST"])
def retry_failed_generations():
    """Retry all failed draft generations by calling mail-writer again."""
    try:
        if not MAIL_WRITER_URL:
            return jsonify({"success": False, "error": "MAIL_WRITER_URL non configuré"}), 500
        
        if not ODOO_DB_URL or not ODOO_SECRET:
            return jsonify({"success": False, "error": "Configuration Odoo manquante"}), 500
        
        # Récupérer tous les drafts en erreur
        error_drafts_ref = db.collection(DRAFT_COLLECTION).where("status", "==", "error")
        error_drafts = list(error_drafts_ref.stream())
        
        if not error_drafts:
            return jsonify({"success": True, "message": "Aucun draft en erreur", "retried": 0, "failed": 0})
        
        retried = 0
        failed = 0
        errors = []
        
        for doc in error_drafts:
            draft_data = doc.to_dict()
            draft_id = doc.id
            x_external_id = draft_data.get("x_external_id", "")
            
            if not x_external_id:
                # Si pas d'external_id, on ne peut pas récupérer les données Odoo
                errors.append(f"Draft {draft_id}: pas de x_external_id")
                failed += 1
                continue
            
            try:
                # Récupérer les données depuis Odoo
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
                    errors.append(f"Draft {draft_id}: lead non trouvé dans Odoo")
                    failed += 1
                    continue
                
                lead = odoo_data[0]
                odoo_id = lead.get("id")
                contact_name = lead.get("contact_name", "")
                name_parts = contact_name.split(" ", 1) if contact_name else ["", ""]
                first_name = name_parts[0] if len(name_parts) > 0 else ""
                last_name = name_parts[1] if len(name_parts) > 1 else ""
                
                # Appeler mail-writer pour régénérer
                mail_writer_payload = {
                    "first_name": first_name,
                    "last_name": last_name,
                    "email": lead.get("email_normalized", ""),
                    "website": lead.get("website", ""),
                    "partner_name": lead.get("partner_name", ""),
                    "function": lead.get("function", ""),
                    "description": lead.get("description", ""),
                    "x_external_id": x_external_id,
                    "odoo_id": odoo_id,
                    "regenerate_id": str(uuid.uuid4())  # Force new generation
                }
                
                mail_writer_response = http_requests.post(MAIL_WRITER_URL, json=mail_writer_payload, timeout=60)
                mail_writer_response.raise_for_status()
                
                # Supprimer l'ancien draft en erreur
                doc.reference.delete()
                retried += 1
                
            except Exception as e:
                errors.append(f"Draft {draft_id}: {str(e)}")
                failed += 1
        
        return jsonify({
            "success": True,
            "retried": retried,
            "failed": failed,
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
        import base64
        from datetime import timedelta
        
        # Pagination parameters
        page_size = 20
        cursor = request.args.get("cursor")
        page_num = int(request.args.get("page", "1"))
        
        # Récupérer le filtre de date et recherche
        date_filter = request.args.get("date", "all")
        custom_date = request.args.get("custom_date", "")
        search_email = request.args.get("search", "").strip()
        
        # Calculer les dates de début et fin selon le filtre
        today = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
        date_start = None
        date_end = None
        
        if date_filter == "today":
            date_start = today
            date_end = today + timedelta(days=1)
        elif date_filter == "yesterday":
            date_start = today - timedelta(days=1)
            date_end = today
        elif date_filter == "week":
            date_start = today - timedelta(days=7)
            date_end = today + timedelta(days=1)
        elif date_filter == "month":
            date_start = today - timedelta(days=30)
            date_end = today + timedelta(days=1)
        elif date_filter == "custom" and custom_date:
            try:
                date_start = datetime.strptime(custom_date, "%Y-%m-%d")
                date_end = date_start + timedelta(days=1)
            except ValueError:
                date_start = None
                date_end = None
        
        # Construire la requête de base
        if search_email:
            # Recherche par email exact (Firestore ne supporte pas LIKE)
            base_query = (
                db.collection(DRAFT_COLLECTION)
                .where("status", "==", "sent")
                .where("to", "==", search_email)
            )
            query = base_query.order_by("sent_at", direction=firestore.Query.DESCENDING)
        elif date_start and date_end:
            # Requête avec filtres de date
            base_query = (
                db.collection(DRAFT_COLLECTION)
                .where("status", "==", "sent")
                .where("sent_at", ">=", date_start)
                .where("sent_at", "<", date_end)
            )
            query = base_query.order_by("sent_at", direction=firestore.Query.DESCENDING)
        else:
            base_query = (
                db.collection(DRAFT_COLLECTION)
                .where("status", "==", "sent")
            )
            query = base_query.order_by("sent_at", direction=firestore.Query.DESCENDING)
        
        # Compter le total (pour afficher le nombre de pages)
        import math
        
        # Utiliser l'agrégation Firestore pour compter sans télécharger les documents
        agg_result = base_query.count().get()
        total_count = agg_result[0][0].value
        total_pages = math.ceil(total_count / page_size) if total_count > 0 else 1
        
        # Appliquer le curseur si présent
        if cursor:
            try:
                # Décoder le curseur (timestamp ISO format)
                cursor_data = base64.b64decode(cursor).decode()
                cursor_timestamp = datetime.fromisoformat(cursor_data)
                
                # Récupérer le document curseur pour start_after
                cursor_doc_query = (
                    db.collection(DRAFT_COLLECTION)
                    .where("status", "==", "sent")
                    .where("sent_at", "==", cursor_timestamp)
                    .limit(1)
                )
                cursor_docs = list(cursor_doc_query.stream())
                if cursor_docs:
                    query = query.start_after(cursor_docs[0])
            except Exception as e:
                logger.warning(f"Invalid cursor: {e}")
                # Continue sans curseur si invalide
        
        # Limiter les résultats
        query = query.limit(page_size)
        docs = list(query.stream())
        
        sent_drafts = []
        
        total_sent = 0
        total_opened = 0
        total_bounced = 0
        total_replied = 0
        
        for doc in docs:
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
                    draft_data["last_opened_at"] = pixel_data.get("last_opened_at")
                    
                    if draft_data["open_count"] > 0:
                        total_opened += 1
            
            followups_ref = db.collection(FOLLOWUP_COLLECTION).where("draft_id", "==", doc.id)
            followups = list(followups_ref.stream())
            draft_data["total_followups"] = len(followups)
            draft_data["scheduled_followups"] = len([f for f in followups if f.to_dict().get("status") == "scheduled"])
            draft_data["sent_followups"] = len([f for f in followups if f.to_dict().get("status") == "sent"])
            draft_data["cancelled_followups"] = len([f for f in followups if f.to_dict().get("status") == "cancelled"])
            
            sent_drafts.append(draft_data)
        
        # Créer le curseur pour la page suivante
        next_cursor = None
        prev_cursor = None
        
        if len(docs) == page_size and page_num < total_pages:
            # Il y a potentiellement une page suivante
            last_doc = docs[-1]
            last_sent_at = last_doc.to_dict().get("sent_at")
            if last_sent_at:
                next_cursor = base64.b64encode(
                    last_sent_at.isoformat().encode()
                ).decode()
        
        # Pour la page précédente, on stocke le premier curseur
        if cursor and docs:
            first_doc = docs[0]
            first_sent_at = first_doc.to_dict().get("sent_at")
            if first_sent_at:
                prev_cursor = base64.b64encode(
                    first_sent_at.isoformat().encode()
                ).decode()
        
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
        
        return render_template(
            "history.html",
            sent_drafts=sent_drafts,
            rejected_drafts=rejected_drafts,
            stats=stats,
            date_filter=date_filter,
            custom_date=custom_date,
            search_email=search_email,
            next_cursor=next_cursor,
            prev_cursor=prev_cursor,
            current_cursor=cursor,
            page_size=page_size,
            current_page=page_num,
            total_pages=total_pages,
            total_count=total_count
        )
    
    except Exception as e:
        flash(f"Erreur lors de la récupération de l'historique: {str(e)}", "error")
        return render_template("history.html", sent_drafts=[], rejected_drafts=[], stats={}, date_filter="all", custom_date="")


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
                draft_data["last_opened_at"] = pixel_data.get("last_opened_at")
                
                opens_ref = db.collection(PIXEL_COLLECTION).document(pixel_id).collection("opens").order_by("opened_at", direction=firestore.Query.DESCENDING)
                for open_doc in opens_ref.stream():
                    open_data = open_doc.to_dict()
                    open_data["id"] = open_doc.id
                    open_history.append(open_data)
        
        followups_ref = db.collection(FOLLOWUP_COLLECTION).where("draft_id", "==", doc.id).order_by("business_days_after")
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
        
        # Récupérer les messages du thread depuis Gmail si has_reply ou si thread_id existe
        thread_messages = []
        reply_info = {}
        
        if draft_data.get("has_reply"):
            try:
                # Nouvelle approche : récupérer TOUTES les conversations liées au draft
                try:
                    id_token = get_id_token(GMAIL_NOTIFIER_URL)
                    headers = {
                        "Authorization": f"Bearer {id_token}",
                        "Content-Type": "application/json"
                    }
                except Exception:
                    headers = {"Content-Type": "application/json"}
                
                # Récupérer d'abord les infos des réponses
                replies_response = http_requests.get(
                    f"{GMAIL_NOTIFIER_URL}/get-draft-replies/{doc.id}",
                    headers=headers,
                    timeout=30
                )
                
                if replies_response.status_code == 200:
                    reply_result = replies_response.json()
                    if reply_result.get("status") == "ok":
                        reply_info = {
                            'total_replies': reply_result.get('total_replies', 0),
                            'replies': reply_result.get('replies', [])
                        }
                        print(f"[INFO] {reply_info['total_replies']} réponse(s) trouvée(s)")
                
                # Récupérer toutes les conversations (thread original + threads des réponses)
                conversations_response = http_requests.get(
                    f"{GMAIL_NOTIFIER_URL}/get-draft-conversations/{doc.id}",
                    headers=headers,
                    timeout=30
                )
                
                if conversations_response.status_code == 200:
                    conv_result = conversations_response.json()
                    if conv_result.get("status") == "ok":
                        # Fusionner tous les messages de toutes les conversations
                        for conversation in conv_result.get('conversations', []):
                            thread_messages.extend(conversation.get('messages', []))
                        
                        # Trier par timestamp
                        thread_messages.sort(key=lambda m: m.get('timestamp', 0))
                        
                        print(f"[INFO] {len(thread_messages)} messages récupérés depuis {conv_result.get('total_threads', 0)} thread(s)")
                    else:
                        print(f"[WARNING] Erreur dans la réponse: {conv_result}")
                else:
                    print(f"[WARNING] Erreur HTTP {conversations_response.status_code}")
                    
            except Exception as fetch_error:
                print(f"[WARNING] Impossible de récupérer le thread depuis Gmail: {fetch_error}")
        
        return render_template("sent_draft_detail.html", draft=draft_data, followups=followups, sent_followup_messages=sent_followup_messages, thread_messages=thread_messages, reply_info=reply_info, open_history=open_history)
    
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
        total_untracked = 0  # Mails sans pixel_id
        
        sends_by_date = defaultdict(int)
        opens_by_date = defaultdict(int)
        replies_by_date = defaultdict(int)
        
        response_times = []
        
        # Taux de réponse par étape (followup_number)
        response_by_step = defaultdict(lambda: {"sent": 0, "replied": 0})
        # Taux d'ouverture par étape
        open_by_step = defaultdict(lambda: {"sent": 0, "opened": 0})
        # followup_number: 0 = premier mail, 1 = première relance, etc.
        
        for doc in sent_drafts:
            data = doc.to_dict()
            
            # Comptabiliser par étape de followup
            followup_number = data.get("followup_number", 0)
            response_by_step[followup_number]["sent"] += 1
            if data.get("has_reply"):
                response_by_step[followup_number]["replied"] += 1
            
            # Vérifier les ouvertures depuis la collection des pixels
            pixel_id = data.get("pixel_id")
            open_count = 0
            first_opened_at = None
            
            # Inclure uniquement les drafts avec pixel_id dans les stats d'ouverture
            if pixel_id:
                open_by_step[followup_number]["sent"] += 1
                
                pixel_doc = db.collection(PIXEL_COLLECTION).document(pixel_id).get()
                if pixel_doc.exists:
                    pixel_data = pixel_doc.to_dict()
                    open_count = pixel_data.get("open_count", 0)
                    first_opened_at = pixel_data.get("first_opened_at")
                
                if open_count > 0:
                    total_opened += 1
                    open_by_step[followup_number]["opened"] += 1
                    
                    # Pour le graphique : utiliser first_opened_at, sinon sent_at comme fallback
                    open_date = first_opened_at if first_opened_at else data.get("sent_at")
                    if open_date:
                        if hasattr(open_date, 'strftime'):
                            date_key = open_date.strftime("%Y-%m-%d")
                        else:
                            date_key = str(open_date)[:10]
                        opens_by_date[date_key] += 1
            else:
                # Compter les mails sans pixel_id (non trackés)
                total_untracked += 1
                    
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
            
            # Utiliser first_reply_at pour les statistiques de réponse
            first_reply_at = data.get("first_reply_at")
            if first_reply_at and sent_at:
                if hasattr(first_reply_at, 'strftime'):
                    date_key = first_reply_at.strftime("%Y-%m-%d")
                else:
                    date_key = str(first_reply_at)[:10]
                replies_by_date[date_key] += 1
                
                if hasattr(first_reply_at, 'timestamp') and hasattr(sent_at, 'timestamp'):
                    diff = first_reply_at.timestamp() - sent_at.timestamp()
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
        
        # Calculer les taux de réponse par étape
        response_rates_by_step = []
        step_labels = {
            0: "Premier mail",
            1: "1ère relance",
            2: "2ème relance",
            3: "3ème relance",
            4: "4ème relance"
        }
        
        for step in sorted(response_by_step.keys()):
            sent = response_by_step[step]["sent"]
            replied = response_by_step[step]["replied"]
            rate = (replied / sent * 100) if sent > 0 else 0
            
            response_rates_by_step.append({
                "step": step,
                "label": step_labels.get(step, f"Relance {step}"),
                "sent": sent,
                "replied": replied,
                "rate": round(rate, 1)
            })
        
        # Calculer les taux d'ouverture par étape
        open_rates_by_step = []
        
        for step in sorted(open_by_step.keys()):
            sent = open_by_step[step]["sent"]
            opened = open_by_step[step]["opened"]
            rate = (opened / sent * 100) if sent > 0 else 0
            
            open_rates_by_step.append({
                "step": step,
                "label": step_labels.get(step, f"Relance {step}"),
                "sent": sent,
                "opened": opened,
                "rate": round(rate, 1)
            })
        
        pending_count = len(list(db.collection(DRAFT_COLLECTION).where("status", "==", "pending").stream()))
        
        return render_template("dashboard.html",
            total_sent=total_sent,
            total_opened=total_opened,
            total_replied=total_replied,
            total_bounced=total_bounced,
            total_untracked=total_untracked,
            open_rate=open_rate,
            reply_rate=reply_rate,
            bounce_rate=bounce_rate,
            avg_response_time=avg_response_formatted,
            pending_count=pending_count,
            chart_data=chart_data,
            response_rates_by_step=response_rates_by_step,
            open_rates_by_step=open_rates_by_step
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
        # Filtrer par statut si demandé
        filter_status = request.args.get("status", "all")
        
        # Si pas de filtre spécifique, on exclut les annulées
        if filter_status == "all":
            # Récupérer seulement les followups scheduled, sent et failed
            followups_scheduled = db.collection(FOLLOWUP_COLLECTION).where("status", "==", "scheduled").order_by("scheduled_for", direction=firestore.Query.ASCENDING).limit(100).stream()
            followups_sent = db.collection(FOLLOWUP_COLLECTION).where("status", "==", "sent").order_by("scheduled_for", direction=firestore.Query.ASCENDING).limit(100).stream()
            followups_failed = db.collection(FOLLOWUP_COLLECTION).where("status", "==", "failed").order_by("scheduled_for", direction=firestore.Query.ASCENDING).limit(100).stream()
            
            all_docs = list(followups_scheduled) + list(followups_sent) + list(followups_failed)
        else:
            # Récupérer tous les followups pour calculer les stats
            all_docs = db.collection(FOLLOWUP_COLLECTION).where("status", "==", filter_status).order_by("scheduled_for", direction=firestore.Query.ASCENDING).limit(200).stream()
        
        # Récupérer TOUS les followups pour les stats (limité à 500)
        all_followups_for_stats = list(db.collection(FOLLOWUP_COLLECTION).limit(500).stream())
        
        followups = []
        draft_cache = {}
        
        # Date d'aujourd'hui pour le filtre
        today = datetime.utcnow().date()
        
        for doc in all_docs:
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
        
        # Trier par date
        followups.sort(key=lambda f: f.get("scheduled_for") or datetime.min)
        
        # Statistiques par statut (calculées sur TOUS les followups)
        stats = {
            "total": len([f for f in all_followups_for_stats if f.to_dict().get("status") in ["scheduled", "sent", "failed"]]),
            "scheduled": len([f for f in all_followups_for_stats if f.to_dict().get("status") == "scheduled"]),
            "sent": len([f for f in all_followups_for_stats if f.to_dict().get("status") == "sent"]),
            "failed": len([f for f in all_followups_for_stats if f.to_dict().get("status") == "failed"]),
            "cancelled": len([f for f in all_followups_for_stats if f.to_dict().get("status") == "cancelled"])
        }
        
        # Statistiques par jours (J+3, J+7, J+10, J+180) - uniquement scheduled (non envoyées)
        days_stats = {
            3: len([f for f in all_followups_for_stats if (f.to_dict().get("business_days_after") or f.to_dict().get("days_after_initial")) == 3 and f.to_dict().get("status") == "scheduled"]),
            7: len([f for f in all_followups_for_stats if (f.to_dict().get("business_days_after") or f.to_dict().get("days_after_initial")) == 7 and f.to_dict().get("status") == "scheduled"]),
            10: len([f for f in all_followups_for_stats if (f.to_dict().get("business_days_after") or f.to_dict().get("days_after_initial")) == 10 and f.to_dict().get("status") == "scheduled"]),
            180: len([f for f in all_followups_for_stats if (f.to_dict().get("business_days_after") or f.to_dict().get("days_after_initial")) == 180 and f.to_dict().get("status") == "scheduled"])
        }
        
        # Compter les relances prévues aujourd'hui (scheduled uniquement)
        def is_today(f):
            scheduled_for = f.get("scheduled_for")
            if scheduled_for and f.get("status") == "scheduled":
                if hasattr(scheduled_for, 'date'):
                    return scheduled_for.date() == today
                elif isinstance(scheduled_for, str):
                    return scheduled_for[:10] == today.isoformat()
            return False
        
        today_count = len([f for f in followups if is_today(f)])
        
        # Filtrer par jours si demandé
        filter_days = request.args.get("days")
        if filter_days and filter_days.isdigit():
            filter_days = int(filter_days)
            # Filtrer par jours ET exclure les envoyées
            followups = [f for f in followups if (f.get("business_days_after") or f.get("days_after_initial")) == filter_days and f.get("status") != "sent"]
        else:
            filter_days = None
        
        # Filtrer pour aujourd'hui si demandé
        filter_today = request.args.get("today") == "1"
        if filter_today:
            followups = [f for f in followups if is_today(f)]
        
        return render_template("followups_timeline.html", followups=followups, stats=stats, days_stats=days_stats, current_filter=filter_status, current_days=filter_days, today_count=today_count, filter_today=filter_today)
    
    except Exception as e:
        flash(f"Erreur lors du chargement des relances: {str(e)}", "error")
        return redirect(url_for("main.index"))


@followups_bp.route("/cancel/<followup_id>", methods=["POST"])
def cancel_followup(followup_id: str):
    """Cancel a scheduled followup."""
    try:
        # Récupérer l'URL de redirection (depuis le formulaire ou par défaut timeline)
        next_url = request.form.get("next") or url_for("followups.timeline")
        
        doc_ref = db.collection(FOLLOWUP_COLLECTION).document(followup_id)
        doc = doc_ref.get()
        
        if not doc.exists:
            flash("Relance non trouvée", "error")
            return redirect(next_url)
        
        followup_data = doc.to_dict()
        
        if followup_data.get("status") != "scheduled":
            flash("Cette relance ne peut pas être annulée", "warning")
            return redirect(next_url)
        
        doc_ref.update({
            "status": "cancelled",
            "cancelled_at": datetime.utcnow(),
            "cancelled_reason": "Annulée manuellement"
        })
        
        flash("Relance annulée avec succès", "success")
        return redirect(next_url)
    
    except Exception as e:
        flash(f"Erreur: {str(e)}", "error")
        return redirect(request.form.get("next") or url_for("followups.timeline"))


@followups_bp.route("/retry/<followup_id>", methods=["POST"])
def retry_followup(followup_id: str):
    """Retry a failed followup by changing status from failed to scheduled."""
    try:
        next_url = request.form.get("next") or url_for("followups.timeline")
        
        doc_ref = db.collection(FOLLOWUP_COLLECTION).document(followup_id)
        doc = doc_ref.get()
        
        if not doc.exists:
            flash("Relance non trouvée", "error")
            return redirect(next_url)
        
        followup_data = doc.to_dict()
        
        if followup_data.get("status") != "failed":
            flash("Cette relance n'est pas en échec", "warning")
            return redirect(next_url)
        
        doc_ref.update({
            "status": "scheduled",
            "error_message": None,
            "retry_at": datetime.utcnow(),
            "retry_count": followup_data.get("retry_count", 0) + 1
        })
        
        flash("Relance replanifiée avec succès", "success")
        return redirect(next_url)
    
    except Exception as e:
        flash(f"Erreur: {str(e)}", "error")
        return redirect(request.form.get("next") or url_for("followups.timeline"))


@followups_bp.route("/retry-all", methods=["POST"])
def retry_all_failed():
    """Retry all failed followups by changing their status to scheduled."""
    try:
        # Récupérer tous les followups échoués
        failed_followups = db.collection(FOLLOWUP_COLLECTION).where("status", "==", "failed").stream()
        
        count = 0
        for doc in failed_followups:
            followup_data = doc.to_dict()
            doc.reference.update({
                "status": "scheduled",
                "error_message": None,
                "retry_at": datetime.utcnow(),
                "retry_count": followup_data.get("retry_count", 0) + 1
            })
            count += 1
        
        if count == 0:
            flash("Aucune relance échouée à réessayer", "info")
        else:
            flash(f"{count} relance(s) replanifiée(s) avec succès", "success")
        
        return redirect(url_for("followups.timeline"))
    
    except Exception as e:
        flash(f"Erreur lors de la replanification: {str(e)}", "error")
        return redirect(url_for("followups.timeline"))


@followups_bp.route("/cancel-all/<draft_id>", methods=["POST"])
def cancel_all_followups(draft_id: str):
    """Cancel all scheduled followups for a draft."""
    try:
        # Récupérer l'URL de redirection
        next_url = request.form.get("next") or url_for("history.sent_draft_detail", draft_id=draft_id)
        
        # Récupérer toutes les relances planifiées pour ce draft
        followups_ref = db.collection(FOLLOWUP_COLLECTION).where("draft_id", "==", draft_id).where("status", "==", "scheduled")
        
        cancelled_count = 0
        for followup_doc in followups_ref.stream():
            followup_doc.reference.update({
                "status": "cancelled",
                "cancelled_at": datetime.utcnow(),
                "cancelled_reason": "Annulée manuellement (toutes)"
            })
            cancelled_count += 1
        
        if cancelled_count > 0:
            flash(f"{cancelled_count} relance(s) annulée(s) avec succès", "success")
        else:
            flash("Aucune relance planifiée à annuler", "info")
        
        return redirect(next_url)
    
    except Exception as e:
        flash(f"Erreur: {str(e)}", "error")
        return redirect(request.form.get("next") or url_for("history.sent_draft_detail", draft_id=draft_id))


# ============================================================================
# Prospects Blueprint
# ============================================================================

prospects_bp = Blueprint("prospects", __name__, url_prefix="/prospects")


@prospects_bp.route("/")
def prospects_list():
    """Show prospects list (one line per unique email address)."""
    try:
        from datetime import timedelta
        from collections import defaultdict
        
        # Récupérer le filtre de date
        date_filter = request.args.get("date", "all")
        
        # Calculer les dates de début et fin selon le filtre
        today = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
        date_start = None
        date_end = None
        
        if date_filter == "today":
            date_start = today
            date_end = today + timedelta(days=1)
        elif date_filter == "week":
            date_start = today - timedelta(days=7)
            date_end = today + timedelta(days=1)
        elif date_filter == "month":
            date_start = today - timedelta(days=30)
            date_end = today + timedelta(days=1)
        
        # Récupérer TOUS les drafts envoyés (initiaux + followups)
        if date_start and date_end:
            sent_drafts_ref = (
                db.collection(DRAFT_COLLECTION)
                .where("status", "==", "sent")
                .where("sent_at", ">=", date_start)
                .where("sent_at", "<", date_end)
                .order_by("sent_at", direction=firestore.Query.DESCENDING)
            )
        else:
            sent_drafts_ref = (
                db.collection(DRAFT_COLLECTION)
                .where("status", "==", "sent")
                .order_by("sent_at", direction=firestore.Query.DESCENDING)
                .limit(500)
            )
        
        # Grouper par x_external_id (ID Pharow)
        prospects_by_external_id = defaultdict(lambda: {
            "emails_sent": [],
            "open_count": 0,
            "has_reply": False,
            "has_bounce": False,
            "first_sent_at": None,
            "last_sent_at": None
        })
        
        for doc in sent_drafts_ref.stream():
            draft_data = doc.to_dict()
            draft_data["id"] = doc.id
            external_id = draft_data.get("x_external_id", "")
            
            if not external_id:
                continue
            
            prospect = prospects_by_external_id[external_id]
            
            # Stocker le draft
            prospect["emails_sent"].append(draft_data)
            
            # Mettre à jour les infos globales (prendre du premier email si pas défini)
            if not prospect.get("contact_name"):
                prospect["contact_name"] = draft_data.get("contact_name", "")
            if not prospect.get("partner_name"):
                prospect["partner_name"] = draft_data.get("partner_name", "")
            if not prospect.get("to"):
                prospect["to"] = draft_data.get("to", "")
            
            # Dates
            sent_at = draft_data.get("sent_at")
            if sent_at:
                if not prospect["first_sent_at"] or sent_at < prospect["first_sent_at"]:
                    prospect["first_sent_at"] = sent_at
                if not prospect["last_sent_at"] or sent_at > prospect["last_sent_at"]:
                    prospect["last_sent_at"] = sent_at
            
            # Agréger les stats
            if draft_data.get("has_reply"):
                prospect["has_reply"] = True
            if draft_data.get("has_bounce"):
                prospect["has_bounce"] = True
            
            # Compter les ouvertures
            pixel_id = draft_data.get("pixel_id")
            if pixel_id:
                pixel_doc = db.collection(PIXEL_COLLECTION).document(pixel_id).get()
                if pixel_doc.exists:
                    pixel_data = pixel_doc.to_dict()
                    prospect["open_count"] += pixel_data.get("open_count", 0)
        
        # Convertir en liste et calculer les stats
        prospects = []
        total_opened = 0
        total_bounced = 0
        total_replied = 0
        
        for external_id, data in prospects_by_external_id.items():
            # Trier les emails par date
            data["emails_sent"].sort(key=lambda x: x.get("sent_at") or datetime.min)
            
            # Compter initial + followups
            data["total_emails"] = len(data["emails_sent"])
            data["initial_email"] = data["emails_sent"][0] if data["emails_sent"] else None
            data["followups_count"] = data["total_emails"] - 1
            
            # Récupérer l'ID du premier email pour les liens
            data["id"] = data["initial_email"]["id"] if data["initial_email"] else None
            data["x_external_id"] = external_id
            
            # Date d'envoi (premier contact)
            data["sent_at"] = data["first_sent_at"]
            
            # Récupérer les relances planifiées pour ce prospect
            if data["id"]:
                try:
                    followups_ref = db.collection(FOLLOWUP_COLLECTION).where("draft_id", "==", data["id"]).where("status", "==", "scheduled")
                    data["scheduled_followups"] = len(list(followups_ref.stream()))
                except Exception:
                    data["scheduled_followups"] = 0
            else:
                data["scheduled_followups"] = 0
            
            # Stats globales
            if data["open_count"] > 0:
                total_opened += 1
            if data["has_bounce"]:
                total_bounced += 1
            if data["has_reply"]:
                total_replied += 1
            
            prospects.append(data)
        
        # Trier par date du dernier email
        prospects.sort(key=lambda x: x.get("last_sent_at") or datetime.min, reverse=True)
        
        total_prospects = len(prospects)
        open_rate = (total_opened / total_prospects * 100) if total_prospects > 0 else 0
        bounce_rate = (total_bounced / total_prospects * 100) if total_prospects > 0 else 0
        reply_rate = (total_replied / total_prospects * 100) if total_prospects > 0 else 0
        
        stats = {
            "total_prospects": total_prospects,
            "total_opened": total_opened,
            "total_bounced": total_bounced,
            "total_replied": total_replied,
            "open_rate": round(open_rate, 1),
            "bounce_rate": round(bounce_rate, 1),
            "reply_rate": round(reply_rate, 1)
        }
        
        return render_template("prospects.html", prospects=prospects, stats=stats, date_filter=date_filter)
    
    except Exception as e:
        flash(f"Erreur lors de la récupération des prospects: {str(e)}", "error")
        return render_template("prospects.html", prospects=[], stats={}, date_filter="all")


@prospects_bp.route("/<draft_id>")
def prospect_detail(draft_id: str):
    """Show prospect details with timeline, replies, and followups."""
    try:
        # Récupérer le draft principal
        doc_ref = db.collection(DRAFT_COLLECTION).document(draft_id)
        doc = doc_ref.get()
        
        if not doc.exists:
            flash("Prospect non trouvé", "error")
            return redirect(url_for("prospects.prospects_list"))
        
        prospect = doc.to_dict()
        prospect["id"] = doc.id
        
        # Récupérer les stats d'ouverture
        pixel_id = prospect.get("pixel_id")
        if pixel_id:
            pixel_doc = db.collection(PIXEL_COLLECTION).document(pixel_id).get()
            if pixel_doc.exists:
                pixel_data = pixel_doc.to_dict()
                prospect["open_count"] = pixel_data.get("open_count", 0)
                prospect["first_opened_at"] = pixel_data.get("first_opened_at")
        
        # Récupérer les followups
        followups_ref = db.collection(FOLLOWUP_COLLECTION).where("draft_id", "==", draft_id).order_by("business_days_after")
        followups = []
        for followup_doc in followups_ref.stream():
            followup_data = followup_doc.to_dict()
            followup_data["id"] = followup_doc.id
            followups.append(followup_data)
        
        prospect["scheduled_followups"] = len([f for f in followups if f.get("status") == "scheduled"])
        
        # Récupérer les messages du thread depuis Gmail si has_reply ou has_bounce
        thread_messages = []
        if prospect.get("gmail_thread_id") and (prospect.get("has_reply") or prospect.get("has_bounce")):
            try:
                thread_id = prospect.get("gmail_thread_id")
                
                try:
                    id_token = get_id_token(GMAIL_NOTIFIER_URL)
                    headers = {
                        "Authorization": f"Bearer {id_token}",
                        "Content-Type": "application/json"
                    }
                except Exception:
                    headers = {"Content-Type": "application/json"}
                
                response = http_requests.get(
                    f"{GMAIL_NOTIFIER_URL}/get-thread/{thread_id}",
                    headers=headers,
                    timeout=30
                )
                
                if response.status_code == 200:
                    result = response.json()
                    if result.get("status") == "ok":
                        thread_messages = result.get("messages", [])
                        print(f"[INFO] Thread récupéré depuis Gmail pour prospect: {len(thread_messages)} messages")
                    else:
                        print(f"[WARNING] Erreur dans la réponse: {result}")
                else:
                    print(f"[WARNING] Erreur HTTP {response.status_code} lors de la récupération du thread")
                    
            except Exception as fetch_error:
                print(f"[WARNING] Impossible de récupérer le thread depuis Gmail: {fetch_error}")
        
        # Construire la timeline
        timeline_items = []
        
        if thread_messages:
            # Utiliser les messages du thread Gmail
            for message in thread_messages:
                timestamp = message.get("timestamp")
                if timestamp:
                    date = datetime.fromtimestamp(timestamp)
                else:
                    date = None
                
                timeline_items.append({
                    "type": "sent" if message.get("is_from_me") else "reply",
                    "date": date,
                    "subject": message.get("subject"),
                    "body": message.get("body"),
                    "from": message.get("from")
                })
        else:
            # Fallback: utiliser les anciennes données
            # Email initial
            timeline_items.append({
                "type": "sent",
                "date": prospect.get("sent_at"),
                "subject": prospect.get("subject"),
                "body": prospect.get("body")
            })
            
            # Bounce si applicable
            if prospect.get("has_bounce"):
                timeline_items.append({
                    "type": "bounce",
                    "date": prospect.get("bounce_detected_at") or prospect.get("sent_at"),
                    "subject": None,
                    "body": prospect.get("bounce_reason")
                })
            
            # Réponses depuis les anciennes données
            if prospect.get("has_reply"):
                # Réponse directe à l'email initial
                if prospect.get("reply_message") or prospect.get("reply_snippet"):
                    timeline_items.append({
                        "type": "reply",
                        "date": prospect.get("reply_received_at") or prospect.get("first_reply_at"),
                        "subject": prospect.get("reply_subject"),
                        "body": prospect.get("reply_message") or prospect.get("reply_snippet", "")
                    })
                
                # Réponse à un followup (stockée sur le draft original)
                if prospect.get("followup_reply_message"):
                    timeline_items.append({
                        "type": "reply",
                        "date": prospect.get("reply_received_at") or prospect.get("first_reply_at"),
                        "subject": prospect.get("followup_reply_subject") or f"Réponse à la relance {prospect.get('followup_replied_number', '')}",
                        "body": prospect.get("followup_reply_message")
                    })
            
            # Followups envoyés
            for followup in followups:
                if followup.get("status") == "sent":
                    timeline_items.append({
                        "type": "followup",
                        "date": followup.get("sent_at"),
                        "subject": followup.get("subject"),
                        "body": followup.get("body"),
                        "followup_number": followup.get("followup_number")
                    })
        
        # Réponses (pour l'onglet séparé - données legacy)
        replies = []
        if prospect.get("has_reply") and not thread_messages:
            # Réponse directe à l'email initial
            if prospect.get("reply_message") or prospect.get("reply_snippet"):
                reply_item = {
                    "received_at": prospect.get("reply_received_at") or prospect.get("first_reply_at"),
                    "subject": prospect.get("reply_subject"),
                    "body": prospect.get("reply_message") or prospect.get("reply_snippet", ""),
                    "type": "direct"
                }
                replies.append(reply_item)
            
            # Réponse à un followup (stockée sur le draft original)
            if prospect.get("followup_reply_message"):
                followup_reply_item = {
                    "received_at": prospect.get("reply_received_at") or prospect.get("first_reply_at"),
                    "subject": prospect.get("followup_reply_subject"),
                    "body": prospect.get("followup_reply_message"),
                    "type": "followup_reply",
                    "followup_number": prospect.get("followup_replied_number", 0)
                }
                replies.append(followup_reply_item)
        
        # Trier par date
        timeline_items.sort(key=lambda x: x.get("date") or datetime.min, reverse=False)
        
        # Calculer le total d'emails
        total_emails = 1 + len([f for f in followups if f.get("status") == "sent"])
        
        return render_template(
            "prospect_detail.html",
            prospect=prospect,
            followups=followups,
            replies=replies,
            timeline_items=timeline_items,
            total_emails=total_emails,
            thread_messages=thread_messages
        )
    
    except Exception as e:
        flash(f"Erreur: {str(e)}", "error")
        return redirect(url_for("prospects.prospects_list"))


# ============================================================================
# Agent Instructions Blueprint
# ============================================================================

agent_instructions_bp = Blueprint("agent_instructions", __name__, url_prefix="/agent-instructions")


@agent_instructions_bp.route("/")
def instructions_list():
    """Show all agent instructions grouped by followup_number."""
    try:
        # Récupérer toutes les instructions
        instructions_ref = db.collection(AGENT_INSTRUCTIONS_COLLECTION).order_by("followup_number").order_by("created_at", direction=firestore.Query.DESCENDING)
        
        # Grouper par followup_number
        instructions_by_step = {}
        step_labels = {
            0: "Mail initial",
            1: "1ère relance",
            2: "2ème relance",
            3: "3ème relance",
            4: "4ème relance"
        }
        
        for doc in instructions_ref.stream():
            instruction_data = doc.to_dict()
            instruction_data["id"] = doc.id
            
            followup_number = instruction_data.get("followup_number", 0)
            
            if followup_number not in instructions_by_step:
                instructions_by_step[followup_number] = {
                    "label": step_labels.get(followup_number, f"Relance {followup_number}"),
                    "versions": []
                }
            
            instructions_by_step[followup_number]["versions"].append(instruction_data)
        
        return render_template("agent_instructions.html", instructions_by_step=instructions_by_step, step_labels=step_labels)
    
    except Exception as e:
        flash(f"Erreur lors du chargement des instructions: {str(e)}", "error")
        return redirect(url_for("main.index"))


@agent_instructions_bp.route("/create", methods=["GET", "POST"])
def create_instruction():
    """Create a new agent instruction."""
    if request.method == "POST":
        try:
            followup_number = int(request.form.get("followup_number", 0))
            version_name = request.form.get("version_name", "").strip()
            instruction_text = request.form.get("instruction_text", "").strip()
            is_active = request.form.get("is_active") == "on"
            
            if not version_name or not instruction_text:
                flash("Le nom de version et les instructions sont obligatoires", "error")
                return redirect(url_for("agent_instructions.create_instruction"))
            
            # Si is_active, désactiver les autres versions pour cette étape
            if is_active:
                existing_instructions = db.collection(AGENT_INSTRUCTIONS_COLLECTION).where("followup_number", "==", followup_number).where("is_active", "==", True).stream()
                for existing_doc in existing_instructions:
                    db.collection(AGENT_INSTRUCTIONS_COLLECTION).document(existing_doc.id).update({"is_active": False})
            
            # Créer la nouvelle instruction
            new_instruction = {
                "followup_number": followup_number,
                "version_name": version_name,
                "instruction_text": instruction_text,
                "is_active": is_active,
                "created_at": firestore.SERVER_TIMESTAMP,
                "updated_at": firestore.SERVER_TIMESTAMP
            }
            
            db.collection(AGENT_INSTRUCTIONS_COLLECTION).add(new_instruction)
            
            flash(f"Instruction créée avec succès pour '{version_name}'", "success")
            return redirect(url_for("agent_instructions.instructions_list"))
        
        except Exception as e:
            flash(f"Erreur lors de la création: {str(e)}", "error")
            return redirect(url_for("agent_instructions.create_instruction"))
    
    step_labels = {
        0: "Mail initial",
        1: "1ère relance",
        2: "2ème relance",
        3: "3ème relance",
        4: "4ème relance"
    }
    
    return render_template("agent_instruction_form.html", instruction=None, step_labels=step_labels)


@agent_instructions_bp.route("/edit/<instruction_id>", methods=["GET", "POST"])
def edit_instruction(instruction_id: str):
    """Edit an existing agent instruction."""
    if request.method == "POST":
        try:
            version_name = request.form.get("version_name", "").strip()
            instruction_text = request.form.get("instruction_text", "").strip()
            is_active = request.form.get("is_active") == "on"
            
            if not version_name or not instruction_text:
                flash("Le nom de version et les instructions sont obligatoires", "error")
                return redirect(url_for("agent_instructions.edit_instruction", instruction_id=instruction_id))
            
            # Récupérer l'instruction actuelle
            instruction_ref = db.collection(AGENT_INSTRUCTIONS_COLLECTION).document(instruction_id)
            instruction_doc = instruction_ref.get()
            
            if not instruction_doc.exists:
                flash("Instruction non trouvée", "error")
                return redirect(url_for("agent_instructions.instructions_list"))
            
            instruction_data = instruction_doc.to_dict()
            followup_number = instruction_data.get("followup_number", 0)
            
            # Si is_active, désactiver les autres versions pour cette étape
            if is_active:
                existing_instructions = db.collection(AGENT_INSTRUCTIONS_COLLECTION).where("followup_number", "==", followup_number).where("is_active", "==", True).stream()
                for existing_doc in existing_instructions:
                    if existing_doc.id != instruction_id:
                        db.collection(AGENT_INSTRUCTIONS_COLLECTION).document(existing_doc.id).update({"is_active": False})
            
            # Mettre à jour l'instruction
            instruction_ref.update({
                "version_name": version_name,
                "instruction_text": instruction_text,
                "is_active": is_active,
                "updated_at": firestore.SERVER_TIMESTAMP
            })
            
            flash(f"Instruction '{version_name}' mise à jour avec succès", "success")
            return redirect(url_for("agent_instructions.instructions_list"))
        
        except Exception as e:
            flash(f"Erreur lors de la mise à jour: {str(e)}", "error")
            return redirect(url_for("agent_instructions.edit_instruction", instruction_id=instruction_id))
    
    # GET request - afficher le formulaire
    try:
        instruction_ref = db.collection(AGENT_INSTRUCTIONS_COLLECTION).document(instruction_id)
        instruction_doc = instruction_ref.get()
        
        if not instruction_doc.exists:
            flash("Instruction non trouvée", "error")
            return redirect(url_for("agent_instructions.instructions_list"))
        
        instruction = instruction_doc.to_dict()
        instruction["id"] = instruction_doc.id
        
        step_labels = {
            0: "Mail initial",
            1: "1ère relance",
            2: "2ème relance",
            3: "3ème relance",
            4: "4ème relance"
        }
        
        return render_template("agent_instruction_form.html", instruction=instruction, step_labels=step_labels)
    
    except Exception as e:
        flash(f"Erreur: {str(e)}", "error")
        return redirect(url_for("agent_instructions.instructions_list"))


@agent_instructions_bp.route("/activate/<instruction_id>", methods=["POST"])
def activate_instruction(instruction_id: str):
    """Set an instruction as active for its step."""
    try:
        # Récupérer l'instruction
        instruction_ref = db.collection(AGENT_INSTRUCTIONS_COLLECTION).document(instruction_id)
        instruction_doc = instruction_ref.get()
        
        if not instruction_doc.exists:
            flash("Instruction non trouvée", "error")
            return redirect(url_for("agent_instructions.instructions_list"))
        
        instruction_data = instruction_doc.to_dict()
        followup_number = instruction_data.get("followup_number", 0)
        
        # Désactiver toutes les autres instructions pour cette étape
        existing_instructions = db.collection(AGENT_INSTRUCTIONS_COLLECTION).where("followup_number", "==", followup_number).where("is_active", "==", True).stream()
        for existing_doc in existing_instructions:
            db.collection(AGENT_INSTRUCTIONS_COLLECTION).document(existing_doc.id).update({"is_active": False})
        
        # Activer cette instruction
        instruction_ref.update({
            "is_active": True,
            "updated_at": datetime.now(firestore.SERVER_TIMESTAMP)
        })
        
        flash("Instruction activée avec succès", "success")
        return redirect(url_for("agent_instructions.instructions_list"))
    
    except Exception as e:
        flash(f"Erreur: {str(e)}", "error")
        return redirect(url_for("agent_instructions.instructions_list"))


@agent_instructions_bp.route("/delete/<instruction_id>", methods=["POST"])
def delete_instruction(instruction_id: str):
    """Delete an agent instruction."""
    try:
        db.collection(AGENT_INSTRUCTIONS_COLLECTION).document(instruction_id).delete()
        flash("Instruction supprimée avec succès", "success")
        return redirect(url_for("agent_instructions.instructions_list"))
    
    except Exception as e:
        flash(f"Erreur lors de la suppression: {str(e)}", "error")
        return redirect(url_for("agent_instructions.instructions_list"))
