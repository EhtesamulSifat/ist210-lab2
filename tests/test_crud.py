"""Route tests for app.py.

These run WITHOUT Azure: pyodbc is replaced by a thin shim over SQLite that has
the same four tables, foreign keys (ON DELETE NO ACTION), CHECK and UNIQUE
constraints. They verify the app's logic (routes, validation, FK handling,
parameterization, CSRF). They do not replace a live run against Azure SQL.

Run:  python tests/test_crud.py
"""
import os
import re
import sqlite3
import sys
import tempfile
import types
from decimal import Decimal

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_FILE = os.path.join(tempfile.mkdtemp(), "uni.db")

# ---- pyodbc shim -----------------------------------------------------------
sqlite3.register_adapter(Decimal, float)


class _Cursor:
    def __init__(self, cur):
        self._cur = cur

    def execute(self, sql, params=()):
        # T-SQL "INSERT ... OUTPUT INSERTED.x VALUES (...)" -> SQLite RETURNING
        sql = re.sub(r"OUTPUT INSERTED\.(\w+)\s+(VALUES\s*\(.*?\))",
                     r"\2 RETURNING \1", sql, flags=re.S)
        self._cur.execute(sql, list(params))
        return self

    def __getattr__(self, name):
        return getattr(self._cur, name)


class _Connection:
    def __init__(self):
        self._conn = sqlite3.connect(DB_FILE)
        self._conn.execute("PRAGMA foreign_keys = ON")

    def cursor(self):
        return _Cursor(self._conn.cursor())

    def commit(self):
        self._conn.commit()

    def rollback(self):
        self._conn.rollback()

    def close(self):
        self._conn.close()


shim = types.ModuleType("pyodbc")
shim.Error = sqlite3.Error
shim.connect = lambda _conn_str: _Connection()
sys.modules["pyodbc"] = shim

# ---- schema (same shape as the Learn tutorial tables) ----------------------
setup = _Connection()
setup._conn.executescript(
    """
    CREATE TABLE Person (
        PersonId INTEGER PRIMARY KEY AUTOINCREMENT,
        FirstName TEXT NOT NULL, MiddleInitial TEXT, LastName TEXT NOT NULL,
        DateOfBirth TEXT NOT NULL);
    CREATE TABLE Student (
        StudentId INTEGER PRIMARY KEY AUTOINCREMENT,
        PersonId INTEGER REFERENCES Person (PersonId) ON DELETE NO ACTION,
        Email TEXT);
    CREATE TABLE Course (
        CourseId INTEGER PRIMARY KEY AUTOINCREMENT,
        Name TEXT NOT NULL, Teacher TEXT NOT NULL);
    CREATE TABLE Credit (
        StudentId INTEGER REFERENCES Student (StudentId) ON DELETE NO ACTION,
        CourseId INTEGER REFERENCES Course (CourseId) ON DELETE NO ACTION,
        Grade REAL CHECK (Grade <= 100.00),
        Attempt INTEGER,
        CONSTRAINT UQ_studentgrades UNIQUE (StudentId, CourseId, Grade, Attempt));
    """
)
setup.commit()
setup.close()

os.environ.update(DB_SERVER="x", DB_NAME="x", DB_USER="x", DB_PASSWORD="x")
sys.path.insert(0, ROOT)
import app as lab  # noqa: E402

lab.app.config["TESTING"] = True
client = lab.app.test_client()
TOKEN = "test-token"
with client.session_transaction() as sess:
    sess["csrf_token"] = TOKEN

results = []


def check(name, cond):
    results.append((name, bool(cond)))
    print(("PASS " if cond else "FAIL ") + name)


def post(url, **data):
    data["csrf_token"] = TOKEN
    return client.post(url, data=data, follow_redirects=True).get_data(as_text=True)


def get(url):
    return client.get(url, follow_redirects=True).get_data(as_text=True)


def n(sql, params=()):
    return lab.query(sql, params)[0]["n"]


# ---------------------------------------------------------------- empty state
check("all pages load on an empty database",
      all(client.get(u).status_code == 200 for u in
          ("/", "/students", "/courses", "/students/new", "/courses/new", "/credits/new")))

# ----------------------------------------------------------------------- CSRF
r = client.post("/courses/new", data={"name": "X", "teacher": "Y"})
check("POST without CSRF token is rejected (400), nothing written",
      r.status_code == 400 and n("SELECT COUNT(*) AS n FROM Course") == 0)

# --------------------------------------------------------------------- CREATE
post("/courses/new", name="Intro to Databases", teacher="Dominick Pope")
post("/courses/new", name="Network Security", teacher="Priya Raman")
check("create courses", n("SELECT COUNT(*) AS n FROM Course") == 2)

post("/students/new", first_name="Noe", middle_initial="", last_name="Coleman",
     date_of_birth="2003-04-12", email="noe@example.edu")
post("/students/new", first_name="Avery", middle_initial="J", last_name="Brooks",
     date_of_birth="2002-09-30", email="")
check("create students writes Person AND Student rows",
      n("SELECT COUNT(*) AS n FROM Person") == 2 and n("SELECT COUNT(*) AS n FROM Student") == 2
      and n("SELECT COUNT(*) AS n FROM Student s JOIN Person p ON p.PersonId = s.PersonId") == 2)

post("/credits/new", student_id="1", course_id="1", grade="88.5", attempt="1")
post("/credits/new", student_id="1", course_id="2", grade="72", attempt="1")
post("/credits/new", student_id="2", course_id="1", grade="", attempt="")
check("create enrollments (including blank grade/attempt -> NULL)",
      n("SELECT COUNT(*) AS n FROM Credit") == 3
      and n("SELECT COUNT(*) AS n FROM Credit WHERE Grade IS NULL AND Attempt IS NULL") == 1)

# ----------------------------------------------------------------------- READ
html = get("/")
check("dashboard 4-table JOIN shows student, email, course, teacher, grade",
      "Noe Coleman" in html and "noe@example.edu" in html and "Intro to Databases" in html
      and "Dominick Pope" in html and "88.50" in html)
check("dashboard stats are correct", ">3<" in html and ">2<" in html)
html = get("/?q=Pope")
check("search by teacher filters rows",
      "Dominick Pope" in html and "Priya Raman" not in html and "Avery" in html)
check("search with no match shows empty message", "No matches." in get("/?q=zzzz"))
check("students and courses lists show enrollment counts",
      "Coleman" in get("/students") and "Priya Raman" in get("/courses"))

# --------------------------------------------------------------------- UPDATE
post("/credits/edit?sid=1&cid=1&grade=88.50&attempt=1",
     student_id="1", course_id="2", grade="90", attempt="2")
check("update enrollment changes the row identified by its 4-column key",
      n("SELECT COUNT(*) AS n FROM Credit WHERE StudentId=1 AND CourseId=2 AND Grade=90 AND Attempt=2") == 1
      and n("SELECT COUNT(*) AS n FROM Credit") == 3)
post("/credits/edit?sid=2&cid=1&grade=&attempt=",
     student_id="2", course_id="1", grade="65.25", attempt="1")
check("update a NULL-grade enrollment (key with blanks)",
      n("SELECT COUNT(*) AS n FROM Credit WHERE StudentId=2 AND Grade=65.25") == 1)
post("/students/1/edit", first_name="Noe", middle_initial="Q", last_name="Coleman-Reid",
     date_of_birth="2003-04-12", email="noe.new@example.edu")
row = lab.query("SELECT p.LastName, p.MiddleInitial, s.Email FROM Student s "
                "JOIN Person p ON p.PersonId = s.PersonId WHERE s.StudentId = 1")[0]
check("update student updates Person and Student",
      row["LastName"] == "Coleman-Reid" and row["MiddleInitial"] == "Q"
      and row["Email"] == "noe.new@example.edu")
post("/courses/1/edit", name="Intro to Databases II", teacher="Dominick Pope")
check("update course", n("SELECT COUNT(*) AS n FROM Course WHERE Name = 'Intro to Databases II'") == 1)
check("edit pages pre-fill current values",
      "Coleman-Reid" in get("/students/1/edit") and "Databases II" in get("/courses/1/edit"))

# ------------------------------------------------------- VALIDATION / CHECK / FK
before = n("SELECT COUNT(*) AS n FROM Credit")
for label, form in {
    "grade above 100": dict(student_id="1", course_id="1", grade="101", attempt="1"),
    "negative grade": dict(student_id="1", course_id="1", grade="-5", attempt="1"),
    "non-numeric grade": dict(student_id="1", course_id="1", grade="abc", attempt="1"),
    "attempt 0": dict(student_id="1", course_id="1", grade="50", attempt="0"),
    "attempt 300 (TINYINT overflow)": dict(student_id="1", course_id="1", grade="50", attempt="300"),
    "garbage student id": dict(student_id="abc", course_id="1", grade="50", attempt="1"),
}.items():
    post("/credits/new", **form)
    check(f"rejected by app validation: {label}", n("SELECT COUNT(*) AS n FROM Credit") == before)

try:
    lab.execute("INSERT INTO Credit (StudentId, CourseId, Grade, Attempt) VALUES (1, 1, 150, 1)")
    check("database CHECK constraint rejects grade 150", False)
except lab.pyodbc.Error as exc:
    check("database CHECK constraint rejects grade 150 (app maps it to a message)",
          "CHECK constraint" in lab.db_error_message(exc))

html = post("/credits/new", student_id="1", course_id="2", grade="90", attempt="2")
check("duplicate enrollment rejected by UNIQUE constraint, friendly message",
      "already exists" in html and n("SELECT COUNT(*) AS n FROM Credit") == before)
html = post("/credits/new", student_id="999", course_id="1", grade="80", attempt="1")
check("enrollment for a nonexistent student rejected by FK, friendly message",
      "foreign key" in html and n("SELECT COUNT(*) AS n FROM Credit") == before)
html = post("/students/new", first_name="Future", last_name="Kid",
            date_of_birth="2999-01-01", email="")
check("future date of birth rejected", "must be in the past" in html
      and n("SELECT COUNT(*) AS n FROM Person") == 2)
html = post("/students/new", first_name="No", last_name="At", date_of_birth="2000-01-01",
            email="not-an-email")
check("bad email rejected", "must contain an @" in html and n("SELECT COUNT(*) AS n FROM Person") == 2)
html = post("/courses/new", name="N" * 51, teacher="T")
check("over-length course name rejected before hitting the database",
      "at most 50" in html and n("SELECT COUNT(*) AS n FROM Course") == 2)

# ------------------------------------------- DELETE: confirm page + FK protection
html = get("/students/1/delete")
check("student delete confirm page warns about dependent enrollments",
      "2 enrollment record(s)" in html and "ON DELETE NO ACTION" in html)
html = post("/students/1/delete")
check("delete student WITH enrollments is blocked by the database; everything intact",
      "Delete blocked by referential integrity" in html
      and n("SELECT COUNT(*) AS n FROM Student WHERE StudentId = 1") == 1
      and n("SELECT COUNT(*) AS n FROM Person") == 2)
html = post("/courses/1/delete")
check("delete course WITH enrollments is blocked by the database",
      "Delete blocked by referential integrity" in html
      and n("SELECT COUNT(*) AS n FROM Course WHERE CourseId = 1") == 1)

# transaction rollback
def failing_work(cur):
    cur.execute("INSERT INTO Person (FirstName, LastName, DateOfBirth) VALUES ('Tmp','Row','2000-01-01')")
    raise RuntimeError("boom")


try:
    lab.transaction(failing_work)
except RuntimeError:
    pass
check("transaction() rolls back partial work on error",
      n("SELECT COUNT(*) AS n FROM Person WHERE FirstName = 'Tmp'") == 0)

# ----------------------------------------------------------- SQL injection
evil = "x'); DROP TABLE Credit;--"
check("injection in search box is inert", client.get("/", query_string={"q": evil}).status_code == 200
      and n("SELECT COUNT(*) AS n FROM Credit") == before)
post("/courses/new", name=evil[:50], teacher="' OR '1'='1")
check("injection payload saved as plain text; tables intact",
      n("SELECT COUNT(*) AS n FROM Course WHERE Teacher = ?", ("' OR '1'='1",)) == 1
      and n("SELECT COUNT(*) AS n FROM Credit") == before)
check("injection in credit key params -> 404, not SQL",
      client.get("/credits/edit", query_string={"sid": "1' OR '1'='1", "cid": "1"}).status_code == 404)

# -------------------------------------------------------------- DELETE (allowed)
check("enrollment delete shows a confirm page first",
      "Delete enrollment record" in get("/credits/delete?sid=1&cid=2&grade=90.00&attempt=2"))
post("/credits/delete?sid=1&cid=2&grade=90.00&attempt=2")
check("enrollment deleted by its 4-column key - exactly that one row, sibling row untouched",
      n("SELECT COUNT(*) AS n FROM Credit WHERE StudentId=1 AND CourseId=2 AND Grade=90 AND Attempt=2") == 0
      and n("SELECT COUNT(*) AS n FROM Credit WHERE StudentId=1 AND CourseId=2 AND Grade=72 AND Attempt=1") == 1
      and n("SELECT COUNT(*) AS n FROM Credit") == before - 1)

# student 1 still has the other enrollment -> remove it, then the student can go
lab.execute("DELETE FROM Credit WHERE StudentId = 1")
html = post("/students/1/delete")
check("delete student with NO enrollments succeeds and removes Person too",
      n("SELECT COUNT(*) AS n FROM Student WHERE StudentId = 1") == 0
      and n("SELECT COUNT(*) AS n FROM Person WHERE LastName = 'Coleman-Reid'") == 0)
lab.execute("DELETE FROM Credit")
post("/courses/2/delete")
check("delete course with no enrollments succeeds",
      n("SELECT COUNT(*) AS n FROM Course WHERE CourseId = 2") == 0)

# --------------------------------------------------------------- misc routes
check("missing records redirect cleanly",
      all(client.get(u).status_code == 302 for u in
          ("/students/999/edit", "/courses/999/edit", "/students/999/delete", "/courses/999/delete")))
check("missing credit key params -> 404", client.get("/credits/edit").status_code == 404)

failed = [name for name, ok in results if not ok]
print(f"\n{len(results) - len(failed)}/{len(results)} checks passed")
sys.exit(1 if failed else 0)
