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

BUILD_FAILED = MessageTemplate(
    name="BUILD_FAILED",
    he="הפרויקט לא עובר בנייה. שגיאת הקומפילציה הראשונה: {error}",
    en="The project does not build. First compiler error: {error}",
)

BUILD_FAILED_NO_DETAIL = MessageTemplate(
    name="BUILD_FAILED_NO_DETAIL",
    he="הפרויקט לא עובר בנייה. הפרטים נמצאים ביומן הבנייה.",
    en="The project does not build. The build log has the details.",
)

STARTUP_CRASHED = MessageTemplate(
    name="STARTUP_CRASHED",
    he="הפרויקט נבנה, אבל קורס בעלייה. החריגה: {exception}",
    en="The project builds but crashes on startup. The exception: {exception}",
)

STARTUP_CRASHED_NO_DETAIL = MessageTemplate(
    name="STARTUP_CRASHED_NO_DETAIL",
    he="הפרויקט נבנה, אבל קורס בעלייה. הפרטים נמצאים ביומן ההרצה.",
    en="The project builds but crashes on startup. The run log has the details.",
)

HTTP_PROBES_FAILED = MessageTemplate(
    name="HTTP_PROBES_FAILED",
    he="בקשות HTTP שלא החזירו את הסטטוס הצפוי:\n{probes}",
    en="HTTP requests that did not return the expected status:\n{probes}",
)

PROBE_WRONG_STATUS = MessageTemplate(
    name="PROBE_WRONG_STATUS",
    he="{method} {path}: צפוי {expected}, התקבל {actual}",
    en="{method} {path}: expected {expected}, got {actual}",
)

PROBE_NO_ANSWER = MessageTemplate(
    name="PROBE_NO_ANSWER",
    he="{method} {path}: צפוי {expected}, לא התקבלה תשובה",
    en="{method} {path}: expected {expected}, no answer",
)

STATUS_SEPARATOR = MessageTemplate(name="STATUS_SEPARATOR", he=" או ", en=" or ")

ALL_TEMPLATES: tuple[MessageTemplate, ...] = (
    FILE_MISSING,
    FILE_TOO_FEW,
    FOLDER_MISSING,
    BUILD_FAILED,
    BUILD_FAILED_NO_DETAIL,
    STARTUP_CRASHED,
    STARTUP_CRASHED_NO_DETAIL,
    HTTP_PROBES_FAILED,
    PROBE_WRONG_STATUS,
    PROBE_NO_ANSWER,
    STATUS_SEPARATOR,
)
