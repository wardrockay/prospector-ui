import os
import requests
from datetime import datetime
from flask import Flask, render_template, request, redirect, url_for, flash, jsonify
from google.cloud import firestore

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "dev-secret-key-change-in-production")

# Firestore client
db = firestore.Client()

# Configuration
DRAFT_COLLECTION = os.environ.get("DRAFT_COLLECTION", "email_drafts")
SEND_MAIL_SERVICE_URL = os.environ.get("SEND_MAIL_SERVICE_URL", "").rstrip("/")


@app.route("/")
def index():
    """Page d'accueil - Liste tous les drafts en attente de review."""
    try:
        # Récupérer tous les drafts avec status "pending"
        drafts_ref = db.collection(DRAFT_COLLECTION).where("status", "==", "pending").order_by("created_at", direction=firestore.Query.DESCENDING)
        drafts = []
        
        for doc in drafts_ref.stream():
            draft_data = doc.to_dict()
            draft_data["id"] = doc.id
            drafts.append(draft_data)
        
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
        
        return render_template("draft_detail.html", draft=draft_data)
    
    except Exception as e:
        flash(f"Erreur: {str(e)}", "error")
        return redirect(url_for("index"))


@app.route("/send/<draft_id>", methods=["POST"])
def send_draft(draft_id):
    """Envoie un draft via le service send_mail."""
    try:
        if not SEND_MAIL_SERVICE_URL:
            flash("Service d'envoi non configuré (SEND_MAIL_SERVICE_URL manquant)", "error")
            return redirect(url_for("view_draft", draft_id=draft_id))
        
        # Appeler le service send_mail
        response = requests.post(
            f"{SEND_MAIL_SERVICE_URL}/send-draft",
            json={"draft_id": draft_id},
            timeout=30
        )
        
        if response.status_code == 200:
            result = response.json()
            flash(f"Email envoyé avec succès! Message ID: {result.get('message_id')}", "success")
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


@app.route("/history")
def history():
    """Page historique - Liste tous les drafts envoyés ou rejetés."""
    try:
        # Récupérer les drafts envoyés
        sent_drafts_ref = db.collection(DRAFT_COLLECTION).where("status", "==", "sent").order_by("sent_at", direction=firestore.Query.DESCENDING).limit(50)
        sent_drafts = []
        
        for doc in sent_drafts_ref.stream():
            draft_data = doc.to_dict()
            draft_data["id"] = doc.id
            sent_drafts.append(draft_data)
        
        # Récupérer les drafts rejetés
        rejected_drafts_ref = db.collection(DRAFT_COLLECTION).where("status", "==", "rejected").order_by("rejected_at", direction=firestore.Query.DESCENDING).limit(50)
        rejected_drafts = []
        
        for doc in rejected_drafts_ref.stream():
            draft_data = doc.to_dict()
            draft_data["id"] = doc.id
            rejected_drafts.append(draft_data)
        
        return render_template("history.html", sent_drafts=sent_drafts, rejected_drafts=rejected_drafts)
    
    except Exception as e:
        flash(f"Erreur lors de la récupération de l'historique: {str(e)}", "error")
        return render_template("history.html", sent_drafts=[], rejected_drafts=[])


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
