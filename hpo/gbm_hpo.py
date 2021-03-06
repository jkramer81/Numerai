# %%
import datetime
import gc
import copy
import datatable as dt
import joblib
import lightgbm as lgb
import neptune
import neptunecontrib.monitoring.optuna as opt_utils
import numpy as np
import optuna
import xgboost as xgb
import pandas as pd
from sklearn.metrics import mean_squared_error
from data_loading import utils
from data_loading import purged_group_time_series as pgs


def optimize(trial: optuna.trial.Trial, data_dict: dict):
    p = {'learning_rate':    trial.suggest_uniform('learning_rate', 1e-4, 1e-1),
         'max_depth':        trial.suggest_int('max_depth', 5, 30),
         'max_leaves':       trial.suggest_int('max_leaves', 5, 50),
         'subsample':        trial.suggest_uniform('subsample', 0.3, 1.0),
         'colsample_bytree': trial.suggest_uniform('colsample_bytree', 0.3, 1.0),
         'min_child_weight': trial.suggest_int('min_child_weight', 5, 100),
         'lambda':           trial.suggest_uniform('lambda', 0.05, 0.2),
         'alpha':            trial.suggest_uniform('alpha', 0.05, 0.2),
         'objective':        'reg:squarederror',
         'booster':          'gbtree',
         'tree_method':      'hist',
         'verbosity':        1,
         'n_jobs':           4,
         'eval_metric':      'rmse'}
    print('Choosing parameters:', p)
    scores = []
    sizes = []
    df= []
    # gts = GroupTimeSeriesSplit()']

    gts = pgs.PurgedGroupTimeSeriesSplit(n_splits=5, group_gap=10)
    for i, (tr_idx, val_idx) in enumerate(gts.split(data_dict['data'], groups=data_dict['era'])):
        x_tr, x_val = data_dict['data'][tr_idx], data_dict['data'][val_idx]
        y_tr, y_val = data_dict['target'][tr_idx], data_dict['target'][val_idx]
        d_tr = xgb.DMatrix(x_tr, label=y_tr)
        d_val = xgb.DMatrix(x_val, label=y_val)
        clf = xgb.train(p, d_tr, 500, [
            (d_val, 'eval')], early_stopping_rounds=50, verbose_eval=True)
        #val_pred = clf.predict(d_val)
        d_dict = {'preds': clf.predict(d_val), 'target': y_val}
        df = pd.DataFrame.from_dict(d_dict)
        #df1 = pd.DataFrame(data=clf.predict(d_val), columns=['preds'])
        #df2 = pd.DataFrame(data=y_val, columns=['target'])
        #df =  pd.concat([df1, df2], axis=1, ignore_index=True)
        #score = mean_squared_error(y_val, val_pred)
        scores.append(utils.score(df))
        sizes.append(len(tr_idx) + len(val_idx))
        del clf, df, d_tr, d_val, x_tr, x_val, y_tr, y_val
        rubbish = gc.collect()
    print(scores)
    avg_score = utils.weighted_mean(scores, sizes)
    print('Avg Score:', avg_score)
    return avg_score


def loptimize(trial, data_dict: dict):
    p = {'learning_rate':    trial.suggest_uniform('learning_rate', 1e-4, 1e-1),
         'max_leaves':       trial.suggest_int('max_leaves', 5, 100),
         'bagging_fraction': trial.suggest_uniform('bagging_fraction', 0.3, 0.99),
         'bagging_freq':     trial.suggest_int('bagging_freq', 1, 10),
         'feature_fraction': trial.suggest_uniform('feature_fraction', 0.3, 0.99),
         'min_data_in_leaf': trial.suggest_int('min_data_in_leaf', 50, 1000),
         'lambda_l1':        trial.suggest_uniform('lambda_l1', 0.005, 0.05),
         'lambda_l2':        trial.suggest_uniform('lambda_l2', 0.005, 0.05),
         'boosting':         trial.suggest_categorical('boosting', ['gbdt', 'goss', 'rf']),
         'objective':        'regression',
         'verbose':          1,
         'n_jobs':           4,
         'metric':           'rmse'}
    if p['boosting'] == 'goss':
        p['bagging_freq'] = 0
        p['bagging_fraction'] = 1.0
    scores = []
    sizes = []
    df= []
    # gts = GroupTimeSeriesSplit()
    gts = pgs.PurgedGroupTimeSeriesSplit(n_splits=5, group_gap=10)
    for i, (tr_idx, val_idx) in enumerate(gts.split(data_dict['data'], groups=data_dict['era'])):
        sizes.append(len(tr_idx) + len(val_idx))
        x_tr, x_val = data_dict['data'][tr_idx], data_dict['data'][val_idx]
        y_tr, y_val = data_dict['target'][tr_idx], data_dict['target'][val_idx]
        train = lgb.Dataset(x_tr, label=y_tr)
        val = lgb.Dataset(x_val, label=y_val)
        clf = lgb.train(p, train, 500, valid_sets=[
            val], early_stopping_rounds=50, verbose_eval=True)
        d_dict = {'preds': clf.predict(x_val), 'target': y_val}
        df = pd.DataFrame.from_dict(d_dict)
        #score = mean_squared_error(y_val, preds)
        scores.append(utils.score(df))
        del clf, df, train, val, x_tr, x_val, y_tr, y_val
        rubbish = gc.collect()
    print(scores)
    avg_score = utils.weighted_mean(scores, sizes)
    print('Avg Score:', avg_score)
    return avg_score


def main():
    api_token = 'eyJhcGlfYWRkcmVzcyI6Imh0dHBzOi8vYXBwLm5lcHR1bmUuYWkiLCJhcGlfdXJsIjoiaHR0cHM6Ly9hcHAubmVwdHVuZS5haSIsImFwaV9rZXkiOiIzMGQ1MWZiNy1iYjNlLTQ3NDctOTE4OS1lNzhlNmVlYmUwMzYifQ=='
    neptune.init(api_token=api_token,
                 project_qualified_name='kramerji/Numerai')
    data = utils.load_data('data/', mode='train')
    data, target, features, era = utils.preprocess_data(data, nn=True)
    data_dict = {'data':     data, 'target': target,
                 'features': features, 'era': era}
    print('creating XGBoost Trials')
    xgb_exp = neptune.create_experiment('XGBoost_HPO')
    xgb_neptune_callback = opt_utils.NeptuneCallback(experiment=xgb_exp)
    study = optuna.create_study(direction='maximize')
    study.optimize(lambda trial: optimize(trial, data_dict),
                   n_trials=100, callbacks=[xgb_neptune_callback])
    joblib.dump(
        study, f'hpo/params/xgb_hpo_{str(datetime.datetime.now().date())}.pkl')
    print('Creating LightGBM Trials')
    lgb_exp = neptune.create_experiment('LGBM_HPO')
    lgbm_neptune_callback = opt_utils.NeptuneCallback(experiment=lgb_exp)
    study = optuna.create_study(direction='maximize')
    study.optimize(lambda trial: loptimize(trial, data_dict),
                   n_trials=100, callbacks=[lgbm_neptune_callback])
    joblib.dump(
        study, f'hpo/params/lgb_hpo_{str(datetime.datetime.now().date())}.pkl')


if __name__ == '__main__':
    main()
