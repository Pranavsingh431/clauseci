"""
Rendering a candidate as an inspectable correction.

Produces the proposed YAML and a unified diff against the actual head file.
Nothing is applied, committed or pushed. No model is involved.

The renderer edits lines rather than regenerating the document, so the patch
stays small and the file keeps its comments. Because line editing can go wrong,
the result is always re-parsed and checked against the candidate's own effective
values before it is returned.
"""

from __future__ import annotations

import difflib

from clauseci.config_resolution import parse_retention_config
from clauseci.domain.candidates import Candidate, effective_map


class RenderError(RuntimeError):
    """The rendered correction does not reproduce the candidate."""


def render_candidate_yaml(head_text: str, candidate: Candidate) -> str:
    """Apply the candidate's field changes to the head configuration text."""
    lines = head_text.splitlines()

    for change in sorted(candidate.changes_vs_head, key=lambda c: c.config_path):
        parts = change.config_path.split(".")
        if parts[0] == "defaults":
            lines = _set_in_block(lines, block_path=["defaults"], field=parts[1],
                                  value=change.after, indent=2)
        elif parts[0] == "customers":
            lines = _set_in_block(lines, block_path=["customers", parts[1]],
                                  field=parts[2], value=change.after, indent=4)
    return "\n".join(lines) + ("\n" if head_text.endswith("\n") else "")


def render_patch(head_text: str, proposed_text: str, path: str) -> str:
    """A unified diff, for a human to read before applying anything."""
    return "".join(
        difflib.unified_diff(
            head_text.splitlines(keepends=True),
            proposed_text.splitlines(keepends=True),
            fromfile=f"a/{path}",
            tofile=f"b/{path}",
            n=3,
        )
    )


def verify_rendering(
    proposed_text: str,
    candidate: Candidate,
    cohort: list[str],
    config_keys: dict[str, str],
) -> None:
    """
    Re-parse the rendered document and confirm it reproduces the candidate.

    This is the check that makes line editing safe. If the edit missed a field
    or wrote it in the wrong block, the effective values will not match and this
    raises rather than handing over a wrong patch.
    """
    try:
        config = parse_retention_config(proposed_text)
    except Exception as exc:  # noqa: BLE001
        raise RenderError(f"the rendered document does not parse: {exc}") from exc

    rendered = {k: v[0] for k, v in effective_map(config, cohort, config_keys).items()}
    expected = {
        (value.customer_id, value.category): value.value
        for value in candidate.effective_values
    }
    if rendered != expected:
        differences = [
            f"{key[0]} {key[1]}: rendered {rendered.get(key)}, candidate {expected.get(key)}"
            for key in sorted(set(rendered) | set(expected))
            if rendered.get(key) != expected.get(key)
        ]
        raise RenderError(
            "the rendered correction does not reproduce the candidate: "
            + "; ".join(differences)
        )


def _set_in_block(
    lines: list[str], block_path: list[str], field: str, value: int | None, indent: int
) -> list[str]:
    """Set `field` inside a nested YAML block, adding the line if it is absent."""
    start, end = _find_block(lines, block_path)
    if start is None:
        raise RenderError(f"could not find the block {'.'.join(block_path)}")

    prefix = " " * indent
    for position in range(start, end):
        stripped = lines[position].strip()
        if stripped.startswith(f"{field}:"):
            if value is None:
                return lines[:position] + lines[position + 1:]
            return lines[:position] + [f"{prefix}{field}: {value}"] + lines[position + 1:]

    if value is None:
        return lines

    insert_at = end
    while insert_at > start and not lines[insert_at - 1].strip():
        insert_at -= 1
    return lines[:insert_at] + [f"{prefix}{field}: {value}"] + lines[insert_at:]


def _find_block(lines: list[str], path: list[str]) -> tuple[int | None, int]:
    """Return the half open line range of a nested mapping key."""
    start, end, indent = 0, len(lines), -1

    for key in path:
        found = None
        for position in range(start, end):
            line = lines[position]
            if not line.strip() or line.lstrip().startswith("#"):
                continue
            current_indent = len(line) - len(line.lstrip())
            if current_indent <= indent:
                break
            if line.lstrip().startswith(f"{key}:"):
                found = position
                indent = current_indent
                break
        if found is None:
            return None, end

        block_end = end
        for position in range(found + 1, end):
            line = lines[position]
            if not line.strip() or line.lstrip().startswith("#"):
                continue
            if len(line) - len(line.lstrip()) <= indent:
                block_end = position
                break
        start, end = found + 1, block_end

    return start, end
