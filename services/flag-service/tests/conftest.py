import sys
from unittest.mock import MagicMock

# Mock the database module so tests never need a real Postgres connection.
# The actual DB dependency is overridden per-test via app.dependency_overrides.
sys.modules["database"] = MagicMock()
