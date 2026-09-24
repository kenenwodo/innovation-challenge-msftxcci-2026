import sys
import re
from pathlib import Path
from bs4 import BeautifulSoup

PROJECT_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = PROJECT_DIR / "data"
OUTPUT_DIR = PROJECT_DIR / "output"


def extract_html_text(html_path: Path) -> str:
    html = html_path.read_text(
        encoding="utf-8",
        errors="replace",
    )

    soup = BeautifulSoup(html, "html.parser")

    pre = soup.find("pre")

    if pre is None:
        raise ValueError(
            f"Could not find the Federal Register <pre> document content "
            f"in {html_path.name}."
        )

    text = pre.get_text("\n", strip=False)

    # Remove unwanted control characters while preserving
    # normal newlines and tabs.
    text = text.replace("\x00", "")
    text = re.sub(r"[\x01-\x08\x0b\x0c\x0e-\x1f]", "", text)

    return text.strip()


def main():
    if len(sys.argv) != 2:
        print("Usage:")
        print("  python src/extract_html.py v1")
        print("  python src/extract_html.py v2")
        sys.exit(1)

    version = sys.argv[1].lower()

    if version not in {"v1", "v2"}:
        print("Error: version must be 'v1' or 'v2'.")
        sys.exit(1)

    input_file = DATA_DIR / f"{version}_official.html"
    output_file = OUTPUT_DIR / f"{version}_official_text.txt"

    if not input_file.exists():
        raise FileNotFoundError(
            f"Could not find {version.upper()} HTML file: {input_file}"
        )

    print(f"Reading {version.upper()} official HTML...")

    text = extract_html_text(input_file)

    OUTPUT_DIR.mkdir(
        parents=True,
        exist_ok=True,
    )

    output_file.write_text(
        text,
        encoding="utf-8",
    )

    print(f"Extracted {len(text):,} characters.")
    print(f"Saved clean {version.upper()} text to: {output_file}")


if __name__ == "__main__":
    main()
