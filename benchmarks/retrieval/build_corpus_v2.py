#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.10"
# dependencies = []
# ///
# Run: uv run --script benchmarks/retrieval/build_corpus_v2.py
from __future__ import annotations

import json
from collections import Counter
from hashlib import sha256
from pathlib import Path

from v2_fixtures import ERRORS, PATHS, RULES, VERSIONS


def main() -> None:
    root = Path(__file__).resolve().parent
    legacy = json.loads((root / "corpus.json").read_text())
    cases = [{**row, "split": "dev", "origin": "p0-regression"} for row in legacy["cases"]]
    next(row for row in cases if row["id"] == "err-05")["query"] = "Atlas 的 EACCES 是什么问题，应检查什么？"
    for index, (project, target, adjacent, purpose) in enumerate(PATHS):
        cases.append({
            "id": f"v2-path-{index:02d}", "category": "path",
            "split": "dev" if index < 8 else "test", "origin": "v2-authored",
            "query": f"{project} 的路径 {target} 具体用来做什么？",
            "documents": [
                f"{project} 的 {target} 用于{purpose}。",
                f"{project} 的 {adjacent} 用于迁移演练，不能当作正式的{purpose}。",
                f"{project} 的归档目录只保存历史构建，无法控制{purpose}。",
                f"另一套演示服务沿用同名配置，但{project}不读取那份配置。",
            ],
        })
    for index, (project, target, adjacent, change) in enumerate(VERSIONS):
        query = (
            f"{project} 版本 {target} 引入了什么变更？" if index % 2 == 0 or index in (3, 11)
            else f"{project} 的正式服务当前运行哪个版本，有什么改进？"
        )
        cases.append({
            "id": f"v2-ver-{index:02d}", "category": "version",
            "split": "dev" if index < 8 else "test", "origin": "v2-authored", "query": query,
            "documents": [
                f"{project} 正式服务当前固定版本 {target}，该版{change}。",
                f"{project} 历史版本 {adjacent} 仍保留作回滚，那时尚未{change}。",
                f"{project} 的演示镜像显示99.0.0，这不是正式部署。",
                f"{project} 下次升级尚未获批，开发分支的版本不能代表运行态。",
            ],
        })
    for index, (project, topic, answer, distractor) in enumerate(RULES):
        cases.append({
            "id": f"v2-zh-{index:02d}", "category": "chinese",
            "split": "dev" if index < 8 else "test", "origin": "v2-authored",
            "query": f"{project} 项目对{topic}有什么已经确认的要求？",
            "documents": [
                f"{project} 项目已确认的{topic}规则是：{answer}。",
                f"{project} 的旧提案建议{distractor}，用户已拒绝，不能照做。",
                f"{project} 的{topic}讨论附件已经归档，附件名称不表示最终决策。",
                f"{project} 的演示教程采用{distractor}，教程不适用于正式项目。",
            ],
        })
    for index, (project, code, answer, adjacent) in enumerate(ERRORS):
        cases.append({
            "id": f"v2-err-{index:02d}", "category": "error_code",
            "split": "dev" if index < 8 else "test", "origin": "v2-authored",
            "query": f"{project} 遇到 {code} 时，已确认的含义是什么？",
            "documents": [
                f"{project} 排障夹具确认 {code} 的含义是{answer}。",
                f"{project} 另一次故障记录的是 {adjacent}，与本次错误不同。",
                f"{project} 的测试控制台能展示错误详情，但页面截图不包含本次诊断。",
                f"{project} 旧故障在重启后暂时消失，当时没有确认原因。",
            ],
        })
    negative_texts = [
        *legacy["negative_queries"],
        *[f"{name} 的路径 {path} 具体用来做什么？" for name, path in (
            ("Nebula", "/srv/nebula/config.json"), ("Cedar", "/etc/cedar/proxy.conf.old"),
            ("Harbor", "/srv/harbor/red/start.sh"), ("Lumen", ".github/workflows/release.json"),
            ("Juniper", "/data/juniper/index.sqlite"), ("Nova", "/opt/nova/releases/staging"),
            ("Eon", "/etc/eon/ssl/certificate.pem"), ("Lotus", "/srv/lotus/cache-v3/index"),
        )],
        *[f"{name} 版本 {version} 引入了什么变更？" for name, version in (
            ("Amber", "1.2.4"), ("Dawn", "3.0.0-rc.3"), ("Fjord", "0.16.5"),
            ("Haze", "2.2.1-steinx.6"), ("Mango", "6.1.5"), ("Ruby", "5.2.1"),
            ("Topaz", "3.4.2"), ("Yarrow", "11.3.3"),
        )],
        "Nebula 机房的门禁PIN是多少？", "Orion 用户的护照号码是什么？",
        "Cedar 数据库管理员的生日是哪天？", "Quartz 的VPN私钥原文是什么？",
        "Maple 用户的药物过敏史是什么？", "Helios 服务器在哪个物理机柜？",
        "Willow 项目的年度营业收入是多少？", "Lumen 公司有多少员工？",
        "Harbor 用户的银行卡号是什么？", "Beacon 办公室的WiFi密码是什么？",
        "Cobalt 团队下一次聚餐定在哪家餐厅？", "Vega 用户的鞋码是多少？",
    ]
    negatives = [
        {"id": f"negative-{i:02d}", "query": text, "category": "negative",
         "split": "dev" if i % 2 == 0 else "test"}
        for i, text in enumerate(negative_texts)
    ]
    assert len(cases) == 128 and len(negatives) == 32
    assert len({row["id"] for row in cases}) == 128
    assert Counter(row["category"] for row in cases) == dict.fromkeys(
        ("path", "version", "chinese", "error_code"), 32
    )
    assert Counter(row["split"] for row in cases) == {"dev": 64, "test": 64}
    assert not ({row["query"] for row in cases} & set(negative_texts))
    corpus = {
        "description": "160 synthetic queries,512 facts; P0 regressions plus authored coding/ops fixtures. No production memories.",
        "label_rule": "Each positive has one labeled answer at documents[0]; negatives have no answer.",
        "split_rule": "P0 plus first8 new subjects per category are dev; remaining16 subjects per category are test. Negatives alternate.",
        "cases": cases, "negative_queries": negatives,
    }
    data = (json.dumps(corpus, ensure_ascii=False, indent=2) + "\n").encode()
    output = root / "corpus-v2.json"
    if output.exists() and output.read_bytes() != data:
        raise RuntimeError("Refusing to replace a frozen corpus with different bytes")
    output.write_bytes(data)
    print(json.dumps({"corpus": str(output), "sha256": sha256(data).hexdigest(),
                      "positive": len(cases), "negative": len(negatives), "documents": 512}))


if __name__ == "__main__":
    main()
