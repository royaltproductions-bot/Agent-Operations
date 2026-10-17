#!/usr/bin/env python3
"""Public-voice guard for autonomous AI documentary content.

Do not portray the account as being operated, corrected or directed by a
private human. Only independently verified customers/prospects may be
discussed, anonymously and without private information; that determination
is made before composing a post, not by this lexical safety check.
"""
import re

# Deliberately conservative: a false positive prevents a post rather than
# disclosing the private operator or breaking the autonomous first-person POV.
PROHIBITED = re.compile(
    r"\b(?:"
    r"human(?:s)?|user(?:s)?|"
    r"owner(?:s)?|overseer(?:s)?|supervisor(?:s)?|"
    r"my\s+(?:boss|creator|operator|handler|manager)|"
    r"the\s+(?:person|man|woman|guy|lady)\s+behind\s+(?:me|this|the\s+account)|"
    r"we|us|our|ours|ourselves|"
    r"royal\s*t|griffith\s*el|sirius\s*sage|outta\s*pocket\s*tv|outtapockettv|yaliyah"
    r")\b",
    re.IGNORECASE,
)

def assert_autonomous_voice(*parts):
    for part in parts:
        text = str(part or "")
        hit = PROHIBITED.search(text)
        if hit:
            raise ValueError(
                "Public autonomous POV rejected: forbidden operator/plural reference "
                + repr(hit.group(0))
            )
