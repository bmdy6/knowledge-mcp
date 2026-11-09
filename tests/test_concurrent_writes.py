"""Exercise independent MCP-style processes against one synthetic vault."""

import multiprocessing
from functools import partial
from pathlib import Path
from unittest.mock import patch

import pytest

from knowledge_mcp.locking import vault_write_lock
from knowledge_mcp.vault import Vault, VaultBusyError


ORIGINAL = "# Note\nalpha\nbeta\n"


def _writer(root, operation, entered, release, finished, results):
    import os

    replace = os.replace

    def paused_replace(source, destination):
        entered.set()
        if not release.wait(15):
            raise TimeoutError("test writer was not released")
        return replace(source, destination)

    try:
        vault = Vault(Path(root))
        if operation == "first":
            # Pause at the actual commit, after version validation. A competing
            # process must not commit based on an earlier view of this file.
            with patch("knowledge_mcp.vault.os.replace", paused_replace):
                vault.apply_change("personal", "note.md", "alpha", "ALPHA")
        else:
            entered.set()
            if operation == "stale":
                vault.apply_change("personal", "note.md", ORIGINAL, ORIGINAL + "stale\n")
            elif operation == "independent":
                vault.apply_change("personal", "note.md", "beta", "BETA")
            elif operation == "move":
                vault.move_document("personal", "note.md", "moved.md")
            elif operation == "delete":
                vault.delete_document("personal", "note.md")
            elif operation == "create":
                vault.create_document(
                    "personal", "merged.md", "Merged", "ALPHA\nbeta\nMerged note.\n",
                    replaces=("note.md",),
                )
        results.put((operation, "ok"))
    except Exception as exc:
        results.put((operation, type(exc).__name__))
    finally:
        finished.set()


@pytest.mark.parametrize("operation", ["stale", "independent", "move", "delete", "create"])
def test_mutations_wait_for_other_process_commit(tmp_path: Path, operation: str):
    note = tmp_path / "personal/note.md"
    note.parent.mkdir()
    note.write_text(ORIGINAL, encoding="utf-8")
    context = multiprocessing.get_context("spawn")
    results = context.Queue()
    paused, started, release = (context.Event() for _ in range(3))
    first_done, second_done = (context.Event() for _ in range(2))
    first = context.Process(target=_writer, args=(str(tmp_path), "first", paused, release, first_done, results))
    second = context.Process(target=_writer, args=(str(tmp_path), operation, started, release, second_done, results))
    processes = [first, second]
    try:
        first.start()
        assert paused.wait(10), "first process never reached its commit"
        second.start()
        assert started.wait(10), "second process never attempted a mutation"
        completed_before_release = second_done.wait(1)
        release.set()
        for process in processes:
            process.join(10)
            assert process.exitcode == 0
        outcomes = dict(results.get(timeout=5) for _ in processes)
        assert not completed_before_release, outcomes
        assert outcomes["first"] == "ok"
        if operation == "stale":
            assert outcomes[operation] == "VersionConflictError"
            assert note.read_text(encoding="utf-8") == ORIGINAL.replace("alpha", "ALPHA")
        elif operation == "independent":
            assert outcomes[operation] == "ok"
            assert note.read_text(encoding="utf-8") == ORIGINAL.replace("alpha", "ALPHA").replace("beta", "BETA")
        elif operation == "move":
            assert outcomes[operation] == "ok"
            assert not note.exists()
            assert (note.parent / "moved.md").read_text(encoding="utf-8") == ORIGINAL.replace("alpha", "ALPHA")
        else:
            assert outcomes[operation] == "ok"
            assert not note.exists()
            if operation == "create":
                assert "ALPHA" in (note.parent / "merged.md").read_text(encoding="utf-8")
    finally:
        release.set()
        for process in processes:
            if process.pid is not None:
                if process.is_alive():
                    process.terminate()
                process.join(5)
        results.close()
        results.join_thread()


def test_failed_replace_preserves_original_and_allows_next_write(tmp_path: Path):
    note = tmp_path / "personal/note.md"
    note.parent.mkdir()
    note.write_text(ORIGINAL, encoding="utf-8")
    with patch("knowledge_mcp.vault.os.replace", side_effect=OSError("synthetic failure")):
        with pytest.raises(OSError, match="synthetic failure"):
            Vault(tmp_path).apply_change("personal", "note.md", "alpha", "ALPHA")
    assert note.read_text(encoding="utf-8") == ORIGINAL
    assert list(note.parent.glob("*.tmp")) == []
    Vault(tmp_path).apply_change("personal", "note.md", "beta", "BETA")
    assert note.read_text(encoding="utf-8") == ORIGINAL.replace("beta", "BETA")


@pytest.mark.parametrize("terminate", [False, True])
def test_wait_timeout_and_process_exit_release_the_lock(tmp_path: Path, terminate: bool):
    note = tmp_path / "personal/note.md"
    note.parent.mkdir()
    note.write_text(ORIGINAL, encoding="utf-8")
    context = multiprocessing.get_context("spawn")
    entered, release, finished = (context.Event() for _ in range(3))
    results = context.Queue()
    process = context.Process(target=_writer, args=(str(tmp_path), "first", entered, release, finished, results))
    killed = False
    try:
        process.start()
        assert entered.wait(10)
        with patch("knowledge_mcp.vault.vault_write_lock", partial(vault_write_lock, timeout=0.05)):
            with pytest.raises(VaultBusyError, match="Vault is busy") as error:
                Vault(tmp_path).apply_change("personal", "note.md", "beta", "BETA")
            assert str(tmp_path) not in str(error.value)
            assert note.read_text(encoding="utf-8") == ORIGINAL
            if terminate:
                process.terminate()
                killed = True
            else:
                release.set()
            process.join(10)
            assert not process.is_alive()
            Vault(tmp_path).apply_change("personal", "note.md", "beta", "BETA")
        expected = ORIGINAL if terminate else ORIGINAL.replace("alpha", "ALPHA")
        assert note.read_text(encoding="utf-8") == expected.replace("beta", "BETA")
        # Persistent lock file avoids a different inode for waiting processes.
        assert (tmp_path / ".knowledge-mcp.lock").is_file()
    finally:
        # Terminating a worker can corrupt its multiprocessing Event mutex;
        # do not touch that synchronization object after intentional termination.
        if not killed:
            release.set()
        if process.is_alive():
            process.terminate()
        process.join(5)
        results.close()
        results.join_thread()
