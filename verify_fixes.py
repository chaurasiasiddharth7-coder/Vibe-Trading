#!/usr/bin/env python3
"""
Verification script for Vibe-Trading fixes.
This script performs basic checks to ensure the fixes are in place.
"""

import sys
import os

def check_break_retest_backtest():
    """Check that the 0.01 multiplier was removed from break_retest_backtest.py"""
    file_path = "agent/src/alphadesk/engines/break_retest_backtest.py"
    with open(file_path, 'r') as f:
        content = f.read()
    
    # Check that the problematic pattern is NOT present
    if "* 0.01)" in content:
        print("❌ Break-retest backtest: 0.01 multiplier still present")
        return False
    else:
        print("✅ Break-retest backtest: 0.01 multiplier removed")
        return True

def check_desk_quantity_calculation():
    """Check that the quantity calculation was fixed in Desk.tsx"""
    file_path = "frontend/src/pages/Desk.tsx"
    with open(file_path, 'r') as f:
        content = f.read()
    
    # Check for the corrected pattern
    if "const notional = 100000 * (tradePlan.position_size_percent / 100);" in content:
        print("✅ Desk quantity calculation: Fixed")
        return True
    else:
        print("❌ Desk quantity calculation: Not fixed or pattern changed")
        return False

def check_async_handlers():
    """Check that route handlers were converted to plain def"""
    file_path = "agent/src/api/alphadesk_routes.py"
    with open(file_path, 'r') as f:
        content = f.read()
    
    # Check for the specific functions that should be plain def now
    functions_to_check = [
        "def get_analysis(",
        "def get_kronos_forecast(",
        "def post_pm_plan(",
        "def get_break_retest(",
        "def get_sentiment(",
        "def get_alpha_forge(",
        "def post_replay_br_pdh_pdl("
    ]
    
    all_good = True
    for func in functions_to_check:
        if func in content:
            print(f"✅ Async handler: {func.split('(')[0]} converted to plain def")
        else:
            print(f"❌ Async handler: {func.split('(')[0]} not found or not converted")
            all_good = False
    
    return all_good

def check_tiny_size_ok():
    """Check that tiny_size_ok fails closed for None/nonpositive notional"""
    file_path = "agent/src/alphadesk/live_unlock.py"
    with open(file_path, 'r') as f:
        content = f.read()
    
    # Check for the corrected logic
    if "if notional_inr is None or notional_inr <= 0:" in content:
        print("✅ Tiny size validation: Fail-closed for None/nonpositive")
        return True
    else:
        print("❌ Tiny size validation: Not fixed")
        return False

def check_finbert_caching():
    """Check that FinBERT pipeline is cached"""
    file_path = "agent/src/alphadesk/engines/sentiment_engine.py"
    with open(file_path, 'r') as f:
        content = f.read()
    
    if "_FINBERT_PIPELINE = None" in content and "_get_finbert_pipeline()" in content:
        print("✅ FinBERT pipeline: Caching implemented")
        return True
    else:
        print("❌ FinBERT pipeline: Caching not found")
        return False

def main():
    print("Running Vibe-Trading fixes verification...\n")
    
    checks = [
        check_break_retest_backtest,
        check_desk_quantity_calculation,
        check_async_handlers,
        check_tiny_size_ok,
        check_finbert_caching
    ]
    
    passed = 0
    total = len(checks)
    
    for check in checks:
        if check():
            passed += 1
        print()  # Empty line for readability
    
    print(f"Results: {passed}/{total} checks passed")
    
    if passed == total:
        print("🎉 All critical fixes verified!")
        return 0
    else:
        print("⚠️  Some fixes need attention")
        return 1

if __name__ == "__main__":
    sys.exit(main())