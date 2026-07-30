"""Shared test configuration.

Puts the repository root on `sys.path` so the CI gate scripts and the custom
import-linter contracts can be imported and driven directly. A gate that can
only be exercised through a subprocess is a gate nobody writes a negative test
for -- and these four gates are only worth having if they are known to reject.
"""

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
