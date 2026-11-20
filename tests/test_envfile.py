"""Dotenv files for the REPL."""

from vis_lang_javascript import envfile


def test_a_dotenv_file_is_read_as_written(tmp_path):
    path = tmp_path / ".env"
    path.write_text(
        "# comment\nexport A=1\nB='x # y'\nC=\"l1\\nl2\"\nD=bare # note\nE=\n"
        "NODE_OPTIONS=--require ./elsewhere.cjs\nnot a line\n"
    )
    assert envfile.read(path) == (
        {"A": "1", "B": "x # y", "C": "l1\nl2", "D": "bare", "E": ""},
        ["NODE_OPTIONS"],
    )
