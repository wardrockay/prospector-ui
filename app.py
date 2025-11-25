import os
import requests
from datetime import datetime
from flask import Flask, render_template, request, redirect, url_for, flash, jsonify
from google.cloud import firestore
import google.auth
from google.auth.transport.requests import Request as GoogleRequest

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "dev-secret-key-change-in-production")

# Firestore client
db = firestore.Client()

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


def get_id_token(target_audience: str) -> str:
    """
    Génère un ID token pour authentifier les appels vers d'autres services Cloud Run.
    Utilise le service account associé à ce Cloud Run.
    """
    try:
        credentials, project_id = google.auth.default()
        
        # Rafraîchir les credentials pour obtenir un ID token
        credentials.refresh(GoogleRequest())
        
        # Si les credentials supportent ID token (cas des services Cloud Run)
        if hasattr(credentials, 'id_token'):
            return credentials.id_token
        
        # Sinon, utiliser l'API IAMCredentials pour générer un ID token
        # Récupérer le service account email
        sa_email = credentials.service_account_email if hasattr(credentials, 'service_account_email') else None
        
        if not sa_email:
            # Fallback: utiliser le service account par défaut
            sa_email = "prospector-ui-sa@handy-resolver-477513-a1.iam.gserviceaccount.com"
        
        # Générer l'ID token via IAMCredentials API
        url = f"https://iamcredentials.googleapis.com/v1/projects/-/serviceAccounts/{sa_email}:generateIdToken"
        
        headers = {
            "Authorization": f"Bearer {credentials.token}",
            "Content-Type": "application/json",
        }
        
        payload = {
            "audience": target_audience,
            "includeEmail": True
        }
        
        response = requests.post(url, json=payload, headers=headers, timeout=10)
        response.raise_for_status()
        
        return response.json()["token"]
        
    except Exception as e:
        print(f"[ERROR] Erreur génération ID token: {e}")
        raise


@app.route("/")
def index():
    """Page d'accueil - Liste tous les drafts en attente de review."""
    try:
        # Récupérer tous les drafts avec status "pending"
        drafts_ref = db.collection(DRAFT_COLLECTION).where("status", "==", "pending").order_by("created_at", direction=firestore.Query.DESCENDING)
        
        # Grouper les drafts par version_group_id
        grouped_drafts = {}
        
        for doc in drafts_ref.stream():
            draft_data = doc.to_dict()
            draft_data["id"] = doc.id
            
            # Utiliser version_group_id comme clé, ou l'id du document si pas de groupe
            group_key = draft_data.get("version_group_id", doc.id)
            
            if group_key not in grouped_drafts:
                grouped_drafts[group_key] = {
                    "versions": [],
                    "latest": None
                }
            
            grouped_drafts[group_key]["versions"].append(draft_data)
        
        # Pour chaque groupe, identifier la version la plus récente
        drafts = []
        for group_key, group_data in grouped_drafts.items():
            versions = group_data["versions"]
            # Trier par date de création (la plus récente en premier)
            versions.sort(key=lambda x: x.get("created_at", datetime.min), reverse=True)
            
            latest = versions[0]
            latest["version_count"] = len(versions)
            latest["all_version_ids"] = [v["id"] for v in versions]
            
            drafts.append(latest)
        
        # Trier les drafts par date de création
        drafts.sort(key=lambda x: x.get("created_at", datetime.min), reverse=True)
        
        return render_template("index.html", drafts=drafts)
    
    except Exception as e:
        flash(f"Erreur lors de la récupération des drafts: {str(e)}", "error")
        return render_template("index.html", drafts=[])


@app.route("/draft/<draft_id>")
def view_draft(draft_id):
    """Page de détail d'un draft pour review."""
    try:
        doc_ref = db.collection(DRAFT_COLLECTION).document(draft_id)
        doc = doc_ref.get()
        
        if not doc.exists:
            flash("Draft non trouvé", "error")
            return redirect(url_for("index"))
        
        draft_data = doc.to_dict()
        draft_data["id"] = doc.id
        
        # Récupérer toutes les versions de ce draft (même version_group_id)
        versions = []
        version_group_id = draft_data.get("version_group_id")
        
        if version_group_id:
            # Récupérer tous les drafts avec le même version_group_id et status pending
            versions_ref = db.collection(DRAFT_COLLECTION).where("version_group_id", "==", version_group_id).where("status", "==", "pending").order_by("created_at")
            
            for idx, version_doc in enumerate(versions_ref.stream()):
                version_data = version_doc.to_dict()
                version_data["id"] = version_doc.id
                version_data["version_number"] = idx + 1
                version_data["is_current"] = version_doc.id == draft_id
                versions.append(version_data)
        
        # Si aucune version trouvée (pas de version_group_id), utiliser juste ce draft
        if not versions:
            draft_data["version_number"] = 1
            versions = [draft_data]
        
        return render_template("draft_detail.html", draft=draft_data, versions=versions)
    
    except Exception as e:
        flash(f"Erreur: {str(e)}", "error")
        return redirect(url_for("index"))


@app.route("/change-email-and-send/<draft_id>", methods=["POST"])
def change_email_and_send(draft_id):
    """Change l'adresse email du draft et l'envoie immédiatement."""
    try:
        new_email = request.form.get("new_email", "").strip()
        
        if not new_email:
            flash("Nouvelle adresse email manquante", "error")
            return redirect(url_for("view_draft", draft_id=draft_id))
        
        # Récupérer le draft
        doc_ref = db.collection(DRAFT_COLLECTION).document(draft_id)
        doc = doc_ref.get()
        
        if not doc.exists:
            flash("Draft non trouvé", "error")
            return redirect(url_for("index"))
        
        # Mettre à jour l'adresse email dans Firestore
        doc_ref.update({
            "to": new_email,
            "email_changed": True,
            "original_email": doc.to_dict().get("to"),
            "email_changed_at": datetime.utcnow()
        })
        
        flash(f"Adresse email mise à jour vers {new_email}", "info")
        
        # Envoyer le draft avec la nouvelle adresse
        if not SEND_MAIL_SERVICE_URL:
            flash("Service d'envoi non configuré (SEND_MAIL_SERVICE_URL manquant)", "error")
            return redirect(url_for("view_draft", draft_id=draft_id))
        
        # Générer l'ID token pour authentifier l'appel
        id_token = get_id_token(SEND_MAIL_SERVICE_URL)
        
        # Appeler le service send_mail
        response = requests.post(
            f"{SEND_MAIL_SERVICE_URL}/send-draft",
            json={"draft_id": draft_id},
            headers={"Authorization": f"Bearer {id_token}"},
            timeout=30
        )
        
        if response.status_code == 200:
            result = response.json()
            flash(f"Email envoyé avec succès à {new_email}! Message ID: {result.get('message_id')}", "success")
            
            # Récupérer le draft envoyé pour obtenir son version_group_id
            doc = doc_ref.get()
            if doc.exists:
                draft_data = doc.to_dict()
                version_group_id = draft_data.get("version_group_id")
                
                # Si ce draft fait partie d'un groupe de versions, rejeter automatiquement les autres versions
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
            
            # Planifier les relances automatiques
            if AUTO_FOLLOWUP_URL:
                try:
                    followup_response = requests.post(
                        f"{AUTO_FOLLOWUP_URL}/schedule-followups",
                        json={"draft_id": draft_id},
                        timeout=10
                    )
                    if followup_response.status_code == 200:
                        followup_result = followup_response.json()
                        flash(f"Relances planifiées: {followup_result.get('followups_created', 0)}", "info")
                except Exception as e:
                    print(f"Erreur lors de la planification des relances: {str(e)}")
            
            return redirect(url_for("index"))
        else:
            error_msg = response.json().get("error", "Erreur inconnue")
            flash(f"Erreur lors de l'envoi: {error_msg}", "error")
            return redirect(url_for("view_draft", draft_id=draft_id))
    
    except Exception as e:
        flash(f"Erreur: {str(e)}", "error")
        return redirect(url_for("view_draft", draft_id=draft_id))


@app.route("/send-test/<draft_id>", methods=["POST"])
def send_test_draft(draft_id):
    """Envoie un draft à une adresse de test sans tracking ni changement de statut."""
    try:
        test_email = request.form.get("test_email", "").strip()
        
        if not test_email:
            flash("Adresse email de test manquante", "error")
            return redirect(url_for("view_draft", draft_id=draft_id))
        
        if not SEND_MAIL_SERVICE_URL:
            flash("Service d'envoi non configuré (SEND_MAIL_SERVICE_URL manquant)", "error")
            return redirect(url_for("view_draft", draft_id=draft_id))
        
        # Récupérer le draft
        doc_ref = db.collection(DRAFT_COLLECTION).document(draft_id)
        doc = doc_ref.get()
        
        if not doc.exists:
            flash("Draft non trouvé", "error")
            return redirect(url_for("index"))
        
        draft_data = doc.to_dict()
        
        # Générer l'ID token pour authentifier l'appel
        id_token = get_id_token(SEND_MAIL_SERVICE_URL)
        
        # Appeler le service send_mail en mode test
        response = requests.post(
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
            result = response.json()
            flash(f"Mail de test envoyé avec succès à {test_email}!", "success")
            return redirect(url_for("view_draft", draft_id=draft_id))
        else:
            error_msg = response.json().get("error", "Erreur inconnue")
            flash(f"Erreur lors de l'envoi du test: {error_msg}", "error")
            return redirect(url_for("view_draft", draft_id=draft_id))
    
    except Exception as e:
        flash(f"Erreur lors de l'envoi du test: {str(e)}", "error")
        return redirect(url_for("view_draft", draft_id=draft_id))


@app.route("/send/<draft_id>", methods=["POST"])
def send_draft(draft_id):
    """Envoie un draft via le service send_mail."""
    try:
        if not SEND_MAIL_SERVICE_URL:
            flash("Service d'envoi non configuré (SEND_MAIL_SERVICE_URL manquant)", "error")
            return redirect(url_for("view_draft", draft_id=draft_id))
        
        # Générer l'ID token pour authentifier l'appel
        id_token = get_id_token(SEND_MAIL_SERVICE_URL)
        
        # Appeler le service send_mail
        response = requests.post(
            f"{SEND_MAIL_SERVICE_URL}/send-draft",
            json={"draft_id": draft_id},
            headers={"Authorization": f"Bearer {id_token}"},
            timeout=30
        )
        
        if response.status_code == 200:
            result = response.json()
            flash(f"Email envoyé avec succès! Message ID: {result.get('message_id')}", "success")
            
            # Récupérer le draft envoyé pour obtenir son version_group_id
            doc_ref = db.collection(DRAFT_COLLECTION).document(draft_id)
            doc = doc_ref.get()
            
            if doc.exists:
                draft_data = doc.to_dict()
                version_group_id = draft_data.get("version_group_id")
                
                # Si ce draft fait partie d'un groupe de versions, rejeter automatiquement les autres versions
                if version_group_id:
                    other_versions_ref = db.collection(DRAFT_COLLECTION).where("version_group_id", "==", version_group_id).where("status", "==", "pending")
                    rejected_count = 0
                    
                    for other_doc in other_versions_ref.stream():
                        # Ne pas rejeter le draft qui vient d'être envoyé
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
            
            # Planifier les relances automatiques
            if AUTO_FOLLOWUP_URL:
                try:
                    followup_response = requests.post(
                        f"{AUTO_FOLLOWUP_URL}/schedule-followups",
                        json={"draft_id": draft_id},
                        timeout=10
                    )
                    if followup_response.status_code == 200:
                        followup_result = followup_response.json()
                        flash(f"Relances planifiées: {followup_result.get('followups_created', 0)}", "info")
                except Exception as e:
                    print(f"Erreur lors de la planification des relances: {str(e)}")
            
            return redirect(url_for("index"))
        else:
            error_msg = response.json().get("error", "Erreur inconnue")
            flash(f"Erreur lors de l'envoi: {error_msg}", "error")
            return redirect(url_for("view_draft", draft_id=draft_id))
    
    except Exception as e:
        flash(f"Erreur lors de l'envoi: {str(e)}", "error")
        return redirect(url_for("view_draft", draft_id=draft_id))


@app.route("/reject/<draft_id>", methods=["POST"])
def reject_draft(draft_id):
    """Rejette un draft."""
    try:
        doc_ref = db.collection(DRAFT_COLLECTION).document(draft_id)
        doc = doc_ref.get()
        
        if not doc.exists:
            flash("Draft non trouvé", "error")
            return redirect(url_for("index"))
        
        # Mettre à jour le statut
        doc_ref.update({
            "status": "rejected",
            "rejected_at": datetime.utcnow()
        })
        
        flash("Draft rejeté", "success")
        return redirect(url_for("index"))
    
    except Exception as e:
        flash(f"Erreur: {str(e)}", "error")
        return redirect(url_for("index"))


@app.route("/edit/<draft_id>", methods=["POST"])
def edit_draft(draft_id):
    """Crée une nouvelle version du draft avec les modifications manuelles."""
    try:
        # Récupérer les données du formulaire
        new_subject = request.form.get("subject", "").strip()
        new_body = request.form.get("body", "").strip()
        
        if not new_subject or not new_body:
            flash("Le sujet et le corps du message ne peuvent pas être vides", "error")
            return redirect(url_for("view_draft", draft_id=draft_id))
        
        # Récupérer le draft original
        doc_ref = db.collection(DRAFT_COLLECTION).document(draft_id)
        doc = doc_ref.get()
        
        if not doc.exists:
            flash("Draft non trouvé", "error")
            return redirect(url_for("index"))
        
        original_data = doc.to_dict()
        
        # Créer une nouvelle version avec les modifications
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
        
        # Ajouter contact_info si présent
        if "contact_info" in original_data:
            new_draft_data["contact_info"] = original_data["contact_info"]
        
        # Créer le nouveau draft
        new_draft_ref = db.collection(DRAFT_COLLECTION).add(new_draft_data)
        new_draft_id = new_draft_ref[1].id
        
        flash("Nouvelle version du draft créée avec vos modifications", "success")
        return redirect(url_for("view_draft", draft_id=new_draft_id))
    
    except Exception as e:
        flash(f"Erreur lors de la modification: {str(e)}", "error")
        return redirect(url_for("view_draft", draft_id=draft_id))


@app.route("/regenerate/<draft_id>", methods=["POST"])
def regenerate_draft(draft_id):
    """Régénère un draft en récupérant les données depuis Odoo."""
    try:
        # Récupérer le draft actuel
        doc_ref = db.collection(DRAFT_COLLECTION).document(draft_id)
        doc = doc_ref.get()
        
        if not doc.exists:
            flash("Draft non trouvé", "error")
            return redirect(url_for("index"))
        
        draft_data = doc.to_dict()
        x_external_id = draft_data.get("x_external_id")
        version_group_id = draft_data.get("version_group_id")  # Récupérer le version_group_id
        
        if not x_external_id:
            flash("Impossible de régénérer: x_external_id manquant", "error")
            return redirect(url_for("view_draft", draft_id=draft_id))
        
        # Vérifier la config
        if not ODOO_DB_URL or not ODOO_SECRET:
            flash("Configuration Odoo manquante (ODOO_DB_URL ou ODOO_SECRET)", "error")
            return redirect(url_for("view_draft", draft_id=draft_id))
        
        if not MAIL_WRITER_URL:
            flash("Configuration mail_writer manquante (MAIL_WRITER_URL)", "error")
            return redirect(url_for("view_draft", draft_id=draft_id))
        
        # 1. Récupérer les données depuis Odoo
        odoo_url = f"{ODOO_DB_URL}/json/2/crm.lead/search_read"
        odoo_payload = {
            "domain": [["x_external_id", "ilike", x_external_id]],
            "fields": [
                "id",
                "email_normalized",
                "website",
                "contact_name",
                "partner_name",
                "function",
                "description"
            ]
        }
        odoo_headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {ODOO_SECRET}"
        }
        
        print(f"[DEBUG] Récupération données Odoo pour x_external_id: {x_external_id}")
        odoo_response = requests.post(odoo_url, json=odoo_payload, headers=odoo_headers, timeout=15)
        odoo_response.raise_for_status()
        odoo_data = odoo_response.json()
        
        if not odoo_data or len(odoo_data) == 0:
            flash(f"Aucun lead trouvé dans Odoo avec x_external_id: {x_external_id}", "error")
            return redirect(url_for("view_draft", draft_id=draft_id))
        
        # Prendre le premier résultat
        lead = odoo_data[0]
        print(f"[DEBUG] Lead récupéré depuis Odoo: {lead}")
        
        # Extraire les informations
        odoo_id = lead.get("id")
        contact_name = lead.get("contact_name", "")
        name_parts = contact_name.split(" ", 1) if contact_name else ["", ""]
        first_name = name_parts[0] if len(name_parts) > 0 else ""
        last_name = name_parts[1] if len(name_parts) > 1 else ""
        
        # 2. Appeler mail_writer pour régénérer le mail
        mail_writer_payload = {
            "first_name": first_name,
            "last_name": last_name,
            "email": lead.get("email_normalized", ""),
            "website": lead.get("website", ""),
            "partner_name": lead.get("partner_name", ""),
            "function": lead.get("function", ""),
            "description": lead.get("description", ""),
            "x_external_id": x_external_id,
            "version_group_id": version_group_id,  # Garder le même groupe de versions
            "odoo_id": odoo_id  # Ajouter l'ID Odoo
        }
        
        print(f"[DEBUG] Appel mail_writer avec: {mail_writer_payload}")
        mail_writer_response = requests.post(MAIL_WRITER_URL, json=mail_writer_payload, timeout=60)
        mail_writer_response.raise_for_status()
        mail_writer_data = mail_writer_response.json()
        
        print(f"[DEBUG] Réponse mail_writer: {mail_writer_data}")
        
        # Récupérer le nouveau draft_id depuis la réponse
        new_draft_id = mail_writer_data.get("draft", {}).get("draft_id")
        
        if new_draft_id:
            flash(f"Nouvelle version du mail générée avec succès!", "success")
            return redirect(url_for("view_draft", draft_id=new_draft_id))
        else:
            flash("Mail régénéré mais impossible de récupérer le nouveau draft", "warning")
            return redirect(url_for("index"))
    
    except requests.exceptions.RequestException as e:
        flash(f"Erreur lors de la communication avec les services: {str(e)}", "error")
        return redirect(url_for("view_draft", draft_id=draft_id))
    except Exception as e:
        flash(f"Erreur lors de la régénération: {str(e)}", "error")
        return redirect(url_for("view_draft", draft_id=draft_id))


@app.route("/history")
def history():
    """Page historique - Liste tous les drafts envoyés ou rejetés avec leurs stats."""
    try:
        # Récupérer les drafts envoyés
        sent_drafts_ref = db.collection(DRAFT_COLLECTION).where("status", "==", "sent").order_by("sent_at", direction=firestore.Query.DESCENDING).limit(50)
        sent_drafts = []
        
        # Statistiques globales
        total_sent = 0
        total_opened = 0
        total_bounced = 0
        total_replied = 0
        
        for doc in sent_drafts_ref.stream():
            draft_data = doc.to_dict()
            draft_data["id"] = doc.id
            
            total_sent += 1
            
            # Vérifier les bounces
            if draft_data.get("has_bounce"):
                total_bounced += 1
            
            # Vérifier les réponses
            if draft_data.get("has_reply"):
                total_replied += 1
            
            # Récupérer les stats d'ouverture pour ce draft
            pixel_id = draft_data.get("pixel_id")
            if pixel_id:
                pixel_doc = db.collection(PIXEL_COLLECTION).document(pixel_id).get()
                if pixel_doc.exists:
                    pixel_data = pixel_doc.to_dict()
                    draft_data["open_count"] = pixel_data.get("open_count", 0)
                    draft_data["first_opened_at"] = pixel_data.get("first_opened_at")
                    draft_data["last_open_at"] = pixel_data.get("last_open_at")
                    
                    # Compter comme ouvert si open_count > 0
                    if draft_data["open_count"] > 0:
                        total_opened += 1
            
            # Compter les relances pour ce draft
            followups_ref = db.collection(FOLLOWUP_COLLECTION).where("draft_id", "==", doc.id)
            followups = list(followups_ref.stream())
            draft_data["total_followups"] = len(followups)
            draft_data["scheduled_followups"] = len([f for f in followups if f.to_dict().get("status") == "scheduled"])
            draft_data["sent_followups"] = len([f for f in followups if f.to_dict().get("status") == "sent"])
            draft_data["cancelled_followups"] = len([f for f in followups if f.to_dict().get("status") == "cancelled"])
            
            sent_drafts.append(draft_data)
        
        # Calculer les taux
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
        
        # Récupérer les drafts rejetés
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


@app.route("/delete-rejected-drafts", methods=["POST"])
def delete_rejected_drafts():
    """Supprime tous les drafts rejetés."""
    try:
        # Récupérer tous les drafts rejetés
        rejected_drafts_ref = db.collection(DRAFT_COLLECTION).where("status", "==", "rejected")
        
        deleted_count = 0
        for doc in rejected_drafts_ref.stream():
            doc.reference.delete()
            deleted_count += 1
        
        flash(f"✓ {deleted_count} draft(s) rejeté(s) supprimé(s) avec succès", "success")
        
    except Exception as e:
        flash(f"Erreur lors de la suppression: {str(e)}", "error")
    
    return redirect(url_for("history"))


@app.route("/sent/<draft_id>")
def view_sent_draft(draft_id):
    """Page de détail d'un mail envoyé."""
    try:
        doc_ref = db.collection(DRAFT_COLLECTION).document(draft_id)
        doc = doc_ref.get()
        
        if not doc.exists:
            flash("Mail non trouvé", "error")
            return redirect(url_for("history"))
        
        draft_data = doc.to_dict()
        draft_data["id"] = doc.id
        
        # Vérifier que le mail est bien envoyé
        if draft_data.get("status") != "sent":
            flash("Ce mail n'a pas encore été envoyé", "warning")
            return redirect(url_for("view_draft", draft_id=draft_id))
        
        # Récupérer les stats d'ouverture
        pixel_id = draft_data.get("pixel_id")
        open_history = []
        
        if pixel_id:
            pixel_doc = db.collection(PIXEL_COLLECTION).document(pixel_id).get()
            if pixel_doc.exists:
                pixel_data = pixel_doc.to_dict()
                draft_data["open_count"] = pixel_data.get("open_count", 0)
                draft_data["first_opened_at"] = pixel_data.get("first_opened_at")
                draft_data["last_open_at"] = pixel_data.get("last_open_at")
                
                # Récupérer l'historique des ouvertures depuis la sous-collection
                opens_ref = db.collection(PIXEL_COLLECTION).document(pixel_id).collection("opens").order_by("opened_at", direction=firestore.Query.DESCENDING)
                for open_doc in opens_ref.stream():
                    open_data = open_doc.to_dict()
                    open_data["id"] = open_doc.id
                    open_history.append(open_data)
        
        # Récupérer les relances
        followups_ref = db.collection(FOLLOWUP_COLLECTION).where("draft_id", "==", doc.id).order_by("days_after_initial")
        followups = []
        sent_followup_messages = []  # Pour affichage dans le thread de conversation
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
                # Ajouter aux messages envoyés pour le thread
                sent_followup_messages.append(followup_data)
            elif status == "cancelled":
                cancelled_followups += 1
        
        draft_data["total_followups"] = total_followups
        draft_data["scheduled_followups"] = scheduled_followups
        draft_data["sent_followups"] = sent_followups
        draft_data["cancelled_followups"] = cancelled_followups
        
        # Récupérer les messages du thread depuis la sous-collection
        thread_messages = []
        if draft_data.get("gmail_thread_id"):
            thread_ref = doc_ref.collection('thread_messages').order_by('timestamp')
            thread_count = 0
            for msg_doc in thread_ref.stream():
                msg_data = msg_doc.to_dict()
                msg_data["id"] = msg_doc.id
                thread_messages.append(msg_data)
                thread_count += 1
            
            # Si aucun message dans le thread et qu'on a un thread_id, essayer de le récupérer
            if thread_count == 0:
                try:
                    print(f"[INFO] Récupération du thread pour draft {draft_id}")
                    fetch_thread_messages(draft_id)
                    # Recharger les messages après récupération
                    thread_messages = []
                    for msg_doc in thread_ref.stream():
                        msg_data = msg_doc.to_dict()
                        msg_data["id"] = msg_doc.id
                        thread_messages.append(msg_data)
                except Exception as fetch_error:
                    print(f"[WARNING] Impossible de récupérer le thread: {fetch_error}")
        
        # Si le draft a une réponse mais pas de message stocké, essayer de le récupérer
        if draft_data.get("has_reply") and not draft_data.get("reply_message"):
            try:
                fetch_missing_reply(draft_id)
                # Recharger les données après récupération
                updated_doc = doc_ref.get()
                if updated_doc.exists:
                    draft_data = updated_doc.to_dict()
                    draft_data["id"] = doc.id
                    # Réappliquer les stats calculées
                    draft_data["total_followups"] = total_followups
                    draft_data["scheduled_followups"] = scheduled_followups
                    draft_data["sent_followups"] = sent_followups
                    draft_data["cancelled_followups"] = cancelled_followups
            except Exception as fetch_error:
                print(f"[WARNING] Impossible de récupérer la réponse: {fetch_error}")
        
        return render_template("sent_draft_detail.html", draft=draft_data, followups=followups, sent_followup_messages=sent_followup_messages, thread_messages=thread_messages, open_history=open_history)
    
    except Exception as e:
        flash(f"Erreur: {str(e)}", "error")
        return redirect(url_for("history"))


def fetch_missing_reply(draft_id):
    """
    Appelle le service gmail-notifier pour récupérer le contenu d'une réponse manquante.
    """
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
    
    response = requests.post(
        f"{GMAIL_NOTIFIER_URL}/fetch-reply",
        json={"draft_id": draft_id},
        headers=headers,
        timeout=30
    )
    response.raise_for_status()
    return response.json()


def fetch_thread_messages(draft_id):
    """
    Appelle le service gmail-notifier pour récupérer tout le thread de conversation.
    """
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
    
    response = requests.post(
        f"{GMAIL_NOTIFIER_URL}/fetch-thread",
        json={"draft_id": draft_id},
        headers=headers,
        timeout=30
    )
    response.raise_for_status()
    return response.json()


@app.route("/fetch-reply/<draft_id>", methods=["POST"])
def fetch_reply_endpoint(draft_id):
    """Endpoint pour récupérer manuellement une réponse manquante."""
    try:
        result = fetch_missing_reply(draft_id)
        flash(f"Réponse récupérée avec succès: {result.get('message', '')}", "success")
    except Exception as e:
        flash(f"Erreur lors de la récupération de la réponse: {str(e)}", "error")
    
    return redirect(url_for("view_sent_draft", draft_id=draft_id))


@app.route("/fetch-thread/<draft_id>", methods=["POST"])
def fetch_thread_endpoint(draft_id):
    """Endpoint pour récupérer manuellement tout le thread de conversation."""
    try:
        result = fetch_thread_messages(draft_id)
        flash(f"Thread récupéré avec succès: {result.get('message_count', 0)} messages", "success")
    except Exception as e:
        flash(f"Erreur lors de la récupération du thread: {str(e)}", "error")
    
    return redirect(url_for("view_sent_draft", draft_id=draft_id))


@app.route("/resend-bounced/<draft_id>", methods=["POST"])
def resend_bounced_email(draft_id):
    """Crée un nouveau draft avec une nouvelle adresse pour un email qui a bounced."""
    try:
        new_email = request.form.get("new_email", "").strip()
        
        if not new_email:
            flash("Nouvelle adresse email manquante", "error")
            return redirect(url_for("view_sent_draft", draft_id=draft_id))
        
        # Récupérer le draft bounced
        doc_ref = db.collection(DRAFT_COLLECTION).document(draft_id)
        doc = doc_ref.get()
        
        if not doc.exists:
            flash("Draft non trouvé", "error")
            return redirect(url_for("history"))
        
        draft_data = doc.to_dict()
        
        # Vérifier que c'est bien un bounce
        if not draft_data.get("has_bounce"):
            flash("Ce draft n'a pas bounced", "warning")
            return redirect(url_for("view_sent_draft", draft_id=draft_id))
        
        # Créer un nouveau draft avec la nouvelle adresse
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
        
        # Ajouter contact_info si présent
        if "contact_info" in draft_data:
            new_draft_data["contact_info"] = draft_data["contact_info"]
        
        # Créer le nouveau draft
        new_draft_ref = db.collection(DRAFT_COLLECTION).add(new_draft_data)
        new_draft_id = new_draft_ref[1].id
        
        # Mettre à jour le draft bounced pour indiquer qu'un nouveau draft a été créé
        doc_ref.update({
            "resent_draft_id": new_draft_id,
            "resent_at": datetime.utcnow()
        })
        
        flash(f"Nouveau draft créé avec l'adresse {new_email}. Vous pouvez le vérifier et l'envoyer.", "success")
        return redirect(url_for("view_draft", draft_id=new_draft_id))
    
    except Exception as e:
        flash(f"Erreur: {str(e)}", "error")
        return redirect(url_for("view_sent_draft", draft_id=draft_id))


@app.route("/api/stats")
def api_stats():
    """API endpoint pour récupérer des statistiques."""
    try:
        # Compter les drafts par statut
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


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port, debug=True)
