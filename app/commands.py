import re
from dataclasses import dataclass


@dataclass
class DuplicateCommand:
    canonical_number: int


@dataclass
class NotDuplicateCommand:
    pass


def parse_comment(body: str) -> DuplicateCommand | NotDuplicateCommand | None:
    """
    Parse maintainer commands from issue comments.

    Supported:
      /duplicate of #42
      /duplicate of 42
      /not-duplicate
    """
    body = body.strip()

    dup_match = re.search(
        r"^/duplicate\s+of\s+#?(\d+)",
        body,
        re.IGNORECASE | re.MULTILINE,
    )
    if dup_match:
        return DuplicateCommand(canonical_number=int(dup_match.group(1)))

    not_dup_match = re.search(
        r"^/not-duplicate",
        body,
        re.IGNORECASE | re.MULTILINE,
    )
    if not_dup_match:
        return NotDuplicateCommand()

    return None
