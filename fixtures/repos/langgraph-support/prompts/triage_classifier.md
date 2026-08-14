# Triage classifier

Classify one inbound support message into exactly one intent.

Message: {message}

Intents: `order_status`, `refund`, `delivery`, `account`, `other`.

Return JSON: `{"intent": string, "confidence": number}`. Return `other` rather
than guessing between two intents.
