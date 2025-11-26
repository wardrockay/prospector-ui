# Prospector UI

Professional-grade email prospecting management interface with analytics, draft management, and email tracking.

## Architecture

```
prospector-ui/
├── src/
│   ├── __init__.py           # Package info
│   ├── app.py                # Flask application factory
│   ├── blueprints.py         # Flask blueprints (routes)
│   ├── config.py             # Pydantic settings
│   ├── models.py             # Data models
│   ├── repositories/
│   │   └── draft_repository.py  # Firestore data access
│   └── services/
│       └── draft_service.py  # Business logic
├── templates/
│   ├── base.html             # Base template
│   ├── index.html            # Pending drafts
│   ├── draft_detail.html     # Draft details with actions
│   ├── history.html          # Sent email history
│   ├── sent_draft_detail.html
│   ├── dashboard.html        # Analytics dashboard
│   └── kanban.html           # Kanban board view
├── static/                   # CSS, JS assets
├── tests/
├── pyproject.toml
├── requirements.txt
└── requirements-dev.txt
```

## Features

### Draft Management
- View and approve/reject pending drafts
- Edit draft content before sending
- Send test emails
- Keyboard shortcuts for quick actions

### Analytics Dashboard
- Total stats (sent, opens, replies, bounces)
- Daily activity charts
- Open and reply rates
- Conversion funnel

### Kanban Board
- Visual pipeline view
- Pending → Sent → Replied → Bounced columns

### Email Tracking
- Open tracking with timestamps
- Reply detection
- Thread message history

### Keyboard Shortcuts
| Key | Action |
|-----|--------|
| A | Approve and send |
| R | Reject draft |
| E | Edit draft |
| T | Send test email |
| C | Copy email content |
| N | Next pending draft |
| Esc | Cancel current action |

## Configuration

```python
from src.config import get_settings

settings = get_settings()
print(settings.services.draft_creator_url)
```

### Environment Variables

| Variable | Description | Default |
|----------|-------------|---------|
| `GCP_PROJECT_ID` | Google Cloud project | `light-and-shutter` |
| `SECRET_KEY` | Flask secret key | Dev key |
| `DRAFT_CREATOR_URL` | Draft creator service URL | Cloud Run URL |
| `MAIL_WRITER_URL` | Mail writer service URL | Cloud Run URL |
| `ENVIRONMENT` | Environment name | `development` |

## Routes

### Main Routes
- `GET /` - Pending drafts list
- `GET /draft/<id>` - Draft details
- `POST /draft/<id>/approve` - Approve and send
- `POST /draft/<id>/reject` - Reject draft
- `POST /draft/<id>/test` - Send test email
- `POST /draft/<id>/resend` - Resend to another

### History Routes
- `GET /history/` - Sent email history
- `GET /history/draft/<id>` - Sent draft details

### Dashboard Routes
- `GET /dashboard/` - Analytics dashboard

### Kanban Routes
- `GET /kanban/` - Kanban board view

### API Routes
- `GET/POST /api/draft/<id>/notes` - Get/update notes
- `GET /api/stats` - Get statistics
- `GET /api/activity` - Get daily activity
- `POST /api/delete-rejected` - Delete rejected drafts

### Followup Routes
- `GET /followup/<id>` - Followup details
- `POST /followup/<id>/send` - Send followup
- `POST /followup/generate` - Generate new followup

## Development

### Setup

```bash
python -m venv venv
source venv/bin/activate
pip install -r requirements-dev.txt
```

### Running Locally

```bash
# New modular app
python -m src.app

# Or with the old entry point
python app.py
```

### Testing

```bash
pytest
pytest --cov=src
mypy src/
ruff check src/
```

## Code Quality

### Architecture Patterns
- **Repository Pattern**: Clean separation of data access
- **Service Layer**: Business logic encapsulation
- **Blueprints**: Modular route organization
- **Pydantic Models**: Type-safe data validation

### Type Safety
- Full type hints with mypy validation
- Pydantic models for all data structures
- TypedDict for complex dictionaries

### Error Handling
- Centralized error handlers
- Proper HTTP status codes
- Structured error responses

## Deployment

```bash
gcloud builds submit --tag gcr.io/PROJECT_ID/prospector-ui
gcloud run deploy prospector-ui \
  --image gcr.io/PROJECT_ID/prospector-ui \
  --region europe-west1 \
  --platform managed
```

## License

Proprietary - LightAndShutter
