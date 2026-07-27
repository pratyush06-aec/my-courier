# Architecture — AWS Builder Mail Watcher Agent

## System Overview

```
┌─────────────┐     ┌──────────────────┐     ┌──────────────────┐     ┌─────────────────┐
│   Gmail      │────▶│  Google Cloud     │────▶│  AWS API Gateway │────▶│  AWS Lambda      │
│   Inbox      │     │  Pub/Sub          │     │  (POST /webhook) │     │  (handler.py)    │
└─────────────┘     └──────────────────┘     └──────────────────┘     └────────┬────────┘
                                                                               │
                          ┌────────────────────────────────────────────────────┤
                          │                    │                  │             │
                          ▼                    ▼                  ▼             ▼
                   ┌──────────────┐    ┌──────────────┐  ┌────────────┐  ┌───────────┐
                   │ Amazon       │    │   Slack       │  │  Twilio    │  │ Amazon    │
                   │ Bedrock      │    │   Webhook     │  │  WhatsApp  │  │ DynamoDB  │
                   │ (Summarise)  │    │   (Notify)    │  │  (Notify)  │  │ (Store)   │
                   └──────────────┘    └──────────────┘  └────────────┘  └───────────┘
```

## Data Flow

### 1. Gmail Push Notification (Event Source)

```
Gmail Inbox
    │
    ▼ users.watch() registered
Google Cloud Pub/Sub Topic
    │
    ▼ Push subscription (HTTPS)
AWS API Gateway (POST /webhook/gmail)
    │
    ▼ Proxy integration
AWS Lambda (handler.lambda_handler)
```

- **Why Push, not Poll?** Polling every minute wastes compute, costs money, and introduces latency. Gmail push via Pub/Sub is free, instant, and event-driven.
- **Watch Renewal:** Gmail watches expire after 7 days. An EventBridge scheduled rule triggers `renew_watch_handler` every 6 days.

### 2. Message Processing Pipeline

```
Lambda Handler
    │
    ├─▶ 1. Parse Pub/Sub notification (decode base64 → historyId)
    │
    ├─▶ 2. Gmail History API (fetch new message IDs since historyId)
    │
    ├─▶ 3. For each message:
    │       │
    │       ├─▶ DynamoDB: is_duplicate(message_id)? → Skip if yes
    │       │
    │       ├─▶ Gmail API: get full message (headers, body)
    │       │
    │       ├─▶ Sender Validation: is sender in allow-list? → Skip if no
    │       │
    │       ├─▶ Bedrock: summarise_email() → structured JSON
    │       │       {subject, sender, priority, deadline, action_items, summary}
    │       │
    │       ├─▶ DynamoDB: store_email() (conditional put, atomic dedup)
    │       │
    │       ├─▶ Slack: send_notification() (Block Kit formatted)
    │       │
    │       └─▶ WhatsApp: send_notification() (Twilio API)
    │
    └─▶ Return HTTP 200 (acknowledge Pub/Sub)
```

### 3. Deduplication Strategy

```
                   ┌──────────────────────────────┐
                   │       DynamoDB Table          │
                   │  EmailAgentProcessedMessages  │
                   ├──────────────────────────────┤
                   │  PK: message_id (String)      │
                   │  ttl: epoch (auto-expire 30d) │
                   └──────────────────────────────┘
```

- **Pre-check:** `is_duplicate(message_id)` returns early before any processing.
- **Atomic write:** `put_item` with `attribute_not_exists(message_id)` condition prevents race conditions between concurrent Lambda invocations.
- **TTL cleanup:** Records auto-expire after 30 days at no cost.

## AWS Services Map

| Service | Purpose | Free Tier |
|---------|---------|-----------|
| Lambda | Compute (Python 3.13) | 1M requests/month |
| API Gateway | HTTPS endpoint for Pub/Sub | 1M calls/month |
| DynamoDB | Deduplication + metadata storage | 25 GB + 25 WCU/RCU |
| Secrets Manager | Credential storage | $0.40/secret/month |
| Bedrock | AI email summarisation | Pay-per-token |
| CloudWatch | Structured JSON logging | 5 GB ingest/month |
| EventBridge | Scheduled watch renewal | Free |

## Security

- All credentials stored in **AWS Secrets Manager** (not env vars).
- IAM roles follow **least privilege** — each Lambda only gets permissions it needs.
- DynamoDB access scoped to the specific table.
- Secrets Manager access scoped to `email-agent/*` prefix.

## Module Dependency Graph

```
handler.py (orchestrator)
    ├── config.py          (environment configuration)
    ├── models.py          (Pydantic data models)
    ├── utils.py           (logging, retry, parsing)
    ├── secrets_manager.py (credential retrieval)
    ├── gmail_client.py    (Gmail API interaction)
    ├── bedrock_client.py  (AI summarisation)
    ├── dynamodb_client.py (storage + deduplication)
    ├── slack_client.py    (Slack notifications)
    └── whatsapp_client.py (WhatsApp via Twilio)
```

Each module is **independently testable** with mocked dependencies.
