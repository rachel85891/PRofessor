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

PROJECT_TOO_FEW = MessageTemplate(
    name="PROJECT_TOO_FEW",
    he="נדרשו לפחות {min_count} פרויקטים (קבצי csproj.), ונמצאו {found}.",
    en="At least {min_count} projects (.csproj files) were expected; {found} found.",
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

HTTP_PROBES_WRONG_ANSWER = MessageTemplate(
    name="HTTP_PROBES_WRONG_ANSWER",
    he="בקשות HTTP שלא החזירו את התשובה הצפויה:\n{probes}",
    en="HTTP requests that did not return the expected answer:\n{probes}",
)

PROBE_WRONG_SHAPE = MessageTemplate(
    name="PROBE_WRONG_SHAPE",
    he="{method} {path}: צפוי {expected} ב-JSON, התקבל {actual}",
    en="{method} {path}: expected {expected} in JSON, got {actual}",
)

PROBE_PLAIN_VALUES_LIST = MessageTemplate(
    name="PROBE_PLAIN_VALUES_LIST",
    he=(
        "{method} {path}: הרשימה מחזירה ערכים פשוטים (למשל טקסט) במקום אובייקטים; "
        "כנראה זה עדיין הקוד של תבנית הפרויקט"
    ),
    en=(
        "{method} {path}: the list returns plain values (such as text) instead of objects; "
        "this is probably still the project template's code"
    ),
)

PROBE_MISSING_VALUES = MessageTemplate(
    name="PROBE_MISSING_VALUES",
    he="{method} {path}: התשובה אינה מכילה את {values}",
    en="{method} {path}: the response does not contain {values}",
)

STATUS_SEPARATOR = MessageTemplate(name="STATUS_SEPARATOR", he=" או ", en=" or ")

NAMING_TYPES = MessageTemplate(
    name="NAMING_TYPES",
    he=(
        "ב-C# שמות של מחלקות וטיפוסים נכתבים ב-PascalCase (אות ראשונה גדולה, בלי קו תחתון). "
        "לדוגמה: {examples}. סך הכול: {count}."
    ),
    en=(
        "In C#, class and type names are written in PascalCase (a capital first letter, no "
        "underscores). For example: {examples}. Total: {count}."
    ),
)

NAMING_PROPERTIES = MessageTemplate(
    name="NAMING_PROPERTIES",
    he="ב-C# מאפיינים ציבוריים נכתבים ב-PascalCase. לדוגמה: {examples}. סך הכול: {count}.",
    en="In C#, public properties are written in PascalCase. For example: {examples}. "
    "Total: {count}.",
)

NAMING_METHODS = MessageTemplate(
    name="NAMING_METHODS",
    he="ב-C# שמות של מתודות נכתבים ב-PascalCase. לדוגמה: {examples}. סך הכול: {count}.",
    en="In C#, method names are written in PascalCase. For example: {examples}. Total: {count}.",
)

NAMING_LOCALS = MessageTemplate(
    name="NAMING_LOCALS",
    he=(
        "ב-C# משתנים מקומיים נכתבים ב-camelCase (אות ראשונה קטנה). "
        "לדוגמה: {examples}. סך הכול: {count}."
    ),
    en=(
        "In C#, local variables are written in camelCase (a lowercase first letter). "
        "For example: {examples}. Total: {count}."
    ),
)

NAMING_FILES = MessageTemplate(
    name="NAMING_FILES",
    he=(
        "ב-C# כל קובץ נקרא על שם הטיפוס שהוא מגדיר, ב-PascalCase. "
        "לדוגמה: {examples}. סך הכול: {count}."
    ),
    en=(
        "In C#, each file is named after the type it declares, in PascalCase. "
        "For example: {examples}. Total: {count}."
    ),
)

NAMING_CONTROLLER_SUFFIX = MessageTemplate(
    name="NAMING_CONTROLLER_SUFFIX",
    he=(
        "ב-ASP.NET Core שם של מחלקת controller מסתיים ב-Controller. "
        "לדוגמה: {examples}. סך הכול: {count}."
    ),
    en=(
        "In ASP.NET Core, controller class names end with Controller. "
        "For example: {examples}. Total: {count}."
    ),
)

NAMING_EXAMPLE = MessageTemplate(
    name="NAMING_EXAMPLE",
    he="{name} → {suggestion} ({file}, שורה {line})",
    en="{name} → {suggestion} ({file}, line {line})",
)

NAMING_EXAMPLE_NO_SUGGESTION = MessageTemplate(
    name="NAMING_EXAMPLE_NO_SUGGESTION",
    he="{name} ({file}, שורה {line})",
    en="{name} ({file}, line {line})",
)

LOCATED_EXAMPLE = MessageTemplate(
    name="LOCATED_EXAMPLE",
    he="{item} ({file}, שורה {line})",
    en="{item} ({file}, line {line})",
)

GITIGNORE_MISSING = MessageTemplate(
    name="GITIGNORE_MISSING",
    he=(
        "בריפוזיטורי אין קובץ .gitignore, ולכן קבצי בנייה וקבצים של סביבת הפיתוח נכנסים "
        "אליו. כדאי להוסיף .gitignore{how_to_add}."
    ),
    en=(
        "The repository has no `.gitignore`, so build and IDE files end up in it. "
        "Add a `.gitignore`{how_to_add}."
    ),
)

GITIGNORE_NOT_COVERING = MessageTemplate(
    name="GITIGNORE_NOT_COVERING",
    he=(
        "אלה נמצאים בריפוזיטורי, ואף .gitignore לא משאיר אותם בחוץ: {examples}. "
        "סך הכול: {count}. כדאי להוסיף ל-.gitignore את {patterns}."
    ),
    en=(
        "These are in the repository and no `.gitignore` leaves them out: {examples}. "
        "Total: {count}. Add {patterns} to the `.gitignore`."
    ),
)

NOT_COMMITTED = MessageTemplate(
    name="NOT_COMMITTED",
    he=(
        "אלה נמצאים בריפוזיטורי, אבל לא אמורים להיות בו: {examples}. סך הכול: {count}. "
        "צריך להסיר אותם מהריפוזיטורי (למשל git rm -r --cached {first}); "
        "אחר כך ה-.gitignore ישאיר אותם בחוץ."
    ),
    en=(
        "These are in the repository but should not be committed: {examples}. "
        "Total: {count}. Remove them from the repository (for example "
        "`git rm -r --cached {first}`); the `.gitignore` keeps them out after that."
    ),
)

ABSOLUTE_PATHS = MessageTemplate(
    name="ABSOLUTE_PATHS",
    he=(
        "בקוד יש נתיב מלא על דיסק של המחשב, ולכן הפרויקט לא ירוץ במחשב אחר: {examples}. "
        "סך הכול: {count}. כדאי להשתמש בנתיב יחסי לפרויקט."
    ),
    en=(
        "The code uses a full path on a disk of your computer, so the project cannot run on "
        "another computer: {examples}. Total: {count}. Use a path relative to the project."
    ),
)

CLIENT_FILE_REFS = MessageTemplate(
    name="CLIENT_FILE_REFS",
    he="דפים מפנים לקבצים שלא קיימים, ולכן הדף לא יעבוד: {examples}. סך הכול: {count}.",
    en="Pages refer to files that do not exist, so the page breaks: {examples}. Total: {count}.",
)

CLIENT_REF_MISSING = MessageTemplate(
    name="CLIENT_REF_MISSING",
    he="{ref} ({file}, שורה {line}): אין קובץ כזה",
    en="{ref} ({file}, line {line}): no such file",
)

CLIENT_REF_CASE = MessageTemplate(
    name="CLIENT_REF_CASE",
    he=(
        "{ref} ({file}, שורה {line}): שם הקובץ הוא {actual}; על שרת יש הבדל בין "
        "אותיות גדולות לקטנות"
    ),
    en=(
        "{ref} ({file}, line {line}): the file is named {actual}; a server tells upper "
        "and lower case apart"
    ),
)

CLIENT_ELEMENT_IDS = MessageTemplate(
    name="CLIENT_ELEMENT_IDS",
    he=(
        "סקריפטים מחפשים אלמנט לפי id שאף דף שטוען אותם לא מגדיר, ולכן החיפוש מחזיר null: "
        "{examples}. סך הכול: {count}."
    ),
    en=(
        "Scripts look up an element id that no page loading them declares, so the lookup "
        "returns null: {examples}. Total: {count}."
    ),
)

CLIENT_FETCH_ROUTES = MessageTemplate(
    name="CLIENT_FETCH_ROUTES",
    he="בקשות בקוד הלקוח שלא מתאימות לאף נתיב שה-API מגדיר: {examples}. סך הכול: {count}.",
    en="Requests in the client code that match no route the API declares: {examples}. "
    "Total: {count}.",
)


SHAPE_NAMES: dict[str, Localized] = {
    "object": Localized(he="אובייקט", en="an object"),
    "array_of_objects": Localized(he="רשימה של אובייקטים", en="a list of objects"),
    "array_of_scalars": Localized(he="רשימה של ערכים פשוטים", en="a list of plain values"),
    "empty_array": Localized(he="רשימה ריקה", en="an empty list"),
    "scalar": Localized(he="ערך בודד", en="a single value"),
    "not_json": Localized(he="תשובה שאינה JSON", en="a response that is not JSON"),
    "empty": Localized(he="תשובה ריקה", en="an empty response"),
}
"""How a response shape is named inside the messages above."""

DI_STARTED = MessageTemplate(
    name="DI_STARTED",
    he="הזרקת תלויות התחילה ({registrations} רישומים, {constructors} בנאים שמקבלים ממשק).",
    en="DI has started ({registrations} registrations, {constructors} constructors taking an "
    "interface).",
)

DI_NOT_STARTED = MessageTemplate(
    name="DI_NOT_STARTED",
    he=(
        "הזרקת תלויות לא התחילה: נמצאו {registrations} רישומים (לפחות {min_registrations}); "
        "נמצאו {constructors} בנאים שמקבלים ממשק (לפחות {min_constructors})."
    ),
    en=(
        "DI has not started: {registrations} registrations (at least {min_registrations}) and "
        "{constructors} constructors taking an interface (at least {min_constructors})."
    ),
)

LAYER_PROJECTS_MISSING = MessageTemplate(
    name="LAYER_PROJECTS_MISSING",
    he=(
        "הקוד עדיין לא מחולק לפרויקטים של שכבות: לא נמצא פרויקט עבור {missing}. "
        "הפרויקטים שנמצאו: {projects}.{unassigned}"
    ),
    en=(
        "The code is not split into the layer projects yet: no project was found for "
        "{missing}. Projects found: {projects}.{unassigned}"
    ),
)

LAYER_PROJECTS_FOUND = MessageTemplate(
    name="LAYER_PROJECTS_FOUND",
    he="פרויקטי השכבות: {layers}.{unassigned}",
    en="Layer projects: {layers}.{unassigned}",
)

LAYER_UNASSIGNED = MessageTemplate(
    name="LAYER_UNASSIGNED",
    he=" בלי שכבה ברורה לפי השם: {projects}.",
    en=" With no clear layer by name: {projects}.",
)

LAYER_REFERENCES = MessageTemplate(
    name="LAYER_REFERENCES",
    he="ההפניות בין הפרויקטים לא תואמות את השכבות: {examples}. סך הכול: {count}.",
    en="The project references do not follow the layers: {examples}. Total: {count}.",
)

LAYER_REFERENCE_MISSING = MessageTemplate(
    name="LAYER_REFERENCE_MISSING",
    he="{source} לא מפנה ל-{target} (נדרש {source_role} → {target_role})",
    en="{source} does not reference {target} ({source_role} → {target_role} is required)",
)

LAYER_REFERENCE_MISSING_ANY_PATH = MessageTemplate(
    name="LAYER_REFERENCE_MISSING_ANY_PATH",
    he=(
        "{source} לא מפנה ל-{target}, לא ישירות ולא דרך שכבה אחרת "
        "(נדרש {source_role} → {target_role})"
    ),
    en=(
        "{source} doesn't reference {target}, directly or through another layer "
        "({source_role} → {target_role} is required)"
    ),
)

LAYER_REFERENCE_FORBIDDEN = MessageTemplate(
    name="LAYER_REFERENCE_FORBIDDEN",
    he="{source} מפנה ל-{target} (אסור {source_role} → {target_role})",
    en="{source} references {target} ({source_role} → {target_role} is not allowed)",
)

CODE_OUTSIDE_LAYER = MessageTemplate(
    name="CODE_OUTSIDE_LAYER",
    he=(
        "הקוד הזה שייך רק לשכבת {allowed}, אבל הוא נמצא גם בפרויקטים אחרים: {examples}. "
        "סך הכול: {count}."
    ),
    en=(
        "This code belongs only in the {allowed} layer, but it is also in other projects: "
        "{examples}. Total: {count}."
    ),
)

NO_INTERFACE = MessageTemplate(
    name="NO_INTERFACE",
    he=(
        "מחלקות בשכבת {role} שלא מממשות אף ממשק שמוגדר בריפוזיטורי: {examples}. "
        "סך הכול: {count}.{ambiguous}"
    ),
    en=(
        "Classes in the {role} layer that implement no interface declared in the "
        "repository: {examples}. Total: {count}.{ambiguous}"
    ),
)

CONSTRUCTOR_TAKES_CLASS = MessageTemplate(
    name="CONSTRUCTOR_TAKES_CLASS",
    he=(
        "בנאים בשכבת {consumer} מקבלים מחלקה משכבת {dependency} במקום הממשק שלה: "
        "{examples}. סך הכול: {count}.{ambiguous}"
    ),
    en=(
        "Constructors in the {consumer} layer take a {dependency} class instead of its "
        "interface: {examples}. Total: {count}.{ambiguous}"
    ),
)

TAKES_CLASS_EXAMPLE = MessageTemplate(
    name="TAKES_CLASS_EXAMPLE",
    he="{consumer} מקבל {taken}; צריך לקבל {interface}",
    en="{consumer} takes {taken}; take {interface}",
)

INTERFACES_UNREGISTERED = MessageTemplate(
    name="INTERFACES_UNREGISTERED",
    he=(
        "בנאים מקבלים ממשק שלא נרשם (AddScoped, AddTransient או AddSingleton עם הממשק "
        "הזה): {examples}. סך הכול: {count}.{untyped}{ambiguous}"
    ),
    en=(
        "Constructors take an interface that is not registered (AddScoped, AddTransient or "
        "AddSingleton with that interface): {examples}. Total: {count}.{untyped}{ambiguous}"
    ),
)

UNREGISTERED_EXAMPLE = MessageTemplate(
    name="UNREGISTERED_EXAMPLE",
    he="{consumer} מקבל {interface}",
    en="{consumer} takes {interface}",
)

UNTYPED_REGISTRATIONS = MessageTemplate(
    name="UNTYPED_REGISTRATIONS",
    he=" ב-{count} רישומים לא כתוב סוג (factory או מפתח), ולכן כדאי לבדוק את אלה ידנית.",
    en=" {count} registrations name no type (a factory or a key), so check these by hand.",
)

CONCRETE_CONSTRUCTION = MessageTemplate(
    name="CONCRETE_CONSTRUCTION",
    he=(
        "מחלקות יוצרות עם new מחלקה משכבת {of} במקום לקבל את הממשק שלה בבנאי: "
        "{examples}. סך הכול: {count}.{ambiguous}"
    ),
    en=(
        "Classes create a {of} class with new instead of receiving its interface through "
        "the constructor: {examples}. Total: {count}.{ambiguous}"
    ),
)

CREATION_EXAMPLE = MessageTemplate(
    name="CREATION_EXAMPLE",
    he="{creator} יוצר {created}",
    en="{creator} creates {created}",
)

AMBIGUOUS_NAMES = MessageTemplate(
    name="AMBIGUOUS_NAMES",
    he=" לא נבדק, כי השם מוגדר יותר מפעם אחת: {names}.",
    en=" Not checked, because the name is declared more than once: {names}.",
)

ALL_TEMPLATES: tuple[MessageTemplate, ...] = (
    FILE_MISSING,
    FILE_TOO_FEW,
    FOLDER_MISSING,
    PROJECT_TOO_FEW,
    BUILD_FAILED,
    BUILD_FAILED_NO_DETAIL,
    STARTUP_CRASHED,
    STARTUP_CRASHED_NO_DETAIL,
    HTTP_PROBES_FAILED,
    PROBE_WRONG_STATUS,
    PROBE_NO_ANSWER,
    HTTP_PROBES_WRONG_ANSWER,
    PROBE_WRONG_SHAPE,
    PROBE_PLAIN_VALUES_LIST,
    PROBE_MISSING_VALUES,
    STATUS_SEPARATOR,
    NAMING_TYPES,
    NAMING_PROPERTIES,
    NAMING_METHODS,
    NAMING_LOCALS,
    NAMING_FILES,
    NAMING_CONTROLLER_SUFFIX,
    NAMING_EXAMPLE,
    NAMING_EXAMPLE_NO_SUGGESTION,
    LOCATED_EXAMPLE,
    GITIGNORE_MISSING,
    GITIGNORE_NOT_COVERING,
    NOT_COMMITTED,
    ABSOLUTE_PATHS,
    CLIENT_FILE_REFS,
    CLIENT_REF_MISSING,
    CLIENT_REF_CASE,
    CLIENT_ELEMENT_IDS,
    CLIENT_FETCH_ROUTES,
    DI_STARTED,
    DI_NOT_STARTED,
    LAYER_PROJECTS_MISSING,
    LAYER_PROJECTS_FOUND,
    LAYER_UNASSIGNED,
    LAYER_REFERENCES,
    LAYER_REFERENCE_MISSING,
    LAYER_REFERENCE_MISSING_ANY_PATH,
    LAYER_REFERENCE_FORBIDDEN,
    CODE_OUTSIDE_LAYER,
    NO_INTERFACE,
    CONSTRUCTOR_TAKES_CLASS,
    TAKES_CLASS_EXAMPLE,
    INTERFACES_UNREGISTERED,
    UNREGISTERED_EXAMPLE,
    UNTYPED_REGISTRATIONS,
    CONCRETE_CONSTRUCTION,
    CREATION_EXAMPLE,
    AMBIGUOUS_NAMES,
)
