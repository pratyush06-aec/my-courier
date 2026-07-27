# AWS Builder Mail Watcher Agent

An autonomous AI-powered email monitoring agent that continuously watches a Gmail inbox for new emails from specific AWS Builder Center senders. Fully event-driven — no polling.

## Features

- **Event-Driven:** Gmail Push Notifications via Google Cloud Pub/Sub → AWS API Gateway → Lambda. Zero polling.
- **AI Summarisation:** Amazon Bedrock (Claude 3 Haiku) extracts priority, deadlines, and action items as structured JSON.
- **Deduplication:** DynamoDB conditional writes with TTL ensure each email is processed exactly once.
- **Multi-Channel Notifications:** Slack (Block Kit) + WhatsApp (Twilio).
- **Structured Logging:** JSON-formatted logs for CloudWatch Logs Insights queries.
- **Retry Logic:** Exponential backoff on all external API calls.
- **Secrets Management:** All credentials retrieved from AWS Secrets Manager (local `.env` fallback for development).

## Architecture

See [architecture.md](architecture.md) for a detailed architecture diagram and data flow.

```
Gmail → Google Pub/Sub → API Gateway → Lambda → Bedrock + DynamoDB + Slack + WhatsApp
```

## Project Structure

```
email-agent/
├── lambda/
│   ├── handler.py             # Lambda entry point (orchestrator)
│   ├── gmail_client.py        # Gmail API + OAuth + History API
│   ├── bedrock_client.py      # Amazon Bedrock summarisation
│   ├── dynamodb_client.py     # Deduplication + metadata storage
│   ├── slack_client.py        # Slack Block Kit notifications
│   ├── whatsapp_client.py     # WhatsApp via Twilio
│   ├── secrets_manager.py     # AWS Secrets Manager client (cached)
│   ├── config.py              # Environment configuration
│   ├── models.py              # Pydantic data models
│   └── utils.py               # Logging, retry, email parsing
├── infrastructure/
│   ├── template.yaml          # AWS SAM template
│   └── iam_policies.md        # IAM permissions documentation
├── tests/
│   ├── unit/                  # Unit tests (mocked dependencies)
│   └── integration/           # Integration tests
├── requirements.txt
├── .env.example
├── architecture.md
└── README.md
```

## Prerequisites

- Python 3.13+
- AWS CLI + AWS SAM CLI
- Google Cloud Project (for Gmail API + Pub/Sub)
- Twilio account (for WhatsApp)
- Slack workspace (for Incoming Webhooks)

## Local Development Setup

1. **Clone and create virtual environment:**
   ```bash
   cd email-agent
   python -m venv .venv
   # Windows
   .venv\Scripts\activate
   # Linux/Mac
   source .venv/bin/activate
   ```

2. **Install dependencies:**
   ```bash
   pip install -r requirements.txt
   ```

3. **Configure environment:**
   ```bash
   cp .env.example .env
   # Edit .env with your credentials
   ```

4. **Set up Gmail OAuth (one-time):**
   - Create a Google Cloud Project at https://console.cloud.google.com
   - Enable the Gmail API
   - Create OAuth 2.0 credentials (Desktop app type)
   - Download the credentials file as `credentials.json`
   - Run the local auth flow:
     ```python
     from lambda.gmail_client import get_service
     get_service()  # Opens browser for consent; saves token.json
     ```

5. **Run tests:**
   ```bash
   pytest tests/ -v
   ```

## Deployment

### 1. Store secrets in AWS Secrets Manager

```bash
# Gmail OAuth credentials
aws secretsmanager create-secret \
    --name email-agent/gmail-credentials \
    --secret-string '{"client_id":"...","client_secret":"...","refresh_token":"...","token_uri":"https://oauth2.googleapis.com/token"}'

# Slack webhook
aws secretsmanager create-secret \
    --name email-agent/slack \
    --secret-string '{"webhook_url":"https://hooks.slack.com/services/..."}'

# Twilio credentials
aws secretsmanager create-secret \
    --name email-agent/twilio \
    --secret-string '{"account_sid":"...","auth_token":"...","from_number":"whatsapp:+14155238886","to_number":"whatsapp:+..."}'
```

### 2. Deploy with AWS SAM

```bash
cd infrastructure
sam build
sam deploy --guided
```

### 3. Configure Google Cloud Pub/Sub

After deployment, SAM outputs the webhook URL. Configure your Pub/Sub push subscription to point to it:

```bash
gcloud pubsub subscriptions create gmail-push-sub \
    --topic=gmail-push \
    --push-endpoint=https://<api-id>.execute-api.<region>.amazonaws.com/prod/webhook/gmail
```

### 4. Register Gmail Watch

```python
from lambda.gmail_client import get_service, setup_watch
service = get_service()
setup_watch(service, "projects/<your-project>/topics/gmail-push")
```

## Environment Variables

| Variable | Description | Default |
|----------|-------------|---------|
| `AWS_REGION` | AWS region for DynamoDB, Secrets Manager | `eu-north-1` |
| `BEDROCK_REGION` | AWS region for Bedrock (model availability varies) | `us-east-1` |
| `BEDROCK_MODEL_ID` | Bedrock model ID | `anthropic.claude-3-haiku-20240307-v1:0` |
| `ALLOWED_SENDERS` | Comma-separated list of allowed sender emails | `StudentBuilders@amazon.com,judkatha@amazon.com` |
| `DYNAMODB_TABLE_NAME` | DynamoDB table name | `EmailAgentProcessedMessages` |
| `SLACK_WEBHOOK_URL` | Slack webhook URL (dev only) | — |
| `TWILIO_ACCOUNT_SID` | Twilio SID (dev only) | — |
| `TWILIO_AUTH_TOKEN` | Twilio auth token (dev only) | — |
| `TWILIO_FROM_NUMBER` | Twilio WhatsApp sender | — |
| `TWILIO_TO_NUMBER` | Recipient WhatsApp number | — |
| `PUBSUB_TOPIC_NAME` | Google Pub/Sub topic for watch renewal | — |

## Development Phases

- [x] Phase 1: Project setup
- [x] Phase 2: Google Gmail API integration
- [x] Phase 3: Gmail Push Notifications
- [x] Phase 4: AWS Infrastructure (SAM)
- [x] Phase 5: Sender filtering & deduplication
- [x] Phase 6: Slack integration
- [x] Phase 7: WhatsApp integration
- [x] Phase 8: Amazon Bedrock integration
- [x] Phase 9: Logging & error handling
- [x] Phase 10: Deployment & documentation

## License

This project is built for the AWS Builder Center Weekend Challenge.
