#!/usr/bin/env python3
"""CLI entry point for the DevOps Repository Auditing Agent.

Usage:
    python agent.py --repo <github_repository_url> --output report.json
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

from agent_loop import AgentLoopError, DevOpsAuditingAgent
from llm_client import LLMError, create_llm_client
from report import write_report
from repo_manager import cleanup_clone, clone_repository, normalize_github_url
from tools import ToolRegistry


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "AI-powered DevOps repository auditing agent. "
            "Clones a public GitHub repo, investigates with tools, "
            "and writes an evidence-based JSON report."
        )
    )
    parser.add_argument(
        "--repo",
        required=True,
        help="Public GitHub repository URL (https://github.com/<owner>/<repo>)",
    )
    parser.add_argument(
        "--output",
        required=True,
        help="Path to write the JSON report (e.g. report.json)",
    )
    parser.add_argument(
        "--max-steps",
        type=int,
        default=None,
        help="Maximum agent tool-loop steps (default: AGENT_MAX_STEPS or 30)",
    )
    parser.add_argument(
        "--keep-clone",
        action="store_true",
        help="Keep the temporary clone directory after the run (for debugging)",
    )
    parser.add_argument(
        "--trace",
        default=None,
        help="Optional path to write the tool-call trace JSON",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Print agent progress to stderr",
    )
    parser.add_argument(
        "--work-dir",
        default=None,
        help="Optional directory for clones (default: system temp)",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    load_dotenv()
    args = parse_args(argv)

    logging.basicConfig(
        level=logging.INFO if args.verbose else logging.WARNING,
        format="%(levelname)s: %(message)s",
    )

    cloned = None
    try:
        canonical, _, _ = normalize_github_url(args.repo)
        timeout = int(os.getenv("CLONE_TIMEOUT_SECONDS", "120"))
        tool_timeout = int(os.getenv("TOOL_TIMEOUT_SECONDS", "30"))
        max_steps = args.max_steps or int(os.getenv("AGENT_MAX_STEPS", "30"))

        if args.verbose:
            print(f"[agent] cloning {canonical} ...", file=sys.stderr)

        work_root = Path(args.work_dir) if args.work_dir else None
        cloned = clone_repository(
            canonical,
            work_root=work_root,
            timeout_seconds=timeout,
        )

        if args.verbose:
            print(f"[agent] clone ready at {cloned.path}", file=sys.stderr)

        # Avoid Windows cp1252 crashes if the model returns Unicode arrows/symbols
        if hasattr(sys.stdout, "reconfigure"):
            try:
                sys.stdout.reconfigure(encoding="utf-8", errors="replace")
                sys.stderr.reconfigure(encoding="utf-8", errors="replace")
            except Exception:
                pass

        llm = create_llm_client()
        tools = ToolRegistry(cloned.path, tool_timeout_seconds=tool_timeout)
        agent = DevOpsAuditingAgent(
            llm=llm,
            tools=tools,
            max_steps=max_steps,
            verbose=args.verbose,
        )
        result = agent.run(cloned.url)

        write_report(result.report, args.output)

        if args.trace:
            Path(args.trace).write_text(
                json.dumps(
                    {
                        "repository": cloned.url,
                        "steps_used": result.steps_used,
                        "trace": result.trace.steps,
                    },
                    indent=2,
                    ensure_ascii=False,
                )
                + "\n",
                encoding="utf-8",
            )

        if args.verbose:
            print(
                f"[agent] wrote {args.output} "
                f"({len(result.report['violations'])} violation(s), "
                f"{result.steps_used} step(s))",
                file=sys.stderr,
            )
        return 0

    except (ValueError, LLMError, AgentLoopError, RuntimeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("error: interrupted", file=sys.stderr)
        return 130
    except Exception as exc:  # noqa: BLE001
        print(f"error: unexpected failure: {exc}", file=sys.stderr)
        return 1
    finally:
        if cloned is not None and not args.keep_clone:
            cleanup_clone(cloned)


if __name__ == "__main__":
    raise SystemExit(main())
