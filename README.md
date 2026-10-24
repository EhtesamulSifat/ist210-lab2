# IST 210 Lab 2 - CRUD App

Flask web app that performs Create / Read / Update / Delete against an Azure SQL
Database. It runs on the four tables from the Lab 1 Microsoft Learn tutorial:
`Person`, `Student`, `Course` and `Credit` (the enrollment/grade table that links
a student to a course).

Built with AI-assisted scaffolding (Claude), then reviewed and tested by hand.

## Rubric mapping

| Requirement | Where it lives |
|---|---|
| Secure connection via environment variables + ODBC | `get_connection()` in `app.py`; values come from `.env` (git-ignored), driver is ODBC Driver 18 with `Encrypt=yes` |
| **Create** form interfaces | `/credits/new`, `/students/new` (writes Person + Student in one transaction), `/courses/new` |
| **Read** dashboard with multi-table JOIN | `/` joins `Credit` + `Student` + `Person` + `Course`; `/students` and `/courses` use `LEFT JOIN` with `COUNT` |
| **Update** interface | `/credits/edit`, `/students/<id>/edit`, `/courses/<id>/edit` |
| **Delete** with `ON DELETE NO ACTION` | Confirmation page first, then the database enforces the rule. Deleting a student or course that still has enrollments is refused by the database and the app shows a clear message |
| SQL injection protection | Every statement uses `?` placeholders; no user input is concatenated into SQL. Also: CSRF tokens on every POST, input validation, no raw DB errors shown to users |
| Referential integrity on inserts/updates | Enrollments for a nonexistent student/course are rejected by the foreign key; duplicate enrollments by `UQ_studentgrades`; grade > 100 by the `CHECK` constraint |

## Setup

1. **Database tables.** In SSMS, open a new query on `ist210-db-sifat` (not master) and run `schema_and_seed.sql`. It creates the four tables (the tutorial's DDL with `ON DELETE NO ACTION` written explicitly) and loads sample data. It's safe to re-run.
2. **ODBC driver.** Install *ODBC Driver 18 for SQL Server* from Microsoft.
3. **Python environment.**
   ```
   python -m venv .venv
   .venv\Scripts\activate
   pip install -r requirements.txt
   ```
4. **Settings.** Copy `.env.example` to `.env` and fill in `DB_PASSWORD`.
5. **Firewall.** Your current public IP must be allowed: Azure Portal -> `brucewayne-dbs` -> Networking -> *Add your client IPv4 address* -> Save. Redo this if you change networks.
6. **Run.**
   ```
   python app.py
   ```
   Open http://127.0.0.1:5000

The database is on the serverless tier and auto-pauses, so the first request after a pause can take up to a minute while it resumes. The app retries automatically.

## Demo checklist (screenshots / screen recording)

1. **Read:** Enrollments dashboard showing the 4-table JOIN, then search for `Pope`.
2. **Create:** add a student, a course, then an enrollment linking them.
3. **Update:** edit that enrollment's grade.
4. **Delete - blocked:** Students -> Delete on *Noe Coleman* -> "Try to delete". The database refuses (foreign key, `ON DELETE NO ACTION`) and nothing changes.
5. **Delete - allowed:** delete the enrollment you created, then the student/course you created (or *Eli Novak* / *Cloud Computing* from the seed data, which have no enrollments).
6. **Live proof:** in SSMS run `SELECT * FROM Credit;` before and after to show the same changes in Azure.

## Tests

`python tests/test_crud.py` runs 40 route-level checks against a SQLite stand-in with the same tables and constraints (no Azure needed). It verifies the app logic: routes, validation, FK handling, parameterization, CSRF, transaction rollback. It is not a substitute for the live demo above, because SQLite and SQL Server differ in places.

## Notes

- `Credit` has no single-column key in the tutorial schema, so a row is identified by all four columns of its unique constraint (student, course, grade, attempt). Blank grade/attempt means `NULL`.
- Never commit `.env`. It is already in `.gitignore`.
