"""``flask ai train-appeals`` (and, from P3-F16, ``flask ai evaluate``)."""

import json
from pathlib import Path

import click
import joblib
from flask import current_app
from flask.cli import with_appcontext

from app.ai import appeals
from app.models.hospital import normalize_facility_name
from app.synthetic_data import load_register


@click.group("ai")
def ai_group() -> None:
    """The AI engine's commands."""


def split_metrics(model: object, rows: list[dict], names: list[str]) -> dict[str, float | int]:
    """Precision and recall of "likely a scam" (score of 50 or more) over labelled messages."""
    tp = fp = fn = tn = 0
    for row in rows:
        probability = float(model.predict_proba([row["text"]])[0][1])  # type: ignore[attr-defined]
        score = appeals.score_from(appeals.rule_features(names, row["text"]), probability)
        flagged, scam = score >= appeals.FLAG_FROM, row["label"] == "scam"
        tp += flagged and scam
        fp += flagged and not scam
        fn += (not flagged) and scam
        tn += (not flagged) and not scam
    return {
        "n": len(rows),
        "precision": round(tp / max(1, tp + fp), 3),
        "recall": round(tp / max(1, tp + fn), 3),
        "tp": tp,
        "fp": fp,
        "fn": fn,
        "tn": tn,
    }


@ai_group.command("train-appeals")
@click.option(
    "--out", type=click.Path(path_type=Path), default=appeals.MODEL_PATH, show_default=True
)
@click.option(
    "--on", "train_on", type=click.Choice(["dev", "all"]), default="dev", show_default=True
)
@with_appcontext
def train_appeals_command(out: Path, train_on: str) -> None:
    """Train the appeal classifier on data/synthetic/appeals.jsonl and save it.

    By default only the ``dev`` split is used, so the ``test`` split stays unseen and its score
    is an honest estimate; the score is printed and saved with the model.
    """
    data_dir = Path(current_app.config["SYNTHETIC_DATA_DIR"])
    with (data_dir / "appeals.jsonl").open(encoding="utf-8") as handle:
        rows = [json.loads(line) for line in handle if line.strip()]
    training = rows if train_on == "all" else [r for r in rows if r["split"] == "dev"]
    held_out = [r for r in rows if r["split"] == "test"]
    artifact = appeals.train(training)
    names = [normalize_facility_name(r.name) for r in load_register(data_dir)]
    artifact["trained_on"] = train_on
    artifact["test_metrics"] = split_metrics(artifact["model"], held_out, names)
    out.parent.mkdir(parents=True, exist_ok=True)
    joblib.dump(artifact, out, compress=3)
    appeals.forget_model()
    click.echo(f"Trained on {len(training)} messages ({train_on}) -> {out}")
    click.echo(f"Held-out test split: {artifact['test_metrics']}")
