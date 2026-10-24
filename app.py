"""IST 210 Lab 2 - University CRUD app (Flask + pyodbc + Azure SQL Database).

Runs against the four tables from the Lab 1 Microsoft Learn tutorial:
Person, Student, Course and Credit (Credit is the operational table that links
a student to a course with a grade and attempt number).

Security / integrity notes
  * Every SQL statement is parameterized (? placeholders). User input is never
    concatenated into SQL text; the only SQL assembled in Python is fixed
    fragments chosen by code (e.g. "Grade IS NULL" vs "Grade = ?").
  * Credentials come from environment variables (.env), never from source code.
  * POST requests require a CSRF token.
  * Foreign keys use ON DELETE NO ACTION, so the database itself refuses to
    delete a parent row that still has children. The app shows a confirmation
    page first, then lets the database enforce the rule and reports the result.
"""
import hmac
import os
import secrets
import time
from datetime import date
from decimal import Decimal, InvalidOperation

import pyodbc
from dotenv import load_dotenv
from flask import (Flask, abort, flash, redirect, render_template, request,
                   session, url_for)

load_dotenv()

app = Flask(__name__)
app.secret_key = os.environ.get("FLASK_SECRET_KEY") or secrets.token_hex(32)

REQUIRED_ENV = ("DB_SERVER", "DB_NAME", "DB_USER", "DB_PASSWORD")


class ConfigError(Exception):
    """A required setting is missing from the environment / .env file."""


class ValidationError(ValueError):
    """User input failed validation; the message is safe to show to the user."""


# --------------------------------------------------------------------------
# Database helpers
# --------------------------------------------------------------------------
def _is_transient(exc):
    """Errors worth retrying: serverless database resuming, or a timeout."""
    text = str(exc).lower()
    return "40613" in text or "timeout" in text or "timed out" in text


def get_connection():
    """Connect to Azure SQL with settings from environment variables.

    The serverless tier auto-pauses; the first connection after a pause can fail
    while the database resumes, so transient failures are retried.
    """
    missing = [k for k in REQUIRED_ENV if not os.environ.get(k)]
    if missing:
        raise ConfigError("Missing settings in .env: " + ", ".join(missing))

    password = os.environ["DB_PASSWORD"].replace("}", "}}")  # ODBC brace escaping
    conn_str = (
        f"DRIVER={{{os.environ.get('DB_DRIVER', 'ODBC Driver 18 for SQL Server')}}};"
        f"SERVER={os.environ['DB_SERVER']},1433;"
        f"DATABASE={os.environ['DB_NAME']};"
        f"UID={os.environ['DB_USER']};"
        f"PWD={{{password}}};"
        "Encrypt=yes;TrustServerCertificate=no;Connection Timeout=30;"
    )
    attempts = 4
    for attempt in range(1, attempts + 1):
        try:
            return pyodbc.connect(conn_str)
        except pyodbc.Error as exc:
            if attempt == attempts or not _is_transient(exc):
                raise
            time.sleep(10)


def query(sql, params=()):
    """Run a SELECT and return a list of dicts."""
    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute(sql, params)
        cols = [c[0] for c in cur.description]
        return [dict(zip(cols, row)) for row in cur.fetchall()]
    finally:
        conn.close()


def transaction(work):
    """Run work(cursor) in one transaction: commit on success, roll back on error."""
    conn = get_connection()
    try:
        cur = conn.cursor()
        result = work(cur)
        conn.commit()
        return result
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def execute(sql, params=()):
    """Run one INSERT/UPDATE/DELETE in its own transaction; returns rows affected."""
    return transaction(lambda cur: cur.execute(sql, params).rowcount)


def db_error_message(exc, action="write"):
    """Translate a database error into a message that is safe to show."""
    text = str(exc).upper()
    if "REFERENCE CONSTRAINT" in text or "FOREIGN KEY" in text:
        if action == "delete":
            return ("Delete blocked by referential integrity: other rows still "
                    "reference this record (ON DELETE NO ACTION). Remove or "
                    "reassign those rows first.")
        return ("Rejected by a foreign key: the selected student or course does "
                "not exist.")
    if "CHECK CONSTRAINT" in text:
        return "Rejected by a CHECK constraint: a grade can't be more than 100."
    if "UNIQUE" in text or "DUPLICATE KEY" in text:
        return "That exact student / course / grade / attempt record already exists."
    app.logger.error("Unexpected database error: %s", exc)
    return "Unexpected database error. Check the server console for details."


# --------------------------------------------------------------------------
# Input validation
# --------------------------------------------------------------------------
def clean_text(form, key, label, max_len, required=True):
    value = (form.get(key) or "").strip()
    if required and not value:
        raise ValidationError(f"{label} is required.")
    if len(value) > max_len:
        raise ValidationError(f"{label} must be at most {max_len} characters.")
    return value or None


def clean_int(form, key, label):
    try:
        return int(form.get(key, ""))
    except (TypeError, ValueError):
        raise ValidationError(f"Please choose a valid {label}.")


def clean_grade(raw):
    """Blank -> NULL. Otherwise a number from 0 to 100 with 2 decimals."""
    raw = (raw or "").strip()
    if raw == "":
        return None
    try:
        grade = Decimal(raw)
    except InvalidOperation:
        raise ValidationError("Grade must be a number.")
    if not grade.is_finite() or grade < 0 or grade > 100:
        raise ValidationError("Grade must be between 0 and 100.")
    return grade.quantize(Decimal("0.01"))


def clean_attempt(raw):
    """Blank -> NULL. Otherwise 1-255 (the column is TINYINT)."""
    raw = (raw or "").strip()
    if raw == "":
        return None
    try:
        attempt = int(raw)
    except ValueError:
        raise ValidationError("Attempt must be a whole number.")
    if not 1 <= attempt <= 255:
        raise ValidationError("Attempt must be between 1 and 255.")
    return attempt


def clean_dob(raw):
    try:
        dob = date.fromisoformat((raw or "").strip())
    except ValueError:
        raise ValidationError("Date of birth must be a valid date.")
    if dob >= date.today():
        raise ValidationError("Date of birth must be in the past.")
    return dob.isoformat()


def clean_email(form):
    email = clean_text(form, "email", "Email", 256, required=False)
    if email and "@" not in email:
        raise ValidationError("Email must contain an @.")
    return email


# --------------------------------------------------------------------------
# CSRF protection, template helpers, error pages
# --------------------------------------------------------------------------
def csrf_token():
    if "csrf_token" not in session:
        session["csrf_token"] = secrets.token_hex(16)
    return session["csrf_token"]


app.jinja_env.globals["csrf_token"] = csrf_token


@app.before_request
def csrf_protect():
    if request.method == "POST":
        sent = request.form.get("csrf_token", "")
        expected = session.get("csrf_token", "")
        if not expected or not hmac.compare_digest(sent, expected):
            abort(400, "Invalid or missing CSRF token. Reload the page and try again.")


@app.template_filter("grade")
def grade_filter(value):
    return "-" if value is None else f"{float(value):.2f}"


@app.errorhandler(ConfigError)
def config_error(exc):
    return render_template("error.html", message=str(exc)), 500


@app.errorhandler(pyodbc.Error)
def database_unreachable(exc):
    app.logger.error("Database error: %s", exc)
    return render_template(
        "error.html",
        message=("Could not talk to the database. Check your .env values, that "
                 "your current IP is allowed in the Azure server firewall, and "
                 "give a paused serverless database about a minute to wake up."),
    ), 503


# --------------------------------------------------------------------------
# Credit keys. The Credit table has no single-column key, so a row is identified
# by all four columns of its UNIQUE constraint (blank = NULL).
# --------------------------------------------------------------------------
def credit_key(row):
    return {
        "sid": row["StudentId"],
        "cid": row["CourseId"],
        "grade": "" if row["Grade"] is None else f"{float(row['Grade']):.2f}",
        "attempt": "" if row["Attempt"] is None else row["Attempt"],
    }


def key_from_args():
    try:
        sid = int(request.args["sid"])
        cid = int(request.args["cid"])
        grade = clean_grade(request.args.get("grade", ""))
        attempt = clean_attempt(request.args.get("attempt", ""))
    except (KeyError, ValueError):
        abort(404)
    return sid, cid, grade, attempt


def key_where(sid, cid, grade, attempt, alias=""):
    """WHERE clause (fixed text) and parameters that match exactly one Credit row.

    alias is a hard-coded table alias such as "c." - never user input.
    """
    a = alias
    sql = f"{a}StudentId = ? AND {a}CourseId = ?"
    params = [sid, cid]
    if grade is None:
        sql += f" AND {a}Grade IS NULL"
    else:
        sql += f" AND {a}Grade = ?"
        params.append(grade)
    if attempt is None:
        sql += f" AND {a}Attempt IS NULL"
    else:
        sql += f" AND {a}Attempt = ?"
        params.append(attempt)
    return sql, params


def find_credit(sid, cid, grade, attempt):
    where, params = key_where(sid, cid, grade, attempt, alias="c.")
    rows = query(
        """
        SELECT c.StudentId, c.CourseId, c.Grade, c.Attempt,
               p.FirstName, p.LastName, co.Name AS CourseName, co.Teacher
        FROM Credit AS c
        INNER JOIN Student AS s ON s.StudentId = c.StudentId
        INNER JOIN Person AS p ON p.PersonId = s.PersonId
        INNER JOIN Course AS co ON co.CourseId = c.CourseId
        WHERE """ + where,
        params,
    )
    return rows[0] if rows else None


def student_options():
    return query(
        """
        SELECT s.StudentId, p.FirstName, p.LastName
        FROM Student AS s INNER JOIN Person AS p ON p.PersonId = s.PersonId
        ORDER BY p.LastName, p.FirstName
        """
    )


def course_options():
    return query("SELECT CourseId, Name, Teacher FROM Course ORDER BY Name")


# --------------------------------------------------------------------------
# READ - dashboard: four-table JOIN
# --------------------------------------------------------------------------
DASHBOARD_SQL = """
SELECT c.StudentId, c.CourseId, c.Grade, c.Attempt,
       p.FirstName, p.LastName, s.Email,
       co.Name AS CourseName, co.Teacher
FROM Credit AS c
INNER JOIN Student AS s ON s.StudentId = c.StudentId
INNER JOIN Person AS p ON p.PersonId = s.PersonId
INNER JOIN Course AS co ON co.CourseId = c.CourseId
"""


@app.route("/")
def dashboard():
    q = request.args.get("q", "").strip()[:100]
    sql = DASHBOARD_SQL
    params = []
    if q:
        sql += (" WHERE p.FirstName LIKE ? OR p.LastName LIKE ?"
                " OR co.Name LIKE ? OR co.Teacher LIKE ?")
        params = [f"%{q}%"] * 4
    sql += " ORDER BY p.LastName, p.FirstName, co.Name, c.Attempt"

    credits = query(sql, params)
    for row in credits:
        row["Key"] = credit_key(row)
    stats = query(
        """
        SELECT (SELECT COUNT(*) FROM Student) AS Students,
               (SELECT COUNT(*) FROM Course) AS Courses,
               (SELECT COUNT(*) FROM Credit) AS Credits,
               (SELECT AVG(Grade) FROM Credit) AS AvgGrade
        """
    )[0]
    return render_template("dashboard.html", credits=credits, stats=stats, q=q)


# --------------------------------------------------------------------------
# Credits (operational table): CREATE / UPDATE / DELETE
# --------------------------------------------------------------------------
def credit_form_values(form):
    return (
        clean_int(form, "student_id", "student"),
        clean_int(form, "course_id", "course"),
        clean_grade(form.get("grade")),
        clean_attempt(form.get("attempt")),
    )


@app.route("/credits/new", methods=["GET", "POST"])
def credit_create():
    if request.method == "POST":
        try:
            sid, cid, grade, attempt = credit_form_values(request.form)
            execute("INSERT INTO Credit (StudentId, CourseId, Grade, Attempt) "
                    "VALUES (?, ?, ?, ?)", (sid, cid, grade, attempt))
        except ValidationError as exc:
            flash(str(exc), "error")
        except pyodbc.Error as exc:
            flash(db_error_message(exc), "error")
        else:
            flash("Enrollment record created.", "success")
            return redirect(url_for("dashboard"))
    return render_template("credit_form.html", editing=False, v=request.form,
                           students=student_options(), courses=course_options())


@app.route("/credits/edit", methods=["GET", "POST"])
def credit_edit():
    sid, cid, grade, attempt = key_from_args()
    row = find_credit(sid, cid, grade, attempt)
    if not row:
        flash("Enrollment record not found.", "error")
        return redirect(url_for("dashboard"))

    values = {"student_id": row["StudentId"], "course_id": row["CourseId"],
              "grade": "" if row["Grade"] is None else f"{float(row['Grade']):.2f}",
              "attempt": "" if row["Attempt"] is None else row["Attempt"]}
    if request.method == "POST":
        try:
            new = credit_form_values(request.form)
            where, key_params = key_where(sid, cid, grade, attempt)
            execute("UPDATE Credit SET StudentId = ?, CourseId = ?, Grade = ?, "
                    "Attempt = ? WHERE " + where, list(new) + key_params)
        except ValidationError as exc:
            flash(str(exc), "error")
            values = request.form
        except pyodbc.Error as exc:
            flash(db_error_message(exc), "error")
            values = request.form
        else:
            flash("Enrollment record updated.", "success")
            return redirect(url_for("dashboard"))
    return render_template("credit_form.html", editing=True, v=values,
                           students=student_options(), courses=course_options())


@app.route("/credits/delete", methods=["GET", "POST"])
def credit_delete():
    sid, cid, grade, attempt = key_from_args()
    row = find_credit(sid, cid, grade, attempt)
    if not row:
        flash("Enrollment record not found.", "error")
        return redirect(url_for("dashboard"))
    if request.method == "POST":
        try:
            where, params = key_where(sid, cid, grade, attempt)
            execute("DELETE FROM Credit WHERE " + where, params)
        except pyodbc.Error as exc:
            flash(db_error_message(exc, "delete"), "error")
        else:
            flash("Enrollment record deleted.", "success")
        return redirect(url_for("dashboard"))
    return render_template(
        "confirm_delete.html", title="Delete enrollment record",
        details=[("Student", f"{row['FirstName']} {row['LastName']}"),
                 ("Course", row["CourseName"]), ("Teacher", row["Teacher"]),
                 ("Grade", grade_filter(row["Grade"])),
                 ("Attempt", row["Attempt"] if row["Attempt"] is not None else "-")],
        dependents=0, dependents_label="", back_url=url_for("dashboard"))


# --------------------------------------------------------------------------
# Students (Person + Student, parent of Credit)
# --------------------------------------------------------------------------
def load_student(student_id):
    rows = query(
        """
        SELECT s.StudentId, s.PersonId, s.Email,
               p.FirstName, p.MiddleInitial, p.LastName, p.DateOfBirth
        FROM Student AS s INNER JOIN Person AS p ON p.PersonId = s.PersonId
        WHERE s.StudentId = ?
        """,
        (student_id,),
    )
    return rows[0] if rows else None


def student_form_values(form):
    return {
        "first": clean_text(form, "first_name", "First name", 128),
        "mi": clean_text(form, "middle_initial", "Middle initial", 10, required=False),
        "last": clean_text(form, "last_name", "Last name", 128),
        "dob": clean_dob(form.get("date_of_birth")),
        "email": clean_email(form),
    }


@app.route("/students")
def student_list():
    students = query(
        """
        SELECT s.StudentId, p.FirstName, p.MiddleInitial, p.LastName,
               p.DateOfBirth, s.Email, COUNT(c.CourseId) AS Enrollments
        FROM Student AS s
        INNER JOIN Person AS p ON p.PersonId = s.PersonId
        LEFT JOIN Credit AS c ON c.StudentId = s.StudentId
        GROUP BY s.StudentId, p.FirstName, p.MiddleInitial, p.LastName,
                 p.DateOfBirth, s.Email
        ORDER BY p.LastName, p.FirstName
        """
    )
    return render_template("students.html", students=students)


@app.route("/students/new", methods=["GET", "POST"])
def student_create():
    if request.method == "POST":
        try:
            v = student_form_values(request.form)

            def work(cur):  # parent row first, then child, in ONE transaction
                cur.execute(
                    "INSERT INTO Person (FirstName, MiddleInitial, LastName, DateOfBirth) "
                    "OUTPUT INSERTED.PersonId VALUES (?, ?, ?, ?)",
                    (v["first"], v["mi"], v["last"], v["dob"]))
                person_id = cur.fetchone()[0]
                cur.execute("INSERT INTO Student (PersonId, Email) VALUES (?, ?)",
                            (person_id, v["email"]))

            transaction(work)
        except ValidationError as exc:
            flash(str(exc), "error")
        except pyodbc.Error as exc:
            flash(db_error_message(exc), "error")
        else:
            flash("Student created.", "success")
            return redirect(url_for("student_list"))
    return render_template("student_form.html", editing=False, v=request.form)


@app.route("/students/<int:student_id>/edit", methods=["GET", "POST"])
def student_edit(student_id):
    row = load_student(student_id)
    if not row:
        flash("Student not found.", "error")
        return redirect(url_for("student_list"))

    values = {"first_name": row["FirstName"], "middle_initial": row["MiddleInitial"] or "",
              "last_name": row["LastName"], "date_of_birth": row["DateOfBirth"],
              "email": row["Email"] or ""}
    if request.method == "POST":
        try:
            v = student_form_values(request.form)

            def work(cur):
                cur.execute("UPDATE Person SET FirstName = ?, MiddleInitial = ?, "
                            "LastName = ?, DateOfBirth = ? WHERE PersonId = ?",
                            (v["first"], v["mi"], v["last"], v["dob"], row["PersonId"]))
                cur.execute("UPDATE Student SET Email = ? WHERE StudentId = ?",
                            (v["email"], student_id))

            transaction(work)
        except ValidationError as exc:
            flash(str(exc), "error")
            values = request.form
        except pyodbc.Error as exc:
            flash(db_error_message(exc), "error")
            values = request.form
        else:
            flash("Student updated.", "success")
            return redirect(url_for("student_list"))
    return render_template("student_form.html", editing=True, v=values)


@app.route("/students/<int:student_id>/delete", methods=["GET", "POST"])
def student_delete(student_id):
    row = load_student(student_id)
    if not row:
        flash("Student not found.", "error")
        return redirect(url_for("student_list"))

    if request.method == "POST":
        try:
            def work(cur):
                # Credit -> Student is ON DELETE NO ACTION: if the student has
                # enrollments this DELETE fails and the transaction rolls back.
                cur.execute("DELETE FROM Student WHERE StudentId = ?", (student_id,))
                # Remove the Person too, but only if no other Student uses it.
                cur.execute("DELETE FROM Person WHERE PersonId = ? AND NOT EXISTS "
                            "(SELECT 1 FROM Student WHERE PersonId = ?)",
                            (row["PersonId"], row["PersonId"]))

            transaction(work)
        except pyodbc.Error as exc:
            flash(db_error_message(exc, "delete"), "error")
        else:
            flash("Student deleted.", "success")
        return redirect(url_for("student_list"))

    count = query("SELECT COUNT(*) AS n FROM Credit WHERE StudentId = ?",
                  (student_id,))[0]["n"]
    return render_template(
        "confirm_delete.html", title="Delete student",
        details=[("Name", f"{row['FirstName']} {row['LastName']}"),
                 ("Email", row["Email"] or "-")],
        dependents=count, dependents_label="enrollment record(s)",
        back_url=url_for("student_list"))


# --------------------------------------------------------------------------
# Courses (parent of Credit)
# --------------------------------------------------------------------------
def load_course(course_id):
    rows = query("SELECT CourseId, Name, Teacher FROM Course WHERE CourseId = ?",
                 (course_id,))
    return rows[0] if rows else None


@app.route("/courses")
def course_list():
    courses = query(
        """
        SELECT co.CourseId, co.Name, co.Teacher, COUNT(c.StudentId) AS Enrollments
        FROM Course AS co
        LEFT JOIN Credit AS c ON c.CourseId = co.CourseId
        GROUP BY co.CourseId, co.Name, co.Teacher
        ORDER BY co.Name
        """
    )
    return render_template("courses.html", courses=courses)


@app.route("/courses/new", methods=["GET", "POST"])
def course_create():
    if request.method == "POST":
        try:
            name = clean_text(request.form, "name", "Course name", 50)
            teacher = clean_text(request.form, "teacher", "Teacher", 256)
            execute("INSERT INTO Course (Name, Teacher) VALUES (?, ?)", (name, teacher))
        except ValidationError as exc:
            flash(str(exc), "error")
        except pyodbc.Error as exc:
            flash(db_error_message(exc), "error")
        else:
            flash("Course created.", "success")
            return redirect(url_for("course_list"))
    return render_template("course_form.html", editing=False, v=request.form)


@app.route("/courses/<int:course_id>/edit", methods=["GET", "POST"])
def course_edit(course_id):
    row = load_course(course_id)
    if not row:
        flash("Course not found.", "error")
        return redirect(url_for("course_list"))
    values = {"name": row["Name"], "teacher": row["Teacher"]}
    if request.method == "POST":
        try:
            name = clean_text(request.form, "name", "Course name", 50)
            teacher = clean_text(request.form, "teacher", "Teacher", 256)
            execute("UPDATE Course SET Name = ?, Teacher = ? WHERE CourseId = ?",
                    (name, teacher, course_id))
        except ValidationError as exc:
            flash(str(exc), "error")
            values = request.form
        except pyodbc.Error as exc:
            flash(db_error_message(exc), "error")
            values = request.form
        else:
            flash("Course updated.", "success")
            return redirect(url_for("course_list"))
    return render_template("course_form.html", editing=True, v=values)


@app.route("/courses/<int:course_id>/delete", methods=["GET", "POST"])
def course_delete(course_id):
    row = load_course(course_id)
    if not row:
        flash("Course not found.", "error")
        return redirect(url_for("course_list"))

    if request.method == "POST":
        try:
            execute("DELETE FROM Course WHERE CourseId = ?", (course_id,))
        except pyodbc.Error as exc:
            flash(db_error_message(exc, "delete"), "error")
        else:
            flash("Course deleted.", "success")
        return redirect(url_for("course_list"))

    count = query("SELECT COUNT(*) AS n FROM Credit WHERE CourseId = ?",
                  (course_id,))[0]["n"]
    return render_template(
        "confirm_delete.html", title="Delete course",
        details=[("Course", row["Name"]), ("Teacher", row["Teacher"])],
        dependents=count, dependents_label="enrollment record(s)",
        back_url=url_for("course_list"))


if __name__ == "__main__":
    app.run(host="127.0.0.1", debug=os.environ.get("FLASK_DEBUG") == "1")
