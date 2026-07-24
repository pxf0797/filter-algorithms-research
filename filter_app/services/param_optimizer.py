"""
Optuna-based Bayesian parameter optimization for backtest parameters.
"""

import optuna


def suggest_params(trial, param_space):
    """
    根据 param_space 定义，在 trial 上 suggest 所有参数。

    param_space 格式:
        {
            "param_name": {
                "type": "float" | "int" | "categorical",
                "low": ...,
                "high": ...,
                "choices": [...],
                "log": True | False,
                "step": ...,
            },
            ...
        }

    返回 dict: param_name -> suggested value
    """
    suggested = {}
    for name, spec in param_space.items():
        ptype = spec["type"]
        if ptype == "float":
            suggested[name] = trial.suggest_float(
                name,
                spec["low"],
                spec["high"],
                log=spec.get("log", False),
                step=spec.get("step"),
            )
        elif ptype == "int":
            suggested[name] = trial.suggest_int(
                name,
                spec["low"],
                spec["high"],
                step=spec.get("step", 1),
                log=spec.get("log", False),
            )
        elif ptype == "categorical":
            suggested[name] = trial.suggest_categorical(
                name,
                spec["choices"],
            )
        else:
            raise ValueError(f"Unknown param type: {ptype}")
    return suggested


def create_study(direction="maximize", sampler=None, pruner=None):
    """创建 Optuna study。

    Args:
        direction: "maximize" 或 "minimize"
        sampler: 自定义采样器（默认使用 TPESampler 贝叶斯优化）
        pruner: 自定义剪枝器（默认 MedianPruner）
    """
    if sampler is None:
        sampler = optuna.samplers.TPESampler(seed=42)
    if pruner is None:
        pruner = optuna.pruners.MedianPruner()
    return optuna.create_study(
        direction=direction,
        sampler=sampler,
        pruner=pruner,
    )


def optimize_backtest_params(objective_fn, n_trials=100, n_jobs=1,
                              direction="maximize", study=None):
    """
    贝叶斯优化回测参数。

    Args:
        objective_fn: trial -> float 返回回测得分
        n_trials: 评估次数 (默认100，建议300+)
        n_jobs: 并行数 (-1 = 全部核心)
        direction: "maximize" 或 "minimize"
        study: 已有的 study，为 None 时自动创建

    Returns:
        (best_params, best_value) 元组
    """
    if study is None:
        study = create_study(direction=direction)
    study.optimize(objective_fn, n_trials=n_trials, n_jobs=n_jobs)
    return study.best_params, study.best_value
