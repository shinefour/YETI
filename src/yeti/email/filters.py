"""Email noise filters and pipeline glue.

Decides whether to ingest an email at all, and converts ingest-worthy
emails into Notes for triage.
"""

import logging
import re

from yeti.models.email_blacklist import EmailBlacklistStore

logger = logging.getLogger(__name__)

NOISE_SENDER_PATTERNS = [
    r"^no[-_]?reply",
    r"^do[-_]?not[-_]?reply",
    r"^notifications?@",
    r"^automated",
    r"^mailer[-_]?daemon",
    r"^postmaster@",
    r"^noreply",
    r"^bounce",
]
_NOISE_SENDER_RE = re.compile(
    "|".join(NOISE_SENDER_PATTERNS), re.IGNORECASE
)


def filter_email(
    sender: str,
    headers: dict | None = None,
) -> tuple[bool, str]:
    """Decide whether to ingest an email.

    Returns (should_ingest, reason_if_skipped).
    """
    headers = headers or {}

    # 1. Manual blacklist
    blacklist = EmailBlacklistStore()
    matched = blacklist.matches(sender)
    if matched:
        return False, f"blacklisted ({matched})"

    # 2. Noisy sender pattern
    if _NOISE_SENDER_RE.search(sender or ""):
        return False, "noisy sender pattern"

    # 3. Mailing list signal
    if (
        headers.get("List-Unsubscribe")
        or headers.get("list-unsubscribe")
    ):
        return False, "mailing list (List-Unsubscribe header)"

    # 4. Auto-generated
    auto_submitted = (
        headers.get("Auto-Submitted")
        or headers.get("auto-submitted")
        or ""
    )
    if (
        auto_submitted.lower() not in ("", "no")
        and auto_submitted
    ):
        return False, f"auto-submitted ({auto_submitted})"

    return True, ""
