"""Open existing diff pairs in an isolated VS Code workspace."""

import json
from pathlib import Path
import shutil
import subprocess
import tempfile


def _read_review(review_file):
    review = json.loads(review_file.read_text(encoding='utf-8-sig'))
    if not isinstance(review, dict):
        raise ValueError('Review must be an object containing a diffs array.')
    diffs = review.get('diffs')
    if not isinstance(diffs, list) or not 1 <= len(diffs) <= 8:
        raise ValueError('Review must contain between 1 and 8 diff pairs.')
    title = review.get('title', 'Review')
    if not isinstance(title, str) or not title.strip():
        raise ValueError('Review title must be a nonempty string.')
    pairs = []
    for pair in diffs:
        if (not isinstance(pair, list) or len(pair) != 2
                or any(not isinstance(value, str) or not value for value in pair)):
            raise ValueError('Each diff must be a [before, working_file] pair.')
        paths = tuple((review_file.parent / value).resolve(strict=True) for value in pair)
        for path in paths:
            if not path.is_file():
                raise ValueError(f'Diff path is not a regular file: {path}')
        pairs.append(paths)
    if {before for before, _ in pairs} & {after for _, after in pairs}:
        raise ValueError('A working file cannot also be a read-only before snapshot.')
    return title, pairs


def open_review(review_file):
    """Open ordered diff pairs without changing inputs; return the workspace path."""
    review_file = Path(review_file).resolve(strict=True)
    title, pairs = _read_review(review_file)
    code = shutil.which('code')
    if code is None:
        raise FileNotFoundError('VS Code was not found on PATH.')

    with tempfile.NamedTemporaryFile(
            mode='w', encoding='utf-8', newline='\n', prefix='review-',
            suffix='.code-workspace', dir=review_file.parent, delete=False) as stream:
        json.dump({
            'folders': [],
            'settings': {
                'workbench.startupEditor': 'none',
                'workbench.editor.enablePreview': False,
                'diffEditor.ignoreTrimWhitespace': False,
                'window.title': f'${{dirty}}{title} - Diff Review',
                'files.readonlyInclude': {before.as_posix(): True for before, _ in pairs},
            },
        }, stream, indent=2)
        stream.write('\n')
        workspace = Path(stream.name)

    for index, (before, after) in enumerate(pairs):
        command = [code]
        if index == 0:
            command.append('--new-window')
        command.extend([str(workspace), '--diff', str(before), str(after)])
        subprocess.run(
            command, check=True, capture_output=True, text=True,
            encoding='utf-8', errors='replace', timeout=30)
    return workspace
