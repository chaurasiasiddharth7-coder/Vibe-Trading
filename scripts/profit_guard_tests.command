#!/bin/bash
# Syncs the project .venv with agent/requirements.txt, then runs the Profit Guard tests
# and the regression tests. Double-click in Finder.
cd "$(dirname "$0")/.." || exit 1
OUT=profit_guard_test_result.txt
echo "== Installing missing packages into .venv (from agent/requirements.txt) ==" | tee $OUT
.venv/bin/python -m pip install -q -r agent/requirements.txt aiohttp 2>&1 | tail -5 | tee -a $OUT
echo "pip exit: ${PIPESTATUS[0]}" | tee -a $OUT
cd agent || exit 1
echo | tee -a ../$OUT
echo "== Profit Guard tests ==" | tee -a ../$OUT
../.venv/bin/python --version | tee -a ../$OUT
PYTHONDONTWRITEBYTECODE=1 ../.venv/bin/python -m pytest tests/profit_guard -q -p no:cacheprovider -W ignore 2>&1 | tail -2 | tee -a ../$OUT
echo | tee -a ../$OUT
echo "== Users, pages, Strategies, review and audit fixes ==" | tee -a ../$OUT
PYTHONDONTWRITEBYTECODE=1 ../.venv/bin/python -m pytest tests/test_desk_users.py tests/test_wealth_pages.py tests/test_review_fixes.py tests/test_desk_profiles.py tests/test_auth_precedence.py tests/test_strategies.py tests/test_audit_fixes.py -q -p no:cacheprovider -W ignore 2>&1 | tail -2 | tee -a ../$OUT
echo | tee -a ../$OUT
echo "== Regression: runner / triggers / halt / flatten / live / btst ==" | tee -a ../$OUT
PYTHONDONTWRITEBYTECODE=1 ../.venv/bin/python -m pytest tests -q -p no:cacheprovider -W ignore -rf --tb=line -k "trigger or runner or halt or flatten or live or btst" --continue-on-collection-errors 2>&1 | grep -E "^FAILED|^ERROR|^E |passed|failed" | tail -40 | tee -a ../$OUT
echo
echo "Done. You can close this window."
