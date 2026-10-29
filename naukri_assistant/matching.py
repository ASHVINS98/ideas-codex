"""Explainable keyword scoring; no remote AI service or resume upload."""

from __future__ import annotations

import hashlib
import json
import math
import re
from dataclasses import asdict, dataclass
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit
from zipfile import ZipFile
from xml.etree import ElementTree


STOP_WORDS = set("a an and are as at be by for from in is it of on or our the this to we will with you your job work team years experience skills responsibilities required requirements role company candidate have has using looking ability strong good knowledge developer engineer development engineering".split())
ALIASES = {"bangalore": "bengaluru", "gurgaon": "gurugram", "node.js": "nodejs", "react.js": "react", "postgresql": "postgres", "rest apis": "rest api"}


def normal(text: str) -> str:
    text = re.sub(r"\s+", " ", text.lower()).strip()
    for source, target in ALIASES.items():
        text = re.sub(r"(?<!\w)" + re.escape(source) + r"(?!\w)", target, text)
    return text


def contains(text: str, term: str) -> bool:
    return bool(re.search(r"(?<![\w+#])" + re.escape(normal(term)) + r"(?![\w+#])", normal(text)))


def words(text: str) -> set[str]:
    return {w for w in re.findall(r"[a-z][a-z0-9+#.]*", normal(text)) if len(w) > 1 and w not in STOP_WORDS}


def job_url(value: str) -> str:
    parsed = urlsplit(value)
    if (parsed.scheme != "https" or parsed.hostname not in {"www.naukri.com", "naukri.com"}
            or parsed.username or parsed.password or parsed.port not in (None, 443)
            or not re.fullmatch(r"/job-listings-[a-zA-Z0-9_-]+-\d{10,20}/?", parsed.path)):
        raise ValueError("Expected an HTTPS Naukri job-listings URL with a numeric job ID")
    return urlunsplit(("https", "www.naukri.com", parsed.path.rstrip("/"), "", ""))


@dataclass
class Job:
    url: str
    title: str
    company: str
    location: str
    description: str
    min_experience: float | None = None
    max_experience: float | None = None

    @property
    def id(self) -> str:
        return self.url.rsplit("-", 1)[-1]

    @classmethod
    def from_dict(cls, data: dict) -> "Job":
        result = cls(**{key: data[key] for key in cls.__dataclass_fields__ if key in data})
        result.url = job_url(result.url)
        for key in ("title", "company", "location", "description"):
            if not isinstance(getattr(result, key), str):
                raise ValueError(f"Job {key} must be text")
        for key in ("min_experience", "max_experience"):
            value = getattr(result, key)
            if value is not None and (isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0):
                raise ValueError(f"Invalid {key}")
        if result.min_experience is not None and result.max_experience is not None and result.min_experience > result.max_experience:
            raise ValueError("Invalid experience range")
        return result


def resume_text(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix == ".pdf":
        from pypdf import PdfReader
        text = "\n".join(page.extract_text() or "" for page in PdfReader(path).pages)
    elif suffix == ".docx":
        with ZipFile(path) as archive:
            root = ElementTree.fromstring(archive.read("word/document.xml"))
        text = " ".join(node.text or "" for node in root.iter() if node.tag.endswith("}t"))
    elif suffix in {".txt", ".md"}:
        text = path.read_text(encoding="utf-8")
    else:
        raise ValueError("Resume must be PDF, DOCX, TXT, or Markdown")
    if len(text.strip()) < 40:
        raise ValueError("Resume has insufficient readable text; export scanned PDFs as text first")
    return text


@dataclass
class Profile:
    config: dict
    resume: str
    fingerprint: str

    @classmethod
    def load(cls, path: Path) -> "Profile":
        config = json.loads(path.read_text(encoding="utf-8"))
        for field in ("target_roles", "skills"):
            if not isinstance(config.get(field), list) or not config[field]:
                raise ValueError(f"{field} must be a nonempty list")
        for field in ("target_roles", "skills", "preferred_locations", "exclude_companies", "exclude_keywords"):
            config.setdefault(field, [])
            if not isinstance(config[field], list) or any(not isinstance(item, str) or not item.strip() for item in config[field]):
                raise ValueError(f"{field} must contain nonempty strings")
        for field, default, upper in (("experience_years", None, 60), ("minimum_score", 75, 100)):
            value = config.setdefault(field, default)
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not 0 <= value <= upper:
                raise ValueError(f"{field} must be a number between 0 and {upper}")
        config.setdefault("allow_remote", True)
        if not isinstance(config["allow_remote"], bool):
            raise ValueError("allow_remote must be true or false")
        if not isinstance(config.get("resume_path"), str):
            raise ValueError("resume_path must name your local resume file")
        resume = resume_text(path.parent / config["resume_path"])
        digest = hashlib.sha256((json.dumps(config, sort_keys=True) + resume).encode()).hexdigest()
        return cls(config, resume, digest)


def score(job: Job, profile: Profile) -> dict:
    cfg = profile.config
    text = job.title + " " + job.description
    reasons, review = [], []
    if not job.title.strip() or len(job.description.strip()) < 80:
        review.append("Job title or full description unavailable")
    if not job.company.strip():
        review.append("Company unavailable")
    if any(contains(job.company, company) for company in cfg["exclude_companies"]):
        reasons.append("Excluded company")
    if any(contains(text, term) for term in cfg["exclude_keywords"]):
        reasons.append("Excluded keyword")
    remote = contains(job.location, "remote")
    if remote and not cfg["allow_remote"]:
        reasons.append("Remote work excluded")
    if not job.location.strip():
        review.append("Location unavailable")
    elif cfg["preferred_locations"] and not (remote and cfg["allow_remote"]) and not any(contains(job.location, loc) for loc in cfg["preferred_locations"]):
        reasons.append("Outside preferred locations")
    experience_points = 0
    if job.min_experience is None or job.max_experience is None:
        review.append("Experience range unavailable")
    elif job.min_experience <= cfg["experience_years"] <= job.max_experience:
        experience_points = 20
    else:
        reasons.append("Experience outside advertised range")
    skills = list(dict.fromkeys(normal(skill) for skill in cfg["skills"]))
    matched = [skill for skill in skills if contains(text, skill)]
    # Keep role words for title matching; they are intentionally excluded only from resume overlap.
    def role_tokens(value: str) -> set[str]:
        return set(re.findall(r"[a-z][a-z0-9+#.]*", normal(value))) - {"a", "an", "and", "or", "the", "of"}
    title_tokens = role_tokens(job.title)
    title_match = max(len(role_tokens(role) & title_tokens) / max(len(role_tokens(role)), 1) for role in cfg["target_roles"])
    if title_match < 0.5:
        reasons.append("Title does not match target roles")
    description_words = words(job.description)
    overlap = len(description_words & words(profile.resume)) / max(len(description_words), 1)
    components = {
        "skill_keywords": round(40 * len(matched) / len(skills), 2),
        "target_title": round(25 * title_match, 2),
        "experience": experience_points,
        "resume_overlap": round(15 * overlap, 2),
    }
    total = round(sum(components.values()), 2)
    return {"job": asdict(job), "job_id": job.id, "score": total, "components": components,
            "matched_skills": matched, "missing_profile_keywords": [skill for skill in skills if skill not in matched],
            "excluded_reasons": reasons, "manual_review_reasons": review,
            "eligible": not reasons and not review and total >= cfg["minimum_score"]}


def rank(jobs: list[Job], profile: Profile) -> list[dict]:
    unique = {job.id: job for job in jobs}
    return sorted((score(job, profile) for job in unique.values()), key=lambda row: (not row["excluded_reasons"], row["score"]), reverse=True)
