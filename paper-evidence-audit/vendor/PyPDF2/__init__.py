"""
PyPDF2 is a free and open-source pure-python PDF library capable of splitting,
merging, cropping, and transforming the pages of PDF files. It can also add
custom data, viewing options, and passwords to PDF files. PyPDF2 can retrieve
text and metadata from PDFs as well.

You can read the full docs at https://pypdf2.readthedocs.io/.
"""

# Vendored reader-only copy for section-evidence-notes. Writer, merger,
# page-range and paper-size helpers are removed to keep the package within
# the 50-text-file submission limit; text extraction uses PdfReader only.
from ._encryption import PasswordType
from ._page import PageObject, Transformation
from ._reader import DocumentInformation, PdfFileReader, PdfReader
from ._version import __version__

__all__ = [
    "__version__",
    "DocumentInformation",
    "PdfFileReader",
    "PdfReader",
    "Transformation",
    "PageObject",
    "PasswordType",
]
