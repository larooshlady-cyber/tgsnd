import re
import random

_SPINTAX_RE = re.compile(r'\{spin:([^}]+)\}')


def spin(text: str) -> str:
    """Replace all {spin:a|b|c} tokens with a random choice.

    Example: "Hello {spin:friend|mate|buddy}!" -> "Hello mate!"
    """
    def _pick(match):
        options = match.group(1).split('|')
        return random.choice(options).strip()
    return _SPINTAX_RE.sub(_pick, text)
