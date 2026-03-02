# SWP to Overleaf/arXiv Converter

Convert Scientific Workplace (`.tex`) projects into export-ready LaTeX packages for:
- Overleaf
- arXiv

The app checks bibliography setup, applies safe syntax fixes for common SWP issues, runs a LaTeX build, and creates export folders.

## What It Does
- Requires:
  - one main `.tex` file (Scientific Workplace input)
  - one project/resource directory (contains all figures, optional `.bib`, `.bst`)
  - optional `.bib` file input (used only if required `.bib` is missing from project/resource directory)
- Bibliography checks:
  - validates `\bibliography{...}` entries (e.g. `general` -> `general.bib`)
  - blocks and reports exact missing `.bib` path if not found
- `econometrica.bst` handling:
  - if style is `\bibliographystyle{econometrica}` and `.bst` is missing, asks whether to add bundled version
- Safe syntax repair:
  - fixes known brace issues (e.g. `\shortstack\textbf{...}` -> `\shortstack{\textbf{...}}`)
  - always removes SWP `tcilatex` includes (e.g. `\input{tcilatex.tex}`, `\input{styfolder/tcilatex.tex}`)
  - injects a compatibility shim for common `tcilatex` macros (including `\limfunc`, `\func`, `\QTR`, `\Qlb`, `\Qcb`, etc.)
  - converts SWP Beamer titles (`\QTR{frametitle}{...}` -> `\frametitle{...}`)
- Build:
  - runs `latexmk -pdf -interaction=nonstopmode -halt-on-error`
  - returns build errors with LaTeX error context
  - converts `.wmf/.emf` graphics to `.png` via Windows GDI (PowerShell/.NET `System.Drawing`)
  - if a referenced figure file is missing, comments out only the `\includegraphics` command in the normalized file and reports a warning in the UI
  - removes LaTeX temp artifacts after each run (`.aux`, `.fdb_latexmk`, `.fls`, `.log`, `.bbl`, `.blg`, ...)

## Export Modes

### Overleaf mode (`overleaf`)
Creates `overleaf-export/` containing:
- generated main `.tex` (`*_tex.tex`)
- all referenced figures
- required `.bib` files
- `econometrica.bst` if needed

Run-generated intermediate files (normalized `.tex`, auto-converted `.png`, auto-added bst) are cleaned from the project directory after export.

### arXiv mode (`arxiv`)
Creates `arxiv-export/` and `arxiv-export.zip` containing:
- generated main `.tex` (`*_arxiv.tex`)
- all referenced figures
- generated `.bbl`

Excludes `.bib`, `.bst`, and PDF from arXiv export.

## Setup
From project root:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -e .[dev]
```

## Run (GUI)
```powershell
python -m swp2tex
```

In the UI, pick:
1. Main `.tex` file (Scientific Workplace)
2. Project/resource directory (Browse opens in suggested folder based on selected main file path)
3. Optional `.bib` file (Browse opens at a common Dropbox suggestion and is used as fallback if required bibliography is missing)
4. Export target:
   - SWP to Overleaf
   - SWP to arXiv

Each input row has an `Info` button for quick usage guidance.
The GUI shows a running status and progress bar while conversion/build is in progress.

## Run (CLI)
Overleaf export:

```powershell
swp2tex-bib run --main C:\path\main.ltx --project-dir C:\path\project --export-mode overleaf
```

With optional bib fallback:

```powershell
swp2tex-bib run --main C:\path\main.ltx --project-dir C:\path\project --bib-file C:\path\general.bib --export-mode overleaf
```

arXiv export:

```powershell
swp2tex-bib run --main C:\path\main.ltx --project-dir C:\path\project --export-mode arxiv
```

No export (check/fix/build only):

```powershell
swp2tex-bib run --main C:\path\main.ltx --project-dir C:\path\project --export-mode none
```

## Test
```powershell
python -m pytest -q
```

## Build Windows EXE
```powershell
.\build_exe.ps1
```

Output:
- `dist\swp2tex.exe` (GUI, double-click this)
- `dist\swp2tex-cli.exe` (CLI)

## License
MIT. See `LICENSE`.
