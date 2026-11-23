"""Reproduce both public runs with one command from the repository root."""
import argparse
from pathlib import Path
import subprocess
import sys


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output-dir', type=Path, default=root / 'evals')
    args = parser.parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    for mode in ('bm25', 'rerank'):
        subprocess.run([
            sys.executable, str(root / 'evals/run.py'), '--mode', mode,
            '--output', str(args.output_dir.resolve() / f'public-{mode}.json'),
        ], cwd=root, check=True)


if __name__ == '__main__':
    main()
