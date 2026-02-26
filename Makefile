# Basil Claw: common targets. Use from repo root.
# Requires: Python env with deps (see README). For standalone-dry-run: .env with DATABASE_URL, OPENAI_API_KEY.

.PHONY: test standalone-dry-run

# Run unit tests (policy_retrieval, filters, basil_moment, standalone_once). No DB or network required for tests that mock.
test:
	python3 -m tests.test_policy_retrieval
	python3 -m tests.test_filters
	python3 -m tests.test_basil_moment
	python3 -m tests.test_standalone_once

# Standalone post dry-run: generate one post, print mode + filter result + text. No posting. Bypasses interval/post-enabled checks.
# Set DRY_RUN=1 or X_DRY_RUN=1 (default). Needs DATABASE_URL, OPENAI_API_KEY; STANDALONE_POST_ENABLED not required in dry-run.
standalone-dry-run:
	X_DRY_RUN=1 python3 -m x_bridge.run_standalone_once
