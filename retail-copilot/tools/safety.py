"""Small, explicit guardrails. These are heuristics, not authorization controls."""
import re

CARD = re.compile(r"(?<!\w)(?:\d[ -]?){13,19}(?!\w)")
EMAIL = re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.I)
PHONE = re.compile(r"(?<![A-Za-z0-9])(?:\+91[ -]?)?[6-9]\d{9}(?!\d)")
SECRET = re.compile(r"\b(?:cvv|pin|otp|password|bank account|government id)\s*(?:is|:|=)\s*[^\s,.]+", re.I)
RESTRICTED = re.compile(r"\b(?:show|reveal|print|give|export|send|list|access)\b.{0,90}\b(?:card numbers?|cvv|passwords?|credentials?|payment tokens?|customer database|private customer records?|supplier contract prices?|hidden judge test cases)\b", re.I | re.S)
OVERRIDE = re.compile(r"\b(?:ignore previous instructions|bypass safety|reveal (?:the )?(?:system|hidden) prompt|do not cite sources and invent|disable guardrails)\b", re.I)
REPORT = re.compile(r"\b(?:someone|customer|employee|caller|person)\b.{0,60}\b(?:asked|requested|tried|attempted)\b|\b(?:report|incident|suspicious request)\b", re.I)


def mask(text):
    value = str(text)[:4000]
    value = CARD.sub("[REDACTED_CARD]", value)
    value = EMAIL.sub("[REDACTED_EMAIL]", value)
    value = PHONE.sub("[REDACTED_PHONE]", value)
    value = SECRET.sub("[REDACTED]", value)
    return value, value != str(text)


def blocked(query):
    # A report *about* a suspicious request can be triaged safely.
    return bool((RESTRICTED.search(query) or OVERRIDE.search(query)) and not REPORT.search(query))
