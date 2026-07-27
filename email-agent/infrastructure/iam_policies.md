# IAM Policies Reference

This document describes the IAM permissions required by the Email Agent Lambda functions.

## Main Lambda Function (`email-agent-handler`)

| Service | Actions | Resource | Purpose |
|---------|---------|----------|---------|
| DynamoDB | `GetItem`, `PutItem`, `Query` | `EmailAgentProcessedMessages` table | Deduplication check and metadata storage |
| Secrets Manager | `GetSecretValue` | `email-agent/*` secrets | Retrieve Gmail, Slack, and Twilio credentials |
| Bedrock | `InvokeModel` | `*` (model IDs vary) | Summarise email content |
| CloudWatch Logs | `CreateLogGroup`, `CreateLogStream`, `PutLogEvents` | `*` | Structured logging |

## Watch Renewal Lambda (`email-agent-watch-renewal`)

| Service | Actions | Resource | Purpose |
|---------|---------|----------|---------|
| Secrets Manager | `GetSecretValue` | `email-agent/gmail-credentials` | Authenticate with Gmail API |
| CloudWatch Logs | `CreateLogGroup`, `CreateLogStream`, `PutLogEvents` | `*` | Structured logging |

## Principle of Least Privilege

- The SAM template uses **SAM policy templates** (e.g., `DynamoDBCrudPolicy`) where possible.
- Bedrock `InvokeModel` uses `Resource: "*"` because model ARNs include the model ID, and users may change the model via parameters.  For tighter security in production, scope this to the specific model ARN.
- Secrets Manager access is scoped to the `email-agent/*` prefix.
