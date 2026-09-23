from pathlib import Path
from pypdf import PdfReader


def extract_pdf_text(pdf_path: str) -> str:
    """
    Extract text from every page of the PDF.
    Keep page numbers so we can trace AI results
    back to the original document later.
    """
    reader = PdfReader(pdf_path)

    pages = []

    for page_number, page in enumerate(reader.pages, start=1):
        text = page.extract_text() or ""

        pages.append(
            f"\n\n--- PAGE {page_number} ---\n\n{text}"
        )

    return "".join(pages)


if __name__ == "__main__":
    project_dir = Path(__file__).resolve().parent.parent

    pdf_path = project_dir / "data" / "2025-16554.pdf"
    output_path = project_dir / "output" / "extracted_text.txt"

    print("Reading PDF...")

    text = extract_pdf_text(str(pdf_path))

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(text, encoding="utf-8")

    print(f"Extracted {len(text):,} characters.")
    print(f"Saved to {output_path}")