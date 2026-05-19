"""Pipeline entry: input.json -> result.json -> Markdown + HTML files.

Usage:
    python -m scripts.run_assessment --input <input.json> --out-dir <dir>

Stdout: the rendered Markdown report (so the conversation layer can paste it back).
Stderr: file paths of the generated artifacts.
"""
import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

from scripts.render_html import render as render_html_text
from scripts.render_markdown import render as render_md
from scripts.score_engine import compute_all


def run(input_path: Path, out_dir: Path, markdown_only: bool = False) -> dict:
    """Run the full pipeline.

    Args:
        input_path: Path to input.json (answers + selected majors).
        out_dir: Directory to write report files into.
        markdown_only: If True, skip HTML generation.

    Returns:
        Dict with keys 'result', 'markdown_path', 'html_path' (None if skipped).
    """
    with open(input_path, encoding="utf-8") as f:
        input_data = json.load(f)
    result = compute_all(input_data)
    out_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    md_path = out_dir / f"gaokao-adi-report-{timestamp}.md"
    md_path.write_text(render_md(result), encoding="utf-8")
    html_path = None
    if not markdown_only:
        html_path = out_dir / f"gaokao-adi-report-{timestamp}.html"
        html_path.write_text(render_html_text(result), encoding="utf-8")
    return {"result": result, "markdown_path": md_path, "html_path": html_path}


def main() -> None:
    """CLI entry."""
    parser = argparse.ArgumentParser(description="Major-ADI pipeline: score + render")
    parser.add_argument("--input", required=True, help="Path to input.json")
    parser.add_argument("--out-dir", default=".", help="Output directory (default: cwd)")
    parser.add_argument("--markdown-only", action="store_true", help="Skip HTML")
    args = parser.parse_args()

    try:
        out = run(Path(args.input), Path(args.out_dir), args.markdown_only)
    except (FileNotFoundError, json.JSONDecodeError, KeyError, ValueError) as exc:
        print(f"[gaokao-adi] ERROR: {type(exc).__name__}: {exc}", file=sys.stderr)
        sys.exit(2)

    print(out["markdown_path"].read_text(encoding="utf-8"))
    print(f"\n[gaokao-adi] Markdown: {out['markdown_path']}", file=sys.stderr)
    if out["html_path"]:
        print(f"[gaokao-adi] HTML: {out['html_path']}", file=sys.stderr)


if __name__ == "__main__":
    main()
