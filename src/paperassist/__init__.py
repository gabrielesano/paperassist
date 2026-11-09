"""PaperAssist: a local research paper reader."""
import logging

__version__ = "0.2.0a1"


def _allow_pypdf_warning(record):
    # fontTools is intentionally omitted; suppress only pypdf's per-font CFF notice.
    return not record.getMessage().startswith(
        "fontTools is required to fully parse the encoding of a CFF Type1 font"
    )


logging.getLogger("pypdf._cmap").addFilter(_allow_pypdf_warning)
