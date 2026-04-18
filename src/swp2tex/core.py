from __future__ import annotations

from dataclasses import dataclass, field
import os
from pathlib import Path
import sys
import re
import shutil
import subprocess
import stat
from typing import Callable


BUNDLE_BST_NAME = "econometrica.bst"
BUNDLE_BST_DATE = "February 26, 2026"
ERROR_MISSING_BIB = "MISSING_BIB"
ERROR_MISSING_BST = "MISSING_BST"
ERROR_SUSPECT_SYNTAX = "SUSPECT_SYNTAX"
ERROR_BUILD_FAILED = "BUILD_FAILED"
ERROR_MISSING_BBL_FOR_EXPORT = "MISSING_BBL_FOR_EXPORT"
ERROR_WMF_CONVERT_FAILED = "WMF_CONVERT_FAILED"
NON_WINDOWS_WMF_WARNING_PREFIX = "WMF/EMF conversion skipped on non-Windows"
NON_WINDOWS_WMF_ALT_SUFFIXES = (".png", ".pdf", ".jpg", ".jpeg")


@dataclass
class RunOptions:
    main_file: Path
    project_dir: Path
    interactive: bool = True
    log_path: Path | None = None
    bib_file: Path | None = None
    export_mode: str | None = None


@dataclass
class RunReport:
    missing_bib: list[str] = field(default_factory=list)
    missing_bst: bool = False
    syntax_fixes: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    build_status: str = "not_run"
    errors: list[str] = field(default_factory=list)
    error_codes: list[str] = field(default_factory=list)
    export_path: str | None = None
    normalized_tex_path: str | None = None


def parse_bibliography_commands(tex: str) -> tuple[str | None, list[str]]:
    style_match = re.search(r"\\bibliographystyle\s*\{([^}]*)\}", tex)
    style = style_match.group(1).strip() if style_match else None
    bib_entries: list[str] = []
    for match in re.finditer(r"\\bibliography\s*\{([^}]*)\}", tex):
        raw = match.group(1)
        bib_entries.extend([x.strip() for x in raw.split(",") if x.strip()])
    return style, bib_entries


def normalize_bibliography_commands(tex: str) -> tuple[str, list[str]]:
    fixes: list[str] = []
    pattern = re.compile(r"\\bibliography\s*\{([^}]*)\}")

    def repl(match: re.Match[str]) -> str:
        raw = match.group(1)
        items = [x.strip() for x in raw.split(",") if x.strip()]
        changed = False
        out_items: list[str] = []
        for item in items:
            if item.lower().endswith(".bib"):
                out_items.append(item)
            else:
                out_items.append(f"{item}.bib")
                changed = True
        if changed:
            fixes.append(
                f"Normalized bibliography extension: {raw} -> {','.join(out_items)}"
            )
        return "\\bibliography{" + ",".join(out_items) + "}"

    return pattern.sub(repl, tex), fixes


def expected_bib_filename(entry: str) -> str:
    return entry if entry.lower().endswith(".bib") else f"{entry}.bib"


def apply_safe_syntax_repairs(tex: str) -> tuple[str, list[str], list[str]]:
    fixes: list[str] = []
    suspects: list[str] = []
    out = tex

    pattern_shortstack = re.compile(r"\\shortstack\s*(\\[A-Za-z]+\s*\{[^{}]*\})")

    def repl_shortstack(match: re.Match[str]) -> str:
        inner = match.group(1)
        fixes.append(f"Wrapped shortstack argument: {inner}")
        return "\\shortstack{" + inner + "}"

    out = pattern_shortstack.sub(repl_shortstack, out)

    for command in ("textbf", "emph"):
        pattern = re.compile(rf"\\{command}\s+([^\s\\{{}}]+)")

        def repl_token(match: re.Match[str], cmd: str = command) -> str:
            token = match.group(1)
            fixes.append(f"Wrapped bare argument for \\{cmd}: {token}")
            return f"\\{cmd}{{{token}}}"

        out = pattern.sub(repl_token, out)

    for line in out.splitlines():
        if "\\shortstack" in line:
            shortstack_idx = line.find("\\shortstack")
            tail = line[shortstack_idx + len("\\shortstack") :].lstrip()
            if tail and not tail.startswith("{"):
                suspects.append(line.strip())

    return out, fixes, suspects


def _extract_latex_error(output: str) -> str:
    lines = output.splitlines()
    for idx, line in enumerate(lines):
        if line.startswith("!"):
            start = max(0, idx - 1)
            end = min(len(lines), idx + 3)
            return "\n".join(lines[start:end])
    return "No explicit '!'-prefixed LaTeX error line found."


def run_latex_build(project_dir: Path, tex_path: Path) -> tuple[bool, str]:
    try:
        tex_arg = str(tex_path.relative_to(project_dir))
    except ValueError:
        tex_arg = str(tex_path)
    cmd = [
        "latexmk",
        "-pdf",
        "-interaction=nonstopmode",
        "-halt-on-error",
        tex_arg,
    ]
    try:
        result = subprocess.run(
            cmd,
            cwd=project_dir,
            capture_output=True,
            text=True,
            check=False,
        )
    except FileNotFoundError:
        return False, "latexmk was not found in PATH."
    output = f"{result.stdout}\n{result.stderr}"
    return result.returncode == 0, output


def _copy_if_exists(src: Path, dst: Path) -> bool:
    if not src.exists():
        return False
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dst)
    return True


def _resolve_graphics_path(project_dir: Path, raw_path: str) -> Path | None:
    clean = _sanitize_graphics_ref(raw_path).strip().strip("\"'")
    if not clean:
        return None
    p = Path(clean)
    if p.is_absolute():
        return p if p.exists() else None
    candidate = (project_dir / p).resolve()
    if candidate.exists():
        return candidate
    if p.suffix:
        by_name = _find_graphic_by_name(project_dir, p.name)
        return by_name
    for suffix in (".png", ".jpg", ".jpeg", ".pdf", ".eps", ".wmf", ".emf"):
        c2 = (project_dir / f"{clean}{suffix}").resolve()
        if c2.exists():
            return c2
    for suffix in (".png", ".jpg", ".jpeg", ".pdf", ".eps", ".wmf", ".emf"):
        by_name = _find_graphic_by_name(project_dir, f"{p.name}{suffix}")
        if by_name is not None:
            return by_name
    return None


def _find_graphic_by_name(project_dir: Path, filename: str) -> Path | None:
    if not filename:
        return None
    target_lower = filename.lower()
    try:
        for candidate in project_dir.rglob("*"):
            if not candidate.is_file():
                continue
            if candidate.name.lower() == target_lower:
                return candidate.resolve()
    except OSError:
        return None
    return None


def _extract_graphics(tex: str) -> list[str]:
    pattern = re.compile(r"\\includegraphics(?:\[[^\]]*\])?\{([^}]+)\}")
    out: list[str] = []
    for m in pattern.finditer(tex):
        if _is_pos_in_latex_comment(tex, m.start()):
            continue
        out.append(_sanitize_graphics_ref(m.group(1)))
    return out


def _sanitize_graphics_ref(raw: str) -> str:
    # SWP often wraps file names as {%
    # figure3.jpg} where % is line-continuation/comment syntax.
    no_comments = re.sub(r"(?<!\\)%[^\n]*", "", raw)
    parts = [p.strip() for p in no_comments.splitlines()]
    return "".join(parts).strip()


def _bundle_bst_path() -> Path:
    return Path(__file__).resolve().parent / "assets" / BUNDLE_BST_NAME


def _convert_vector_with_windows_gdi(src: Path) -> tuple[bool, str]:
    if sys.platform != "win32":
        return False, "Windows GDI conversion is only supported on Windows."
    out_png = src.with_suffix(".png")
    src_s = str(src).replace("'", "''")
    out_s = str(out_png).replace("'", "''")
    ps_script = (
        "$ErrorActionPreference='Stop'; "
        "Add-Type -AssemblyName System.Drawing; "
        f"$src='{src_s}'; $dst='{out_s}'; "
        "$meta = New-Object System.Drawing.Imaging.Metafile($src); "
        "$w = [Math]::Max([int]$meta.Width, 1); "
        "$h = [Math]::Max([int]$meta.Height, 1); "
        "$bmp = New-Object System.Drawing.Bitmap($w, $h); "
        "$gfx = [System.Drawing.Graphics]::FromImage($bmp); "
        "$gfx.Clear([System.Drawing.Color]::White); "
        "$gfx.DrawImage($meta, 0, 0, $w, $h); "
        "$bmp.Save($dst, [System.Drawing.Imaging.ImageFormat]::Png); "
        "$gfx.Dispose(); $bmp.Dispose(); $meta.Dispose();"
    )
    result = subprocess.run(
        ["powershell", "-NoProfile", "-NonInteractive", "-Command", ps_script],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        output = f"{result.stdout}\n{result.stderr}".strip()
        return False, output or "Windows GDI conversion failed."
    if not out_png.exists():
        return False, f"Expected PNG not found after conversion: {out_png}"
    return True, ""


def _convert_vector_to_png(src: Path) -> tuple[bool, str]:
    return _convert_vector_with_windows_gdi(src)


def _find_existing_vector_alternative(
    vector_path: Path,
) -> Path | None:
    for suffix in NON_WINDOWS_WMF_ALT_SUFFIXES:
        candidate = vector_path.with_suffix(suffix)
        if candidate.exists():
            return candidate
    return None


def convert_wmf_graphics_to_png(
    tex: str, project_dir: Path
) -> tuple[str, list[str], list[str], list[Path]]:
    fixes: list[str] = []
    warnings: list[str] = []
    created_pngs: list[Path] = []
    warned_non_windows: set[Path] = set()
    pattern = re.compile(r"(\\includegraphics(?:\[[^\]]*\])?\{)([^}]+)(\})")

    def repl(match: re.Match[str]) -> str:
        if _is_pos_in_latex_comment(tex, match.start()):
            return match.group(0)
        prefix = match.group(1)
        raw = _sanitize_graphics_ref(match.group(2))
        suffix = match.group(3)
        resolved = _resolve_graphics_path(project_dir, raw)
        if resolved is None:
            return match.group(0)
        if resolved.suffix.lower() not in {".wmf", ".emf"}:
            return match.group(0)

        # On non-Windows platforms, we cannot use the built-in GDI converter.
        if sys.platform != "win32":
            alt = _find_existing_vector_alternative(resolved)
            if alt is not None:
                try:
                    rel_alt = alt.relative_to(project_dir).as_posix()
                except ValueError:
                    rel_alt = alt.name
                fixes.append(
                    f"Used pre-converted image on non-Windows: {resolved} -> {rel_alt}"
                )
                return prefix + rel_alt + suffix
            if resolved not in warned_non_windows:
                platform_label = "macOS" if sys.platform == "darwin" else sys.platform
                suggestions = ", ".join(
                    str(resolved.with_suffix(sfx).name)
                    for sfx in NON_WINDOWS_WMF_ALT_SUFFIXES
                )
                warnings.append(
                    f"{NON_WINDOWS_WMF_WARNING_PREFIX}: {resolved}. "
                    f"Platform: {platform_label}. "
                    f"Please pre-convert this file to one of: {suggestions}."
                )
                warned_non_windows.add(resolved)
            return match.group(0)

        png_path = resolved.with_suffix(".png")
        existed_before = png_path.exists()
        ok, detail = _convert_vector_to_png(resolved)
        if not ok:
            warnings.append(f"Failed converting {resolved} to PNG: {detail}")
            return match.group(0)
        if not existed_before and png_path.exists():
            created_pngs.append(png_path)

        # Prefer stable relative path from project root in rewritten TeX.
        try:
            rel_png = resolved.with_suffix(".png").relative_to(project_dir).as_posix()
        except ValueError:
            rel_png = resolved.with_suffix(".png").name
        fixes.append(f"Converted vector image to PNG: {resolved} -> {rel_png}")
        return prefix + rel_png + suffix

    out = pattern.sub(repl, tex)
    return out, fixes, warnings, created_pngs


def _comment_tex_snippet(snippet: str) -> str:
    commented_lines: list[str] = []
    for line in snippet.splitlines():
        if not line.strip():
            commented_lines.append(line)
            continue
        indent_len = len(line) - len(line.lstrip(" \t"))
        indent = line[:indent_len]
        rest = line[indent_len:]
        if rest.startswith("%"):
            commented_lines.append(line)
        else:
            commented_lines.append(f"{indent}%{rest}")
    return "\n".join(commented_lines)


def comment_out_missing_includegraphics(
    tex: str, project_dir: Path
) -> tuple[str, list[str], list[str]]:
    fixes: list[str] = []
    warnings: list[str] = []
    pattern = re.compile(r"\\includegraphics(?:\[[^\]]*\])?\{([^}]+)\}")
    out: list[str] = []
    i = 0
    for m in pattern.finditer(tex):
        start, end = m.span()
        out.append(tex[i:start])
        i = end
        if _is_pos_in_latex_comment(tex, start):
            out.append(m.group(0))
            continue
        raw = _sanitize_graphics_ref(m.group(1))
        resolved = _resolve_graphics_path(project_dir, raw)
        if resolved is not None:
            out.append(m.group(0))
            continue
        out.append(_comment_tex_snippet(m.group(0)))
        fixes.append(f"Commented out missing includegraphics: {raw}")
        warnings.append(
            f"Missing figure file, includegraphics commented out in normalized file: {raw}"
        )
    out.append(tex[i:])
    return "".join(out), fixes, warnings


def normalize_qtr_frametitle(tex: str) -> tuple[str, list[str]]:
    fixes: list[str] = []
    pattern = re.compile(r"\\QTR\s*\{\s*frametitle\s*\}\s*\{([^}]*)\}")

    def repl(match: re.Match[str]) -> str:
        if _is_pos_in_latex_comment(tex, match.start()):
            return match.group(0)
        title = match.group(1).strip()
        fixes.append(f"Converted QTR frametitle: {title}")
        return r"\frametitle{" + title + "}"

    return pattern.sub(repl, tex), fixes


def normalize_step_lists(tex: str) -> tuple[str, list[str]]:
    fixes: list[str] = []
    token_pattern = re.compile(
        r"\\begin\s*\{(stepitemize|stepenumerate)\}"
        r"|\\end\s*\{(stepitemize|stepenumerate)\}"
        r"|\\item\b"
    )
    out: list[str] = []
    env_stack: list[str] = []
    converted_envs: set[str] = set()
    item_overlay_count = 0
    i = 0
    for match in token_pattern.finditer(tex):
        start, end = match.span()
        out.append(tex[i:start])
        i = end
        if _is_pos_in_latex_comment(tex, start):
            out.append(match.group(0))
            continue
        begin_name = match.group(1)
        end_name = match.group(2)
        token = match.group(0)
        if begin_name:
            target = "itemize" if begin_name == "stepitemize" else "enumerate"
            env_stack.append(target)
            converted_envs.add(begin_name)
            out.append(r"\begin{" + target + "}")
            continue
        if end_name:
            target = "itemize" if end_name == "stepitemize" else "enumerate"
            if env_stack:
                target = env_stack.pop()
            out.append(r"\end{" + target + "}")
            continue
        if token == r"\item" and env_stack:
            remainder = tex[end:]
            if re.match(r"\s*<", remainder):
                out.append(token)
                continue
            item_overlay_count += 1
            out.append(r"\item<+->")
            continue
        out.append(token)
    out.append(tex[i:])
    if "stepitemize" in converted_envs:
        fixes.append("Converted stepitemize to itemize with overlay items.")
    if "stepenumerate" in converted_envs:
        fixes.append("Converted stepenumerate to enumerate with overlay items.")
    if item_overlay_count:
        fixes.append(
            f"Added Beamer overlay spec to {item_overlay_count} item(s) in step lists."
        )
    return "".join(out), fixes


def normalize_display_math_operators(tex: str) -> tuple[str, list[str]]:
    fixes: list[str] = []
    counts = {"dint": 0, "dsum": 0}
    pattern = re.compile(r"\\(dint|dsum)\b")
    out: list[str] = []
    i = 0
    for match in pattern.finditer(tex):
        start, end = match.span()
        out.append(tex[i:start])
        i = end
        if _is_pos_in_latex_comment(tex, start):
            out.append(match.group(0))
            continue
        op = match.group(1)
        if op == "dint":
            out.append(r"\int")
            counts["dint"] += 1
        else:
            out.append(r"\sum")
            counts["dsum"] += 1
    out.append(tex[i:])
    if counts["dint"]:
        fixes.append(
            f"Normalized \\dint to \\int ({counts['dint']} occurrence(s))."
        )
    if counts["dsum"]:
        fixes.append(
            f"Normalized \\dsum to \\sum ({counts['dsum']} occurrence(s))."
        )
    return "".join(out), fixes


def _is_pos_inside_environment(tex: str, pos: int, env_name: str) -> bool:
    token_pattern = re.compile(
        rf"\\begin\s*\{{{re.escape(env_name)}\}}|\\end\s*\{{{re.escape(env_name)}\}}"
    )
    depth = 0
    for match in token_pattern.finditer(tex, 0, pos):
        if _is_pos_in_latex_comment(tex, match.start()):
            continue
        token = match.group(0)
        if token.lstrip().startswith(r"\begin"):
            depth += 1
        elif depth > 0:
            depth -= 1
    return depth > 0


def normalize_extra_center_blocks_for_beamer(tex: str) -> tuple[str, list[str]]:
    fixes: list[str] = []
    pattern = re.compile(r"\\begin\s*\{center\}.*?\\end\s*\{center\}", re.DOTALL)
    out: list[str] = []
    i = 0
    converted_count = 0
    for match in pattern.finditer(tex):
        start, end = match.span()
        out.append(tex[i:start])
        i = end
        block = match.group(0)
        if _is_pos_in_latex_comment(tex, start):
            out.append(block)
            continue
        if r"\Extra" not in block:
            out.append(block)
            continue
        if _is_pos_inside_environment(tex, start, "frame"):
            out.append(block)
            continue
        converted_count += 1
        out.append(r"\begin{frame}[plain]" + "\n" + block + "\n" + r"\end{frame}")
    out.append(tex[i:])
    if converted_count:
        fixes.append(
            f"Wrapped {converted_count} center block(s) with \\Extra into beamer frame(s)."
        )
    return "".join(out), fixes


def _strip_wrapper(raw: str, macro_name: str) -> str:
    cleaned = re.sub(r"(?<!\\)%[^\n]*", "", raw).strip()
    pattern = re.compile(rf"\\{macro_name}\s*\{{(.*)\}}", re.DOTALL)
    m = pattern.fullmatch(cleaned)
    return m.group(1).strip() if m else raw.strip()


def _extract_special_filename(special_arg: str) -> str | None:
    patterns = [
        r"filename\s*'([^']+)'",
        r'tempfilename\s*"([^"]+)"',
        r"tempfilename\s*'([^']+)'",
    ]
    for p in patterns:
        m = re.search(p, special_arg, flags=re.DOTALL)
        if m:
            return m.group(1).strip()
    return None


def _read_braced_group(text: str, start: int) -> tuple[str, int] | None:
    if start >= len(text) or text[start] != "{":
        return None
    depth = 0
    i = start
    while i < len(text):
        ch = text[i]
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return text[start + 1 : i], i + 1
        i += 1
    return None


def _is_pos_in_latex_comment(text: str, pos: int) -> bool:
    line_start = text.rfind("\n", 0, pos) + 1
    i = line_start
    while i < pos:
        p = text.find("%", i, pos)
        if p == -1:
            return False
        backslashes = 0
        j = p - 1
        while j >= line_start and text[j] == "\\":
            backslashes += 1
            j -= 1
        if backslashes % 2 == 0:
            return True
        i = p + 1
    return False


def convert_swp_frames_to_figures(tex: str) -> tuple[str, list[str]]:
    fixes: list[str] = []
    out: list[str] = []
    i = 0
    token = r"\FRAME"
    while i < len(tex):
        pos = tex.find(token, i)
        if pos == -1:
            out.append(tex[i:])
            break
        if _is_pos_in_latex_comment(tex, pos):
            out.append(tex[i : pos + len(token)])
            i = pos + len(token)
            continue
        out.append(tex[i:pos])
        j = pos + len(token)
        args: list[str] = []
        ok = True
        for _ in range(8):
            while j < len(tex) and tex[j].isspace():
                j += 1
            grp = _read_braced_group(tex, j)
            if grp is None:
                ok = False
                break
            value, j = grp
            args.append(value)
        if not ok or len(args) != 8:
            out.append(tex[pos : pos + len(token)])
            i = pos + len(token)
            continue

        width = args[1].strip()
        caption = _strip_wrapper(args[4], "Qcb")
        label = _strip_wrapper(args[5], "Qlb")
        file_arg = _sanitize_graphics_ref(args[6])
        special = args[7]

        filename = None
        if file_arg and file_arg.lower() != "figure":
            filename = file_arg
        else:
            filename = _extract_special_filename(special)
        if filename:
            filename = _sanitize_graphics_ref(filename).strip().strip("\"'")

        fig_lines = [r"\begin{figure}[htbp]", r"\centering"]
        if filename:
            include = r"\includegraphics"
            if width and width != "0pt":
                include += f"[width={width}]"
            include += "{" + filename + "}"
            fig_lines.append(include)
        if caption:
            fig_lines.append(r"\caption{" + caption + "}")
        if label:
            fig_lines.append(r"\label{" + label + "}")
        fig_lines.append(r"\end{figure}")
        out.append("\n".join(fig_lines))
        fixes.append(f"Converted SWP FRAME to figure (label={label or 'none'}).")
        i = j
    return "".join(out), fixes


def _normalize_tcilatex_inputs(tex: str) -> tuple[str, list[str]]:
    fixes: list[str] = []
    pattern = re.compile(r"\\input\s*\{([^}]*)\}")

    def repl(match: re.Match[str]) -> str:
        raw = match.group(1).strip()
        path = Path(raw)
        stem = path.stem.lower() if path.suffix else path.name.lower()
        if stem != "tcilatex":
            return match.group(0)
        fixes.append(f"Removed SWP tcilatex input '{raw}'")
        return f"% Removed SWP input: {raw}"

    return pattern.sub(repl, tex), fixes


TCILATEX_COMPAT_MARKER = "% SWP TCILATEX COMPATIBILITY SHIM"
TCILATEX_COMPAT_BLOCK = r"""
% SWP TCILATEX COMPATIBILITY SHIM
\providecommand{\func}[1]{\mathop{\mathrm{#1}}}
\providecommand{\limfunc}[1]{\mathop{\mathrm{#1}}}
\providecommand{\Extra}{\Large}
\providecommand{\QTR}[2]{{\csname #1\endcsname #2}}
\providecommand{\QTP}[1]{}
\providecommand{\QEXCLUDE}[1]{}
\providecommand{\Qlb}[1]{#1}
\providecommand{\Qlt}[1]{#1}
\providecommand{\Qcb}[1]{#1}
\providecommand{\Qct}[1]{#1}
\providecommand{\QTagDef}[3]{}
\providecommand{\QSubDoc}[2]{#2}
\providecommand{\TeXButton}[2]{#2}
\providecommand{\QQA}[2]{}
\providecommand{\QQQ}[2]{\expandafter\def\csname #1\endcsname{#2}}
\providecommand{\TEXUX}[1]{"texux"}
\providecommand{\TEXTsymbol}[1]{\mbox{$#1$}}
\providecommand{\QQfnmark}[1]{\footnotemark}
\providecommand{\QQfntext}[2]{\addtocounter{footnote}{#1}\footnotetext{#2}}
"""


def _inject_tcilatex_compatibility(tex: str) -> tuple[str, list[str]]:
    if TCILATEX_COMPAT_MARKER in tex:
        return tex, []
    block = TCILATEX_COMPAT_BLOCK.strip() + "\n\n"
    doc_pos = tex.find(r"\begin{document}")
    if doc_pos != -1:
        out = tex[:doc_pos] + block + tex[doc_pos:]
    else:
        out = block + tex
    return out, ["Inserted SWP compatibility shim (replaces common tcilatex macros)."]


def _copy_graphics_to_export(project_dir: Path, export_dir: Path, tex: str) -> None:
    for raw_graphic in _extract_graphics(tex):
        resolved = _resolve_graphics_path(project_dir, raw_graphic)
        if resolved is None:
            continue
        try:
            rel = resolved.relative_to(project_dir)
        except ValueError:
            rel = Path(resolved.name)
        _copy_if_exists(resolved, export_dir / rel)


def _safe_unlink(path: Path) -> str | None:
    if not path.exists():
        return None
    try:
        path.unlink()
        return None
    except PermissionError:
        try:
            os.chmod(path, stat.S_IWRITE)
            path.unlink()
            return None
        except OSError as exc:
            return str(exc)
    except OSError as exc:
        return str(exc)


def _cleanup_latex_tempfiles(
    tex_path: Path, project_dir: Path
) -> tuple[list[Path], list[str]]:
    removed: list[Path] = []
    failures: list[str] = []
    for suffix in (
        ".aux",
        ".fdb_latexmk",
        ".fls",
        ".log",
        ".bbl",
        ".blg",
        ".out",
        ".toc",
        ".snm",
        ".nav",
        ".synctex.gz",
    ):
        candidates = {
            tex_path.with_suffix(suffix),
            project_dir / f"{tex_path.stem}{suffix}",
        }
        for candidate in candidates:
            if not candidate.exists():
                continue
            try:
                candidate.unlink()
                removed.append(candidate)
            except PermissionError:
                # Common on Windows/OneDrive: retry after enabling write permission.
                try:
                    os.chmod(candidate, stat.S_IWRITE)
                    candidate.unlink()
                    removed.append(candidate)
                except OSError as exc:
                    failures.append(f"{candidate.name}: {exc}")
            except OSError as exc:
                failures.append(f"{candidate.name}: {exc}")
    return removed, failures


def _find_latex_artifact(tex_path: Path, project_dir: Path, suffix: str) -> Path | None:
    candidates = [
        tex_path.with_suffix(suffix),
        project_dir / f"{tex_path.stem}{suffix}",
    ]
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return None


def _working_tex_path(main_file: Path, export_mode: str | None) -> Path:
    suffix_tag = "arxiv" if export_mode == "arxiv" else "tex"
    return main_file.with_name(f"{main_file.stem}_{suffix_tag}.tex")


def run_workflow(
    options: RunOptions,
    prompt_yes_no: Callable[[str], bool] | None = None,
) -> RunReport:
    report = RunReport()
    options.main_file = options.main_file.resolve()
    options.project_dir = options.project_dir.resolve()
    if options.bib_file is not None:
        options.bib_file = options.bib_file.resolve()

    if not options.main_file.exists():
        report.errors.append(f"Main file not found: {options.main_file}")
        report.error_codes.append(ERROR_BUILD_FAILED)
        return report
    if not options.project_dir.exists():
        report.errors.append(f"Project directory not found: {options.project_dir}")
        report.error_codes.append(ERROR_BUILD_FAILED)
        return report

    tex = options.main_file.read_text(encoding="utf-8", errors="replace")
    tex, tci_fixes = _normalize_tcilatex_inputs(tex)
    report.syntax_fixes.extend(tci_fixes)
    tex, compat_fixes = _inject_tcilatex_compatibility(tex)
    report.syntax_fixes.extend(compat_fixes)
    tex, bib_norm_fixes = normalize_bibliography_commands(tex)
    report.syntax_fixes.extend(bib_norm_fixes)
    tex, frame_fixes = convert_swp_frames_to_figures(tex)
    report.syntax_fixes.extend(frame_fixes)
    tex, qtr_fixes = normalize_qtr_frametitle(tex)
    report.syntax_fixes.extend(qtr_fixes)
    tex, step_fixes = normalize_step_lists(tex)
    report.syntax_fixes.extend(step_fixes)
    tex, display_op_fixes = normalize_display_math_operators(tex)
    report.syntax_fixes.extend(display_op_fixes)
    tex, extra_center_fixes = normalize_extra_center_blocks_for_beamer(tex)
    report.syntax_fixes.extend(extra_center_fixes)
    tex, vector_fixes, vector_warnings, created_pngs = convert_wmf_graphics_to_png(
        tex, options.project_dir
    )
    report.syntax_fixes.extend(vector_fixes)
    if vector_warnings:
        non_windows_warnings = [
            w for w in vector_warnings if w.startswith(NON_WINDOWS_WMF_WARNING_PREFIX)
        ]
        conversion_failures = [
            w for w in vector_warnings if not w.startswith(NON_WINDOWS_WMF_WARNING_PREFIX)
        ]
        if non_windows_warnings:
            report.warnings.extend(non_windows_warnings)
        if conversion_failures:
            report.error_codes.append(ERROR_WMF_CONVERT_FAILED)
            report.errors.extend(conversion_failures)
    tex, missing_fig_fixes, missing_fig_warnings = comment_out_missing_includegraphics(
        tex, options.project_dir
    )
    report.syntax_fixes.extend(missing_fig_fixes)
    report.warnings.extend(missing_fig_warnings)
    style, bib_entries = parse_bibliography_commands(tex)
    bst_target = options.project_dir / BUNDLE_BST_NAME
    bst_added_this_run = False
    copied_bib_paths: list[Path] = []
    missing_bib = []
    missing_bib_paths: list[Path] = []
    for entry in bib_entries:
        filename = expected_bib_filename(entry)
        required = options.project_dir / filename
        if not required.exists():
            missing_bib.append(str(required))
            missing_bib_paths.append(required)

    if missing_bib_paths and options.bib_file is not None and options.bib_file.exists():
        candidate = options.bib_file
        matched_any = False
        for required in list(missing_bib_paths):
            if candidate.name.lower() == required.name.lower():
                shutil.copy2(candidate, required)
                copied_bib_paths.append(required)
                matched_any = True
                report.syntax_fixes.append(
                    f"Copied optional bib file to project directory: {required.name}"
                )
                missing_bib_paths.remove(required)
                missing_bib.remove(str(required))
        # If exactly one bib is missing and names differ, use provided file as source.
        if not matched_any and len(missing_bib_paths) == 1:
            required = missing_bib_paths[0]
            shutil.copy2(candidate, required)
            copied_bib_paths.append(required)
            report.syntax_fixes.append(
                f"Copied optional bib file to required name: {candidate.name} -> {required.name}"
            )
            missing_bib_paths.clear()
            missing_bib.clear()

    if missing_bib:
        report.missing_bib = missing_bib
        report.error_codes.append(ERROR_MISSING_BIB)
        for path in missing_bib:
            report.errors.append(f"Missing bibliography file: {path}")
        return report

    if style == "econometrica":
        if not bst_target.exists():
            report.missing_bst = True
            if not options.interactive:
                report.error_codes.append(ERROR_MISSING_BST)
                report.errors.append(f"Missing {BUNDLE_BST_NAME}: {bst_target}")
                return report
            if prompt_yes_no is None:
                report.error_codes.append(ERROR_MISSING_BST)
                report.errors.append("No prompt handler available for missing .bst.")
                return report
            question = (
                f"{BUNDLE_BST_NAME} is missing. Add bundled version dated "
                f"{BUNDLE_BST_DATE} to:\n{bst_target}\nProceed?"
            )
            if prompt_yes_no(question):
                bundled_bst = _bundle_bst_path()
                if not bundled_bst.exists():
                    report.error_codes.append(ERROR_MISSING_BST)
                    report.errors.append(
                        f"Bundled {BUNDLE_BST_NAME} not found at {bundled_bst}"
                    )
                    return report
                shutil.copy2(bundled_bst, bst_target)
                bst_added_this_run = True
                report.missing_bst = False
            else:
                report.error_codes.append(ERROR_MISSING_BST)
                report.errors.append(f"User declined adding {BUNDLE_BST_NAME}.")
                return report
    normalized = _working_tex_path(options.main_file, options.export_mode)
    fixed_text, fixes, suspects = apply_safe_syntax_repairs(tex)
    report.syntax_fixes.extend(fixes)
    if suspects:
        report.error_codes.append(ERROR_SUSPECT_SYNTAX)
        for line in suspects:
            report.errors.append(f"Suspicious syntax (not auto-fixed): {line}")
    normalized.write_text(fixed_text, encoding="utf-8")
    report.normalized_tex_path = str(normalized)

    try:
        ok, build_output = run_latex_build(options.project_dir, normalized)
        if options.log_path:
            options.log_path.parent.mkdir(parents=True, exist_ok=True)
            options.log_path.write_text(build_output, encoding="utf-8")
        if not ok:
            report.build_status = "failed"
            report.error_codes.append(ERROR_BUILD_FAILED)
            report.errors.append("LaTeX build failed.")
            report.errors.append(_extract_latex_error(build_output))
            return report
        report.build_status = "success"

        if not options.export_mode:
            return report

        if options.export_mode == "arxiv":
            export_dir = options.project_dir / "arxiv-export"
            export_dir.mkdir(parents=True, exist_ok=True)
            shutil.copy2(normalized, export_dir / normalized.name)
            _copy_graphics_to_export(options.project_dir, export_dir, fixed_text)

            bbl_path = _find_latex_artifact(normalized, options.project_dir, ".bbl")
            if bbl_path is None:
                report.error_codes.append(ERROR_MISSING_BBL_FOR_EXPORT)
                report.errors.append(
                    "Expected .bbl file missing. Checked: "
                    f"{normalized.with_suffix('.bbl')} and "
                    f"{options.project_dir / f'{normalized.stem}.bbl'}"
                )
                return report
            shutil.copy2(bbl_path, export_dir / f"{normalized.stem}.bbl")

            zip_file = shutil.make_archive(
                base_name=str(options.project_dir / "arxiv-export"),
                format="zip",
                root_dir=str(options.project_dir),
                base_dir="arxiv-export",
            )
            report.export_path = zip_file
            return report

        if options.export_mode == "overleaf":
            export_dir = options.project_dir / "overleaf-export"
            export_dir.mkdir(parents=True, exist_ok=True)
            shutil.copy2(normalized, export_dir / normalized.name)
            _copy_graphics_to_export(options.project_dir, export_dir, fixed_text)

            for entry in bib_entries:
                bib_name = expected_bib_filename(entry)
                bib_path = options.project_dir / bib_name
                _copy_if_exists(bib_path, export_dir / bib_name)

            if style == "econometrica":
                bst_path = options.project_dir / BUNDLE_BST_NAME
                _copy_if_exists(bst_path, export_dir / BUNDLE_BST_NAME)

            report.export_path = str(export_dir)
            return report

        report.errors.append(f"Unknown export mode: {options.export_mode}")
        report.error_codes.append(ERROR_BUILD_FAILED)
        return report
    finally:
        removed, cleanup_failures = _cleanup_latex_tempfiles(normalized, options.project_dir)
        if removed:
            names = ", ".join(p.name for p in removed)
            report.syntax_fixes.append(f"Removed LaTeX temp files: {names}")
        if cleanup_failures:
            for item in cleanup_failures:
                report.warnings.append(f"Could not delete temporary file: {item}")
        if options.export_mode in {"overleaf", "arxiv"}:
            if report.build_status == "failed":
                broken_path = normalized.with_name(
                    f"{normalized.stem} (broken){normalized.suffix}"
                )
                if normalized.exists():
                    replace_error = _safe_unlink(broken_path)
                    if replace_error:
                        report.warnings.append(
                            f"Could not replace prior broken file {broken_path.name}: "
                            f"{replace_error}"
                        )
                    try:
                        normalized.rename(broken_path)
                        report.normalized_tex_path = str(broken_path)
                        report.warnings.append(
                            f"Build failed; kept generated file as {broken_path.name}."
                        )
                    except OSError as exc:
                        report.warnings.append(
                            f"Build failed; could not rename {normalized.name} to "
                            f"{broken_path.name}: {exc}"
                        )
            else:
                unlink_error = _safe_unlink(normalized)
                if unlink_error:
                    report.warnings.append(
                        f"Could not delete normalized file {normalized.name}: {unlink_error}"
                    )
        if options.export_mode == "overleaf":
            for created in created_pngs:
                unlink_error = _safe_unlink(created)
                if unlink_error:
                    report.warnings.append(
                        f"Could not delete generated PNG {created.name}: {unlink_error}"
                    )
            for copied_bib in copied_bib_paths:
                unlink_error = _safe_unlink(copied_bib)
                if unlink_error:
                    report.warnings.append(
                        f"Could not delete copied bib file {copied_bib.name}: {unlink_error}"
                    )
            if bst_added_this_run:
                unlink_error = _safe_unlink(bst_target)
                if unlink_error:
                    report.warnings.append(
                        f"Could not delete copied bst file {bst_target.name}: {unlink_error}"
                    )
