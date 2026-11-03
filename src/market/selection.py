"""Select bounded, verbatim job evidence; never send an entire advert by default."""

import hashlib
import re
from dataclasses import dataclass

from market.taxonomy import find_skills

SELECTION_VERSION = "sections-v2"


@dataclass(frozen=True)
class Excerpt:
    id: str
    section: str
    text: str
    start: int
    end: int


HEADINGS = [
    ("preferred", r"preferred.*|nice to have.*|desirable.*|bonus.*"),
    (
        "requirements",
        r"required (?:qualifications|skills|experience).*|(?:the )?requirements.*|(?:minimum |essential |required )?qualifications.*|essential|and meet most of the following|your (?:skills|experience|background).*|the essentials|what you(?:’|\x27)ll (?:bring|need|have).*|what you will (?:bring|need).*|what you bring|what you need to succeed|what makes you a great fit|you will need|about you|what we(?:’|\x27)re looking for|what we are looking for|skills.*|experience and .*",
    ),
    (
        "responsibilities",
        r"you and the role|job responsibilities|responsibilities.*|you will be responsible for|what you(?:’|\x27)ll do|what you will do|the role|your role.*|your responsibilities.*|ownership and autonomy",
    ),
    ("stack", r"(?:our |the |technical |tech )?stack.*|technologies.*"),
    (
        "work_arrangement",
        r"where and how you(?:’|\x27)ll work|working pattern|location.*|work(?:ing)? (?:location|arrangement).*|where you(?:’|\x27)ll work.*",
    ),
    ("pay", r"(?:the |base )?salary.*|compensation.*|pay range.*"),
    (
        "exclude",
        r"the (?:company|benefits|culture|offices|package)|perks.*|what you should know about how we recruit|useful links.*|about (?:us|the company|jpmorgan.*)|who we are|our (?:company|mission|values|culture).*|benefits.*|what(?:s|’s|\x27s) in it for you.*|what we offer.*|equal opportunit.*|diversity.*|how to apply.*|application process.*|privacy.*",
    ),
]
SIGNAL = re.compile(
    r"\b(?:AI|LLMs?|GenAI|APIs?|GraphQL|gRPC|OpenAPI|Copilot|Cursor|Claude|RAG|MLOps|requir\w*|qualifications?|experience|proficien\w*|expertise|familiar\w*|knowledge|skills?|degree|certification|responsib\w*|develop\w*|design\w*|implement\w*|architect\w*|maintain\w*|mentor\w*|remote|hybrid|on-site|onsite|timezone|time zone|UTC|salary|per annum|annual pay|tech stack)\b|(?:£|€|USD|GBP|EUR)\s*\d",
    re.I,
)
INJECTION = re.compile(
    r"ignore (?:all |previous |prior )*(?:instructions|prompts)|system prompt|developer message|reveal.*(?:secret|api key)|<\|(?:system|assistant)",
    re.I,
)


def select_excerpts(text, max_chars=8000, max_excerpts=24):
    # Physical line breaks are often HTML formatting, not sentence boundaries.
    blocks = []
    section = "other"
    start = 0
    for match in re.finditer(r"[^\n]+", text):
        line = match.group().strip()
        heading = next(
            (
                kind
                for kind, pattern in HEADINGS
                if len(line) <= 110 and re.fullmatch(pattern, line.rstrip(":").strip(), re.I)
            ),
            None,
        )
        if heading:
            if match.start() > start:
                blocks.append((section, start, match.start()))
            section, start = heading, match.start()
    if start < len(text):
        blocks.append((section, start, len(text)))
    candidates = []
    for kind, left, right in blocks:
        if kind == "exclude":
            # Explicit work location or base pay can appear after benefits/company headings.
            for m in re.finditer(r"[^\n.!?]+(?:[.!?]|$)", text[left:right]):
                line = m.group()
                if re.search(
                    r"\b(?:fully remote|hybrid|on-site|onsite|UTC|base salary|salary range)\b|(?:£|GBP|EUR|USD|€)\s*\d[^\n]*(?:year|annum|hour|month)",
                    line,
                    re.I,
                ) and not re.search(
                    r"funding|revenue|billion|million|credit|bonus|equity", line, re.I
                ):
                    candidates.append(
                        (
                            "work_arrangement"
                            if re.search(r"remote|hybrid|onsite|UTC", line, re.I)
                            else "pay",
                            left + m.start(),
                            left + m.end(),
                        )
                    )
            continue
        # Keep contiguous lines together, up to a short evidence paragraph.
        position = left
        while position < right:
            end = min(position + 900, right)
            if end < right:
                boundary = text.rfind("\n", position + 250, end)
                if boundary == -1:
                    boundary = text.rfind(" ", position + 250, end)
                if boundary != -1:
                    end = boundary
            chunk = text[position:end]
            if INJECTION.search(chunk):
                for clean_line in re.finditer(r"[^\n]+", chunk):
                    heading_only = any(
                        re.fullmatch(pattern, clean_line.group().strip().rstrip(":"), re.I)
                        for _, pattern in HEADINGS
                    )
                    if (
                        not heading_only
                        and not INJECTION.search(clean_line.group())
                        and (
                            SIGNAL.search(clean_line.group())
                            or any(find_skills(clean_line.group()))
                        )
                    ):
                        candidates.append(
                            (kind, position + clean_line.start(), position + clean_line.end())
                        )
            elif kind != "other" or SIGNAL.search(chunk) or any(find_skills(chunk)):
                candidates.append((kind, position, end))
            position = end
    priority = {
        "requirements": 0,
        "preferred": 1,
        "pay": 2,
        "work_arrangement": 2,
        "stack": 3,
        "responsibilities": 4,
        "other": 5,
    }
    selected: list[Excerpt] = []
    used = 0
    seen = set()
    for kind, left, right in sorted(candidates, key=lambda item: (priority[item[0]], item[1])):
        raw = text[left:right]
        left += len(raw) - len(raw.lstrip())
        right -= len(raw) - len(raw.rstrip())
        quote = text[left:right]
        key = " ".join(quote.casefold().split())
        if (
            not quote
            or key in seen
            or used + len(quote) > max_chars
            or len(selected) >= max_excerpts
        ):
            continue
        seen.add(key)
        used += len(quote)
        selected.append(
            Excerpt(id=f"e{len(selected) + 1}", section=kind, text=quote, start=left, end=right)
        )
    return selected


def selection_metadata(text, excerpts):
    return {
        "selector_version": SELECTION_VERSION,
        "source_chars": len(text),
        "selected_chars": sum(len(e.text) for e in excerpts),
        "excerpts": [
            {
                "id": e.id,
                "section": e.section,
                "start": e.start,
                "end": e.end,
                "sha256": hashlib.sha256(e.text.encode()).hexdigest(),
            }
            for e in excerpts
        ],
    }


def payload_excerpts(excerpts):
    return [{"id": e.id, "section": e.section, "text": e.text} for e in excerpts]
