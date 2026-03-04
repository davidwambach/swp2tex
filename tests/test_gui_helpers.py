from pathlib import Path

from swp2tex.gui import (
    extract_drop_targets,
    parse_dnd_file_list,
    select_bib_from_drop_payload,
    select_main_tex_from_drop_payload,
    select_project_dir_from_drop_payload,
)


def test_parse_dnd_file_list_braced_path_with_spaces() -> None:
    payload = r"{C:\Users\example\My Folder\slides.tex}"
    paths = parse_dnd_file_list(payload)
    assert paths == [Path(r"C:\Users\example\My Folder\slides.tex")]


def test_parse_dnd_file_list_plain_path() -> None:
    payload = r"C:\Users\example\slides.tex"
    paths = parse_dnd_file_list(payload)
    assert paths == [Path(r"C:\Users\example\slides.tex")]


def test_select_main_tex_from_drop_payload_chooses_first_tex() -> None:
    payload = (
        r"{C:\Users\example\file.txt} "
        r"{C:\Users\example\talk draft.tex} "
        r"{C:\Users\example\alt.ltx}"
    )
    selected = select_main_tex_from_drop_payload(payload)
    assert selected == Path(r"C:\Users\example\talk draft.tex")


def test_select_main_tex_from_drop_payload_rejects_non_tex() -> None:
    payload = r"{C:\Users\example\notes.txt} {C:\Users\example\figure.png}"
    selected = select_main_tex_from_drop_payload(payload)
    assert selected is None


def test_select_project_dir_from_drop_payload_accepts_directory(tmp_path: Path) -> None:
    project = tmp_path / "project resources"
    project.mkdir()
    payload = "{" + str(project) + "}"
    selected = select_project_dir_from_drop_payload(payload)
    assert selected == project


def test_select_bib_from_drop_payload_selects_first_bib() -> None:
    payload = r"{C:\Users\example\notes.txt} {C:\Users\example\general.bib}"
    selected = select_bib_from_drop_payload(payload)
    assert selected == Path(r"C:\Users\example\general.bib")


def test_extract_drop_targets_populates_main_project_and_bib(tmp_path: Path) -> None:
    tex = tmp_path / "talk.tex"
    tex.write_text("", encoding="utf-8")
    project = tmp_path / "project"
    project.mkdir()
    bib = tmp_path / "general.bib"
    bib.write_text("", encoding="utf-8")
    payload = f"{{{tex}}} {{{project}}} {{{bib}}}"
    main_sel, project_sel, bib_sel = extract_drop_targets(payload)
    assert main_sel == tex
    assert project_sel == project
    assert bib_sel == bib
