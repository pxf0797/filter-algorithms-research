"""
Tests for Optuna-based parameter optimizer.
"""
import optuna
import pytest

from filter_app.services.param_optimizer import (
    create_study,
    optimize_backtest_params,
    suggest_params,
)


def objective_sphere(trial):
    """Simple sphere function: f(x,y) = -(x^2 + y^2), max at (0,0) = 0"""
    x = trial.suggest_float("x", -5, 5)
    y = trial.suggest_float("y", -5, 5)
    return -(x ** 2 + y ** 2)


def test_create_study_basic():
    study = create_study()
    assert study is not None
    assert study.direction == optuna.study.StudyDirection.MAXIMIZE


def test_create_study_minimize():
    study = create_study(direction="minimize")
    assert study.direction == optuna.study.StudyDirection.MINIMIZE


def test_optimize_basic():
    best_params, best_value = optimize_backtest_params(
        objective_sphere, n_trials=30, n_jobs=1,
    )
    assert "x" in best_params
    assert "y" in best_params
    # Should be close to (0, 0) with reasonable tolerance
    assert abs(best_params["x"]) < 2.0
    assert abs(best_params["y"]) < 2.0
    assert best_value > -4.0  # not too far from optimum 0


def test_optimize_with_study():
    study = create_study()
    best_params, best_value = optimize_backtest_params(
        objective_sphere, n_trials=30, n_jobs=1, study=study,
    )
    assert len(study.trials) == 30
    assert best_value <= 0


PARAM_SPACE = {
    "window": {"type": "int", "low": 5, "high": 60},
    "threshold": {"type": "float", "low": 0.01, "high": 0.5, "log": True},
    "mode": {"type": "categorical", "choices": ["fast", "medium", "slow"]},
}


def objective_with_param_space(trial):
    params = suggest_params(trial, PARAM_SPACE)
    score = 0.0
    # prefer low threshold and moderate window
    score -= params["threshold"] * 10
    score -= abs(params["window"] - 20) * 0.05
    if params["mode"] == "fast":
        score += 1.0
    return score


def test_suggest_params():
    import optuna

    study = optuna.create_study(direction="maximize")
    trial = study.ask()

    params = suggest_params(trial, PARAM_SPACE)
    assert 5 <= params["window"] <= 60
    assert 0.01 <= params["threshold"] <= 0.5
    assert params["mode"] in ("fast", "medium", "slow")


def test_optimize_param_space():
    best_params, best_value = optimize_backtest_params(
        objective_with_param_space, n_trials=50, n_jobs=1,
    )
    assert best_params["mode"] == "fast"
    assert best_params["threshold"] < 0.1  # should prefer low threshold
    assert 10 <= best_params["window"] <= 30  # near 20


def test_unknown_param_type():
    import optuna

    study = optuna.create_study(direction="maximize")
    trial = study.ask()
    bad_space = {"x": {"type": "unknown"}}
    with pytest.raises(ValueError, match="Unknown param type"):
        suggest_params(trial, bad_space)


def test_n_trials_limit():
    """A cheap test that n_trials controls trial count exactly."""
    study = create_study()
    optimize_backtest_params(objective_sphere, n_trials=5, n_jobs=1, study=study)
    assert len(study.trials) == 5
