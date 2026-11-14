
"""CLI entry for a Qgentic-AI run.

Loads GOAL.md, downloads Kaggle competition data if needed,
and hands control to MainAgent.run().

Experiment tracking through W&B and Weave is optional.
"""

import argparse
import os
import shutil
import time
from pathlib import Path
from typing import Optional, Tuple

# Optional Weights & Biases integration
try:
    import wandb
except (ImportError, OSError) as exc:
    wandb = None
    print(f"[WARNING] W&B unavailable: {exc}")

# Optional Weave integration
try:
    import weave
except (ImportError, OSError) as exc:
    weave = None
    print(f"[WARNING] Weave unavailable: {exc}")

from agents.main_agent import MainAgent
from project_config import get_config, get_config_value
from utils.competition_data import (
    download_competition_data,
    generate_description_md,
)


_TASK_ROOT = Path(get_config()["paths"]["task_root"])
_REPO_ROOT = Path(__file__).resolve().parent

_TASK_METADATA_FILES = (
    "GOAL.md",
    "RESEARCHER_INSTRUCTIONS.md",
)


def _sync_task_metadata(base_dir: Path) -> None:
    """Copy required task metadata from repository root."""

    missing = [
        name
        for name in _TASK_METADATA_FILES
        if not (_REPO_ROOT / name).exists()
    ]

    if missing:
        cp_lines = []

        for name in missing:
            template = name.removesuffix(".md") + ".example.md"
            cp_lines.append(
                f"  Copy-Item {_REPO_ROOT / template} "
                f"{_REPO_ROOT / name}"
            )

        raise FileNotFoundError(
            f"Required files missing at repo root: "
            f"{', '.join(missing)}.\n"
            f"Copy the templates and fill them in:\n"
            + "\n".join(cp_lines)
        )

    for name in _TASK_METADATA_FILES:
        shutil.copyfile(
            _REPO_ROOT / name,
            base_dir / name,
        )


def _resolve_wandb_target(
    cli_entity: Optional[str],
    cli_project: Optional[str],
) -> Tuple[Optional[str], Optional[str]]:
    """Determine W&B entity and project."""

    entity = next(
        (
            value
            for value in (
                cli_entity,
                os.environ.get("WANDB_ENTITY"),
                get_config_value(
                    "tracking",
                    "wandb",
                    "entity",
                    default=None,
                ),
            )
            if value
        ),
        None,
    )

    project = next(
        (
            value
            for value in (
                cli_project,
                os.environ.get("WANDB_PROJECT"),
                get_config_value(
                    "tracking",
                    "wandb",
                    "project",
                    default=None,
                ),
            )
            if value
        ),
        None,
    )

    return entity, project


def _init_tracking(args: argparse.Namespace) -> None:
    """Initialize optional W&B and Weave tracking."""

    if wandb is None:
        print("[INFO] Experiment tracking disabled.")
        return

    entity, project = _resolve_wandb_target(
        args.wandb_entity,
        args.wandb_project,
    )

    run_name = (
        getattr(args, "wandb_run_name", None)
        or f"{args.run_id}-{args.slug}"
    )

    if not project:
        wandb.init(
            mode="disabled",
            name=run_name,
        )
        return

    wandb_kwargs = {
        "project": project,
        "name": run_name,
    }

    if entity:
        wandb_kwargs["entity"] = entity

    wandb.init(**wandb_kwargs)

    if weave is not None:
        weave_project = (
            f"{entity}/{project}" if entity else project
        )
        weave.init(project_name=weave_project)


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Launch a Qgentic-AI Main Agent session "
            "against a Kaggle competition."
        ),
    )

    parser.add_argument(
        "--slug",
        type=str,
        required=True,
        help=(
            "Kaggle competition slug; data will be "
            "downloaded into task/<slug>/."
        ),
    )

    parser.add_argument(
        "--run-id",
        type=str,
        default=None,
        help="Run identifier (default: current timestamp).",
    )

    parser.add_argument(
        "--goal-file",
        type=Path,
        default=None,
        help="Path to GOAL.md.",
    )

    parser.add_argument(
        "--wandb-entity",
        type=str,
        help="Weights & Biases entity name.",
    )

    parser.add_argument(
        "--wandb-project",
        type=str,
        help="Weights & Biases project name.",
    )

    parser.add_argument(
        "--wandb-run-name",
        type=str,
        help="Optional W&B run name.",
    )

    args = parser.parse_args()

    if args.run_id is None:
        args.run_id = time.strftime("%Y%m%d_%H%M%S")

    base_dir = _TASK_ROOT / args.slug
    base_dir.mkdir(
        parents=True,
        exist_ok=True,
    )

    _sync_task_metadata(base_dir)

    goal_file = (
        args.goal_file
        or base_dir / "GOAL.md"
    )

    if not goal_file.exists():
        raise FileNotFoundError(
            f"Goal file not found: {goal_file}. "
            "Create one, or pass --goal-file."
        )

    goal_text = goal_file.read_text(
        encoding="utf-8"
    )

    os.environ["TASK_SLUG"] = args.slug

    _init_tracking(args)

    try:
        download_competition_data(
            args.slug,
            base_dir,
        )

        generate_description_md(
            args.slug,
            base_dir,
        )

        MainAgent(
            slug=args.slug,
            run_id=args.run_id,
            goal_text=goal_text,
        ).run()

    finally:
        if weave is not None:
            try:
                weave.finish()
            except Exception:
                pass

        if wandb is not None:
            try:
                wandb.finish()
            except Exception:
                pass


if __name__ == "__main__":
    main()
