# Prospector UI - Email Review Interface

Interface web Flask pour reviewer et envoyer les drafts d'emails stockés dans Firestore.

## 🚀 Fonctionnalités

* **Liste des drafts** : Affiche tous les emails en attente de review
* **Détails du draft** : Vue détaillée avec destinataire, sujet et corps du message
* **Envoi d'email** : Bouton pour envoyer via le service `send_mail` (avec pixel de tracking)
* **Rejet de draft** : Marquer un draft comme rejeté
* **Historique** : Voir tous les emails envoyés et rejetés
* **Interface responsive** : Design moderne et épuré

---

## 📦 Installation locale

### Prérequis

* Python 3.9+
* Accès à Firestore
* Le service `send_mail` déployé et accessible

### Installation

```bash
cd prospector-ui

# Créer un environnement virtuel
python -m venv venv
source venv/bin/activate  # Linux/Mac
# ou
venv\Scripts\activate  # Windows

# Installer les dépendances
pip install -r requirements.txt

# Copier et configurer les variables d'environnement
cp .env.example .env
# Éditer .env avec vos configurations
```

### Configuration

Créer un fichier `.env` :

```bash
SECRET_KEY=votre-cle-secrete-random
SEND_MAIL_SERVICE_URL=https://draft-creator-xxxxx.a.run.app
DRAFT_COLLECTION=email_drafts
PORT=8080
```

### Lancement

```bash
# Développement
python app.py

# Production avec Gunicorn
gunicorn -w 4 -b 0.0.0.0:8080 app:app
```

L'application sera accessible sur `http://localhost:8080`

---

## ☁️ Déploiement Cloud Run

### Prérequis

* Projet GCP configuré
* Service account avec accès Firestore
* Service `send_mail` déployé

### Déploiement

```bash
gcloud run deploy prospector-ui \
  --source . \
  --region europe-west1 \
  --allow-unauthenticated \
  --set-env-vars "SEND_MAIL_SERVICE_URL=https://draft-creator-xxxxx.a.run.app" \
  --set-env-vars "DRAFT_COLLECTION=email_drafts" \
  --set-env-vars "SECRET_KEY=$(openssl rand -base64 32)"
```

**Note** : Pour une sécurité optimale, utilisez Cloud Run avec authentification et gérez les secrets via Secret Manager.

---

## 🗄️ Structure Firestore attendue

### Collection `email_drafts`

Chaque document doit avoir :

```json
{
  "to": "client@example.com",
  "subject": "Sujet du mail",
  "body": "Corps du message",
  "created_at": "2024-01-01T10:00:00Z",
  "status": "pending"
}
```

Après envoi, les champs suivants sont ajoutés :

```json
{
  "status": "sent",
  "sent_at": "2024-01-01T11:00:00Z",
  "message_id": "gmail-message-id",
  "pixel_id": "uuid-pixel"
}
```

---

## 📸 Pages disponibles

### `/` - Drafts en attente
Liste tous les drafts avec `status = "pending"`

### `/draft/<draft_id>` - Détails d'un draft
Affiche le contenu complet et permet d'envoyer ou rejeter

### `/history` - Historique
Liste des emails envoyés et drafts rejetés

### `/api/stats` - API Statistiques (JSON)
Retourne le nombre de drafts par statut

---

## 🔗 Intégration avec send_mail

L'application appelle l'endpoint `/send-draft` du service `send_mail` :

```bash
POST https://draft-creator-xxxxx.a.run.app/send-draft
Content-Type: application/json

{
  "draft_id": "uuid-du-draft"
}
```

Le service `send_mail` :
1. Récupère le draft depuis Firestore
2. Envoie l'email avec signature Gmail
3. Ajoute le pixel de tracking
4. Met à jour le statut dans Firestore

---

## 🎨 Personnalisation

### Modifier le style

Éditer le CSS dans `templates/base.html`

### Ajouter des fonctionnalités

* Recherche et filtres
* Édition de draft avant envoi
* Prévisualisation HTML
* Notifications en temps réel
* Statistiques avancées

---

## 🔒 Sécurité

**Recommandations pour la production :**

1. Activer l'authentification Cloud Run
2. Utiliser Secret Manager pour `SECRET_KEY`
3. Limiter les accès Firestore avec des règles de sécurité
4. Configurer HTTPS uniquement
5. Ajouter un système d'authentification utilisateur (OAuth, etc.)

---

## 🐛 Débogage

Les erreurs sont affichées via Flask flash messages.

Pour plus de détails, consulter les logs :

```bash
# Logs Cloud Run
gcloud run logs read prospector-ui --region europe-west1

# Logs locaux
# Activé automatiquement en mode debug
```

---

## 📝 Licence

Projet interne - Tous droits réservés
