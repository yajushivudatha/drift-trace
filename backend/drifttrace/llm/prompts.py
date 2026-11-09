"""Loads the versioned prompt files in /backend/prompts.

A prompt lives in `<name>.v<N>.md`. The highest N is used. Each file has a `<<<SYSTEM>>>` block and
a `<<<USER>>>` block; `{{variable}}` placeholders are filled by simple replacement so that JSON and
scientific text containing braces pass through untouched.
"""

import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

PROMPT_DIR = Path(__file__).resolve().parents[2] / "prompts"
_FILE = re.compile(r"^(?P<name>[a-z_]+)\.v(?P<version>\d+)\.md$")


@dataclass(frozen=True)
class Prompt:
    name: str
    version: int
    system: str
    user: str

    @property
    def tag(self) -> str:
        return f"{self.name}.v{self.version}"

    def render(self, **variables: str) -> tuple[str, str]:
        def fill(template: str) -> str:
            def sub(match: re.Match[str]) -> str:
                key = match.group(1)
                if key not in variables:
                    raise KeyError(f"prompt {self.tag} needs variable {key}")
                return variables[key]

            return re.sub(r"\{\{(\w+)\}\}", sub, template)

        return fill(self.system), fill(self.user)


@lru_cache
def load_prompt(name: str) -> Prompt:
    candidates: list[tuple[int, Path]] = []
    for path in PROMPT_DIR.iterdir():
        match = _FILE.match(path.name)
        if match and match["name"] == name:
            candidates.append((int(match["version"]), path))
    if not candidates:
        raise FileNotFoundError(f"no prompt file for {name!r} in {PROMPT_DIR}")
    version, path = max(candidates)
    raw = path.read_text()
    system = raw.split("<<<SYSTEM>>>", 1)[1].split("<<<USER>>>", 1)[0].strip()
    user = raw.split("<<<USER>>>", 1)[1].strip()
    return Prompt(name=name, version=version, system=system, user=user)
