"""Print the packages that Home Assistant components need, as listed in their manifests.

The test package (pytest-homeassistant-custom-component) installs Home Assistant itself, but not the requirements
of single components. Brain Assist depends on `conversation`, which needs its own packages in the versions of the
installed Home Assistant:  pip install $(python scripts/ha_requirements.py conversation)
"""

import json
from pathlib import Path
import sys

import homeassistant

components = Path(homeassistant.__file__).parent / "components"
seen: set[str] = set()
packages: set[str] = set()
todo = sys.argv[1:] or ["conversation"]
while todo:
    name = todo.pop()
    if name in seen:
        continue
    seen.add(name)
    manifest = json.loads((components / name / "manifest.json").read_text(encoding="utf-8"))
    packages.update(manifest.get("requirements", []))
    todo.extend(manifest.get("dependencies", []))
print("\n".join(sorted(packages)))
