from pathlib import Path

from swp2tex.core import (
    ERROR_MISSING_BBL_FOR_EXPORT,
    ERROR_MISSING_BIB,
    ERROR_WMF_CONVERT_FAILED,
    RunOptions,
    _normalize_tcilatex_inputs,
    _inject_tcilatex_compatibility,
    apply_safe_syntax_repairs,
    convert_wmf_graphics_to_png,
    comment_out_missing_includegraphics,
    convert_swp_frames_to_figures,
    normalize_qtr_frametitle,
    normalize_step_lists,
    normalize_bibliography_commands,
    run_workflow,
)


def test_missing_bib_blocks_run(tmp_path: Path) -> None:
    main = tmp_path / "main.ltx"
    main.write_text("\\bibliography{general}\n", encoding="utf-8")

    report = run_workflow(
        RunOptions(main_file=main, project_dir=tmp_path, interactive=False)
    )

    assert ERROR_MISSING_BIB in report.error_codes
    assert any("general.bib" in x for x in report.missing_bib)


def test_optional_bib_file_satisfies_missing_bib(tmp_path: Path, monkeypatch) -> None:
    main = tmp_path / "main.tex"
    main.write_text("\\bibliography{general}\n", encoding="utf-8")
    external_bib = tmp_path / "myrefs.bib"
    external_bib.write_text("@article{a,title={t}}\n", encoding="utf-8")

    def fake_build(project_dir: Path, tex_path: Path):
        return True, "ok"

    monkeypatch.setattr("swp2tex.core.run_latex_build", fake_build)
    report = run_workflow(
        RunOptions(
            main_file=main,
            project_dir=tmp_path,
            bib_file=external_bib,
            interactive=False,
            export_mode="none",
        )
    )
    assert report.build_status == "success"
    assert (tmp_path / "general.bib").exists()
    assert ERROR_MISSING_BIB not in report.error_codes


def test_optional_bib_file_is_cleaned_after_overleaf_export(
    tmp_path: Path, monkeypatch
) -> None:
    main = tmp_path / "main.tex"
    main.write_text("\\bibliography{general}\n", encoding="utf-8")
    external_bib = tmp_path / "external.bib"
    external_bib.write_text("@article{a,title={t}}\n", encoding="utf-8")

    def fake_build(project_dir: Path, tex_path: Path):
        return True, "ok"

    monkeypatch.setattr("swp2tex.core.run_latex_build", fake_build)
    report = run_workflow(
        RunOptions(
            main_file=main,
            project_dir=tmp_path,
            bib_file=external_bib,
            interactive=False,
            export_mode="overleaf",
        )
    )
    assert report.build_status == "success"
    assert not (tmp_path / "general.bib").exists()
    assert (tmp_path / "overleaf-export" / "general.bib").exists()


def test_shortstack_fix_applied() -> None:
    src = r"\centering\raisebox{-2.0ex}{\shortstack\textbf{Instrument}} \hspace{0em}"
    out, fixes, _suspects = apply_safe_syntax_repairs(src)

    assert r"\shortstack{\textbf{Instrument}}" in out
    assert fixes


def test_bibliography_extension_normalized() -> None:
    src = r"\bibliography{general,other.bib}"
    out, fixes = normalize_bibliography_commands(src)
    assert r"\bibliography{general.bib,other.bib}" in out
    assert fixes


def test_always_remove_styfolder_tcilatex(tmp_path: Path) -> None:
    tex = r"\input{styfolder/tcilatex.tex}"
    (tmp_path / "tcilatex.tex").write_text("% tci", encoding="utf-8")
    out, fixes = _normalize_tcilatex_inputs(tex)
    assert fixes
    assert "Removed SWP input" in out


def test_remove_missing_tcilatex_when_no_candidate_exists(tmp_path: Path) -> None:
    tex = r"\input{styfolder/tcilatex.tex}"
    out, fixes = _normalize_tcilatex_inputs(tex)
    assert "Removed SWP input" in out
    assert fixes


def test_inject_tcilatex_compatibility_contains_limfunc() -> None:
    src = r"\documentclass{article}\begin{document}x\end{document}"
    out, fixes = _inject_tcilatex_compatibility(src)
    assert r"\providecommand{\limfunc}[1]{\mathop{\mathrm{#1}}}" in out
    assert out.index(r"\providecommand{\limfunc}") < out.index(r"\begin{document}")
    assert fixes


def test_inject_tcilatex_compatibility_is_idempotent() -> None:
    src = (
        r"\documentclass{article}" + "\n"
        r"% SWP TCILATEX COMPATIBILITY SHIM" + "\n"
        r"\begin{document}x\end{document}"
    )
    out, fixes = _inject_tcilatex_compatibility(src)
    assert out == src
    assert not fixes


def test_convert_swp_frame_with_explicit_filename() -> None:
    src = (
        r"\FRAME{ftbpFU}{4.3068in}{2.5365in}{0pt}{\Qcb{Caption text}}{\Qlb{fig1}}"
        r"{graphs/fig1.jpg}{\special{type GRAPHIC;}}"
    )
    out, fixes = convert_swp_frames_to_figures(src)
    assert r"\begin{figure}[htbp]" in out
    assert r"\includegraphics[width=4.3068in]{graphs/fig1.jpg}" in out
    assert r"\caption{Caption text}" in out
    assert r"\label{fig1}" in out
    assert fixes


def test_convert_swp_frame_with_tempfilename() -> None:
    src = (
        r"\FRAME{ftbpFU}{3.1379in}{2.9761in}{0pt}{\Qcb{Cap}}{\Qlb{fig3}}{Figure}"
        r"{\special{tempfilename 'T75L0P02.wmf';type GRAPHIC;}}"
    )
    out, fixes = convert_swp_frames_to_figures(src)
    assert r"\includegraphics[width=3.1379in]{T75L0P02.wmf}" in out
    assert r"\label{fig3}" in out
    assert fixes


def test_convert_swp_frame_with_comment_wrapped_figure_placeholder() -> None:
    src = (
        "\\FRAME{ftbpFU}{4.124in}{2.3873in}{0pt}"
        "{\\Qcb{Surplus Share Guarantee as a Function of Elasticity}}"
        "{\\Qlb{fig:g}}{%\n"
        "Figure}{\\special{tempfilename '../../aer submission/T5IBWK0B.wmf';type GRAPHIC;}}"
    )
    out, fixes = convert_swp_frames_to_figures(src)
    assert (
        r"\includegraphics[width=4.124in]{../../aer submission/T5IBWK0B.wmf}" in out
    )
    assert r"\includegraphics[width=4.124in]{Figure}" not in out
    assert fixes


def test_convert_swp_frame_label_with_comment_suffix() -> None:
    src = (
        "\\FRAME{ftbpFU}{3.1379in}{2.9761in}{0pt}{\\Qcb{Cap}}{\\Qlb{fig3}%\n}"
        "{Figure}{\\special{tempfilename 'T75L0P02.wmf';type GRAPHIC;}}"
    )
    out, fixes = convert_swp_frames_to_figures(src)
    assert r"\label{fig3}" in out
    assert r"\label{\Qlb{fig3}%}" not in out
    assert fixes


def test_commented_frame_is_not_converted() -> None:
    src = (
        r"%\FRAME{ftbpFU}{3.1in}{2.9in}{0pt}{\Qcb{Cap}}{\Qlb{fig3}}{Figure}"
        r"{\special{tempfilename 'T75L0P02.wmf';type GRAPHIC;}}"
    )
    out, fixes = convert_swp_frames_to_figures(src)
    assert out == src
    assert not fixes


def test_convert_wmf_graphics_to_png_rewrites_reference(
    tmp_path: Path, monkeypatch
) -> None:
    (tmp_path / "graphs").mkdir()
    wmf = tmp_path / "graphs" / "fig.wmf"
    wmf.write_text("x", encoding="utf-8")

    def fake_convert(src: Path):
        src.with_suffix(".png").write_text("png", encoding="utf-8")
        return True, ""

    monkeypatch.setattr("swp2tex.core._convert_vector_to_png", fake_convert)
    src = r"\includegraphics{graphs/fig.wmf}"
    out, fixes, warnings, created = convert_wmf_graphics_to_png(src, tmp_path)
    assert r"\includegraphics{graphs/fig.png}" in out
    assert fixes
    assert not warnings
    assert created


def test_convert_wmf_graphics_to_png_rewrites_weird_relative_path_by_basename(
    tmp_path: Path, monkeypatch
) -> None:
    (tmp_path / "aersubmission").mkdir()
    wmf = tmp_path / "aersubmission" / "T5IBWK0A.wmf"
    wmf.write_text("x", encoding="utf-8")

    def fake_convert(src: Path):
        src.with_suffix(".png").write_text("png", encoding="utf-8")
        return True, ""

    monkeypatch.setattr("swp2tex.core._convert_vector_to_png", fake_convert)
    src = r"\includegraphics{../../aer submission/T5IBWK0A.wmf}"
    out, fixes, warnings, created = convert_wmf_graphics_to_png(src, tmp_path)
    assert r"\includegraphics{aersubmission/T5IBWK0A.png}" in out
    assert fixes
    assert not warnings
    assert created


def test_comment_out_missing_includegraphics_warns() -> None:
    src = "\\begin{figure}\\n\\includegraphics{missing_figure3.jpg}\\n\\end{figure}\\n"
    out, fixes, warnings = comment_out_missing_includegraphics(src, Path("."))
    assert "%\\includegraphics{missing_figure3.jpg}" in out
    assert fixes
    assert warnings


def test_comment_out_missing_includegraphics_keeps_existing_by_basename(
    tmp_path: Path,
) -> None:
    (tmp_path / "aersubmission").mkdir()
    (tmp_path / "aersubmission" / "T5IBWK0A.wmf").write_text("x", encoding="utf-8")
    src = "\\includegraphics{../../aer submission/T5IBWK0A.wmf}\n"
    out, fixes, warnings = comment_out_missing_includegraphics(src, tmp_path)
    assert out == src
    assert not fixes
    assert not warnings


def test_normalize_qtr_frametitle() -> None:
    src = r"\QTR{frametitle}{Introduction}"
    out, fixes = normalize_qtr_frametitle(src)
    assert r"\frametitle{Introduction}" in out
    assert fixes


def test_commented_qtr_frametitle_not_converted() -> None:
    src = r"%\QTR{frametitle}{Introduction}"
    out, fixes = normalize_qtr_frametitle(src)
    assert out == src
    assert not fixes


def test_normalize_stepitemize_to_itemize_with_overlay_items() -> None:
    src = "\\begin{stepitemize}\n\\item A\n\\item B\n\\end{stepitemize}"
    out, fixes = normalize_step_lists(src)
    assert "\\begin{itemize}" in out
    assert "\\end{itemize}" in out
    assert "\\item<+-> A" in out
    assert "\\item<+-> B" in out
    assert fixes


def test_normalize_stepenumerate_to_enumerate_with_overlay_items() -> None:
    src = "\\begin{stepenumerate}\n\\item 1\n\\item 2\n\\end{stepenumerate}"
    out, fixes = normalize_step_lists(src)
    assert "\\begin{enumerate}" in out
    assert "\\end{enumerate}" in out
    assert "\\item<+-> 1" in out
    assert "\\item<+-> 2" in out
    assert fixes


def test_step_list_does_not_modify_existing_item_overlay() -> None:
    src = "\\begin{stepitemize}\n\\item<2-> Existing\n\\item New\n\\end{stepitemize}"
    out, _fixes = normalize_step_lists(src)
    assert "\\item<2-> Existing" in out
    assert "\\item<+-> New" in out


def test_commented_step_list_not_converted() -> None:
    src = "%\\begin{stepitemize}\n%\\item A\n%\\end{stepitemize}"
    out, fixes = normalize_step_lists(src)
    assert out == src
    assert not fixes


def test_wmf_conversion_failure_reported(tmp_path: Path, monkeypatch) -> None:
    main = tmp_path / "main.tex"
    main.write_text(
        "\\bibliography{general}\n\\includegraphics{graphs/fig.wmf}\n",
        encoding="utf-8",
    )
    (tmp_path / "general.bib").write_text("@article{a,title={t}}\n", encoding="utf-8")
    (tmp_path / "graphs").mkdir()
    (tmp_path / "graphs" / "fig.wmf").write_text("x", encoding="utf-8")

    def fake_convert(src: Path):
        return False, "converter not found"

    monkeypatch.setattr("swp2tex.core._convert_vector_to_png", fake_convert)

    def fake_build(project_dir: Path, tex_path: Path):
        return True, "ok"

    monkeypatch.setattr("swp2tex.core.run_latex_build", fake_build)
    report = run_workflow(
        RunOptions(
            main_file=main,
            project_dir=tmp_path,
            interactive=False,
            export_mode="none",
        )
    )
    assert ERROR_WMF_CONVERT_FAILED in report.error_codes
    assert any("Failed converting" in e for e in report.errors)


def test_cleanup_tempfiles_after_failed_build(tmp_path: Path, monkeypatch) -> None:
    main = tmp_path / "main.tex"
    main.write_text("\\bibliography{general}\n", encoding="utf-8")
    (tmp_path / "general.bib").write_text("@article{a,title={t}}\n", encoding="utf-8")

    def fake_build(project_dir: Path, tex_path: Path):
        for suffix in (
            ".aux",
            ".fdb_latexmk",
            ".fls",
            ".log",
            ".bbl",
            ".blg",
            ".snm",
            ".nav",
        ):
            tex_path.with_suffix(suffix).write_text("tmp", encoding="utf-8")
        return False, "! LaTeX Error"

    monkeypatch.setattr("swp2tex.core.run_latex_build", fake_build)
    report = run_workflow(
        RunOptions(
            main_file=main,
            project_dir=tmp_path,
            interactive=False,
            export_mode="none",
        )
    )
    assert report.build_status == "failed"
    for suffix in (".aux", ".fdb_latexmk", ".fls", ".log", ".bbl", ".blg", ".snm", ".nav"):
        assert not main.with_name("main_tex.tex").with_suffix(suffix).exists()


def test_cleanup_tempfiles_in_project_dir_when_dirs_differ(
    tmp_path: Path, monkeypatch
) -> None:
    src_dir = tmp_path / "srcdir"
    proj_dir = tmp_path / "projectdir"
    src_dir.mkdir()
    proj_dir.mkdir()
    main = src_dir / "main.tex"
    main.write_text("\\bibliography{general}\n", encoding="utf-8")
    (proj_dir / "general.bib").write_text("@article{a,title={t}}\n", encoding="utf-8")

    def fake_build(project_dir: Path, tex_path: Path):
        for suffix in (".aux", ".fdb_latexmk", ".fls", ".log", ".bbl", ".blg", ".snm", ".nav"):
            (project_dir / f"{tex_path.stem}{suffix}").write_text("tmp", encoding="utf-8")
        return False, "! LaTeX Error"

    monkeypatch.setattr("swp2tex.core.run_latex_build", fake_build)
    report = run_workflow(
        RunOptions(
            main_file=main,
            project_dir=proj_dir,
            interactive=False,
            export_mode="none",
        )
    )
    assert report.build_status == "failed"
    for suffix in (".aux", ".fdb_latexmk", ".fls", ".log", ".bbl", ".blg", ".snm", ".nav"):
        assert not (proj_dir / f"main_tex{suffix}").exists()


def test_export_requires_bbl(tmp_path: Path, monkeypatch) -> None:
    main = tmp_path / "main.tex"
    main.write_text(
        "\\bibliographystyle{plain}\n\\bibliography{general}\n",
        encoding="utf-8",
    )
    (tmp_path / "general.bib").write_text("@article{a,title={t}}\n", encoding="utf-8")

    def fake_build(project_dir: Path, tex_path: Path):
        return True, "ok"

    monkeypatch.setattr("swp2tex.core.run_latex_build", fake_build)

    report = run_workflow(
        RunOptions(
            main_file=main,
            project_dir=tmp_path,
            interactive=False,
            export_mode="arxiv",
        )
    )
    assert ERROR_MISSING_BBL_FOR_EXPORT in report.error_codes


def test_arxiv_export_finds_bbl_in_project_dir_when_dirs_differ(
    tmp_path: Path, monkeypatch
) -> None:
    src_dir = tmp_path / "srcdir"
    proj_dir = tmp_path / "projectdir"
    src_dir.mkdir()
    proj_dir.mkdir()
    main = src_dir / "main.tex"
    main.write_text(
        "\\bibliographystyle{plain}\n\\bibliography{general}\n",
        encoding="utf-8",
    )
    (proj_dir / "general.bib").write_text("@article{a,title={t}}\n", encoding="utf-8")

    def fake_build(project_dir: Path, tex_path: Path):
        (project_dir / f"{tex_path.stem}.bbl").write_text("bbl", encoding="utf-8")
        return True, "ok"

    monkeypatch.setattr("swp2tex.core.run_latex_build", fake_build)

    report = run_workflow(
        RunOptions(
            main_file=main,
            project_dir=proj_dir,
            interactive=False,
            export_mode="arxiv",
        )
    )
    assert report.build_status == "success"
    assert report.export_path is not None
    assert ERROR_MISSING_BBL_FOR_EXPORT not in report.error_codes


def test_arxiv_cleanup_deletes_normalized_tex(tmp_path: Path, monkeypatch) -> None:
    main = tmp_path / "main.tex"
    main.write_text("\\bibliography{general}\n", encoding="utf-8")
    (tmp_path / "general.bib").write_text("@article{a,title={t}}\n", encoding="utf-8")

    def fake_build(project_dir: Path, tex_path: Path):
        tex_path.with_suffix(".bbl").write_text("bbl", encoding="utf-8")
        return True, "ok"

    monkeypatch.setattr("swp2tex.core.run_latex_build", fake_build)

    report = run_workflow(
        RunOptions(
            main_file=main,
            project_dir=tmp_path,
            interactive=False,
            export_mode="arxiv",
        )
    )
    assert report.build_status == "success"
    assert not (tmp_path / "main_arxiv.tex").exists()


def test_overleaf_export_contains_bib_and_graphics(tmp_path: Path, monkeypatch) -> None:
    main = tmp_path / "main.tex"
    main.write_text(
        "\\bibliographystyle{econometrica}\n"
        "\\bibliography{general}\n"
        "\\includegraphics{figs/plot}\n",
        encoding="utf-8",
    )
    (tmp_path / "general.bib").write_text("@article{a,title={t}}\n", encoding="utf-8")
    (tmp_path / "econometrica.bst").write_text("% bst\n", encoding="utf-8")
    (tmp_path / "figs").mkdir()
    (tmp_path / "figs" / "plot.png").write_text("img", encoding="utf-8")

    def fake_build(project_dir: Path, tex_path: Path):
        return True, "ok"

    monkeypatch.setattr("swp2tex.core.run_latex_build", fake_build)

    report = run_workflow(
        RunOptions(
            main_file=main,
            project_dir=tmp_path,
            interactive=False,
            export_mode="overleaf",
        )
    )
    export_dir = tmp_path / "overleaf-export"
    assert report.build_status == "success"
    assert export_dir.exists()
    assert (export_dir / "general.bib").exists()
    assert (export_dir / "econometrica.bst").exists()
    assert (export_dir / "figs" / "plot.png").exists()


def test_overleaf_export_copies_split_includegraphics_path(
    tmp_path: Path, monkeypatch
) -> None:
    main = tmp_path / "main.tex"
    main.write_text(
        "\\bibliography{general}\n"
        "\\includegraphics[width=4in]{%\n"
        "figure3.jpg}\n",
        encoding="utf-8",
    )
    (tmp_path / "general.bib").write_text("@article{a,title={t}}\n", encoding="utf-8")
    (tmp_path / "figure3.jpg").write_text("img", encoding="utf-8")

    def fake_build(project_dir: Path, tex_path: Path):
        return True, "ok"

    monkeypatch.setattr("swp2tex.core.run_latex_build", fake_build)

    report = run_workflow(
        RunOptions(
            main_file=main,
            project_dir=tmp_path,
            interactive=False,
            export_mode="overleaf",
        )
    )
    export_dir = tmp_path / "overleaf-export"
    assert report.build_status == "success"
    assert (export_dir / "figure3.jpg").exists()


def test_overleaf_cleanup_leaves_generated_files_only_in_export(
    tmp_path: Path, monkeypatch
) -> None:
    main = tmp_path / "main.tex"
    main.write_text(
        "\\bibliography{general}\n"
        "\\includegraphics{graphs/fig.wmf}\n",
        encoding="utf-8",
    )
    (tmp_path / "general.bib").write_text("@article{a,title={t}}\n", encoding="utf-8")
    (tmp_path / "graphs").mkdir()
    (tmp_path / "graphs" / "fig.wmf").write_text("wmf", encoding="utf-8")

    def fake_convert(src: Path):
        src.with_suffix(".png").write_text("png", encoding="utf-8")
        return True, ""

    def fake_build(project_dir: Path, tex_path: Path):
        return True, "ok"

    monkeypatch.setattr("swp2tex.core._convert_vector_to_png", fake_convert)
    monkeypatch.setattr("swp2tex.core.run_latex_build", fake_build)

    report = run_workflow(
        RunOptions(
            main_file=main,
            project_dir=tmp_path,
            interactive=False,
            export_mode="overleaf",
        )
    )
    export_dir = tmp_path / "overleaf-export"
    assert report.build_status == "success"
    assert (export_dir / "main_tex.tex").exists()
    assert (export_dir / "graphs" / "fig.png").exists()
    assert not (tmp_path / "main_tex.tex").exists()
    assert not (tmp_path / "graphs" / "fig.png").exists()
