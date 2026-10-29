"""Versioned capability themes: technology type and advert requiredness stay independent."""

import re
from dataclasses import dataclass

TAXONOMY_VERSION = "skills-v2"
THEMES = {
    "ai_engineering": "AI engineering",
    "api_and_system_design": "API and system design",
    "languages_and_frameworks": "Languages and frameworks",
    "cloud_and_delivery": "Cloud and delivery",
    "data_and_messaging": "Data and messaging",
    "quality_and_observability": "Quality and observability",
    "security": "Security",
    "collaboration": "Collaboration and leadership",
    "other": "Other skills",
}


@dataclass(frozen=True)
class SkillDefinition:
    name: str
    aliases: tuple[str, ...]
    category: str
    theme: str
    subtheme: str


DEFINITIONS: list[SkillDefinition] = []


def add(theme, subtheme, category, **skills):
    for name, aliases in skills.items():
        DEFINITIONS.append(
            SkillDefinition(name, tuple([name, *aliases]), category, theme, subtheme)
        )


add(
    "ai_engineering",
    "ai_assisted_development",
    "practice",
    **{
        "AI-assisted development": [
            "AI assisted development",
            "AI-assisted software development",
            "AI assisted software development",
            "AI coding assist",
            "AI-assisted coding",
            "AI coding tools",
            "AI coding assistants",
            "AI-assisted development tools",
            "AI-assisted software development tools",
            "AI coding assist tools",
        ],
        "AI output validation": [
            "validate AI-generated outputs",
            "validate, and refine AI-generated outputs",
            "validating AI-generated outputs",
            "critically evaluate AI-generated",
            "evaluate, validate, and refine AI-generated",
        ],
    },
)
add(
    "ai_engineering",
    "ai_assisted_development",
    "tool",
    **{
        "Claude Code": [],
        "GitHub Copilot": ["Github Copilot"],
        "Cursor": [],
        "ChatGPT": [],
        "Amazon Q Developer": ["CodeWhisperer"],
        "Codex": [],
    },
)
add(
    "ai_engineering",
    "applied_ai",
    "practice",
    **{
        "Artificial intelligence": ["AI"],
        "Generative AI": ["GenAI", "generative artificial intelligence"],
        "Large language models": ["LLM", "LLMs", "large language model"],
        "Retrieval-augmented generation": ["RAG", "retrieval augmented generation"],
        "Prompt engineering": [],
        "Agentic AI": ["AI agents", "agentic systems"],
        "AI evaluation": ["LLM evaluation", "model evaluation", "AI evaluations", "evals"],
        "Responsible AI": ["AI safety", "AI governance"],
    },
)
add(
    "ai_engineering",
    "machine_learning",
    "practice",
    **{
        "Machine learning": ["ML"],
        "Deep learning": [],
        "Natural language processing": ["NLP"],
    },
)
add(
    "ai_engineering",
    "ai_platforms",
    "platform",
    **{
        "OpenAI API": ["OpenAI"],
        "Anthropic API": ["Anthropic"],
        "Amazon Bedrock": ["AWS Bedrock"],
        "Azure AI": ["Azure OpenAI"],
        "Hugging Face": [],
        "MLOps": [],
        "Model serving": [],
    },
)
add(
    "ai_engineering",
    "ai_frameworks",
    "framework",
    **{"LangChain": [], "LlamaIndex": [], "PyTorch": [], "TensorFlow": []},
)
add(
    "api_and_system_design",
    "api_design",
    "practice",
    **{
        "API design": ["API designs", "design APIs", "designing APIs", "API architecture"],
        "API development": ["APIs", "API", "API development"],
        "REST APIs": ["REST API", "RESTful", "REST"],
        "API versioning": ["versioned API", "versioned APIs"],
        "API integration": ["API integrations"],
        "API contracts": ["contract-first", "API contracts"],
    },
)
add(
    "api_and_system_design",
    "api_protocols",
    "tool",
    **{"GraphQL": [], "gRPC": [], "OpenAPI": ["Swagger"], "Webhooks": ["webhook"]},
)
add(
    "api_and_system_design",
    "system_design",
    "practice",
    **{
        "System design": ["systems design"],
        "Distributed systems": ["distributed system"],
        "Microservices": ["microservice"],
        "Event-driven architecture": [
            "event-driven systems",
            "event driven architecture",
            "event driven systems",
        ],
        "Domain-driven design": ["DDD", "domain driven design"],
        "SOLID": [],
    },
)
add(
    "languages_and_frameworks",
    "languages",
    "language",
    **{
        "C#": ["C Sharp", "CSharp"],
        "Python": [],
        "JavaScript": ["JS"],
        "TypeScript": [],
        "Java": [],
        "Go": ["Golang"],
        "SQL": [],
        "Groovy": [],
        "Kotlin": [],
        "Rust": [],
    },
)
add(
    "languages_and_frameworks",
    "frameworks",
    "framework",
    **{
        ".NET": [],
        "React": [],
        "Spring Boot": [],
        "Vert.x": [],
        "RxJava": [],
        "Node.js": ["NodeJS", "Node/TypeScript"],
    },
)
add(
    "cloud_and_delivery",
    "cloud",
    "cloud",
    **{"AWS": ["Amazon Web Services"], "Azure": [], "GCP": ["Google Cloud Platform"]},
)
add("cloud_and_delivery", "containers", "platform", **{"Kubernetes": ["K8s"], "Docker": []})
add(
    "cloud_and_delivery",
    "delivery",
    "tool",
    **{
        "Terraform": [],
        "Jenkins": [],
        "GitHub Actions": [],
        "CI/CD": ["continuous integration", "continuous delivery"],
    },
)
add(
    "data_and_messaging",
    "databases",
    "database",
    **{
        "PostgreSQL": ["Postgres"],
        "NoSQL": [],
        "Couchbase": [],
        "DynamoDB": [],
        "Redis": [],
        "Vector databases": ["vector database"],
    },
)
add(
    "data_and_messaging",
    "messaging",
    "messaging",
    **{"Kafka": [], "RabbitMQ": [], "Service Bus": ["Azure Service Bus"]},
)
add(
    "quality_and_observability",
    "observability",
    "tool",
    **{"Prometheus": [], "Grafana": [], "OpenTelemetry": []},
)
add(
    "quality_and_observability",
    "engineering_practices",
    "practice",
    **{
        "Test-driven development": ["TDD", "test driven development"],
        "Behaviour-driven development": ["BDD", "behavior-driven development"],
        "Accessibility": ["WCAG", "assistive technology support"],
    },
)
add(
    "security",
    "application_security",
    "practice",
    **{
        "API security": [],
        "OAuth": ["OAuth2", "OAuth 2.0"],
        "Secure coding": ["secure coding standards"],
    },
)
add(
    "collaboration",
    "leadership",
    "soft_skill",
    **{
        "Mentoring": ["mentor", "mentorship"],
        "Code review": ["code reviews", "peer review"],
        "Pair programming": [],
    },
)

BY_NAME = {s.name: s for s in DEFINITIONS}
ALIASES = {s.name: list(s.aliases) for s in DEFINITIONS}
BY_ALIAS = {alias.casefold(): s for s in DEFINITIONS for alias in s.aliases}


def classify_skill(name, surface="", category="other"):
    known = BY_ALIAS.get(name.strip().casefold()) or BY_ALIAS.get(surface.strip().casefold())
    if known:
        return known.name, known.category, known.theme, known.subtheme
    # Unknown LLM labels may still describe recognized capability areas.
    lower = name.casefold()
    if re.search(r"\b(?:ai|llm|machine learning|prompt|retrieval.augmented)\b", lower):
        return name, category, "ai_engineering", "applied_ai"
    if re.search(r"\b(?:api|rest|graphql|grpc|system design|distributed)\b", lower):
        return name, category, "api_and_system_design", "api_design"
    theme = {
        "language": "languages_and_frameworks",
        "framework": "languages_and_frameworks",
        "cloud": "cloud_and_delivery",
        "database": "data_and_messaging",
        "messaging": "data_and_messaging",
        "soft_skill": "collaboration",
    }.get(category, "other")
    return name, category, theme, "general"


def find_skills(text):
    # Longest aliases first avoids treating AI/API inside a specific phrase as another skill.
    candidates = []
    for skill in DEFINITIONS:
        for alias in skill.aliases:
            for match in re.finditer(r"(?<!\w)" + re.escape(alias) + r"(?!\w)", text, re.I):
                if skill.name == "Go" and match.group() == "go":
                    continue
                candidates.append((match.start(), match.end(), skill, match.group()))
    occupied: list[tuple[int, int]] = []
    seen = set()
    for start, end, skill, surface in sorted(
        candidates, key=lambda item: (-(item[1] - item[0]), item[0])
    ):
        if skill.name in seen or any(start < right and end > left for left, right in occupied):
            continue
        seen.add(skill.name)
        occupied.append((start, end))
        yield skill, surface, start, end


def themed_result(data):
    if not data:
        return data
    result = dict(data)
    result["requirements"] = []
    for req in data.get("requirements", []):
        name, category, theme, subtheme = classify_skill(
            req["canonical_candidate"], req.get("surface_text", ""), req.get("category", "other")
        )
        if theme == "other" and req.get("theme") in THEMES:
            theme = req["theme"]
            subtheme = req.get("subtheme", "general")
        result["requirements"].append(
            {
                **req,
                "canonical_candidate": name,
                "category": category,
                "theme": theme,
                "subtheme": subtheme,
            }
        )
    return result
