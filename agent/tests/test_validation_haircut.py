"""Multiple-testing haircut: the Sharpe the best of N worthless strategies reaches by luck."""
import numpy as np
import pandas as pd

from backtest.validation import multiple_testing_haircut, run_validation, selection_bar


def _equity(daily_mean: float, n: int = 756, seed: int = 0) -> pd.Series:
    rets = np.random.default_rng(seed).normal(daily_mean, 0.01, n)
    return pd.Series(100_000 * np.cumprod(1 + rets))


def test_bar_grows_with_trials_and_shrinks_with_history():
    assert selection_bar(1, 756) == 0.0
    assert selection_bar(10, 756) < selection_bar(1000, 756)
    assert selection_bar(100, 252) > selection_bar(100, 2520)


def test_many_tries_explain_a_modest_sharpe_but_not_a_strong_one():
    modest = _equity(0.0004)
    assert multiple_testing_haircut(modest, 1)["passes"] is True
    assert multiple_testing_haircut(modest, 5000)["passes"] is False
    strong = multiple_testing_haircut(_equity(0.003), 5000)
    assert strong["passes"] is True and strong["haircut_sharpe"] > 0


def test_bad_input_and_runner_wiring():
    assert "error" in multiple_testing_haircut(_equity(0.001), 0)
    out = run_validation({"validation": {"multiple_testing": {"n_trials": 50}}},
                         _equity(0.001), [], 100_000)
    assert out["multiple_testing"]["n_trials"] == 50
