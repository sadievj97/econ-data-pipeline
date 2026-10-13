"""The window: spec listing, the command runner, and that the window opens."""

import threading

import pytest
import yaml

from pipeline.app import Runner, read_specs, report_summary
from pipeline.cli import main


def write_yaml(path, data):
    path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
    return path


def test_specs_are_listed_with_status_and_last_build(tmp_path, example_data):
    specs, out = tmp_path / "specs", tmp_path / "out"
    specs.mkdir()
    write_yaml(specs / "a.yaml", example_data)
    write_yaml(specs / "b.yaml", dict(example_data, name="b_spec", confirmed=True))
    (specs / "c.yaml").write_text("name: [unclosed", encoding="utf-8")
    write_yaml(specs / "a.known_values.yaml", {"values": []})  # not a spec
    (out / "b_spec").mkdir(parents=True)
    (out / "b_spec" / "validation_report.md").write_text("# R\n\n**PASSED**: no errors; 2 flag(s) to look at.\n",
                                                        encoding="utf-8")
    infos = {i.path.name: i for i in read_specs(specs, out)}
    assert sorted(infos) == ["a.yaml", "b.yaml", "c.yaml"]
    assert (infos["a.yaml"].status, infos["a.yaml"].build) == ("Draft", "Not built")
    assert (infos["b.yaml"].status, infos["b.yaml"].build) == ("Confirmed", "Passed")
    assert infos["c.yaml"].status == "Cannot be read"
    assert report_summary(out / "b_spec") == "PASSED: no errors; 2 flag(s) to look at."
    assert report_summary(out / "nothing") == "Not built yet."


def test_runner_passes_on_output_and_exit_code():
    lines, done = [], threading.Event()
    codes = []
    runner = Runner(lines.append, lambda code: (codes.append(code), done.set()))
    runner.start(["--version"])
    assert done.wait(60)
    assert codes == [0] and any(line.startswith("pipeline ") for line in lines)
    assert not runner.busy


def test_confirm_command(capsys, example_data, write_spec):
    draft = dict(example_data, years={"start": 2000, "end": "FILL_ME"})
    path = write_spec(draft, "draft.yaml")
    assert main(["confirm", str(path)]) == 1
    assert "Fix these first" in capsys.readouterr().out
    good = write_spec(example_data, "good.yaml")
    assert main(["confirm", str(good)]) == 0
    assert yaml.safe_load(good.read_text(encoding="utf-8"))["confirmed"] is True
    assert main(["confirm", str(good)]) == 0 and "already confirmed" in capsys.readouterr().out


def test_window_opens():
    tk = pytest.importorskip("tkinter")
    from pipeline.app import App

    try:
        root = tk.Tk()
    except tk.TclError:
        pytest.skip("no display")
    root.withdraw()
    app = None
    try:
        app = App(root)
        root.update()
        assert [app.tabs.tab(i, "text") for i in range(app.tabs.index("end"))] == \
            ["1. Ask for data", "2. Review and build", "3. Results", "Find indicators"]
        assert app.tree.get_children()  # the project's specs are listed
    finally:
        if app is not None:
            app.runner.stop()
        root.destroy()
