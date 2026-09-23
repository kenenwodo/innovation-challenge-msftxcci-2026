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
    pdf_path = "data/2025-16554.pdf"

    print("Reading PDF...")

    text = extract_pdf_text(pdf_path)

    output_path = Path("output/extracted_text.txt")
    output_path.write_text(text, encoding="utf-8")

    print(f"Extracted {len(text):,} characters.")
    print(f"Saved to {output_path}")