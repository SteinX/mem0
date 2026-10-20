import math

import pytest
from pydantic import ValidationError

from mem0.configs.retrieval import RetrievalPolicy


@pytest.mark.parametrize(("query", "text", "expected"), [
    ("读取 /srv/app/config.yaml", "/srv/app/config.yaml 配置", True),
    ("读取 /srv/app/config.yaml", "/srv/app/config.yaml.bak 配置", False),
    ("读取 deploy/.env", "/workspace/app/deploy/.env 配置", True),
    ("读取 deploy/.env", "/workspace/app/.env 配置", False),
    ("读取 /mnt/user/appdata", "/mnt/user/appdata/app 是挂载", True),
    ("读取 ./config/local.yaml", "config/local.yaml 配置", True),
    ("读取 deploy/config", "/workspace/deploy/config/app.yaml 配置", True),
    ("读取 deploy/config", "nested/deploy/config/app.yaml 配置", True),
    ("读取 deploy/config", "/workspace/redeploy/config/app.yaml 配置", False),
    ("读取 deploy/config", "/workspace/deploy/config-old/app.yaml 配置", False),
    ("读取 /deploy/config", "/workspace/deploy/config/app.yaml 配置", False),
    (r"读取 C:\Ops\App\config.json", r"c:\ops\app\CONFIG.json 配置", True),
    (r"读取 ops\app\config.json", r"C:\ops\app\config.json 配置", True),
    (r"读取 .\ops\app\config.json", r"C:\ops\app\config.json 配置", True),
    (r"读取 Ops\App\CONFIG.json", r"c:\ops\app\config.json 配置", True),
    ("读取 Ops/App/CONFIG.json", r"c:\ops\app\config.json 配置", True),
    ("读取 ops/app/config.json", "/srv/Ops/App/config.json 配置", False),
    (r"读取 ops\app\config.json", r"C:\ops\app\config.json.bak 配置", False),
    ("读取 /config.yaml", "/config.yaml 配置", True),
    ("读取 /config.yaml", "/config.yaml.bak 配置", False),
    ("读取 /data", "/data/index.sqlite 索引", True),
    ("读取 /data", "/data-old/index.sqlite 索引", False),
    ("读取 /data", "/backup/data/index.sqlite 索引", False),
    ("版本 v1.2.3", "版本1.2.30", False),
    ("版本 1.2.3", "版本v1.2.3。", True),
    ("版本 1.2.3", "版本1.2.3.", True),
    ("版本 3.0.0-rc.2", "版本3.0.0-rc.20", False),
    ("版本 1.8.0+build.7", "版本1.8.0+build.70", False),
    ("读取 /srv/app/config.yaml 版本1.2.3", "/srv/other/config.yaml 版本1.2.3", False),
    ("版本1.2.3还是1.2.4", "版本1.2.4", True),
    ("连接192.168.2.202", "当前连接正常", True),
    ("用户的语言偏好是什么", "任何原有候选", True),
])
def test_exact_path_and_version_boundaries(query, text, expected):
    assert RetrievalPolicy(exact_identifiers=True).matches(query, text) is expected


@pytest.mark.parametrize("settings", [
    {"exact_identifiers": "true"}, {"rerank_candidate_limit": 0},
    {"rerank_candidate_limit": True}, {"rerank_candidate_limit": "20"},
    {"rerank_min_score": math.nan}, {"rerank_min_score": math.inf},
    {"rerank_min_score": "0.5"}, {"unexpected_option": True},
])
def test_policy_rejects_invalid_configuration(settings):
    with pytest.raises(ValidationError):
        RetrievalPolicy(**settings)


@pytest.mark.parametrize("score", [None, math.nan, math.inf])
def test_configured_score_gate_rejects_unavailable_scores(score):
    row = {} if score is None else {"rerank_score": score}
    with pytest.raises(ValueError, match="finite rerank_score"):
        RetrievalPolicy(rerank_min_score=0.5).filter_scores([row])


def test_score_threshold_keeps_equal_scores_and_original_values():
    rows = [{"rerank_score": 0.49}, {"rerank_score": 0.5}, {"rerank_score": 0.9}]
    selected = RetrievalPolicy(rerank_min_score=0.5).filter_scores(rows)
    assert selected == rows[1:]
    assert len(rows) == 3


def test_disabled_controls_keep_the_original_pool():
    rows = [{"rerank_score": 0.01}]
    policy = RetrievalPolicy()
    assert policy.matches("/srv/config.yaml", "/srv/config.yml")
    assert policy.rerank_pool(rows) is rows
    assert policy.filter_scores(rows) is rows
