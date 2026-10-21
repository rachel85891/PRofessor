"""Every finding text of the deterministic checks, in Hebrew (reviewer) and English (student).

This is the only module that holds finding wording, so it can be reviewed and edited in one
place. Messages are short and factual: what was expected and what was found.
"""

from dataclasses import dataclass

FieldValue = str | int


@dataclass(frozen=True)
class Localized:
    """A text in both languages."""

    he: str
    en: str


@dataclass(frozen=True)
class MessageTemplate:
    """A message with named `{fields}`, in Hebrew and English."""

    name: str
    he: str
    en: str

    def render(self, **fields: FieldValue | Localized) -> Localized:
        """Fill the fields; a `Localized` field contributes its text in the matching language."""
        return Localized(
            he=self.he.format(
                **{k: v.he if isinstance(v, Localized) else v for k, v in fields.items()}
            ),
            en=self.en.format(
                **{k: v.en if isinstance(v, Localized) else v for k, v in fields.items()}
            ),
        )


FILE_MISSING = MessageTemplate(
    name="FILE_MISSING",
    he="לא נמצא בריפוזיטורי קובץ שמתאים לתבנית {glob}.",
    en="No file matching {glob} was found in the repository.",
)

FILE_TOO_FEW = MessageTemplate(
    name="FILE_TOO_FEW",
    he="נדרשו לפחות {min_count} קבצים שמתאימים לתבנית {glob}, ונמצאו {found}.",
    en="At least {min_count} files matching {glob} were expected; {found} found.",
)

FOLDER_MISSING = MessageTemplate(
    name="FOLDER_MISSING",
    he="לא נמצאה בריפוזיטורי תיקייה בשם {path}.",
    en="No folder named {path} was found in the repository.",
)

ALL_TEMPLATES: tuple[MessageTemplate, ...] = (FILE_MISSING, FILE_TOO_FEW, FOLDER_MISSING)
