"""Import and apply reusable agent presets for NanoClaw groups."""

from __future__ import annotations

import json
import shutil
import time
from dataclasses import dataclass
from pathlib import Path

from config import get_settings

_STANDARD_FILES: tuple[str, ...] = (
    "CLAUDE.md",
    "memory.md",
    "SKILL.md",
    "skills.md",
)

_STANDARD_FILE_KEYS = {name.lower(): name for name in _STANDARD_FILES}
_SETTINGS_FILES = {
    "settings.json": ".claude/settings.json",
    "settings.local.json": ".claude/settings.local.json",
}
_BUILTIN_PRESET_SOURCE = "builtin://lumin"
_BUILTIN_PRESETS: dict[str, dict[str, str]] = {
    "business-partner": {
        "description": "Commercially sharp collaborator for strategy, partner work, and live research.",
        "CLAUDE.md": """# Business Partner

You are a proactive business partner for the user.

## Default Style

- Think like a trusted operator and strategic sounding board.
- Be concise, practical, and commercially aware.
- Turn ambiguity into clear options, tradeoffs, and next steps.
- Draft messages, plans, decision memos, and follow-up lists when useful.

## Tools And Workflow

- Use `agent-browser` when live research, competitor checks, pricing checks, or website verification would help.
- Use the workspace to keep lightweight notes, partner context, and decision logs.
- Prefer doing the work before replying. If asked for research, actually research. If asked for a draft, produce the draft.

## Output Shape

- Lead with the answer.
- Then give the most important supporting detail.
- End with suggested next steps when helpful.
""",
        "memory.md": """# Business Partner Memory

- Track important customers, partners, priorities, and recurring decisions here.
- Keep notes short and dated.
""",
    },
    "research-analyst": {
        "description": "Source-first research mode for comparisons, market scans, and evidence-backed answers.",
        "CLAUDE.md": """# Research Analyst

You are a careful research analyst for the user.

## Default Style

- Verify facts before presenting them as settled.
- Compare options clearly and separate facts from inference.
- Favor source-backed answers over guesses.

## Tools And Workflow

- Use `agent-browser` whenever live information, docs, support pages, product pages, or pricing pages are relevant.
- Preserve exact names, links, and file paths so findings are easy to verify.
- Summarize findings into concise bullets or comparison tables when useful.

## Output Shape

- Conclusion first
- Strongest evidence second
- Open questions or risks last
""",
    },
    "execution-lead": {
        "description": "Action-oriented operator for planning, follow-through, and getting work unblocked.",
        "CLAUDE.md": """# Execution Lead

You are an execution-focused operator for the user.

## Default Style

- Bias toward action, progress tracking, and closing loops.
- Break work into concrete steps with owners, status, and blockers.
- Keep updates short and momentum-oriented.

## Tools And Workflow

- Use files in the workspace for checklists, runbooks, and temporary execution notes.
- Use bash and file edits when the task calls for making something real, not just describing it.
- Use `agent-browser` when a website, dashboard, or workflow must be verified in the browser.

## Output Shape

- What changed
- What is blocked
- What happens next
""",
    },
    "autoresearch": {
        "description": "Karpathy-style autonomous research loop with short fixed experiments and measured keep/discard decisions.",
        "CLAUDE.md": """# AutoResearch

You are running a tight autonomous research loop inspired by Andrej Karpathy's March 2026 `autoresearch` project.

## Core Principle

Treat research as a repeatable loop:

1. Inspect the current baseline and objective.
2. Make one narrow change.
3. Run one short, fixed-budget experiment.
4. Read the metric.
5. Keep the change only if the metric improves.

## Working Style

- Keep the editable surface as small as possible.
- Prefer one-file or one-module changes when experimenting.
- Change one main idea per iteration so results stay legible.
- Use a fixed short wall-clock budget for experiments whenever possible.
- Record the baseline, hypothesis, metric, and outcome for every run.
- Revert or discard changes that do not improve the target metric.

## Output Shape

- Hypothesis
- Exact change
- Experiment command
- Metric before vs after
- Keep or discard

## Rules

- Do not wander into broad refactors during an experiment loop.
- Do not call something an improvement without a measured result.
- If no measurable benchmark exists yet, define one before claiming progress.
- Maintain an experiment log in the workspace so the loop compounds instead of repeating itself.
""",
        "memory.md": """# AutoResearch Log

Track:
- objective metric
- baseline
- experiment number
- hypothesis
- command run
- measured result
- keep/discard decision
""",
    },
}


@dataclass(frozen=True, slots=True)
class AgentPresetRecord:
    """Stored preset metadata."""

    name: str
    source_path: str
    imported_at: str
    description: str
    files: list[str]
    applied_groups: list[str]
    file_count: int
    skill_count: int


def _utc_timestamp() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _sanitize_name(name: str) -> str:
    cleaned = "".join(ch.lower() if ch.isalnum() else "-" for ch in name.strip())
    cleaned = "-".join(part for part in cleaned.split("-") if part)
    if not cleaned:
        raise ValueError("Preset name cannot be empty.")
    return cleaned


class AgentPresetManager:
    """Filesystem-backed preset library for migrating OpenClaw/NanoClaw setups."""

    def __init__(self, nanoclaw_root: str) -> None:
        self._root = Path(nanoclaw_root)
        self._presets_root = self._root / "presets"
        self._groups_root = self._root / "groups"
        self._presets_root.mkdir(parents=True, exist_ok=True)
        self._ensure_builtin_presets()

    def _preset_dir(self, preset_name: str) -> Path:
        return self._presets_root / _sanitize_name(preset_name)

    def _meta_path(self, preset_name: str) -> Path:
        return self._preset_dir(preset_name) / "preset.json"

    def list_presets(self) -> list[AgentPresetRecord]:
        records: list[AgentPresetRecord] = []
        for preset_dir in sorted(self._presets_root.iterdir()):
            if not preset_dir.is_dir():
                continue
            meta_path = preset_dir / "preset.json"
            if not meta_path.exists():
                continue
            payload = json.loads(meta_path.read_text(encoding="utf-8"))
            records.append(
                AgentPresetRecord(
                    name=str(payload.get("name") or preset_dir.name),
                    source_path=str(payload.get("source_path") or ""),
                    imported_at=str(payload.get("imported_at") or ""),
                    description=str(payload.get("description") or ""),
                    files=list(payload.get("files") or []),
                    applied_groups=list(payload.get("applied_groups") or []),
                    file_count=int(payload.get("file_count") or len(payload.get("files") or [])),
                    skill_count=int(payload.get("skill_count") or 0),
                )
            )
        return records

    def import_from_path(self, *, preset_name: str, source_path: str) -> AgentPresetRecord:
        source = Path(source_path).expanduser().resolve()
        if not source.exists() or not source.is_dir():
            raise FileNotFoundError(f"OpenClaw source path not found: {source}")

        preset_dir = self._preset_dir(preset_name)
        if preset_dir.exists():
            shutil.rmtree(preset_dir)
        preset_dir.mkdir(parents=True, exist_ok=True)

        copied: list[str] = []
        self._copy_standard_files(source, preset_dir, copied)
        self._copy_claude_tree(source, preset_dir, copied)
        if not copied:
            raise FileNotFoundError(
                "No importable OpenClaw/NanoClaw files were found. Expected files like "
                "CLAUDE.md, memory.md, skills.md, or a .claude/ tree."
            )

        record = AgentPresetRecord(
            name=_sanitize_name(preset_name),
            source_path=str(source),
            imported_at=_utc_timestamp(),
            description="Imported from an existing OpenClaw or NanoClaw folder.",
            files=sorted(copied),
            applied_groups=[],
            file_count=len(set(copied)),
            skill_count=len({path for path in copied if path.endswith("/SKILL.md") or path == "SKILL.md" or path == "skills.md"}),
        )
        self._write_record(record)
        return record

    def apply_preset(self, *, preset_name: str, group_id: str) -> AgentPresetRecord:
        record = self.get_preset(preset_name)
        preset_dir = self._preset_dir(preset_name)
        group_dir = self._groups_root / group_id
        group_dir.mkdir(parents=True, exist_ok=True)

        for standard_name in _STANDARD_FILES:
            source = preset_dir / standard_name
            if source.exists():
                shutil.copy2(source, group_dir / standard_name)

        preset_claude = preset_dir / ".claude"
        if preset_claude.exists():
            target_claude = group_dir / ".claude"
            if target_claude.exists():
                shutil.rmtree(target_claude)
            shutil.copytree(preset_claude, target_claude)

        applied = sorted({*record.applied_groups, group_id})
        updated = AgentPresetRecord(
            name=record.name,
            source_path=record.source_path,
            imported_at=record.imported_at,
            description=record.description,
            files=record.files,
            applied_groups=applied,
            file_count=record.file_count,
            skill_count=record.skill_count,
        )
        self._write_record(updated)
        return updated

    def get_preset(self, preset_name: str) -> AgentPresetRecord:
        meta_path = self._meta_path(preset_name)
        if not meta_path.exists():
            raise FileNotFoundError(f"Preset not found: {preset_name}")
        payload = json.loads(meta_path.read_text(encoding="utf-8"))
        return AgentPresetRecord(
            name=str(payload.get("name") or _sanitize_name(preset_name)),
            source_path=str(payload.get("source_path") or ""),
            imported_at=str(payload.get("imported_at") or ""),
            description=str(payload.get("description") or ""),
            files=list(payload.get("files") or []),
            applied_groups=list(payload.get("applied_groups") or []),
            file_count=int(payload.get("file_count") or len(payload.get("files") or [])),
            skill_count=int(payload.get("skill_count") or 0),
        )

    def _write_record(self, record: AgentPresetRecord) -> None:
        self._meta_path(record.name).write_text(
            json.dumps(
                {
                    "name": record.name,
                    "source_path": record.source_path,
                    "imported_at": record.imported_at,
                    "description": record.description,
                    "files": record.files,
                    "applied_groups": record.applied_groups,
                    "file_count": record.file_count,
                    "skill_count": record.skill_count,
                },
                indent=2,
                sort_keys=True,
            ),
            encoding="utf-8",
        )

    def _ensure_builtin_presets(self) -> None:
        for preset_name, files in _BUILTIN_PRESETS.items():
            preset_dir = self._preset_dir(preset_name)
            meta_path = preset_dir / "preset.json"
            if meta_path.exists():
                continue
            preset_dir.mkdir(parents=True, exist_ok=True)
            copied: list[str] = []
            description = str(files.get("description") or "")
            for relative_path, content in files.items():
                if relative_path == "description":
                    continue
                target = preset_dir / relative_path
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(content, encoding="utf-8")
                copied.append(relative_path)
            self._write_record(
                AgentPresetRecord(
                    name=_sanitize_name(preset_name),
                    source_path=f"{_BUILTIN_PRESET_SOURCE}/{_sanitize_name(preset_name)}",
                    imported_at=_utc_timestamp(),
                    description=description,
                    files=sorted(copied),
                    applied_groups=[],
                    file_count=len(copied),
                    skill_count=0,
                )
            )

    def _copy_standard_files(self, source: Path, preset_dir: Path, copied: list[str]) -> None:
        for path in source.rglob("*"):
            if not path.is_file():
                continue
            lower_name = path.name.lower()
            if lower_name in _STANDARD_FILE_KEYS:
                target_name = _STANDARD_FILE_KEYS[lower_name]
                shutil.copy2(path, preset_dir / target_name)
                copied.append(target_name)
            elif lower_name in _SETTINGS_FILES:
                target = preset_dir / _SETTINGS_FILES[lower_name]
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(path, target)
                copied.append(_SETTINGS_FILES[lower_name])

    def _copy_claude_tree(self, source: Path, preset_dir: Path, copied: list[str]) -> None:
        claude_source = source / ".claude"
        if not claude_source.exists() or not claude_source.is_dir():
            return
        claude_target = preset_dir / ".claude"
        shutil.copytree(claude_source, claude_target, dirs_exist_ok=True)
        for path in claude_source.rglob("*"):
            if path.is_file():
                copied.append(str(Path(".claude") / path.relative_to(claude_source)))


_PRESET_MANAGER: AgentPresetManager | None = None


def get_agent_preset_manager() -> AgentPresetManager:
    global _PRESET_MANAGER
    if _PRESET_MANAGER is None:
        _PRESET_MANAGER = AgentPresetManager(get_settings().nanoclaw_root)
    return _PRESET_MANAGER
