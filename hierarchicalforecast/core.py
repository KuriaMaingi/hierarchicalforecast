# AUTOGENERATED! DO NOT EDIT! File to edit: ../nbs/core.ipynb.

# %% auto 0
__all__ = ['HierarchicalReconciliation']

# %% ../nbs/core.ipynb 2
import re
from inspect import signature, Parameter
from scipy.stats import norm
from typing import Callable, Dict, List, Optional

import numpy as np
import pandas as pd

from .methods import _bootstrap_samples

# %% ../nbs/core.ipynb 4
def _build_fn_name(fn) -> str:
    fn_name = type(fn).__name__
    func_params = fn.__dict__
    args_to_remove = ['insample']
    if not func_params.get('nonnegative', False):
        args_to_remove += ['nonnegative']
    func_params = [f'{name}-{value}' for name, value in func_params.items() if name not in args_to_remove]
    if func_params:
        fn_name += '_' + '_'.join(func_params)
    return fn_name

# %% ../nbs/core.ipynb 8
class HierarchicalReconciliation:
    """Hierarchical Reconciliation Class.

    The `core.HierarchicalReconciliation` class allows you to efficiently fit multiple 
    HierarchicaForecast methods for a collection of time series and base predictions stored in 
    pandas DataFrames. The `Y_df` dataframe identifies series and datestamps with the unique_id and ds columns while the
    y column denotes the target time series variable. The `Y_h` dataframe stores the base predictions, 
    example ([AutoARIMA](https://nixtla.github.io/statsforecast/models.html#autoarima), [ETS](https://nixtla.github.io/statsforecast/models.html#autoets), etc.).

    **Parameters:**<br>
    `reconcilers`: A list of instantiated classes of the [reconciliation methods](https://nixtla.github.io/hierarchicalforecast/methods.html) module .<br>

    **References:**<br>
    [Rob J. Hyndman and George Athanasopoulos (2018). \"Forecasting principles and practice, Hierarchical and Grouped Series\".](https://otexts.com/fpp3/hierarchical.html)
    """
    def __init__(self,
                 reconcilers: List[Callable]):
        self.reconcilers = reconcilers
        self.insample = any([method.insample for method in reconcilers])

    def reconcile(self, 
                  Y_hat_df: pd.DataFrame,
                  S: pd.DataFrame,
                  tags: Dict[str, np.ndarray],
                  Y_df: Optional[pd.DataFrame] = None,
                  level: Optional[List[int]] = None,
                  intervals_method: str = 'normality'):
        """Hierarchical Reconciliation Method.

        The `reconcile` method is analogous to SKLearn `fit` method, it applies different 
        reconciliation methods instantiated in the `reconcilers` list. 
        
        Most reconciliation methods can be described by the following convenient 
        linear algebra notation:

        $$\\tilde{\mathbf{y}}_{[a,b],\\tau} = \mathbf{S}_{[a,b][b]} \mathbf{P}_{[b][a,b]} \hat{\mathbf{y}}_{[a,b],\\tau}$$
        
        where $a, b$ represent the aggregate and bottom levels, $\mathbf{S}_{[a,b][b]}$ contains
        the hierarchical aggregation constraints, and $\mathbf{P}_{[b][a,b]}$ varies across 
        reconciliation methods. The reconciled predictions are $\\tilde{\mathbf{y}}_{[a,b],\\tau}$, and the 
        base predictions $\hat{\mathbf{y}}_{[a,b],\\tau}$.

        **Parameters:**<br>
        `Y_hat_df`: pd.DataFrame, base forecasts with columns `ds` and models to reconcile indexed by `unique_id`.<br>
        `Y_df`: pd.DataFrame, training set of base time series with columns `['ds', 'y']` indexed by `unique_id`.
        If a class of `self.reconciles` receives `y_hat_insample`, `Y_df` must include them as columns.<br>
        `S`: pd.DataFrame with summing matrix of size `(base, bottom)`, see [aggregate method](https://nixtla.github.io/hierarchicalforecast/utils.html#aggregate).<br>
        `tags`: Each key is a level and its value contains tags associated to that level.<br>
        `level`: float list 0-100, confidence levels for prediction intervals.<br>
        `intervals_method`: str, method used to calculate prediction intervals, one of `normality`, `bootstrap`, `permbu`.<br>
         
        **Returns:**<br>
        `y_tilde`: pd.DataFrame, with reconciled predictions.        
        """
        if intervals_method not in ['normality', 'bootstrap']:
            raise ValueError(f'Unkwon interval method: {intervals_method}')
        drop_cols = ['ds', 'y'] if 'y' in Y_hat_df.columns else ['ds']
        model_names = Y_hat_df.drop(columns=drop_cols, axis=1).columns.to_list()
        # store pi names
        pi_model_names = [name for name in model_names if ('-lo' in name or '-hi' in name)]
        #remove prediction intervals
        model_names = [name for name in model_names if name not in pi_model_names]
        uids = Y_hat_df.index.unique()
        # same order of Y_hat_df to prevent errors
        S_ = S.loc[uids]
        common_vals = dict(
            S=S_.values.astype(np.float32),
            idx_bottom=S_.index.get_indexer(S.columns),
            tags={key: S_.index.get_indexer(val) for key, val in tags.items()}
        )
        # we need insample values if 
        # we are using a method that requires them
        # or if we are performing boostrap
        if self.insample or (intervals_method in ['bootstrap']):
            if Y_df is None:
                raise Exception('you need to pass `Y_df`')
            common_vals['y_insample'] = Y_df.pivot(columns='ds', values='y').loc[uids].values.astype(np.float32)
        fcsts = Y_hat_df.copy()
        for reconcile_fn in self.reconcilers:
            reconcile_fn_name = _build_fn_name(reconcile_fn)
            has_fitted = 'y_hat_insample' in signature(reconcile_fn).parameters
            has_level = 'level' in signature(reconcile_fn).parameters
            for model_name in model_names:
                # should we calculate prediction intervals?
                pi_model_name = [pi_name for pi_name in pi_model_names if model_name in pi_name]
                pi = len(pi_model_name) > 0
                # Remember: pivot sorts uid
                y_hat_model = Y_hat_df.pivot(columns='ds', values=model_name).loc[uids].values
                if pi and has_level and level is not None and intervals_method == 'normality':
                    # we need to construct sigmah and add it
                    # to the common_vals
                    # to recover sigmah we only need 
                    # one prediction intervals
                    pi_col = pi_model_name[0]
                    sign = -1 if 'lo' in pi_col else 1
                    level_col = re.findall('[\d]+[.,\d]+|[\d]*[.][\d]+|[\d]+', pi_col)
                    level_col = float(level_col[0])
                    z = norm.ppf(0.5 + level_col / 200)
                    sigmah = Y_hat_df.pivot(columns='ds', values=pi_col).loc[uids].values
                    sigmah = sign * (y_hat_model - sigmah) / z
                    common_vals['sigmah'] = sigmah
                    common_vals['level'] = level
                    common_vals['intervals_method'] = intervals_method
                if (self.insample and has_fitted) or (intervals_method in ['bootstrap']):
                    if model_name in Y_df:
                        y_hat_insample = Y_df.pivot(columns='ds', values=model_name).loc[uids].values
                        y_hat_insample = y_hat_insample.astype(np.float32)
                        if has_fitted:
                            common_vals['y_hat_insample'] = y_hat_insample 
                        if intervals_method == 'bootstrap' and has_level:
                            common_vals['samples'] = _bootstrap_samples(
                                y_insample=common_vals['y_insample'],
                                y_hat_insample=y_hat_insample, 
                                y_hat=y_hat_model, 
                                n_samples=1_000
                            )
                            common_vals['level'] = level
                            common_vals['intervals_method'] = intervals_method
                    else:
                        # some methods have the residuals argument
                        # but they don't need them
                        # ej MinTrace(method='ols')
                        common_vals['y_hat_insample'] = None
                kwargs = [key for key in signature(reconcile_fn).parameters if key in common_vals.keys()]
                kwargs = {key: common_vals[key] for key in kwargs}
                fcsts_model = reconcile_fn(y_hat=y_hat_model, **kwargs)
                fcsts[f'{model_name}/{reconcile_fn_name}'] = fcsts_model['mean'].flatten()
                if (pi and has_level and level is not None) or (intervals_method in ['bootstrap'] and level is not None):
                    for lv in level:
                        fcsts[f'{model_name}/{reconcile_fn_name}-lo-{lv}'] = fcsts_model[f'lo-{lv}'].flatten()
                        fcsts[f'{model_name}/{reconcile_fn_name}-hi-{lv}'] = fcsts_model[f'hi-{lv}'].flatten()
                    del common_vals['level']
                    del common_vals['intervals_method']
                    if intervals_method == 'normality':
                        del common_vals['sigmah']
                    else:
                        del common_vals['samples']
                if self.insample and has_fitted:
                    del common_vals['y_hat_insample']
        return fcsts
