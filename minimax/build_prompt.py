"""Build a MiniMax development request from the seekdb-backed context output."""

from __future__ import annotations

import json
import sys
from pathlib import Path


def main() -> None:
    context = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
    sources = context["sources"]
    if {item["kind"] for item in sources} != {"spec", "code", "test"}:
        raise ValueError("Context must include specification, code, and tests")
    print(f"Task: {context['summary']}")
    print("Use the current specification, source, and tests below to change the code.")
    print("Return a unified diff for project/shipping.py and a short explanation.")
    print("Preserve the regular-order and invalid-input behavior. Do not edit tests.")
    for item in sources:
        print(f"\n--- {item['kind']}: {item['path']} (SHA-256 {item['sha256']}) ---")
        print(item["content"].rstrip())
    if context["previous_state"]:
        print("\n--- PowerContext task state ---")
        for state in context["previous_state"]:
            print(state)


if __name__ == "__main__":
    main()

